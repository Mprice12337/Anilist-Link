"""Local directory scanner: scan filesystem folders and match to AniList."""

from __future__ import annotations

import logging
import os

from src.Clients.AnilistClient import AniListClient
from src.Database.Connection import DatabaseManager
from src.Matching.Normalizer import (
    clean_title_for_search,
    extract_year_from_name,
    strip_bracket_tags,
)
from src.Matching.TitleMatcher import TitleMatcher, get_primary_title
from src.Scanner.LibraryRestructurer import RestructureProgress, ShowInput

logger = logging.getLogger(__name__)


class LocalDirectoryScanner:
    """Scans a local directory for anime show folders and matches to AniList."""

    def __init__(
        self,
        db: DatabaseManager,
        anilist_client: AniListClient,
        title_matcher: TitleMatcher,
    ) -> None:
        self._db = db
        self._anilist = anilist_client
        self._matcher = title_matcher

    async def scan_directory(
        self,
        directory: str,
        progress: RestructureProgress,
        force_rescan: bool = False,
    ) -> list[ShowInput]:
        """Scan a directory for show folders and match each to AniList.

        Each immediate subdirectory is treated as one show. Hidden folders
        (starting with '.') are skipped.

        When *force_rescan* is True, cached mappings are ignored and every
        folder is re-matched against AniList.
        """
        progress.phase = "Scanning directory"
        results: list[ShowInput] = []

        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            logger.error("Cannot read directory: %s", directory)
            progress.error_message = f"Cannot read directory: {directory}"
            return results

        subdirs = [
            name
            for name in entries
            if not name.startswith(".") and os.path.isdir(os.path.join(directory, name))
        ]
        progress.total = len(subdirs)
        progress.phase = "Matching folders to AniList"

        for folder_name in subdirs:
            folder_path = os.path.join(directory, folder_name)
            progress.current_item = folder_name
            progress.processed += 1

            # Check for existing cached mapping (skip if force_rescan)
            if not force_rescan:
                mapping = await self._db.get_mapping_by_source("local", folder_path)
                if mapping and mapping.get("anilist_id"):
                    cached_id = mapping["anilist_id"]
                    logger.debug(
                        "LocalDirectoryScanner: cache hit %r -> anilist_id=%s",
                        folder_name,
                        cached_id,
                    )
                    year = 0
                    romaji = ""
                    english = ""
                    anilist_format = ""
                    anilist_episodes = None
                    cache = await self._db.get_cached_metadata(cached_id)
                    if cache:
                        year = cache.get("year", 0) or 0
                        romaji = cache.get("title_romaji", "")
                        english = cache.get("title_english", "")
                    # Try to get format/episodes/start_date from series_group_entries
                    sge_row = await self._db.fetch_one(
                        "SELECT format, episodes, start_date FROM series_group_entries"
                        " WHERE anilist_id=? LIMIT 1",
                        (cached_id,),
                    )
                    if sge_row:
                        anilist_format = sge_row.get("format", "") or ""
                        anilist_episodes = sge_row.get("episodes")
                        # Fall back to start_date year when anilist_cache has no year
                        if not year:
                            start_date = sge_row.get("start_date") or ""
                            try:
                                year = (
                                    int(start_date[:4]) if len(start_date) >= 4 else 0
                                )
                            except (ValueError, TypeError):
                                year = 0
                    results.append(
                        ShowInput(
                            title=folder_name,
                            local_path=folder_path,
                            source_id=folder_path,
                            anilist_id=cached_id,
                            anilist_title=mapping.get("anilist_title", ""),
                            year=year,
                            anilist_title_romaji=romaji,
                            anilist_title_english=english,
                            anilist_format=anilist_format,
                            anilist_episodes=anilist_episodes,
                        )
                    )
                    continue

            # Extract year hint from folder name (e.g. "[2020]" or "(2022)")
            folder_year = extract_year_from_name(folder_name)

            # Search AniList and fuzzy-match (two-pass: specific then broad)
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
                        year_hint=folder_year,
                        include_all_formats=True,
                    )
            except Exception:
                logger.debug("Specific search failed for '%s'", folder_name)

            # Pass 2: if no match, try with season qualifiers stripped
            if match_result is None:
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
                                year_hint=folder_year,
                                include_all_formats=True,
                            )
                    except Exception:
                        logger.warning("AniList search failed for '%s'", folder_name)

            if match_result is None:
                logger.warning(
                    "LocalDirectoryScanner: no AniList match for %r (query=%r)",
                    folder_name,
                    specific_query,
                )
                results.append(
                    ShowInput(
                        title=folder_name,
                        local_path=folder_path,
                        source_id=folder_path,
                    )
                )
                continue

            matched_entry, score, _season = match_result
            anilist_id = matched_entry.get("id", 0)
            anilist_title = get_primary_title(matched_entry)
            logger.debug(
                "LocalDirectoryScanner: matched %r -> %r (id=%s, score=%.2f)",
                folder_name,
                anilist_title,
                anilist_id,
                score,
            )
            title_obj = matched_entry.get("title") or {}
            year = matched_entry.get("seasonYear") or (
                (matched_entry.get("startDate") or {}).get("year") or 0
            )
            romaji = title_obj.get("romaji", "")
            english = title_obj.get("english", "")
            anilist_format = matched_entry.get("format", "") or ""
            anilist_episodes = matched_entry.get("episodes")

            # Persist mapping for future runs
            await self._db.upsert_media_mapping(
                source="local",
                source_id=folder_path,
                source_title=folder_name,
                anilist_id=anilist_id,
                anilist_title=anilist_title,
                match_confidence=score,
                match_method="fuzzy_local_scan",
                media_type="ANIME",
            )

            results.append(
                ShowInput(
                    title=folder_name,
                    local_path=folder_path,
                    source_id=folder_path,
                    anilist_id=anilist_id,
                    anilist_title=anilist_title,
                    year=year,
                    anilist_title_romaji=romaji,
                    anilist_title_english=english,
                    anilist_format=anilist_format,
                    anilist_episodes=anilist_episodes,
                )
            )

        return results
