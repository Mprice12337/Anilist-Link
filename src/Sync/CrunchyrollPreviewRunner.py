"""Crunchyroll dry-run preview — computes proposed changes without mutating AniList.

Mirrors WatchSyncer matching logic but instead of applying updates, writes
rows to ``cr_sync_preview`` with before/after state for user review.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from src.Clients.AnilistClient import AniListClient
from src.Clients.CrunchyrollClient import CrunchyrollClient, CrunchyrollEpisode
from src.Database.Connection import DatabaseManager
from src.Matching.Normalizer import clean_title_for_search
from src.Matching.TitleMatcher import TitleMatcher, get_primary_title
from src.Utils.Config import AppConfig

logger = logging.getLogger(__name__)


@dataclass
class CrunchyrollPreviewProgress:
    """Tracks progress of an in-flight CR preview scan for the floating widget."""

    status: str = "pending"  # pending | scanning | complete | error
    current_page: int = 0
    max_pages: int = 0
    entries_found: int = 0
    run_id: str = ""
    detail: str = ""
    error: str = ""


class CrunchyrollPreviewRunner:
    """Orchestrates a Crunchyroll→AniList dry-run preview scan.

    Produces rows in ``cr_sync_preview`` without touching AniList.
    Each row represents one AniList entry that would be added/updated.
    """

    def __init__(
        self,
        db: DatabaseManager,
        anilist_client: AniListClient,
        title_matcher: TitleMatcher,
        cr_client: CrunchyrollClient,
        config: AppConfig,
        progress: CrunchyrollPreviewProgress | None = None,
    ) -> None:
        self._db = db
        self._anilist = anilist_client
        self._matcher = title_matcher
        self._cr = cr_client
        self._config = config
        self.progress = progress or CrunchyrollPreviewProgress()

        self._run_id: str = ""
        self._season_structure_cache: dict[str, dict[int, dict[str, Any]]] = {}
        self._episode_data_cache: dict[tuple[str, int], dict[str, Any]] = {}
        self._preview_rows: list[dict[str, Any]] = []
        # Dedup by (user_id, anilist_id) to avoid duplicate preview rows
        self._seen: set[tuple[str, int]] = set()
        # Raw episode list per (series_title, cr_season) for episode detail view
        self._raw_episodes: dict[tuple[str, int], list[dict[str, Any]]] = {}

    # ==================================================================
    # Public entry point
    # ==================================================================

    async def run_preview(self, user: dict[str, Any]) -> str:
        """Execute one full preview scan for a single user.

        Returns the run_id that was used.  Rows are written to
        ``cr_sync_preview`` before returning.
        """
        self._run_id = str(uuid.uuid4())
        self._season_structure_cache.clear()
        self._episode_data_cache.clear()
        self._preview_rows.clear()
        self._seen.clear()
        self._raw_episodes.clear()

        logger.info(
            "Starting CR preview scan for user %s (run_id=%s)",
            user.get("username"),
            self._run_id,
        )

        self.progress.status = "scanning"
        self.progress.run_id = self._run_id
        self.progress.max_pages = self._config.crunchyroll.max_pages

        try:
            await self._scan_with_pagination(user)
        except asyncio.CancelledError:
            logger.info("CR preview scan cancelled by user")
            self.progress.status = "cancelled"
            self.progress.error = "Cancelled by user"
            raise
        except Exception as exc:
            logger.exception("CR preview scan failed")
            self.progress.status = "error"
            self.progress.error = str(exc)
            return self._run_id

        if self._preview_rows:
            await self._db.insert_cr_preview_rows(self._preview_rows)
            logger.info(
                "Preview run %s written %d rows", self._run_id, len(self._preview_rows)
            )
        else:
            logger.info("Preview run %s produced no rows", self._run_id)

        self.progress.status = "complete"
        self.progress.entries_found = len(self._preview_rows)
        return self._run_id

    # ==================================================================
    # Pagination (mirrors WatchSyncer._sync_with_smart_pagination)
    # ==================================================================

    async def _scan_with_pagination(self, user: dict[str, Any]) -> None:
        max_pages = self._config.crunchyroll.max_pages
        page_num = 0
        consecutive_high_skip_pages = 0

        while page_num < max_pages:
            page_num += 1
            self.progress.current_page = page_num
            self.progress.detail = f"Page {page_num}"
            logger.info("Preview page %d...", page_num)

            episodes = await self._cr.get_watch_history_page(page_num)
            if not episodes:
                break

            skipped = await self._process_page(episodes, user)
            self.progress.entries_found = len(self._preview_rows)
            total = len(episodes)
            skip_ratio = skipped / max(total, 1)

            if page_num == 1 and skip_ratio >= 0.7 and not self._preview_rows:
                logger.info(
                    "Page 1 had %.0f%% skip ratio and no new entries — already synced",
                    skip_ratio * 100,
                )
                break

            if skip_ratio >= 0.7:
                consecutive_high_skip_pages += 1
                if consecutive_high_skip_pages >= 2:
                    break
            else:
                consecutive_high_skip_pages = 0

    # ==================================================================
    # Page processing
    # ==================================================================

    async def _process_page(
        self,
        episodes: list[CrunchyrollEpisode],
        user: dict[str, Any],
    ) -> int:
        """Process one page of CR history. Returns number of skipped items."""
        skipped = 0
        series_progress = self._group_episodes(episodes)

        for (series_title, cr_season), cr_episode in series_progress.items():
            try:
                produced = await self._process_series(
                    series_title, cr_season, cr_episode, user
                )
                if not produced:
                    skipped += 1
            except Exception as exc:
                logger.error("Preview error for %s: %s", series_title, exc)
                skipped += 1

        return skipped

    def _group_episodes(
        self, episodes: list[CrunchyrollEpisode]
    ) -> dict[tuple[str, int], int]:
        """Group episodes by (series, season), tracking the highest episode number."""
        progress: dict[tuple[str, int], int] = {}

        for ep in episodes:
            if not ep.series_title:
                continue

            if ep.is_movie:
                key = (ep.series_title, 0)
                progress[key] = 1
                self._episode_data_cache[key] = {
                    "episode_title": ep.episode_title,
                    "season_title": ep.season_title,
                }
                if key not in self._raw_episodes:
                    self._raw_episodes[key] = []
                if not self._raw_episodes[key]:
                    self._raw_episodes[key].append(
                        {
                            "cr_season": 0,
                            "cr_episode": 1,
                            "episode_title": ep.episode_title,
                            "season_title": ep.season_title,
                            "watch_date": ep.watch_date,
                            "is_movie": True,
                        }
                    )
            elif ep.episode_number > 0:
                key = (ep.series_title, ep.season)
                if key not in progress or ep.episode_number > progress[key]:
                    progress[key] = ep.episode_number
                if key not in self._raw_episodes:
                    self._raw_episodes[key] = []
                known = {e["cr_episode"] for e in self._raw_episodes[key]}
                if ep.episode_number not in known:
                    self._raw_episodes[key].append(
                        {
                            "cr_season": ep.season,
                            "cr_episode": ep.episode_number,
                            "episode_title": ep.episode_title,
                            "season_title": ep.season_title,
                            "watch_date": ep.watch_date,
                            "is_movie": False,
                        }
                    )

        return progress

    # ==================================================================
    # Per-series preview computation
    # ==================================================================

    async def _process_series(
        self,
        series_title: str,
        cr_season: int,
        cr_episode: int,
        user: dict[str, Any],
    ) -> bool:
        """Compute the preview row for one CR series entry.

        Returns True if a row was produced.
        """
        user_id = user["user_id"]
        access_token = user["access_token"]
        anilist_user_id = user["anilist_id"]

        if cr_season == 0:
            return await self._process_movie_preview(
                series_title, access_token, user_id, anilist_user_id
            )

        # AniList search (same as WatchSyncer)
        search_with_season = (
            f"{series_title} season {cr_season}" if cr_season > 1 else series_title
        )
        specific = await self._search_comprehensive(search_with_season)
        base = await self._search_comprehensive(series_title)

        search_results: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for r in specific + base:
            if r["id"] not in seen_ids:
                search_results.append(r)
                seen_ids.add(r["id"])

        if not search_results:
            return False

        cache_key = series_title.lower()
        if cache_key not in self._season_structure_cache:
            self._season_structure_cache[cache_key] = (
                self._matcher.build_season_structure(search_results, series_title)
            )
        season_structure = self._season_structure_cache[cache_key]

        matched_entry, actual_season, actual_episode = (
            self._matcher.determine_correct_entry_and_episode(
                series_title, cr_season, cr_episode, season_structure
            )
        )

        if not matched_entry:
            return False

        anilist_id = matched_entry["id"]
        anilist_title = get_primary_title(matched_entry)
        confidence = self._matcher.calculate_title_similarity(
            series_title, matched_entry
        )

        dedup_key = (user_id, anilist_id)
        if dedup_key in self._seen:
            return False
        self._seen.add(dedup_key)

        # Fetch current state from AniList
        existing = await self._anilist.get_anime_list_entry(
            anilist_id, access_token, anilist_user_id
        )
        current_status = (existing or {}).get("status") or ""
        current_progress = (existing or {}).get("progress") or 0

        total_episodes = matched_entry.get("episodes")
        if total_episodes and actual_episode >= total_episodes:
            proposed_status = "COMPLETED"
        else:
            proposed_status = "CURRENT"

        if not existing:
            action = "add"
        elif current_progress >= actual_episode and current_status in (
            "COMPLETED",
            "CURRENT",
        ):
            action = "skip"
        else:
            action = "update"

        raw_eps = sorted(
            self._raw_episodes.get((series_title, cr_season), []),
            key=lambda e: (e["cr_season"], e["cr_episode"]),
        )
        self._preview_rows.append(
            {
                "user_id": user_id,
                "run_id": self._run_id,
                "cr_title": series_title,
                "anilist_id": anilist_id,
                "anilist_title": anilist_title,
                "confidence": round(confidence, 4),
                "proposed_status": proposed_status,
                "proposed_progress": actual_episode,
                "current_status": current_status,
                "current_progress": current_progress,
                "action": action,
                "episodes_json": json.dumps(raw_eps),
            }
        )
        return True

    async def _process_movie_preview(
        self,
        series_title: str,
        access_token: str,
        user_id: str,
        anilist_user_id: int,
    ) -> bool:
        episode_data = self._episode_data_cache.get((series_title, 0), {})

        if episode_data:
            combined = (
                f"{episode_data.get('episode_title', '')} "
                f"{episode_data.get('season_title', '')}".lower()
            )
            for ind in ("compilation", "recap", "summary", "highlight", "digest"):
                if ind in combined:
                    return False

        search_queries: list[str] = []
        movie_title: str | None = None
        if episode_data:
            movie_title = episode_data.get("season_title", "").strip()
            if movie_title and movie_title != series_title:
                search_queries.append(movie_title)
                search_queries.append(clean_title_for_search(movie_title))

        search_queries.extend(
            [
                series_title,
                f"{series_title} movie",
                clean_title_for_search(series_title),
            ]
        )

        best_match: dict[str, Any] | None = None
        best_similarity = 0.0

        for query in search_queries:
            results = await self._anilist.search_anime(query)
            if not results:
                continue
            for result in results:
                fmt = (result.get("format", "") or "").upper()
                if fmt not in ("MOVIE", "SPECIAL"):
                    continue
                similarity = self._matcher.calculate_title_similarity(
                    series_title, result
                )
                if movie_title and movie_title != series_title:
                    movie_sim = self._matcher.calculate_title_similarity(
                        movie_title, result
                    )
                    similarity = max(similarity, movie_sim)
                if similarity > best_similarity and similarity >= 0.75:
                    best_similarity = similarity
                    best_match = result

        if not best_match:
            return False

        anilist_id = best_match["id"]
        anilist_title = get_primary_title(best_match)

        dedup_key = (user_id, anilist_id)
        if dedup_key in self._seen:
            return False
        self._seen.add(dedup_key)

        existing = await self._anilist.get_anime_list_entry(
            anilist_id, access_token, anilist_user_id
        )
        current_status = (existing or {}).get("status") or ""
        current_progress = (existing or {}).get("progress") or 0

        if not existing:
            action = "add"
        elif current_status == "COMPLETED":
            action = "skip"
        else:
            action = "update"

        raw_eps = self._raw_episodes.get((series_title, 0), [])
        self._preview_rows.append(
            {
                "user_id": user_id,
                "run_id": self._run_id,
                "cr_title": series_title,
                "anilist_id": anilist_id,
                "anilist_title": anilist_title,
                "confidence": round(best_similarity, 4),
                "proposed_status": "COMPLETED",
                "proposed_progress": 1,
                "current_status": current_status,
                "current_progress": current_progress,
                "action": action,
                "episodes_json": json.dumps(raw_eps),
            }
        )
        return True

    # ==================================================================
    # Search helper
    # ==================================================================

    async def _search_comprehensive(
        self,
        title: str,
    ) -> list[dict[str, Any]]:
        results = await self._anilist.search_anime(title) or []
        if not results or len(results) < 3:
            no_space = title.replace(" ", "")
            if no_space != title:
                extra = await self._anilist.search_anime(no_space)
                if extra:
                    seen = {r["id"] for r in results}
                    for r in extra:
                        if r["id"] not in seen:
                            results.insert(0, r)
                            seen.add(r["id"])
        return results
