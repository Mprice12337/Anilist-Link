"""Library scanner: scan user-defined libraries and match folders to AniList."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

from src.Clients.AnilistClient import AniListClient
from src.Database.Connection import DatabaseManager
from src.Matching.Normalizer import (
    clean_title_for_search,
    extract_year_from_name,
    strip_bracket_tags,
)
from src.Matching.TitleMatcher import TitleMatcher, get_primary_title

logger = logging.getLogger(__name__)


@dataclass
class LibraryScanProgress:
    """Lightweight progress tracker for library scans."""

    status: str = "pending"  # pending, scanning, complete, error
    phase: str = ""
    processed: int = 0
    total: int = 0
    current_item: str = ""
    error_message: str = ""
    started_at: float = field(default_factory=time.monotonic)


class LibraryScanner:
    """Scans library directories and matches folders to AniList."""

    def __init__(
        self,
        db: DatabaseManager,
        anilist_client: AniListClient,
        title_matcher: TitleMatcher,
    ) -> None:
        self._db = db
        self._anilist = anilist_client
        self._matcher = title_matcher

    async def scan_library(
        self,
        library_id: int,
        paths: list[str],
        progress: LibraryScanProgress,
        force_rescan: bool = False,
    ) -> dict[str, int]:
        """Scan all paths for a library, match to AniList, persist results.

        Returns ``{matched, unmatched, errors, total, pruned}``.
        """
        progress.status = "scanning"
        progress.phase = "Listing directories"
        matched = 0
        unmatched = 0
        errors = 0

        # Collect all immediate subdirs across paths
        folders: list[tuple[str, str]] = []  # (folder_path, folder_name)
        for base_path in paths:
            try:
                entries = sorted(os.listdir(base_path))
            except OSError:
                logger.error("Cannot read directory: %s", base_path)
                continue
            for name in entries:
                if name.startswith("."):
                    continue
                full = os.path.join(base_path, name)
                if os.path.isdir(full):
                    folders.append((full, name))

        progress.total = len(folders)
        progress.phase = "Matching folders to AniList"

        existing_paths = await self._db.get_library_item_folder_paths(library_id)

        for folder_path, folder_name in folders:
            progress.current_item = folder_name
            progress.processed += 1

            # Skip already-matched items unless force_rescan
            if not force_rescan and folder_path in existing_paths:
                item = await self._db.fetch_one(
                    "SELECT anilist_id FROM library_items"
                    " WHERE library_id=? AND folder_path=?",
                    (library_id, folder_path),
                )
                if item and item.get("anilist_id"):
                    matched += 1
                    continue

            # Cross-source hint: check if Plex/Jellyfin already matched this folder
            cross_match = await self._db.find_anilist_match_by_folder(folder_name)
            if cross_match and cross_match.get("anilist_id"):
                xref_id = cross_match["anilist_id"]
                logger.info("  [cross-source] %s -> AniList %d", folder_name, xref_id)
                cached = await self._db.get_cached_metadata(xref_id)
                await self._db.upsert_library_item(
                    library_id=library_id,
                    folder_path=folder_path,
                    folder_name=folder_name,
                    anilist_id=xref_id,
                    anilist_title=cross_match.get("anilist_title", ""),
                    match_confidence=cross_match.get("match_confidence", 0.9),
                    match_method=f"cross_source:{cross_match.get('match_method', '')}",
                    anilist_format=(cached or {}).get("format", ""),
                    anilist_episodes=(cached or {}).get("episodes"),
                    year=(cached or {}).get("year", 0),
                    cover_image=(cached or {}).get("cover_image", ""),
                    series_group_id=cross_match.get("series_group_id"),
                )
                matched += 1
                continue

            # Two-pass AniList search + fuzzy match
            folder_year = extract_year_from_name(folder_name)
            match_result = await self._search_and_match(folder_name, folder_year)

            if match_result is None:
                await self._db.upsert_library_item(
                    library_id=library_id,
                    folder_path=folder_path,
                    folder_name=folder_name,
                )
                unmatched += 1
                continue

            matched_entry, score = match_result
            anilist_id = matched_entry.get("id", 0)
            anilist_title = get_primary_title(matched_entry)
            title_obj = matched_entry.get("title") or {}
            year = matched_entry.get("seasonYear") or (
                (matched_entry.get("startDate") or {}).get("year") or 0
            )
            cover = (matched_entry.get("coverImage") or {}).get("large", "")
            anilist_format = matched_entry.get("format", "") or ""
            anilist_episodes = matched_entry.get("episodes")

            # Cache AniList metadata
            try:
                await self._db.set_cached_metadata(
                    anilist_id=anilist_id,
                    title_romaji=title_obj.get("romaji", ""),
                    title_english=title_obj.get("english", "") or "",
                    title_native=title_obj.get("native", "") or "",
                    episodes=anilist_episodes,
                    cover_image=cover,
                    description=matched_entry.get("description", "") or "",
                    genres=json.dumps(matched_entry.get("genres") or []),
                    status=matched_entry.get("status", ""),
                    year=year,
                )
            except Exception:
                logger.debug("Failed to cache metadata for %d", anilist_id)

            await self._db.upsert_library_item(
                library_id=library_id,
                folder_path=folder_path,
                folder_name=folder_name,
                anilist_id=anilist_id,
                anilist_title=anilist_title,
                match_confidence=score,
                match_method="fuzzy",
                anilist_format=anilist_format,
                anilist_episodes=anilist_episodes,
                year=year,
                cover_image=cover,
            )
            matched += 1

        # Prune items for folders that no longer exist
        progress.phase = "Cleaning up removed folders"
        current_folder_paths = {fp for fp, _ in folders}
        pruned = await self._db.delete_library_items_not_in(
            library_id, current_folder_paths
        )

        progress.status = "complete"
        progress.phase = "Scan complete"
        progress.current_item = ""

        return {
            "matched": matched,
            "unmatched": unmatched,
            "errors": errors,
            "total": len(folders),
            "pruned": pruned,
        }

    async def detect_changes(
        self, library_id: int, paths: list[str]
    ) -> dict[str, object]:
        """Quick change detection: compare filesystem against DB."""
        current_folders: set[str] = set()
        for base_path in paths:
            try:
                entries = os.listdir(base_path)
            except OSError:
                continue
            for name in entries:
                if name.startswith("."):
                    continue
                full = os.path.join(base_path, name)
                if os.path.isdir(full):
                    current_folders.add(full)

        existing = await self._db.get_library_item_folder_paths(library_id)
        new_folders = sorted(current_folders - existing)
        removed_folders = sorted(existing - current_folders)

        return {
            "new_folders": new_folders,
            "removed_folders": removed_folders,
            "new_count": len(new_folders),
            "removed_count": len(removed_folders),
        }

    async def _search_and_match(
        self, folder_name: str, year_hint: int | None
    ) -> tuple[dict, float] | None:
        """Two-pass AniList search + fuzzy match. Returns (entry, score) or None."""
        # Pass 1: search with full title (only strip [year] tags)
        specific_query = strip_bracket_tags(folder_name)
        if not specific_query.strip():
            specific_query = folder_name

        match_result = None
        try:
            candidates = await self._anilist.search_anime(
                specific_query, page=1, per_page=10
            )
            if candidates:
                match_result = self._matcher.find_best_match_with_season(
                    folder_name,
                    candidates,
                    target_season=1,
                    year_hint=year_hint,
                    include_all_formats=True,
                )
        except Exception:
            logger.debug("Specific search failed for '%s'", folder_name)

        if match_result is not None:
            entry, score, _season = match_result
            return (entry, score)

        # Pass 2: try with season qualifiers stripped
        broad_query = clean_title_for_search(folder_name)
        if broad_query.strip() and broad_query != specific_query:
            try:
                candidates = await self._anilist.search_anime(
                    broad_query, page=1, per_page=10
                )
                if candidates:
                    match_result = self._matcher.find_best_match_with_season(
                        folder_name,
                        candidates,
                        target_season=1,
                        year_hint=year_hint,
                        include_all_formats=True,
                    )
            except Exception:
                logger.warning("AniList search failed for '%s'", folder_name)

        if match_result is not None:
            entry, score, _season = match_result
            return (entry, score)

        return None
