"""Jellyfin metadata scanning pipeline: scan, match, cache, apply."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from src.Clients.AnilistClient import AniListClient
from src.Clients.JellyfinClient import JellyfinClient, JellyfinSeason
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
from src.Utils.PathTranslator import PathTranslator

logger = logging.getLogger(__name__)

_MEDIA_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".m4v",
    ".mov",
    ".wmv",
    ".ts",
    ".m2ts",
    ".webm",
    ".flv",
}

# Folder names that are too generic to be useful as AniList search terms.
# When a media file lives inside one of these, use the filename instead.
_GENERIC_FOLDER_RE = re.compile(
    r"^(specials?|extras?|ovas?|movies?|films?|bonus|featurettes?|season\s*0)"
    r"(\s*[\(\[]\d{4}[\)\]])?$",
    re.IGNORECASE,
)

# Season subfolders with generic names (Season 1, Season 2, …) are not useful
# as AniList search terms — fall back to the parent show folder name instead.
_GENERIC_SEASON_RE = re.compile(r"^season\s*\d+$", re.IGNORECASE)


def _clean_filename_for_search(name: str) -> str:
    """Strip extension, resolution tags, and bracketed quality from a filename."""
    # Strip extension
    name = os.path.splitext(name)[0]
    # Strip trailing resolution/quality tags like "- 1080p", "- 1080p (Directors Cut)"
    name = re.sub(r"\s*[-–]\s*\d{3,4}p.*$", "", name, flags=re.IGNORECASE)
    # Strip bracketed resolution tags like "[1080p]"
    name = re.sub(r"\s*\[\d{3,4}p[^\]]*\]", "", name, flags=re.IGNORECASE)
    return name.strip()


def _derive_folder_name(show: object) -> str:  # type: ignore[type-arg]
    """Derive the best search-friendly name for a Jellyfin item.

    Priority:
    1. Filesystem path — preferred because Jellyfin metadata agents replace
       the display Name with scraped titles (e.g. 'Gekijouban K: Missing Kings')
       while the path reflects the original folder/file name.
    2. For file paths (movies/OVAs): use the parent folder name unless that
       folder has a generic name like 'Specials (2014)', in which case fall
       back to the cleaned filename itself.
    3. show.name — last resort when no path is available.
    """
    from src.Clients.JellyfinClient import JellyfinShow  # local to avoid circular

    assert isinstance(show, JellyfinShow)

    path: str = show.path or ""
    if not path:
        logger.debug(
            "No path for Jellyfin item '%s' (%s) — using display name",
            show.name,
            show.media_type,
        )
        return show.name

    basename = os.path.basename(path)
    _, ext = os.path.splitext(basename)

    if ext.lower() in _MEDIA_EXTENSIONS:
        # Path is a file — try parent folder first
        parent = os.path.basename(os.path.dirname(path))
        if parent and not _GENERIC_FOLDER_RE.match(parent.strip()):
            return parent
        # Parent is generic (e.g. "Specials (2021)") — use the cleaned filename
        cleaned = _clean_filename_for_search(basename)
        if cleaned:
            logger.debug(
                "Generic parent folder '%s' for '%s' — using filename '%s'",
                parent,
                show.name,
                cleaned,
            )
            return cleaned
        return parent or show.name

    # Path is a directory — if the folder name is a generic "Season N", use the
    # parent directory (the show folder) as the search term instead.
    if _GENERIC_SEASON_RE.match(basename.strip()):
        parent = os.path.basename(os.path.dirname(path))
        if parent:
            logger.debug(
                "Generic season folder '%s' — using parent '%s' for AniList matching",
                basename,
                parent,
            )
            return parent
    return basename or show.name


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
        self._path_translator_ready = False

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
                shows = await self._jellyfin.get_library_shows(lib.id, by_season=True)
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
            "Scanning Jellyfin library: %s (%d shows)",
            library_title,
            len(shows),
        )
        for show in shows:
            folder_name = _derive_folder_name(show)
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
                    await self._apply_metadata_for_show(
                        item_id, title, anilist_id, "", 1.0, "manual_override", dry_run
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

            # 3. Cross-source hint: check if another source (local library,
            # Plex) already matched this folder to AniList — avoids a
            # redundant search_anime() API call.
            cross_match = await self._db.find_anilist_match_by_folder(
                folder_name, exclude_source="jellyfin"
            )
            if cross_match and cross_match.get("anilist_id"):
                xref_id = cross_match["anilist_id"]
                xref_title = cross_match.get("anilist_title", "")
                xref_conf = cross_match.get("match_confidence", 0.9)
                logger.info(
                    "  [cross-source] %s -> AniList %d (%s)",
                    title,
                    xref_id,
                    xref_title,
                )
                if not preview:
                    await self._apply_metadata_for_show(
                        item_id,
                        title,
                        xref_id,
                        xref_title,
                        xref_conf,
                        f"cross_source:{cross_match.get('match_method', '')}",
                        dry_run,
                    )
                else:
                    results.items.append(
                        ScanItemDetail(
                            rating_key=item_id,
                            plex_title=title,
                            plex_year=year,
                            library_title=library_title,
                            status="matched",
                            reason="cross-source match",
                            anilist_id=xref_id,
                            anilist_title=xref_title,
                            confidence=xref_conf,
                            match_method="cross_source",
                            folder_name=folder_name,
                        )
                    )
                results.matched += 1
                return

            # 4. Search AniList and match
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

            # Use folder_name as the match target so season-specific info
            # (e.g. "ω", "S", subtitle) is preserved.  Jellyfin's display
            # Name may have been overwritten by a metadata agent and lost the
            # season indicator.  Also extract a year hint for disambiguation.
            match_target = folder_name or title
            year_hint = 0
            year_match = re.search(r"\((\d{4})\)", folder_name or "")
            if year_match:
                year_hint = int(year_match.group(1))
            match_result = self._matcher.find_best_match_with_season(
                match_target,
                candidates,
                target_season=1,
                year_hint=year_hint,
                include_all_formats=True,
            )
            if not match_result:
                logger.warning("  [no match] %s (searched: '%s')", title, search_title)
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
                await self._apply_metadata_for_show(
                    item_id,
                    title,
                    anilist_id,
                    anilist_title,
                    confidence,
                    "fuzzy",
                    dry_run,
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

    async def _apply_metadata_for_show(
        self,
        item_id: str,
        title: str,
        anilist_id: int,
        hint_title: str,
        confidence: float,
        method: str,
        dry_run: bool,
    ) -> None:
        """Build series group, detect Structure B, store mapping, apply metadata.

        Centralises the logic so override, cross-source, and fuzzy-match paths
        all benefit from per-season metadata when a show has multiple Jellyfin
        season containers.
        """
        group_id: int | None = None
        tv_entries: list[dict] = []
        if self._group_builder:
            try:
                group_id, group_entries = await self._group_builder.get_or_build_group(
                    anilist_id
                )
                tv_entries = [
                    e
                    for e in group_entries
                    if e.get("format", "") in ("TV", "TV_SHORT")
                ]
            except Exception:
                logger.exception("  Failed to build series group for %s", title)

        is_structure_b = False
        jf_real_seasons: list[JellyfinSeason] = []
        if group_id and len(tv_entries) > 1:
            try:
                jf_seasons = await self._jellyfin.get_show_seasons(item_id)
                jf_real_seasons = sorted(
                    [s for s in jf_seasons if s.index > 0],
                    key=lambda s: s.index,
                )
                is_structure_b = len(jf_real_seasons) > 1
            except Exception:
                logger.debug(
                    "  Could not fetch seasons for %s, assuming Structure A",
                    item_id,
                    exc_info=True,
                )

        show_anilist_id = (
            tv_entries[0]["anilist_id"]
            if (is_structure_b and tv_entries)
            else anilist_id
        )
        show_anilist_title = (
            tv_entries[0].get("display_title", hint_title)
            if (is_structure_b and tv_entries)
            else hint_title
        )

        await self._db.upsert_media_mapping(
            source="jellyfin",
            source_id=item_id,
            source_title=title,
            anilist_id=show_anilist_id,
            anilist_title=show_anilist_title,
            match_confidence=confidence,
            match_method=method,
            series_group_id=group_id,
            season_number=1,
        )

        if is_structure_b:
            await self._apply_structure_b_metadata(
                item_id, title, jf_real_seasons, tv_entries, confidence, dry_run
            )
        else:
            # For flat-folder / Structure A, use the first TV entry's anilist_id
            # so the series-level poster always shows Season 1 artwork regardless
            # of which entry the matcher happened to match to.
            _series_anilist_id = (
                tv_entries[0]["anilist_id"] if tv_entries else anilist_id
            )
            await self._apply_anilist_metadata(
                item_id, title, _series_anilist_id, confidence, method, dry_run
            )

    async def _apply_structure_b_metadata(
        self,
        series_id: str,
        series_title: str,
        real_seasons: list[JellyfinSeason],
        tv_entries: list[dict],
        confidence: float,
        dry_run: bool,
        force_refresh: bool = False,
    ) -> None:
        """Apply per-season posters and show-level first-entry poster for Structure B.

        Mirrors Plex's ``_store_structure_b_mappings`` / ``_apply_season_metadata``.
        Each Jellyfin Season item gets the poster for the corresponding series-group
        entry; the parent Series item gets the first entry's poster.
        """
        for i, season in enumerate(real_seasons):
            entry = tv_entries[i] if i < len(tv_entries) else tv_entries[-1]
            entry_title = entry.get("display_title") or entry.get("title_romaji") or ""
            await self._apply_jellyfin_season_metadata(
                season.item_id,
                entry["anilist_id"],
                entry_title,
                dry_run,
                force_refresh=force_refresh,
            )

        # Show level = first entry
        first_entry = tv_entries[0]
        await self._apply_anilist_metadata(
            series_id,
            series_title,
            first_entry["anilist_id"],
            confidence,
            "fuzzy",
            dry_run,
            force_refresh=force_refresh,
        )
        logger.info(
            "  [structure B] Applied metadata for %d seasons of '%s'",
            len(real_seasons),
            series_title,
        )

    async def _apply_jellyfin_season_metadata(
        self,
        season_item_id: str,
        anilist_id: int,
        season_title: str,
        dry_run: bool,
        force_refresh: bool = False,
    ) -> None:
        """Write title and poster to a single Jellyfin Season item."""
        try:
            metadata = await self._get_anilist_metadata(
                anilist_id, force_refresh=force_refresh
            )

            title_display = await self._db.get_setting("app.title_display") or "romaji"
            resolved_title = season_title
            cover_url = ""

            if metadata:
                title_obj = metadata.get("title", {})
                romaji = title_obj.get("romaji") or ""
                english = title_obj.get("english") or ""
                if title_display in ("english", "both_english_primary"):
                    resolved_title = english or romaji or season_title
                else:
                    resolved_title = romaji or english or season_title
                cover_url = (metadata.get("coverImage") or {}).get("large") or ""

            if dry_run:
                logger.info(
                    "  [dry-run] Would update season %s to title='%s'",
                    season_item_id,
                    resolved_title,
                )
                return

            try:
                await self._jellyfin.update_item_metadata(
                    item_id=season_item_id,
                    title=resolved_title,
                )
            except Exception:
                logger.exception(
                    "  Failed to update season metadata for %s", season_item_id
                )
                # Fall through — still attempt poster upload below

            if cover_url:
                try:
                    await self._jellyfin.upload_poster(season_item_id, cover_url)
                except Exception:
                    logger.debug(
                        "  Failed to upload poster for Jellyfin season %s",
                        season_item_id,
                        exc_info=True,
                    )
            logger.info(
                "  [season] Updated Jellyfin season %s -> '%s'",
                season_item_id,
                resolved_title,
            )
        except Exception:
            logger.exception(
                "  Failed to apply season metadata for Jellyfin season %s",
                season_item_id,
            )

    async def _ensure_path_translator(self) -> None:
        """Build and install a PathTranslator on the Jellyfin client (once per scan)."""
        if self._path_translator_ready:
            return
        try:
            jf_libraries = await self._jellyfin.get_libraries()
            service_locations: list[str] = [
                loc for lib in jf_libraries for loc in lib.locations
            ]
            db_libraries = await self._db.get_all_libraries()
            local_paths: list[str] = []
            for lib in db_libraries:
                raw = lib.get("paths") or "[]"
                try:
                    paths = json.loads(raw)
                    if isinstance(paths, list):
                        local_paths.extend(str(p) for p in paths)
                except Exception:
                    pass
            translator = PathTranslator.build(
                service_locations=service_locations,
                local_library_paths=local_paths,
            )
            self._jellyfin.set_path_translator(translator)
        except Exception:
            logger.warning(
                "Failed to build PathTranslator — folder.jpg writes use raw paths"
            )
        self._path_translator_ready = True

    async def _apply_anilist_metadata(
        self,
        item_id: str,
        jellyfin_title: str,
        anilist_id: int,
        confidence: float,
        method: str,
        dry_run: bool,
        force_refresh: bool = False,
    ) -> None:
        """Fetch AniList metadata and write it to a Jellyfin item."""
        await self._ensure_path_translator()
        metadata = await self._get_anilist_metadata(
            anilist_id, force_refresh=force_refresh
        )
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
            # Fall through — still attempt poster upload below

        if cover_url:
            try:
                await self._jellyfin.upload_poster(item_id, cover_url)
            except Exception:
                logger.warning(
                    "  Failed to upload poster for %s", jellyfin_title, exc_info=True
                )
            # Also set on the parent Folder container if this is a child item
            # (mixed libraries show Folders in the grid, not the media items)
            await self._jellyfin.upload_poster_to_parent_folder(item_id, cover_url)

        # Write tvshow.nfo so Jellyfin classifies the folder as a TV show on
        # the next library scan. This prevents mixed-library "versions" grouping
        # where episode files are stacked as alternate cuts of one movie.
        await self._jellyfin.write_tvshow_nfo(item_id, al_title)

        logger.info("  [applied] Jellyfin metadata written to '%s'", jellyfin_title)

    async def _get_anilist_metadata(
        self, anilist_id: int, force_refresh: bool = False
    ) -> dict[str, Any] | None:
        """Fetch metadata from cache or AniList API.

        When *force_refresh* is True the local cache is bypassed and a fresh
        request is made to AniList (the result is still stored in cache).
        """
        if force_refresh:
            await self._db.delete_cached_metadata(anilist_id)

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
