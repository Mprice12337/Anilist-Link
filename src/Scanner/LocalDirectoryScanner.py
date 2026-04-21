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
        manage_total: bool = True,
        _name_cache: dict | None = None,
    ) -> list[ShowInput]:
        """Scan a directory for show folders and match each to AniList.

        Each immediate subdirectory is treated as one show. Hidden folders
        (starting with '.') are skipped.

        When *force_rescan* is True, cached mappings are ignored and every
        folder is re-matched against AniList.

        When *manage_total* is False, the caller is responsible for setting
        ``progress.total`` before calling.  This prevents the per-directory
        reset that causes incorrect fractions when scanning multiple dirs.

        *_name_cache* is an optional dict shared across multiple ``scan_directory``
        calls.  When provided, a folder name that already produced a successful
        AniList match in a previous call is re-used without hitting the API again.
        Pass the same dict for every call in a multi-source scan to avoid burning
        rate-limit tokens on identical folder names in different source dirs.
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
        if manage_total:
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
                        "LocalDirectoryScanner: cache hit %r -> anilist_id=%s"
                        " (method=%s)",
                        folder_name,
                        cached_id,
                        mapping.get("match_method", ""),
                    )
                    year = 0
                    romaji = ""
                    english = ""
                    anilist_format = ""
                    anilist_episodes = None
                    cache = await self._db.get_cached_metadata(cached_id)
                    if not cache and mapping.get("match_method") == "manual":
                        # Manual rematch without cached metadata — fetch from
                        # AniList now so folder tokens have proper titles.
                        try:
                            entry = await self._anilist.get_anime_by_id(cached_id)
                            if entry:
                                _t = entry.get("title") or {}
                                _y = entry.get("seasonYear") or (
                                    (entry.get("startDate") or {}).get("year") or 0
                                )
                                import json as _json

                                await self._db.set_cached_metadata(
                                    anilist_id=cached_id,
                                    title_romaji=_t.get("romaji") or "",
                                    title_english=_t.get("english") or "",
                                    title_native=_t.get("native") or "",
                                    episodes=entry.get("episodes"),
                                    cover_image=(
                                        (entry.get("coverImage") or {}).get("large")
                                        or ""
                                    ),
                                    description=entry.get("description") or "",
                                    genres=_json.dumps(entry.get("genres") or []),
                                    status=entry.get("status") or "",
                                    year=_y,
                                )
                                cache = await self._db.get_cached_metadata(cached_id)
                        except Exception:
                            logger.debug(
                                "Failed to fetch metadata for manual match %d",
                                cached_id,
                            )
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

            # Name-cache hit: same folder name already matched in this scan
            # session (e.g. identical show in two source dirs).  Re-use the
            # result without making another API call.
            if _name_cache is not None and folder_name in _name_cache:
                cached_entry = _name_cache[folder_name]
                logger.debug(
                    "LocalDirectoryScanner: name-cache hit %r -> id=%s",
                    folder_name,
                    cached_entry.get("id"),
                )
                match_result = (cached_entry, cached_entry.get("_score", 0.0), 1)
            else:
                match_result = None

            # Search AniList and fuzzy-match (two-pass: specific then broad).
            # Skipped entirely when a name-cache hit already populated match_result.
            if match_result is None:
                # Pass 1: search with full title (only strip [year] tags)
                specific_query = strip_bracket_tags(folder_name)
                if not specific_query.strip():
                    specific_query = folder_name

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
                                match_result = (
                                    self._matcher.find_best_match_with_season(
                                        folder_name,
                                        candidates,
                                        target_season=1,
                                        year_hint=folder_year,
                                        include_all_formats=True,
                                    )
                                )
                        except Exception:
                            logger.warning(
                                "AniList search failed for '%s'", folder_name
                            )

            # Define specific_query for the warning log below (needed if name-cache hit)
            else:
                specific_query = strip_bracket_tags(folder_name) or folder_name

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
            romaji = title_obj.get("romaji") or ""
            english = title_obj.get("english") or ""
            anilist_format = matched_entry.get("format", "") or ""
            anilist_episodes = matched_entry.get("episodes")

            # Store score on the entry so the name-cache can replay it.
            matched_entry["_score"] = score
            if _name_cache is not None:
                _name_cache.setdefault(folder_name, matched_entry)

            # Cache AniList metadata (cover, description, etc.) so that
            # library seeding can populate cover images without an extra
            # API call or expensive series-group traversal.
            cover = (matched_entry.get("coverImage") or {}).get("large", "")
            import json as _json

            await self._db.set_cached_metadata(
                anilist_id=anilist_id,
                title_romaji=romaji,
                title_english=english,
                title_native=title_obj.get("native", "") or "",
                episodes=anilist_episodes,
                cover_image=cover,
                description=matched_entry.get("description", "") or "",
                genres=_json.dumps(matched_entry.get("genres") or []),
                status=matched_entry.get("status", "") or "",
                year=year,
            )

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

        # --- Retry pass for items that failed due to transient API errors ---
        unmatched_indices = [i for i, si in enumerate(results) if not si.anilist_id]
        if unmatched_indices:
            logger.info(
                "LocalDirectoryScanner: retrying %d unmatched items",
                len(unmatched_indices),
            )
            for idx in unmatched_indices:
                si = results[idx]
                folder_name = si.title
                folder_path = si.local_path
                folder_year = extract_year_from_name(folder_name)
                query = strip_bracket_tags(folder_name)
                if not query.strip():
                    query = folder_name

                match_result = None
                try:
                    candidates = await self._anilist.search_anime(
                        query, page=1, per_page=10
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
                    pass

                if match_result is None:
                    broad_query = clean_title_for_search(folder_name)
                    if broad_query.strip() and broad_query != query:
                        try:
                            candidates = await self._anilist.search_anime(
                                broad_query, page=1, per_page=10
                            )
                            if candidates:
                                match_result = (
                                    self._matcher.find_best_match_with_season(
                                        folder_name,
                                        candidates,
                                        target_season=1,
                                        year_hint=folder_year,
                                        include_all_formats=True,
                                    )
                                )
                        except Exception:
                            pass

                if match_result is None:
                    continue

                matched_entry, score, _season = match_result
                anilist_id = matched_entry.get("id", 0)
                anilist_title = get_primary_title(matched_entry)
                logger.info(
                    "LocalDirectoryScanner: retry matched %r -> %r (id=%s)",
                    folder_name,
                    anilist_title,
                    anilist_id,
                )
                title_obj = matched_entry.get("title") or {}
                year = matched_entry.get("seasonYear") or (
                    (matched_entry.get("startDate") or {}).get("year") or 0
                )
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
                results[idx] = ShowInput(
                    title=folder_name,
                    local_path=folder_path,
                    source_id=folder_path,
                    anilist_id=anilist_id,
                    anilist_title=anilist_title,
                    year=year,
                    anilist_title_romaji=title_obj.get("romaji", ""),
                    anilist_title_english=title_obj.get("english", ""),
                    anilist_format=matched_entry.get("format", "") or "",
                    anilist_episodes=matched_entry.get("episodes"),
                )

        return results
