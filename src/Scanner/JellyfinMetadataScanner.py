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
from src.Clients.TVMazeClient import TVMazeClient
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
from src.Web.Routes.Helpers import build_rematch_changes

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


def _build_provider_ids(
    *,
    anilist_id: int = 0,
    imdb_id: str = "",
    tvdb_id: str = "",
    tvmaze_id: str = "",
) -> dict[str, str]:
    """Build a Jellyfin-compatible ProviderIds dict from raw IDs.

    Jellyfin stores provider IDs keyed by provider name (e.g. ``"Imdb"``,
    ``"Tvdb"``).  Only non-empty values are included.
    """
    ids: dict[str, str] = {}
    if anilist_id:
        ids["AniList"] = str(anilist_id)
    if imdb_id:
        ids["Imdb"] = imdb_id
    if tvdb_id:
        ids["Tvdb"] = str(tvdb_id)
    if tvmaze_id:
        ids["TvMaze"] = str(tvmaze_id)
    return ids


def _derive_folder_name(show: object) -> str:  # type: ignore[type-arg]
    """Derive the best search-friendly name for a Jellyfin item.

    Priority:
    1. Filesystem path — preferred because Jellyfin metadata agents replace
       the display Name with scraped titles (e.g. 'Gekijouban K: Missing Kings')
       while the path reflects the original folder/file name.
    2. For file paths (standalone movies/OVAs not in a generic sub-folder):
       use the parent folder name.  Items in generic sub-folders (Specials,
       Extras, etc.) are skipped before this function's result is used.
    3. show.name — last resort when no path is available.
    """
    from src.Clients.JellyfinClient import JellyfinShow  # local to avoid circular

    assert isinstance(show, JellyfinShow)

    path: str = show.path or ""
    if not path:
        return show.name

    basename = os.path.basename(path)
    _, ext = os.path.splitext(basename)

    if ext.lower() in _MEDIA_EXTENSIONS:
        # Path is a file — use the parent folder name as the match target.
        # (Items in generic parent folders are already filtered out by the
        # caller before this result is acted upon.)
        parent = os.path.basename(os.path.dirname(path))
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
        tvmaze_client: TVMazeClient | None = None,
    ) -> None:
        self._db = db
        self._anilist = anilist_client
        self._matcher = title_matcher
        self._jellyfin = jellyfin_client
        self._config = config
        self._group_builder = group_builder
        self._tvmaze = tvmaze_client or TVMazeClient()
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
            # Skip any item without a real filesystem path — these are virtual
            # Jellyfin containers (e.g. "Season Unknown", auto-generated season
            # buckets) that have no on-disk presence.  There is nothing to match,
            # no NFO to write, and no metadata to apply.
            if not show.path:
                logger.debug(
                    "Skipping virtual item '%s' (%s) — no filesystem path",
                    show.name,
                    show.media_type,
                )
                if progress:
                    progress.scanned += 1
                continue
            # Skip generic sub-folders and episode files that belong to an
            # already-mapped series, not standalone AniList entries.
            # Two forms Jellyfin surfaces these as:
            #   • Season/Movie item whose path IS a generic directory
            #     (e.g. ".../Shaman King/Specials (2000)/")
            #   • Movie item whose path is a file inside a generic directory
            #     (e.g. ".../Another/Specials (2013)/ep.mkv")
            _path_ext = os.path.splitext(show.path)[1].lower()
            if _path_ext in _MEDIA_EXTENSIONS:
                _parent = os.path.basename(os.path.dirname(show.path))
                if _GENERIC_FOLDER_RE.match(_parent.strip()):
                    logger.debug(
                        "Skipping episode file '%s' in generic folder '%s'",
                        show.name,
                        _parent,
                    )
                    if progress:
                        progress.scanned += 1
                    continue
            else:
                _basename = os.path.basename(show.path.rstrip("/\\"))
                if _GENERIC_FOLDER_RE.match(_basename.strip()):
                    logger.debug(
                        "Skipping generic sub-folder '%s' (%s)",
                        show.name,
                        _basename,
                    )
                    if progress:
                        progress.scanned += 1
                    continue
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
                logger.warning(
                    "  [no results] %s (searched: '%s')", title, search_title
                )
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
                changes = build_rematch_changes(matched_entry, title)

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
        group_entries: list[dict] = []
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
            # Structure A: each season folder is processed individually.
            # Always write to the parent show folder, but use the series ROOT
            # entry's data for those writes so the parent always displays S1's
            # art and title regardless of which season item is processed last.
            root_anilist_id = (
                group_entries[0]["anilist_id"] if group_entries else anilist_id
            )
            # Determine season number from this entry's position in the group
            # (chronologically sorted, so position 0 = season 1).
            season_number: int | None = None
            if group_entries:
                for i, entry in enumerate(group_entries):
                    if entry.get("anilist_id") == anilist_id:
                        season_number = i + 1
                        break
                if season_number is None:
                    season_number = 1
            await self._apply_anilist_metadata(
                item_id,
                title,
                anilist_id,
                confidence,
                method,
                dry_run,
                parent_anilist_id=root_anilist_id,
                season_number=season_number,
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

        When Jellyfin has both a physical custom-named season folder (e.g.
        "Jujutsu Kaisen: Shibuya Incident") AND a virtual "Season 2" container
        (created from S02Exx episode numbering), both share the same IndexNumber.
        Group by index so both receive the correct AniList entry rather than
        the simple enumerate() letting the virtual ones push physical ones off.
        """
        from collections import defaultdict

        # Fetch root-entry metadata once to get series-level provider IDs for
        # all season.nfo writes in this loop.
        root_meta = (
            await self._get_anilist_metadata(
                tv_entries[0]["anilist_id"], force_refresh=force_refresh
            )
            or {}
        )
        s_imdb_id = root_meta.get("imdb_id") or None
        s_tvdb_id = root_meta.get("tvdb_id") or None
        s_tvmaze_id = root_meta.get("tvmaze_id") or None

        # Build index → [seasons] map (stable order within each index preserved)
        seasons_by_index: dict[int, list] = defaultdict(list)
        for s in real_seasons:
            seasons_by_index[s.index].append(s)

        distinct_indices = sorted(seasons_by_index.keys())
        total_applied = 0
        for pos, idx in enumerate(distinct_indices):
            entry = tv_entries[pos] if pos < len(tv_entries) else tv_entries[-1]
            entry_title = entry.get("display_title") or entry.get("title_romaji") or ""
            for season in seasons_by_index[idx]:
                await self._apply_jellyfin_season_metadata(
                    season.item_id,
                    entry["anilist_id"],
                    entry_title,
                    dry_run,
                    force_refresh=force_refresh,
                    season_number=pos + 1,
                    series_imdb_id=s_imdb_id,
                    series_tvdb_id=s_tvdb_id,
                    series_tvmaze_id=s_tvmaze_id,
                )
                total_applied += 1

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
            "  [structure B] Applied metadata for %d seasons (%d distinct indices)"
            " of '%s'",
            total_applied,
            len(distinct_indices),
            series_title,
        )

    async def _apply_jellyfin_season_metadata(
        self,
        season_item_id: str,
        anilist_id: int,
        season_title: str,
        dry_run: bool,
        force_refresh: bool = False,
        season_number: int | None = None,
        series_imdb_id: str | None = None,
        series_tvdb_id: str | None = None,
        series_tvmaze_id: str | None = None,
    ) -> None:
        """Write title, poster, and season.nfo to a single Jellyfin Season item.

        Each season carries its own AniList entry's full metadata so that
        movie/OVA entries placed as numbered seasons (e.g. JJK 0 as Season 2)
        show their own description, rating, and title rather than inheriting
        the parent show's TVDB data.
        """
        try:
            metadata = await self._get_anilist_metadata(
                anilist_id, force_refresh=force_refresh
            )

            title_display = await self._db.get_setting("app.title_display") or "romaji"
            resolved_title = season_title
            season_original_title: str | None = None
            cover_url = ""
            season_plot: str | None = None
            season_genres: list[str] = []
            season_studio: str | None = None
            season_rating: float | None = None
            season_year: int | None = None
            season_tags: list[str] = []

            if metadata:
                title_obj = metadata.get("title", {})
                romaji = title_obj.get("romaji") or ""
                english = title_obj.get("english") or ""
                native = title_obj.get("native") or ""
                if title_display in ("english", "both_english_primary"):
                    resolved_title = english or romaji or season_title
                    if romaji and romaji != resolved_title:
                        season_original_title = romaji
                else:
                    resolved_title = romaji or english or season_title
                    if english and english != resolved_title:
                        season_original_title = english
                cover_url = (metadata.get("coverImage") or {}).get("large") or ""
                season_plot = metadata.get("description") or None
                season_genres = metadata.get("genres") or []
                s_studios = (metadata.get("studios") or {}).get("nodes") or []
                season_studio = s_studios[0]["name"] if s_studios else None
                s_score = metadata.get("averageScore")
                season_rating = round(s_score / 10, 1) if s_score else None
                season_year = metadata.get("seasonYear") or None
                season_tags = sorted({t for t in [romaji, english, native] if t})

            if dry_run:
                logger.info(
                    "  [dry-run] Would update season %s to title='%s'",
                    season_item_id,
                    resolved_title,
                )
                return

            season_provider_ids = _build_provider_ids(
                anilist_id=anilist_id,
                imdb_id=series_imdb_id or "",
                tvdb_id=series_tvdb_id or "",
                tvmaze_id=series_tvmaze_id or "",
            )
            try:
                await self._jellyfin.update_item_metadata(
                    item_id=season_item_id,
                    title=resolved_title,
                    original_title=season_original_title,
                    summary=season_plot,
                    genres=season_genres,
                    rating=season_rating,
                    studio=season_studio,
                    provider_ids=season_provider_ids,
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
            if season_number is not None:
                await self._jellyfin.write_season_nfo(
                    season_item_id,
                    resolved_title,
                    season_number,
                    original_title=season_original_title,
                    plot=season_plot,
                    year=season_year,
                    anilist_id=anilist_id,
                    genres=season_genres,
                    studio=season_studio,
                    rating=season_rating,
                    tags=season_tags,
                    series_imdb_id=series_imdb_id,
                    series_tvdb_id=series_tvdb_id,
                    series_tvmaze_id=series_tvmaze_id,
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
        parent_anilist_id: int | None = None,
        season_number: int | None = None,
    ) -> None:
        """Fetch AniList metadata and write it to a Jellyfin item.

        ``parent_anilist_id`` controls which AniList entry's art and title are
        written to the parent show folder.  Pass the series group root's ID so
        the parent always shows the first season's artwork regardless of which
        season item happens to be processed last.  When omitted (or equal to
        ``anilist_id``) the item's own metadata is used for the parent write.
        """
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
        native = title_obj.get("native") or ""

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
        item_year = metadata.get("seasonYear") or None
        # All title variants for search tags — deduplicated
        item_tags: list[str] = sorted({t for t in [romaji, english, native] if t})

        # Resolve metadata for the parent show folder write.
        # If parent_anilist_id differs, fetch that entry's data so the parent
        # always shows the series root (S1) art rather than whichever season
        # happened to be processed last.
        effective_parent_id = parent_anilist_id or anilist_id
        if effective_parent_id != anilist_id:
            parent_meta = (
                await self._get_anilist_metadata(
                    effective_parent_id, force_refresh=force_refresh
                )
                or metadata
            )
        else:
            parent_meta = metadata

        p_title_obj = parent_meta.get("title", {})
        p_romaji = p_title_obj.get("romaji") or ""
        p_english = p_title_obj.get("english") or ""
        p_native = p_title_obj.get("native") or ""
        p_original_title: str | None = None
        if title_display in ("english", "both_english_primary"):
            parent_title = p_english or p_romaji or al_title
            if p_romaji and p_romaji != parent_title:
                p_original_title = p_romaji
        else:
            parent_title = p_romaji or p_english or al_title
            if p_english and p_english != parent_title:
                p_original_title = p_english
        parent_cover_url = parent_meta.get("coverImage", {}).get("large", "")
        p_studios = (parent_meta.get("studios") or {}).get("nodes") or []
        p_studio = p_studios[0]["name"] if p_studios else None
        p_score = parent_meta.get("averageScore")
        p_rating = round(p_score / 10, 1) if p_score else None
        p_year = parent_meta.get("seasonYear") or None
        p_status = parent_meta.get("status") or None
        p_genres = parent_meta.get("genres") or []
        parent_tags: list[str] = sorted(
            {t for t in [p_romaji, p_english, p_native] if t}
        )
        # Provider IDs sourced from our TVMaze cache — written into tvshow.nfo
        # and season.nfo so all installed plugins (TMDB, TVDB, OMDB, TVMaze)
        # can resolve per-episode metadata after restructure renames folders.
        # Also pushed via the Jellyfin API so the IDs appear in the metadata
        # editor immediately without requiring NFO plugin support.
        p_imdb_id = parent_meta.get("imdb_id") or ""
        p_tvdb_id = parent_meta.get("tvdb_id") or ""
        p_tvmaze_id = parent_meta.get("tvmaze_id") or ""
        p_provider_ids = _build_provider_ids(
            anilist_id=effective_parent_id,
            imdb_id=p_imdb_id,
            tvdb_id=p_tvdb_id,
            tvmaze_id=p_tvmaze_id,
        )

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
                provider_ids=p_provider_ids,
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

        # Write to the parent Folder/Series container.  Always uses the root
        # entry's data so multi-season shows consistently show S1 artwork.
        if parent_cover_url:
            await self._jellyfin.upload_poster_to_parent_folder(
                item_id, parent_cover_url
            )
        # Write tvshow.nfo with full AniList metadata so Jellyfin classifies the
        # folder as a TV show and our series arrangement is preserved on refresh.
        await self._jellyfin.write_tvshow_nfo(
            item_id,
            parent_title,
            original_title=p_original_title,
            plot=parent_meta.get("description") or None,
            genres=p_genres,
            studio=p_studio,
            rating=p_rating,
            year=p_year,
            status=p_status,
            anilist_id=effective_parent_id,
            tags=parent_tags,
            imdb_id=p_imdb_id or None,
            tvdb_id=p_tvdb_id or None,
            tvmaze_id=p_tvmaze_id or None,
        )

        # Write season.nfo when this item IS a season folder (Structure A).
        # Each season carries its own AniList entry's data (title, plot, rating,
        # etc.) so e.g. a movie entry used as Season 2 shows its own metadata.
        if season_number is not None:
            await self._jellyfin.write_season_nfo(
                item_id,
                al_title,
                season_number,
                original_title=original_title,
                plot=description or None,
                year=item_year,
                anilist_id=anilist_id,
                genres=genres,
                studio=studio_name,
                rating=rating,
                tags=item_tags,
                series_imdb_id=p_imdb_id or None,
                series_tvdb_id=p_tvdb_id or None,
                series_tvmaze_id=p_tvmaze_id or None,
            )

        logger.info("  [applied] Jellyfin metadata written to '%s'", jellyfin_title)

    async def _get_sibling_provider_ids(self, anilist_id: int) -> dict[str, str] | None:
        """Check if a sibling in the same series group already has provider IDs.

        TVMaze treats a show as one entity with multiple seasons, so all
        entries in a series group share the same IMDB/TVDB/TVMaze IDs.  If
        any sibling already has them cached we can skip the TVMaze API call.

        Returns ``{"imdb_id": ..., "tvdb_id": ..., "tvmaze_id": ...}`` or
        ``None`` if no sibling has provider IDs.
        """
        group = await self._db.get_series_group_by_anilist_id(anilist_id)
        if not group:
            return None

        entries = await self._db.get_series_group_entries(group["id"])
        sibling_ids = [
            e["anilist_id"] for e in entries if e["anilist_id"] != anilist_id
        ]
        if not sibling_ids:
            return None

        for sid in sibling_ids:
            cached = await self._db.get_cached_metadata(sid)
            if not cached:
                continue
            imdb = cached.get("imdb_id") or ""
            tvdb = cached.get("tvdb_id") or ""
            tvmaze = cached.get("tvmaze_id") or ""
            if imdb or tvdb or tvmaze:
                logger.debug(
                    "Reusing provider IDs from sibling %d for entry %d "
                    "(imdb=%s, tvdb=%s, tvmaze=%s)",
                    sid,
                    anilist_id,
                    imdb,
                    tvdb,
                    tvmaze,
                )
                return {"imdb_id": imdb, "tvdb_id": tvdb, "tvmaze_id": tvmaze}

        return None

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
            cached_rating = cached.get("rating")
            cached_studio = cached.get("studio") or ""
            imdb_id = cached.get("imdb_id") or ""
            tvdb_id = cached.get("tvdb_id") or ""
            tvmaze_id = cached.get("tvmaze_id") or ""

            # If provider IDs are missing (entry was cached before TVMaze lookup
            # was added), run the lookup now and persist the result so subsequent
            # runs don't need to hit TVMaze again.
            if not imdb_id and not tvdb_id and not tvmaze_id:
                # Try sibling reuse first — avoids TVMaze API calls for sequels.
                sibling_ids = await self._get_sibling_provider_ids(anilist_id)
                if sibling_ids:
                    tvmaze_ids = sibling_ids  # type: ignore[assignment]
                else:
                    tvmaze_titles = [
                        t
                        for t in [
                            cached.get("title_english") or "",
                            cached.get("title_romaji") or "",
                            cached.get("title_native") or "",
                        ]
                        if t
                    ]
                    tvmaze_ids = await self._tvmaze.search_show_multi(tvmaze_titles)  # type: ignore[assignment]
                if tvmaze_ids:
                    imdb_id = tvmaze_ids.get("imdb_id") or ""
                    tvdb_id = tvmaze_ids.get("tvdb_id") or ""
                    tvmaze_id = tvmaze_ids.get("tvmaze_id") or ""
                    # Patch the cached row so next run skips this lookup.
                    await self._db.execute(
                        "UPDATE anilist_cache"
                        " SET imdb_id=?, tvdb_id=?, tvmaze_id=?"
                        " WHERE anilist_id=?",
                        (imdb_id, tvdb_id, tvmaze_id, anilist_id),
                    )

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
                # Reconstruct averageScore from stored rating (rating = score/10)
                "averageScore": (
                    int(round(cached_rating * 10))
                    if cached_rating is not None
                    else None
                ),
                "studios": {
                    "nodes": [{"name": cached_studio}] if cached_studio else []
                },
                "status": cached.get("status", ""),
                "seasonYear": cached.get("year", 0),
                "imdb_id": imdb_id,
                "tvdb_id": tvdb_id,
                "tvmaze_id": tvmaze_id,
            }

        metadata = await self._anilist.get_anime_by_id(anilist_id)
        if not metadata:
            return None

        title_obj = metadata.get("title", {})
        year = metadata.get("seasonYear") or (
            (metadata.get("startDate") or {}).get("year") or 0
        )
        raw_score = metadata.get("averageScore")
        cached_rating = round(raw_score / 10, 1) if raw_score else None
        studios_nodes = (metadata.get("studios") or {}).get("nodes") or []
        cached_studio = studios_nodes[0].get("name") or "" if studios_nodes else ""

        # TVMaze lookup for IMDB/TVDB IDs — runs once per entry on cache miss.
        # Try sibling reuse first (all entries in a series group share the same
        # TVMaze/TVDB/IMDB IDs since TVMaze treats a show as one entity).
        tvmaze_ids = await self._get_sibling_provider_ids(anilist_id)  # type: ignore[assignment]
        if not tvmaze_ids:
            # No sibling IDs available — do the full multi-title search.
            tvmaze_titles = [
                t
                for t in [
                    title_obj.get("english") or "",
                    title_obj.get("romaji") or "",
                    title_obj.get("native") or "",
                ]
                if t
            ]
            for syn in metadata.get("synonyms") or []:
                if syn and syn.strip():
                    tvmaze_titles.append(syn.strip())
            tvmaze_ids = await self._tvmaze.search_show_multi(tvmaze_titles)  # type: ignore[assignment]
        imdb_id = (tvmaze_ids or {}).get("imdb_id") or ""
        tvdb_id = (tvmaze_ids or {}).get("tvdb_id") or ""
        tvmaze_id = (tvmaze_ids or {}).get("tvmaze_id") or ""

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
            rating=cached_rating,
            studio=cached_studio,
            imdb_id=imdb_id,
            tvdb_id=tvdb_id,
            tvmaze_id=tvmaze_id,
        )
        # Attach IDs to the live metadata dict so the caller has them without
        # a second cache read.
        metadata["imdb_id"] = imdb_id
        metadata["tvdb_id"] = tvdb_id
        metadata["tvmaze_id"] = tvmaze_id
        return metadata
