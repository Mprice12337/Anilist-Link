"""Jellyfin metadata scanning pipeline: scan, match, cache, apply."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from src.Clients.AnilistClient import AniListClient
from src.Clients.JellyfinClient import JellyfinClient
from src.Database.Connection import DatabaseManager
from src.Matching.Normalizer import clean_title_for_search
from src.Matching.TitleMatcher import TitleMatcher, get_primary_title
from src.Scanner.MetadataScanner import (
    ScanItemDetail,
    ScanProgress,
    ScanResults,
    _safe_parse_genres,
)
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Utils.Config import AppConfig

logger = logging.getLogger(__name__)


class JellyfinMetadataScanner:
    """Orchestrates the scan → match → cache → apply pipeline for Jellyfin."""

    def __init__(
        self,
        db: DatabaseManager,
        anilist_client: AniListClient,
        title_matcher: TitleMatcher,
        jellyfin_client: JellyfinClient,
        config: AppConfig,
        group_builder: SeriesGroupBuilder | None = None,
    ) -> None:
        self._db = db
        self._anilist = anilist_client
        self._matcher = title_matcher
        self._jellyfin = jellyfin_client
        self._config = config
        self._group_builder = group_builder

    async def run_scan(
        self,
        dry_run: bool = False,
        library_ids: list[str] | None = None,
        preview: bool = False,
        progress: ScanProgress | None = None,
    ) -> ScanResults:
        """Run a full Jellyfin metadata scan.

        If *dry_run* is True, matches are logged but metadata is NOT written.
        If *preview* is True, populate per-item details without storing
        mappings or writing metadata.
        If *progress* is provided, it is updated in-place.
        """
        results = ScanResults()
        mode = " (PREVIEW)" if preview else (" (DRY RUN)" if dry_run else "")
        logger.info("Starting Jellyfin metadata scan%s", mode)

        if progress:
            progress.status = "running"
            progress.started_at = time.monotonic()
            self._anilist.on_rate_limit_wait = lambda secs: setattr(
                progress, "current_title", f"Rate limited — waiting {secs}s..."
            )

        try:
            return await self._run_scan_inner(
                results, mode, dry_run, library_ids, preview, progress
            )
        finally:
            if progress:
                if progress.status == "running":
                    progress.status = "complete"
                progress.current_title = ""
                self._anilist.on_rate_limit_wait = None

    async def _run_scan_inner(
        self,
        results: ScanResults,
        mode: str,
        dry_run: bool,
        library_ids: list[str] | None,
        preview: bool,
        progress: ScanProgress | None,
    ) -> ScanResults:
        try:
            libraries = await self._jellyfin.get_libraries()
        except Exception:
            logger.exception("Failed to connect to Jellyfin server")
            results.errors.append("Failed to connect to Jellyfin server")
            if progress:
                progress.status = "error"
                progress.error_message = "Failed to connect to Jellyfin server"
            return results

        eligible = [
            lib for lib in libraries if lib.type in ("tvshows", "movies", "mixed", "")
        ]
        if library_ids:
            id_set = set(library_ids)
            eligible = [lib for lib in eligible if lib.id in id_set]

        if not eligible:
            logger.warning("No eligible Jellyfin libraries found")
            return results

        logger.info(
            "Found %d Jellyfin librar%s: %s",
            len(eligible),
            "y" if len(eligible) == 1 else "ies",
            ", ".join(lib.name for lib in eligible),
        )

        library_shows: dict[str, list] = {}
        if progress:
            progress.current_title = "Counting shows..."
        for lib in eligible:
            try:
                shows = await self._jellyfin.get_library_shows(lib.id)
                library_shows[lib.id] = shows
            except Exception:
                logger.exception(
                    "Failed to get shows from Jellyfin library %s", lib.name
                )
                results.errors.append(f"Failed to read library: {lib.name}")
                results.failed += 1
        if progress:
            progress.total = sum(len(s) for s in library_shows.values())

        effective_dry_run = dry_run or preview
        for lib in eligible:
            shows = library_shows.get(lib.id) or []
            if not shows:
                continue
            await self._scan_library_shows(
                shows, lib.id, lib.name, results, effective_dry_run, preview, progress
            )

        logger.info(
            "Jellyfin scan complete%s: %d matched, %d skipped, %d failed",
            mode,
            results.matched,
            results.skipped,
            results.failed,
        )
        return results

    async def _scan_library_shows(
        self,
        shows: list,
        library_id: str,
        library_title: str,
        results: ScanResults,
        dry_run: bool,
        preview: bool,
        progress: ScanProgress | None,
    ) -> None:
        logger.info(
            "Scanning Jellyfin library: %s (%d shows)", library_title, len(shows)
        )
        for show in shows:
            folder_name = os.path.basename(show.path) if show.path else show.name
            # Always populate jellyfin_media so the browser can display items
            try:
                await self._db.upsert_jellyfin_media(
                    item_id=show.item_id,
                    title=show.name,
                    year=show.year,
                    path=show.path or "",
                    library_id=library_id,
                    library_name=library_title,
                    folder_name=folder_name,
                )
            except Exception:
                logger.debug("Failed to upsert jellyfin_media for %s", show.name)
            if progress:
                progress.current_title = folder_name or show.name
            await self._process_show(
                show.item_id,
                show.name,
                results,
                dry_run,
                preview,
                library_title,
                show.year,
                folder_name=folder_name,
            )
            if progress:
                progress.scanned += 1

    async def _process_show(
        self,
        item_id: str,
        title: str,
        results: ScanResults,
        dry_run: bool,
        preview: bool,
        library_title: str,
        year: int | None,
        folder_name: str,
    ) -> None:
        try:
            # 1. Check manual override
            override = await self._db.get_override("jellyfin", item_id)
            if override:
                anilist_id = override["anilist_id"]
                logger.info("  [override] %s -> AniList %d", title, anilist_id)
                if not preview:
                    await self._apply_anilist_metadata(
                        item_id, title, anilist_id, 1.0, "manual_override", dry_run
                    )
                else:
                    results.items.append(
                        ScanItemDetail(
                            rating_key=item_id,
                            plex_title=title,
                            plex_year=year,
                            library_title=library_title,
                            status="matched",
                            reason="manual override",
                            anilist_id=anilist_id,
                            confidence=1.0,
                            match_method="manual_override",
                            folder_name=folder_name,
                        )
                    )
                results.matched += 1
                return

            # 2. Check existing mapping
            existing = await self._db.get_mapping_by_source("jellyfin", item_id)
            if existing:
                logger.debug(
                    "  [cached] %s -> AniList %d", title, existing["anilist_id"]
                )
                if preview:
                    results.items.append(
                        ScanItemDetail(
                            rating_key=item_id,
                            plex_title=title,
                            plex_year=year,
                            library_title=library_title,
                            status="skipped",
                            reason="already mapped",
                            anilist_id=existing["anilist_id"],
                            anilist_title=existing.get("anilist_title"),
                            folder_name=folder_name,
                        )
                    )
                results.skipped += 1
                return

            # 3. Search AniList and match
            search_title = clean_title_for_search(folder_name or title)
            candidates = await self._anilist.search_anime(search_title, per_page=15)

            if not candidates:
                logger.warning("  [no results] %s", title)
                if preview:
                    results.items.append(
                        ScanItemDetail(
                            rating_key=item_id,
                            plex_title=title,
                            plex_year=year,
                            library_title=library_title,
                            status="failed",
                            reason="no AniList results found",
                            folder_name=folder_name,
                        )
                    )
                results.failed += 1
                return

            match_result = self._matcher.find_best_match_with_season(
                title, candidates, target_season=1
            )
            if not match_result:
                if preview:
                    results.items.append(
                        ScanItemDetail(
                            rating_key=item_id,
                            plex_title=title,
                            plex_year=year,
                            library_title=library_title,
                            status="failed",
                            reason="no confident match found",
                            folder_name=folder_name,
                        )
                    )
                results.failed += 1
                return

            matched_entry, confidence, _season = match_result
            anilist_id = matched_entry["id"]
            anilist_title = get_primary_title(matched_entry)
            title_obj = matched_entry.get("title", {})
            anilist_title_romaji = title_obj.get("romaji") or None
            anilist_title_english = title_obj.get("english") or None

            logger.info(
                "  [matched] %s -> %s (AniList %d, confidence %.2f)",
                title,
                anilist_title,
                anilist_id,
                confidence,
            )

            # Build series group if available
            group_id: int | None = None
            if self._group_builder:
                try:
                    group_id, _entries = await self._group_builder.get_or_build_group(
                        anilist_id
                    )
                except Exception:
                    logger.exception("  Failed to build series group for %s", title)
                    group_id = None

            if preview:
                changes: dict[str, str] = {}
                al_title = (
                    matched_entry.get("title", {}).get("english")
                    or matched_entry.get("title", {}).get("romaji")
                    or ""
                )
                if al_title and al_title != title:
                    changes["title"] = al_title
                if matched_entry.get("description"):
                    changes["summary"] = "(will update)"
                if matched_entry.get("genres"):
                    changes["genres"] = ", ".join(matched_entry["genres"])
                score = matched_entry.get("averageScore")
                if score:
                    changes["rating"] = str(round(score / 10, 1))
                cover = matched_entry.get("coverImage", {}).get("large", "")
                if cover:
                    changes["poster"] = "(will update)"

                start_date = matched_entry.get("startDate") or {}
                al_year = matched_entry.get("seasonYear") or start_date.get("year")

                results.items.append(
                    ScanItemDetail(
                        rating_key=item_id,
                        plex_title=title,
                        plex_year=year,
                        library_title=library_title,
                        status="matched",
                        reason="fuzzy match",
                        anilist_id=anilist_id,
                        anilist_title=anilist_title,
                        anilist_title_romaji=anilist_title_romaji,
                        anilist_title_english=anilist_title_english,
                        confidence=confidence,
                        match_method="fuzzy",
                        changes=changes,
                        folder_name=folder_name,
                        anilist_year=al_year,
                        anilist_season=matched_entry.get("season"),
                        anilist_format=matched_entry.get("format"),
                    )
                )
            else:
                await self._db.upsert_media_mapping(
                    source="jellyfin",
                    source_id=item_id,
                    source_title=title,
                    anilist_id=anilist_id,
                    anilist_title=anilist_title,
                    match_confidence=confidence,
                    match_method="fuzzy",
                    series_group_id=group_id,
                    season_number=1,
                )
                await self._apply_anilist_metadata(
                    item_id, title, anilist_id, confidence, "fuzzy", dry_run
                )
            results.matched += 1

        except Exception:
            logger.exception("  [error] Failed to process: %s", title)
            if preview:
                results.items.append(
                    ScanItemDetail(
                        rating_key=item_id,
                        plex_title=title,
                        plex_year=year,
                        library_title=library_title,
                        status="failed",
                        reason="unexpected error",
                        folder_name=folder_name,
                    )
                )
            results.failed += 1

    async def _apply_anilist_metadata(
        self,
        item_id: str,
        jellyfin_title: str,
        anilist_id: int,
        confidence: float,
        method: str,
        dry_run: bool,
    ) -> None:
        """Fetch AniList metadata and write it to a Jellyfin item."""
        metadata = await self._get_anilist_metadata(anilist_id)
        if not metadata:
            logger.warning("  Could not fetch AniList metadata for %d", anilist_id)
            return

        title_display = await self._db.get_setting("app.title_display") or "romaji"
        title_obj = metadata.get("title", {})
        romaji = title_obj.get("romaji") or ""
        english = title_obj.get("english") or ""

        al_title: str
        original_title: str | None = None

        if title_display in ("english", "both_english_primary"):
            al_title = english or romaji or jellyfin_title
            if romaji and romaji != al_title:
                original_title = romaji
        else:
            al_title = romaji or english or jellyfin_title
            if english and english != al_title:
                original_title = english

        description = metadata.get("description", "")
        genres = metadata.get("genres", [])
        score = metadata.get("averageScore")
        rating = round(score / 10, 1) if score else None
        cover_url = metadata.get("coverImage", {}).get("large", "")
        studios = metadata.get("studios", {}).get("nodes", [])
        studio_name = studios[0]["name"] if studios else None

        if dry_run:
            logger.info(
                "  [dry-run] Would apply to '%s': title='%s', genres=%s, rating=%s",
                jellyfin_title,
                al_title,
                genres,
                rating,
            )
            return

        try:
            await self._jellyfin.update_item_metadata(
                item_id=item_id,
                title=al_title,
                original_title=original_title,
                summary=description,
                genres=genres,
                rating=rating,
                studio=studio_name,
            )
        except Exception:
            logger.exception(
                "  Failed to update Jellyfin metadata for %s", jellyfin_title
            )
            return

        if cover_url:
            try:
                await self._jellyfin.upload_poster(item_id, cover_url)
            except Exception:
                logger.warning("  Failed to upload poster for %s", jellyfin_title)

        logger.info("  [applied] Jellyfin metadata written to '%s'", jellyfin_title)

    async def _get_anilist_metadata(self, anilist_id: int) -> dict[str, Any] | None:
        """Fetch metadata from cache or AniList API."""
        cached = await self._db.get_cached_metadata(anilist_id)
        if cached:
            return {
                "id": anilist_id,
                "title": {
                    "romaji": cached.get("title_romaji", ""),
                    "english": cached.get("title_english", ""),
                    "native": cached.get("title_native", ""),
                },
                "episodes": cached.get("episodes"),
                "coverImage": {"large": cached.get("cover_image", "")},
                "description": cached.get("description", ""),
                "genres": _safe_parse_genres(cached.get("genres", "[]")),
                "averageScore": None,
                "studios": {"nodes": []},
                "status": cached.get("status", ""),
                "seasonYear": cached.get("year", 0),
            }

        metadata = await self._anilist.get_anime_by_id(anilist_id)
        if not metadata:
            return None

        title_obj = metadata.get("title", {})
        year = metadata.get("seasonYear") or (
            (metadata.get("startDate") or {}).get("year") or 0
        )
        await self._db.set_cached_metadata(
            anilist_id=anilist_id,
            title_romaji=title_obj.get("romaji") or "",
            title_english=title_obj.get("english") or "",
            title_native=title_obj.get("native") or "",
            episodes=metadata.get("episodes"),
            cover_image=(metadata.get("coverImage") or {}).get("large") or "",
            description=metadata.get("description") or "",
            genres=json.dumps(metadata.get("genres") or []),
            status=metadata.get("status") or "",
            year=year,
        )
        return metadata
