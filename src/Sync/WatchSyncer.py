"""Watch status synchronization from Crunchyroll to AniList.

Ported from the original Crunchyroll-Anilist-Sync SyncManager and
AniListClient rewatch logic.  The class is fully async — each AniList
API call goes through the new async ``AniListClient`` and each CR
page fetch goes through the async ``CrunchyrollClient``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from src.Clients.AnilistClient import AniListClient
from src.Clients.CrunchyrollClient import CrunchyrollClient, CrunchyrollEpisode
from src.Database.Connection import DatabaseManager
from src.Matching.Normalizer import clean_title_for_search
from src.Matching.TitleMatcher import TitleMatcher, get_primary_title
from src.Utils.Config import AppConfig

logger = logging.getLogger(__name__)


class WatchSyncer:
    """Orchestrates Crunchyroll → AniList watch synchronization."""

    def __init__(
        self,
        db: DatabaseManager,
        anilist_client: AniListClient,
        title_matcher: TitleMatcher,
        cr_client: CrunchyrollClient,
        config: AppConfig,
        dry_run: bool = False,
    ) -> None:
        self._db = db
        self._anilist = anilist_client
        self._matcher = title_matcher
        self._cr = cr_client
        self._config = config
        self._dry_run = dry_run

        self._sync_results: dict[str, int] = {}
        self._season_structure_cache: dict[str, dict[int, dict[str, Any]]] = {}
        self._episode_data_cache: dict[tuple[str, int], dict[str, Any]] = {}
        # Key: (user_id, anime_id), Value: highest_progress_processed
        self._processed: dict[tuple[str, int], int] = {}
        # Unique ID for this sync run — used when writing cr_sync_log entries
        self._sync_run_id: str = uuid.uuid4().hex

    # ==================================================================
    # Public entry point
    # ==================================================================

    async def run_sync(self) -> bool:
        """Execute one full Crunchyroll → AniList sync cycle."""
        try:
            if self._dry_run:
                logger.info("DRY RUN MODE — no AniList mutations will be made")
            logger.info("Starting Crunchyroll-AniList sync...")

            users = await self._db.get_users_by_service("anilist")
            if not users:
                logger.warning("No AniList accounts linked - skipping sync")
                return False

            self._reset_results()
            await self._sync_with_smart_pagination(users)
            self._report_results()

            return True

        except Exception:
            logger.exception("Sync process failed")
            return False

    # ==================================================================
    # Smart pagination (ported from SyncManager)
    # ==================================================================

    async def _sync_with_smart_pagination(self, users: list[dict[str, Any]]) -> None:
        """Page through CR history with aggressive early stopping."""
        max_pages = self._config.crunchyroll.max_pages
        page_num = 0
        total_processed = 0
        consecutive_high_skip_pages = 0

        while page_num < max_pages:
            page_num += 1
            logger.info("Processing page %d...", page_num)

            episodes = await self._cr.get_watch_history_page(page_num)
            if not episodes:
                logger.info("No more episodes to process")
                break

            page_stats = await self._process_page_episodes(episodes, users)
            total_processed += len(episodes)

            total_items = (
                page_stats["successful_updates"]
                + page_stats["failed_updates"]
                + page_stats["skipped_episodes"]
            )
            skip_ratio = (
                page_stats["skipped_episodes"] / max(total_items, 1)
                if total_items > 0
                else 0
            )

            logger.info(
                "Page %d stats: %d updates, %d skipped, %d failed (%.0f%% skip)",
                page_num,
                page_stats["successful_updates"],
                page_stats["skipped_episodes"],
                page_stats["failed_updates"],
                skip_ratio * 100,
            )

            # Early stopping
            if page_num == 1:
                if skip_ratio >= 0.7 and page_stats["successful_updates"] <= 3:
                    logger.info(
                        "Stopping early - Page 1 had %d/%d items skipped (%.0f%%) "
                        "with only %d updates. Recent history already synced.",
                        page_stats["skipped_episodes"],
                        total_items,
                        skip_ratio * 100,
                        page_stats["successful_updates"],
                    )
                    break

            if skip_ratio >= 0.7:
                consecutive_high_skip_pages += 1
                logger.info(
                    "High skip ratio (%d/2 consecutive pages)",
                    consecutive_high_skip_pages,
                )
                if consecutive_high_skip_pages >= 2:
                    logger.info(
                        "Stopping early - 2 consecutive pages with >70%% already synced"
                    )
                    break
            else:
                consecutive_high_skip_pages = 0

        logger.info(
            "Processed %d total episodes across %d pages",
            total_processed,
            page_num,
        )

    # ==================================================================
    # Page processing
    # ==================================================================

    async def _process_page_episodes(
        self,
        episodes: list[CrunchyrollEpisode],
        users: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Process all episodes on a single page for all users."""
        page_stats = {
            "successful_updates": 0,
            "failed_updates": 0,
            "skipped_episodes": 0,
        }

        series_progress = self._group_episodes_by_series_and_season(episodes)

        for (series_title, cr_season), latest_episode in series_progress.items():
            try:
                for user in users:
                    success = await self._process_series_entry(
                        series_title, cr_season, latest_episode, user
                    )
                    if success:
                        page_stats["successful_updates"] += 1
                    else:
                        page_stats["skipped_episodes"] += 1
            except Exception as exc:
                logger.error("Error processing %s: %s", series_title, exc)
                page_stats["failed_updates"] += 1

        self._sync_results["successful_updates"] += page_stats["successful_updates"]
        self._sync_results["failed_updates"] += page_stats["failed_updates"]
        self._sync_results["skipped_episodes"] += page_stats["skipped_episodes"]

        return page_stats

    def _group_episodes_by_series_and_season(
        self, episodes: list[CrunchyrollEpisode]
    ) -> dict[tuple[str, int], int]:
        """Group episodes by (series, season), tracking the highest episode."""
        series_season_progress: dict[tuple[str, int], int] = {}

        for ep in episodes:
            if not ep.series_title:
                continue

            if ep.is_movie:
                key = (ep.series_title, 0)
                series_season_progress[key] = 1
                self._episode_data_cache[key] = {
                    "episode_title": ep.episode_title,
                    "season_title": ep.season_title,
                }
            elif ep.episode_number > 0:
                key = (ep.series_title, ep.season)
                if (
                    key not in series_season_progress
                    or ep.episode_number > series_season_progress[key]
                ):
                    series_season_progress[key] = ep.episode_number

        self._sync_results["total_episodes"] = len(episodes)
        return series_season_progress

    # ==================================================================
    # Single series entry processing
    # ==================================================================

    async def _process_series_entry(
        self,
        series_title: str,
        cr_season: int,
        cr_episode: int,
        user: dict[str, Any],
    ) -> bool:
        """Process a single series/season/episode for one user."""
        access_token = user["access_token"]
        user_id = user["user_id"]
        anilist_user_id = user["anilist_id"]

        if cr_season == 0:
            episode_data = self._episode_data_cache.get((series_title, 0), {})
            return await self._process_movie(
                series_title, episode_data, access_token, user_id, anilist_user_id
            )

        try:
            logger.info("Searching AniList for: %s", series_title)

            # Comprehensive search: season-specific + base title
            search_with_season = (
                f"{series_title} season {cr_season}" if cr_season > 1 else series_title
            )
            specific_results = await self._search_anime_comprehensive(
                search_with_season
            )
            all_results = await self._search_anime_comprehensive(series_title)

            search_results: list[dict[str, Any]] = []
            seen_ids: set[int] = set()

            if specific_results:
                for result in specific_results:
                    if result["id"] not in seen_ids:
                        search_results.append(result)
                        seen_ids.add(result["id"])

            if all_results:
                for result in all_results:
                    if result["id"] not in seen_ids:
                        search_results.append(result)
                        seen_ids.add(result["id"])

            if not search_results:
                logger.warning("No AniList results found for: %s", series_title)
                self._sync_results["no_matches_found"] += 1
                return False

            logger.info("Found %d AniList entries", len(search_results))

            # Build season structure
            cache_key = series_title.lower()
            if cache_key in self._season_structure_cache:
                season_structure = self._season_structure_cache[cache_key]
            else:
                season_structure = self._matcher.build_season_structure(
                    search_results, series_title
                )
                self._season_structure_cache[cache_key] = season_structure

            # Determine correct entry + episode
            matched_entry, actual_season, actual_episode = (
                self._matcher.determine_correct_entry_and_episode(
                    series_title, cr_season, cr_episode, season_structure
                )
            )

            if not matched_entry:
                logger.warning(
                    "Could not determine correct AniList entry for %s", series_title
                )
                self._sync_results["no_matches_found"] += 1
                return False

            anime_id = matched_entry["id"]
            anime_title = get_primary_title(matched_entry)

            # Check if already processed at higher episode in this session
            proc_key = (user_id, anime_id)
            if proc_key in self._processed:
                previous = self._processed[proc_key]
                if actual_episode <= previous:
                    logger.debug(
                        "%s S%dE%d already processed at higher ep %d, skipping",
                        series_title,
                        actual_season,
                        actual_episode,
                        previous,
                    )
                    self._sync_results["skipped_episodes"] += 1
                    return False

            # Check if AniList entry needs updating
            if not await self._needs_update(
                anime_id, actual_episode, access_token, anilist_user_id
            ):
                logger.debug(
                    "%s S%dE%d already synced, skipping",
                    series_title,
                    actual_season,
                    actual_episode,
                )
                self._sync_results["skipped_episodes"] += 1
                return False

            total_episodes = matched_entry.get("episodes")

            # Log match type
            if actual_season == cr_season and actual_episode == cr_episode:
                logger.info(
                    "Direct match: %s S%dE%d",
                    anime_title,
                    actual_season,
                    actual_episode,
                )
                self._sync_results["season_matches"] += 1
            else:
                logger.info(
                    "Converted: %s S%dE%d -> %s S%dE%d",
                    series_title,
                    cr_season,
                    cr_episode,
                    anime_title,
                    actual_season,
                    actual_episode,
                )
                self._sync_results["episode_conversions"] += 1
                if actual_season != cr_season:
                    self._sync_results["season_mismatches"] += 1

            # Perform the update with rewatch logic
            result = await self._update_with_rewatch_logic(
                anime_id,
                actual_episode,
                total_episodes,
                access_token,
                anilist_user_id,
                user_id=user_id,
                show_title=anime_title,
            )

            if result["success"]:
                logger.info(
                    "Successfully updated %s to episode %d",
                    anime_title,
                    actual_episode,
                )
                if result["was_rewatch"]:
                    self._sync_results["rewatches_detected"] += 1
                    if result["was_completion"]:
                        self._sync_results["rewatches_completed"] += 1
                elif result["was_new_series"]:
                    self._sync_results["new_series_started"] += 1

                self._processed[proc_key] = actual_episode
            else:
                logger.error("Failed to update %s", anime_title)

            return result["success"]

        except Exception as exc:
            logger.error("Error processing %s: %s", series_title, exc)
            return False

    # ==================================================================
    # Movie processing
    # ==================================================================

    async def _process_movie(
        self,
        series_title: str,
        episode_data: dict[str, Any],
        access_token: str,
        user_id: str,
        anilist_user_id: int,
    ) -> bool:
        """Process movie entries with skip detection."""
        try:
            logger.info("Processing movie: %s", series_title)

            if episode_data:
                episode_title = episode_data.get("episode_title", "").strip()
                season_title = episode_data.get("season_title", "").strip()
                skip_indicators = [
                    "compilation",
                    "recap",
                    "summary",
                    "highlight",
                    "digest",
                ]
                combined = f"{episode_title} {season_title}".lower()
                for ind in skip_indicators:
                    if ind in combined:
                        logger.info(
                            "Skipping compilation/recap: %s - %s",
                            series_title,
                            season_title,
                        )
                        self._sync_results["movies_skipped"] += 1
                        return False

            # Search for the movie
            search_queries = []
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
                    if fmt not in ["MOVIE", "SPECIAL"]:
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
                logger.warning("No movie match found for: %s", series_title)
                self._sync_results["movies_skipped"] += 1
                return False

            anime_title = get_primary_title(best_match)
            anime_id = best_match["id"]
            logger.info(
                "Found movie: %s (similarity: %.2f)", anime_title, best_similarity
            )

            proc_key = (user_id, anime_id)
            if proc_key in self._processed:
                logger.debug("Movie %s already processed, skipping", anime_title)
                self._sync_results["movies_skipped"] += 1
                return False

            if not await self._needs_update(anime_id, 1, access_token, anilist_user_id):
                logger.info("Movie %s already completed, skipping", anime_title)
                self._sync_results["movies_skipped"] += 1
                return False

            result = await self._update_with_rewatch_logic(
                anime_id,
                1,
                1,
                access_token,
                anilist_user_id,
                user_id=user_id,
                show_title=anime_title,
            )

            if result["success"]:
                logger.info("Updated movie %s", anime_title)
                self._sync_results["movies_completed"] += 1
                if result["was_rewatch"]:
                    self._sync_results["rewatches_detected"] += 1
                    if result["was_completion"]:
                        self._sync_results["rewatches_completed"] += 1
                self._processed[proc_key] = 1
            else:
                logger.error("Failed to update movie %s", anime_title)

            return result["success"]

        except Exception as exc:
            logger.error("Error processing movie %s: %s", series_title, exc)
            return False

    # ==================================================================
    # AniList helpers
    # ==================================================================

    async def _search_anime_comprehensive(
        self, series_title: str
    ) -> list[dict[str, Any]]:
        """Search AniList, falling back to space-removed title if needed."""
        results = await self._anilist.search_anime(series_title) or []

        if not results or len(results) < 3:
            no_space = series_title.replace(" ", "")
            if no_space != series_title:
                space_removed_results = await self._anilist.search_anime(no_space)
                if space_removed_results:
                    logger.debug("Found results by removing spaces: %s", no_space)
                    seen_ids = {r["id"] for r in results}
                    for r in space_removed_results:
                        if r["id"] not in seen_ids:
                            results.insert(0, r)
                            seen_ids.add(r["id"])

        return results

    async def _needs_update(
        self,
        anime_id: int,
        target_progress: int,
        access_token: str,
        anilist_user_id: int,
    ) -> bool:
        """Check if an anime entry needs updating, accounting for rewatches.

        Ported verbatim from SyncManager._needs_update().
        """
        try:
            existing = await self._anilist.get_anime_list_entry(
                anime_id, access_token, anilist_user_id
            )

            if not existing:
                return True

            current_progress = existing.get("progress", 0)
            current_status = existing.get("status")

            # Same progress + already COMPLETED/CURRENT => skip
            if current_progress == target_progress:
                if current_status in ("COMPLETED", "CURRENT"):
                    logger.debug(
                        "Anime %d already at ep %d (%s) - skipping",
                        anime_id,
                        target_progress,
                        current_status,
                    )
                    return False

            # COMPLETED + target < current: only rewatch if ep <= 3
            if current_status == "COMPLETED" and target_progress < current_progress:
                if target_progress <= 3:
                    logger.debug(
                        "Anime %d rewatch detected: COMPLETED at %d, now ep %d",
                        anime_id,
                        current_progress,
                        target_progress,
                    )
                    return True
                else:
                    logger.debug(
                        "Anime %d skipping old ep %d (already at %d, %s)",
                        anime_id,
                        target_progress,
                        current_progress,
                        current_status,
                    )
                    return False

            # Normal: skip if already at or past this episode
            if current_progress >= target_progress:
                logger.debug(
                    "Anime %d already at ep %d (target: %d) - skipping",
                    anime_id,
                    current_progress,
                    target_progress,
                )
                return False

            return True

        except Exception as exc:
            logger.debug("Error checking update need: %s", exc)
            return True

    # ==================================================================
    # Rewatch logic (ported from old AniListClient)
    # ==================================================================

    async def _update_with_rewatch_logic(
        self,
        anime_id: int,
        progress: int,
        total_episodes: int | None,
        access_token: str,
        anilist_user_id: int,
        user_id: str = "",
        show_title: str = "",
    ) -> dict[str, Any]:
        """Update anime progress with intelligent rewatch detection.

        Returns dict with: success, was_rewatch, was_completion,
        was_new_series, repeat_count.
        """
        result: dict[str, Any] = {
            "success": False,
            "was_rewatch": False,
            "was_completion": False,
            "was_new_series": False,
            "repeat_count": 0,
        }

        try:
            existing = await self._anilist.get_anime_list_entry(
                anime_id, access_token, anilist_user_id
            )

            if existing:
                if self._is_rewatch_scenario(existing, progress, total_episodes):
                    result["was_rewatch"] = True
                    result["repeat_count"] = existing.get("repeat", 0)

                    if total_episodes and progress >= total_episodes:
                        result["was_completion"] = True
                        result["repeat_count"] += 1

                    if self._dry_run:
                        logger.info(
                            "[DRY RUN] Would update rewatch #%d: " "anime %d ep %d/%s",
                            result["repeat_count"],
                            anime_id,
                            progress,
                            total_episodes or "?",
                        )
                        result["success"] = True
                        return result

                    success = await self._handle_rewatch_update(
                        anime_id, progress, existing, total_episodes, access_token
                    )
                else:
                    current_status = existing.get("status")
                    if (
                        current_status in ("PLANNING", None)
                        or existing.get("progress", 0) == 0
                    ):
                        result["was_new_series"] = True

                    if total_episodes and progress >= total_episodes:
                        result["was_completion"] = True

                    if self._dry_run:
                        logger.info(
                            "[DRY RUN] Would update: anime %d ep %d/%s "
                            "(current: %s ep %d)",
                            anime_id,
                            progress,
                            total_episodes or "?",
                            current_status or "NEW",
                            existing.get("progress", 0),
                        )
                        result["success"] = True
                        return result

                    success = await self._handle_normal_update(
                        anime_id, progress, existing, total_episodes, access_token
                    )
            else:
                result["was_new_series"] = True
                if total_episodes and progress >= total_episodes:
                    result["was_completion"] = True

                if self._dry_run:
                    logger.info(
                        "[DRY RUN] Would add new: anime %d ep %d/%s",
                        anime_id,
                        progress,
                        total_episodes or "?",
                    )
                    result["success"] = True
                    return result

                success = await self._handle_new_watch(
                    anime_id, progress, total_episodes, access_token
                )

            result["success"] = success

            # Write an audit entry to cr_sync_log so auto-approve runs appear
            # in the history tab alongside manually-applied preview runs.
            if success and not self._dry_run and user_id:
                before_status = (existing or {}).get("status") or ""
                before_progress = (existing or {}).get("progress") or 0
                after_status = (
                    "COMPLETED"
                    if (total_episodes and progress >= total_episodes)
                    else "CURRENT"
                )
                try:
                    await self._db.insert_cr_sync_log_entry(
                        user_id=user_id,
                        anilist_id=anime_id,
                        show_title=show_title,
                        before_status=before_status,
                        before_progress=before_progress,
                        after_status=after_status,
                        after_progress=progress,
                        sync_run_id=self._sync_run_id,
                        cr_sync_preview_id=None,
                    )
                except Exception as log_exc:
                    logger.warning(
                        "Failed to write cr_sync_log for %s: %s", show_title, log_exc
                    )

            return result

        except Exception as exc:
            logger.error("Error in rewatch logic: %s", exc)
            return result

    @staticmethod
    def _is_rewatch_scenario(
        existing_entry: dict[str, Any],
        progress: int,
        total_episodes: int | None,
    ) -> bool:
        """A rewatch is detected when repeat > 0 (already in a rewatch)."""
        current_repeat = existing_entry.get("repeat", 0)
        if current_repeat > 0:
            logger.debug("In rewatch scenario (repeat count: %d)", current_repeat)
            return True
        return False

    async def _handle_rewatch_update(
        self,
        anime_id: int,
        progress: int,
        existing: dict[str, Any],
        total_episodes: int | None,
        access_token: str,
    ) -> bool:
        """Handle progress updates for ongoing rewatches (repeat > 0)."""
        current_repeat = existing.get("repeat", 0)

        if total_episodes and progress >= total_episodes:
            status = "COMPLETED"
            logger.info("Completed rewatch #%d", current_repeat)
        else:
            status = "CURRENT"
            logger.info("Continuing rewatch #%d (episode %d)", current_repeat, progress)

        resp = await self._anilist.update_anime_progress(
            anime_id, access_token, progress, status, current_repeat
        )
        return bool(resp)

    async def _handle_normal_update(
        self,
        anime_id: int,
        progress: int,
        existing: dict[str, Any],
        total_episodes: int | None,
        access_token: str,
    ) -> bool:
        """Handle normal updates. Detects start-of-rewatch when COMPLETED + ep <= 3."""
        current_status = existing.get("status")
        current_progress = existing.get("progress", 0)
        current_repeat = existing.get("repeat", 0)

        if total_episodes and progress >= total_episodes:
            new_status = "COMPLETED"
            new_repeat = current_repeat

            if current_status != "COMPLETED" or current_progress < total_episodes:
                logger.info(
                    "Completing series (episode %d/%d)", progress, total_episodes
                )
            else:
                logger.info(
                    "Series already completed, maintaining status (%d/%d)",
                    progress,
                    total_episodes,
                )
        else:
            new_status = "CURRENT"

            # CRITICAL: Only increment rewatch if rewatching from the beginning
            if current_status == "COMPLETED" and progress <= 3:
                new_repeat = current_repeat + 1
                logger.info(
                    "Starting rewatch #%d (was COMPLETED, now ep %d)",
                    new_repeat,
                    progress,
                )
            elif current_status == "COMPLETED" and progress > current_progress:
                new_repeat = current_repeat
                logger.info(
                    "Continuing beyond previous completion (episode %d)", progress
                )
            else:
                new_repeat = current_repeat
                if current_status in ("PLANNING", "PAUSED"):
                    logger.info("Starting to watch (episode %d)", progress)
                else:
                    logger.info("Updating progress (episode %d)", progress)

        resp = await self._anilist.update_anime_progress(
            anime_id, access_token, progress, new_status, new_repeat
        )
        return bool(resp)

    async def _handle_new_watch(
        self,
        anime_id: int,
        progress: int,
        total_episodes: int | None,
        access_token: str,
    ) -> bool:
        """Handle updates for new anime (no existing entry)."""
        if total_episodes and progress >= total_episodes:
            status = "COMPLETED"
            logger.info(
                "Completing new series (episode %d/%d)", progress, total_episodes
            )
        else:
            status = "CURRENT"
            logger.info("Starting new series (episode %d)", progress)

        resp = await self._anilist.update_anime_progress(
            anime_id, access_token, progress, status
        )
        return bool(resp)

    # ==================================================================
    # Results
    # ==================================================================

    def _reset_results(self) -> None:
        self._sync_results = {
            "total_episodes": 0,
            "successful_updates": 0,
            "failed_updates": 0,
            "skipped_episodes": 0,
            "season_matches": 0,
            "season_mismatches": 0,
            "no_matches_found": 0,
            "movies_completed": 0,
            "movies_skipped": 0,
            "episode_conversions": 0,
            "rewatches_detected": 0,
            "rewatches_completed": 0,
            "new_series_started": 0,
        }
        self._season_structure_cache.clear()
        self._episode_data_cache.clear()
        self._processed.clear()

    def _report_results(self) -> None:
        """Log sync results summary."""
        r = self._sync_results

        prefix = "DRY RUN " if self._dry_run else ""
        logger.info("=" * 60)
        logger.info("%sSync Results:", prefix)
        logger.info("=" * 60)
        logger.info("  Total episodes found: %d", r["total_episodes"])
        logger.info("  Successful updates: %d", r["successful_updates"])
        logger.info("  Failed updates: %d", r["failed_updates"])
        logger.info("  Skipped episodes: %d", r["skipped_episodes"])
        logger.info("  Direct matches: %d", r["season_matches"])
        logger.info("  Episode conversions: %d", r["episode_conversions"])
        logger.info("  Season corrections: %d", r["season_mismatches"])
        logger.info("  No matches found: %d", r["no_matches_found"])
        logger.info("  Movies completed: %d", r["movies_completed"])
        logger.info("  Movies skipped: %d", r["movies_skipped"])
        logger.info("  ---")
        logger.info("  Rewatches detected: %d", r["rewatches_detected"])
        logger.info("  Rewatches completed: %d", r["rewatches_completed"])
        logger.info("  New series started: %d", r["new_series_started"])

        if r["successful_updates"] > 0:
            total_attempts = r["successful_updates"] + r["failed_updates"]
            success_rate = (r["successful_updates"] / total_attempts) * 100
            logger.info("  Success rate: %.1f%%", success_rate)

        logger.info("=" * 60)

        if r["episode_conversions"] > 0:
            logger.info(
                "Episode numbers were automatically converted "
                "from absolute to per-season numbering"
            )
        if r["rewatches_detected"] > 0:
            logger.info(
                "Rewatch detection is active - completed series "
                "are marked as 'watching' when rewatched"
            )
