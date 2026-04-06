"""Metadata scanning pipeline: scan, match, cache, apply."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from src.Clients.AnilistClient import AniListClient
from src.Clients.PlexClient import PlexClient
from src.Database.Connection import DatabaseManager
from src.Matching.Normalizer import clean_title_for_search
from src.Matching.TitleMatcher import TitleMatcher, get_primary_title
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Utils.Config import AppConfig

logger = logging.getLogger(__name__)


def _safe_parse_genres(raw: str) -> list[str]:
    """Parse a genres string that may be JSON or a Python repr list."""
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    # Handle Python repr format: "['Action', 'Drama']"
    import ast

    try:
        result = ast.literal_eval(raw)
        if isinstance(result, list):
            return [str(g) for g in result]
    except (ValueError, SyntaxError):
        pass
    return []


@dataclass
class ScanItemDetail:
    """Per-item detail from a scan."""

    rating_key: str
    plex_title: str
    plex_year: int | None
    library_title: str
    status: str  # "matched", "skipped", "failed"
    reason: str
    anilist_id: int | None = None
    anilist_title: str | None = None
    anilist_title_romaji: str | None = None
    anilist_title_english: str | None = None
    confidence: float | None = None
    match_method: str | None = None
    changes: dict[str, str] = field(default_factory=dict)
    folder_name: str = ""
    location: str = ""
    anilist_year: int | None = None
    anilist_season: str | None = None
    anilist_format: str | None = None


@dataclass
class ScanProgress:
    """Live progress tracking for a running scan."""

    status: str = "pending"  # "pending", "running", "complete", "error"
    scanned: int = 0
    total: int = 0
    current_title: str = ""
    error_message: str = ""
    started_at: float = 0.0


@dataclass
class ScanResults:
    """Tracks the outcome of a metadata scan."""

    matched: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    items: list[ScanItemDetail] = field(default_factory=list)


class MetadataScanner:
    """Orchestrates the scan -> match -> cache -> apply pipeline for Plex."""

    def __init__(
        self,
        db: DatabaseManager,
        anilist_client: AniListClient,
        title_matcher: TitleMatcher,
        plex_client: PlexClient,
        config: AppConfig,
        group_builder: SeriesGroupBuilder | None = None,
    ) -> None:
        self._db = db
        self._anilist = anilist_client
        self._matcher = title_matcher
        self._plex = plex_client
        self._config = config
        self._group_builder = group_builder

    async def run_scan(
        self,
        dry_run: bool = False,
        library_keys: list[str] | None = None,
        preview: bool = False,
        progress: ScanProgress | None = None,
    ) -> ScanResults:
        """Run a full Plex metadata scan.

        If *dry_run* is True, matches are logged but metadata is NOT written
        to Plex.

        If *library_keys* is provided, only scan those library section keys.

        If *preview* is True, populate per-item details in the results but
        do NOT store mappings or write metadata to Plex.

        If *progress* is provided, it is updated in-place as items are
        processed (useful for live progress reporting).
        """
        results = ScanResults()
        mode = " (PREVIEW)" if preview else (" (DRY RUN)" if dry_run else "")
        logger.info("Starting Plex metadata scan%s", mode)

        if progress:
            progress.status = "running"
            progress.started_at = time.monotonic()
            # Hook rate-limit callback so the UI shows wait times
            self._anilist.on_rate_limit_wait = lambda secs: setattr(
                progress,
                "current_title",
                f"Rate limited — waiting {secs}s...",
            )

        try:
            return await self._run_scan_inner(
                results, mode, dry_run, library_keys, preview, progress
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
        library_keys: list[str] | None,
        preview: bool,
        progress: ScanProgress | None,
    ) -> ScanResults:
        """Inner scan loop (separated for clean try/finally in run_scan)."""
        try:
            libraries = await self._plex.get_libraries()
        except Exception:
            logger.exception("Failed to connect to Plex server")
            results.errors.append("Failed to connect to Plex server")
            if progress:
                progress.status = "error"
                progress.error_message = "Failed to connect to Plex server"
            return results

        show_libraries = [lib for lib in libraries if lib.type in ("show", "movie")]

        # Filter by selected anime library keys if provided
        if library_keys:
            key_set = set(library_keys)
            show_libraries = [lib for lib in show_libraries if lib.key in key_set]

        if not show_libraries:
            logger.warning("No show libraries found on Plex server")
            return results

        logger.info(
            "Found %d show librar%s: %s",
            len(show_libraries),
            "y" if len(show_libraries) == 1 else "ies",
            ", ".join(lib.title for lib in show_libraries),
        )

        # Pre-fetch shows for all libraries once (avoids double Plex call)
        library_shows: dict[str, list] = {}
        if progress:
            progress.current_title = "Counting shows..."
        for library in show_libraries:
            try:
                shows = await self._plex.get_library_shows(
                    library.key, library_type=library.type
                )
                library_shows[library.key] = shows
            except Exception:
                logger.exception("Failed to get shows from library %s", library.title)
                results.errors.append(f"Failed to read library: {library.title}")
                results.failed += 1
        if progress:
            progress.total = sum(len(s) for s in library_shows.values())

        effective_dry_run = dry_run or preview
        for library in show_libraries:
            shows = library_shows.get(library.key) or []
            if not shows:
                continue
            await self._scan_library_shows(
                shows,
                library.title,
                results,
                effective_dry_run,
                preview,
                progress,
            )

        logger.info(
            "Plex scan complete%s: %d matched, %d skipped, %d failed",
            mode,
            results.matched,
            results.skipped,
            results.failed,
        )
        return results

    # ------------------------------------------------------------------
    # Library scanning
    # ------------------------------------------------------------------

    async def _scan_library_shows(
        self,
        shows: list,
        library_title: str,
        results: ScanResults,
        dry_run: bool,
        preview: bool = False,
        progress: ScanProgress | None = None,
    ) -> None:
        """Scan all shows in a single Plex library."""
        logger.info("Scanning library: %s (%d shows)", library_title, len(shows))

        for show in shows:
            folder_name = getattr(show, "folder_name", "") or ""
            # The bulk /all endpoint omits Location data.  Always fetch
            # the real filesystem path so we can show it in the UI and
            # distinguish same-titled shows.
            if hasattr(self._plex, "get_show_locations"):
                locs = await self._plex.get_show_locations(show.rating_key)
                if locs:
                    real_name = os.path.basename(locs[0])
                    if real_name:
                        folder_name = real_name
                    show.locations = locs
            if progress:
                progress.current_title = folder_name or show.title
            # Persist show to plex_media for library browser
            await self._db.upsert_plex_media(
                rating_key=show.rating_key,
                title=show.title,
                year=show.year,
                thumb=getattr(show, "thumb", "") or "",
                summary=getattr(show, "summary", "") or "",
                library_key=getattr(show, "library_key", "") or "",
                library_title=library_title,
                folder_name=folder_name,
            )
            show_location = ""
            if hasattr(show, "locations") and show.locations:
                show_location = show.locations[0]
            await self._process_show(
                show.rating_key,
                show.title,
                results,
                dry_run,
                preview,
                library_title,
                show.year,
                folder_name=folder_name,
                location=show_location,
            )
            if progress:
                progress.scanned += 1

    # ------------------------------------------------------------------
    # Per-show processing
    # ------------------------------------------------------------------

    async def _process_show(
        self,
        rating_key: str,
        title: str,
        results: ScanResults,
        dry_run: bool,
        preview: bool = False,
        library_title: str = "",
        year: int | None = None,
        folder_name: str = "",
        location: str = "",
    ) -> None:
        """Process a single Plex show: match to AniList and apply metadata."""
        try:
            # 1. Check manual override
            override = await self._db.get_override("plex", rating_key)
            if override:
                anilist_id = override["anilist_id"]
                logger.info("  [override] %s -> AniList %d", title, anilist_id)
                if not preview:
                    await self._apply_anilist_metadata(
                        rating_key,
                        title,
                        anilist_id,
                        1.0,
                        "manual_override",
                        dry_run,
                    )
                else:
                    results.items.append(
                        ScanItemDetail(
                            rating_key=rating_key,
                            plex_title=title,
                            plex_year=year,
                            library_title=library_title,
                            status="matched",
                            reason="manual override",
                            anilist_id=anilist_id,
                            confidence=1.0,
                            match_method="manual_override",
                            folder_name=folder_name,
                            location=location,
                        )
                    )
                results.matched += 1
                return

            # 2. Check existing mapping
            existing = await self._db.get_mapping_by_source("plex", rating_key)
            if existing:
                logger.debug(
                    "  [cached] %s -> AniList %d", title, existing["anilist_id"]
                )
                if preview:
                    results.items.append(
                        ScanItemDetail(
                            rating_key=rating_key,
                            plex_title=title,
                            plex_year=year,
                            library_title=library_title,
                            status="skipped",
                            reason="already mapped",
                            anilist_id=existing["anilist_id"],
                            anilist_title=existing.get("anilist_title"),
                            folder_name=folder_name,
                            location=location,
                        )
                    )
                results.skipped += 1
                return

            # 3. Cross-source hint: check if another source (local library,
            # Jellyfin) already matched this folder to AniList — avoids a
            # redundant search_anime() API call.
            cross_match = await self._db.find_anilist_match_by_folder(
                folder_name, exclude_source="plex"
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
                    await self._apply_anilist_metadata(
                        rating_key,
                        title,
                        xref_id,
                        xref_conf,
                        f"cross_source:{cross_match.get('match_method', '')}",
                        dry_run,
                    )
                else:
                    results.items.append(
                        ScanItemDetail(
                            rating_key=rating_key,
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
                            location=location,
                        )
                    )
                results.matched += 1
                return

            # 4. Search AniList and match — prefer folder name over Plex title
            search_title = clean_title_for_search(folder_name or title)
            candidates = await self._anilist.search_anime(search_title, per_page=15)

            if not candidates:
                logger.warning("  [no results] %s", title)
                if preview:
                    results.items.append(
                        ScanItemDetail(
                            rating_key=rating_key,
                            plex_title=title,
                            plex_year=year,
                            library_title=library_title,
                            status="failed",
                            reason="no AniList results found",
                            folder_name=folder_name,
                            location=location,
                        )
                    )
                results.failed += 1
                return

            match_result = self._matcher.find_best_match_with_season(
                title, candidates, target_season=1
            )

            if not match_result:
                logger.debug("  First search failed for '%s', trying broader", title)
                if preview:
                    results.items.append(
                        ScanItemDetail(
                            rating_key=rating_key,
                            plex_title=title,
                            plex_year=year,
                            library_title=library_title,
                            status="failed",
                            reason="no confident match found",
                            folder_name=folder_name,
                            location=location,
                        )
                    )
                results.failed += 1
                return

            matched_entry, confidence, season = match_result
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

            # Build series group if group_builder is available
            group_id: int | None = None
            group_entries: list[dict[str, Any]] = []
            tv_entries: list[dict[str, Any]] = []
            structure: str = "A"
            if self._group_builder:
                try:
                    group_id, group_entries = (
                        await self._group_builder.get_or_build_group(anilist_id)
                    )
                    # TV-only entries for season mapping (skip OVA, MOVIE,
                    # SPECIAL, etc. which don't correspond to Plex seasons)
                    tv_entries = [
                        e
                        for e in group_entries
                        if e.get("format", "") in ("TV", "TV_SHORT")
                    ]
                    if group_id and len(tv_entries) > 1:
                        structure = await self._detect_structure(
                            rating_key, tv_entries, anilist_id
                        )
                except Exception:
                    logger.exception("  Failed to build series group for %s", title)
                    # Non-fatal — continue with simple mapping
                    group_id = None

            if preview:
                # Build a summary of what would change — use the search
                # result directly instead of making another API call.
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

                # Extract year/season/format from matched entry
                start_date = matched_entry.get("startDate") or {}
                al_year = matched_entry.get("seasonYear") or start_date.get("year")
                al_season = matched_entry.get("season")
                al_format = matched_entry.get("format")

                results.items.append(
                    ScanItemDetail(
                        rating_key=rating_key,
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
                        location=location,
                        anilist_year=al_year,
                        anilist_season=al_season,
                        anilist_format=al_format,
                    )
                )
            elif structure == "B" and group_id and tv_entries:
                # Structure B: multi-season — one mapping per Plex season
                await self._store_structure_b_mappings(
                    rating_key,
                    title,
                    group_id,
                    tv_entries,
                    confidence,
                    dry_run,
                )
            else:
                # Structure A or C: single mapping (with group reference)
                await self._db.upsert_media_mapping(
                    source="plex",
                    source_id=rating_key,
                    source_title=title,
                    anilist_id=anilist_id,
                    anilist_title=anilist_title,
                    match_confidence=confidence,
                    match_method="fuzzy",
                    series_group_id=group_id,
                    season_number=1,
                )

                # Apply metadata to Plex
                await self._apply_anilist_metadata(
                    rating_key, title, anilist_id, confidence, "fuzzy", dry_run
                )
            results.matched += 1

        except Exception:
            logger.exception("  [error] Failed to process: %s", title)
            if preview:
                results.items.append(
                    ScanItemDetail(
                        rating_key=rating_key,
                        plex_title=title,
                        plex_year=year,
                        library_title=library_title,
                        status="failed",
                        reason="unexpected error",
                        folder_name=folder_name,
                        location=location,
                    )
                )
            results.failed += 1

    # ------------------------------------------------------------------
    # Structure detection
    # ------------------------------------------------------------------

    async def _detect_structure(
        self,
        rating_key: str,
        group_entries: list[dict[str, Any]],
        matched_anilist_id: int,
    ) -> str:
        """Detect Plex file structure: A (1:1), B (multi-season), C (absolute).

        Returns "A", "B", or "C".
        """
        try:
            seasons = await self._plex.get_show_seasons(rating_key)
        except Exception:
            logger.debug("  Could not fetch seasons for %s, assuming A", rating_key)
            return "A"

        # Filter out Season 0 (Specials)
        real_seasons = [s for s in seasons if s.index > 0]

        if len(real_seasons) > 1:
            logger.debug(
                "  Structure B detected: %d Plex seasons, %d group entries",
                len(real_seasons),
                len(group_entries),
            )
            return "B"

        if len(real_seasons) == 1:
            plex_ep_count = real_seasons[0].episode_count
            first_entry_eps = group_entries[0].get("episodes") or 0
            if first_entry_eps > 0 and plex_ep_count > first_entry_eps * 1.5:
                logger.debug(
                    "  Structure C detected: %d Plex eps vs %d first-entry eps",
                    plex_ep_count,
                    first_entry_eps,
                )
                return "C"

        return "A"

    async def _store_structure_b_mappings(
        self,
        rating_key: str,
        title: str,
        group_id: int,
        tv_entries: list[dict[str, Any]],
        confidence: float,
        dry_run: bool,
        force_refresh: bool = False,
    ) -> None:
        """Create one mapping per Plex season for Structure B shows.

        *tv_entries* should be pre-filtered to TV/TV_SHORT format only
        so that OVAs, movies, and specials don't consume season slots.
        """
        try:
            seasons = await self._plex.get_show_seasons(rating_key)
        except Exception:
            logger.exception("  Could not fetch seasons for structure B mapping")
            return

        real_seasons = sorted(
            [s for s in seasons if s.index > 0], key=lambda s: s.index
        )

        for i, season in enumerate(real_seasons):
            if i < len(tv_entries):
                entry = tv_entries[i]
            else:
                # More Plex seasons than TV entries — map to last entry
                entry = tv_entries[-1]
                logger.debug(
                    "  Season %d exceeds TV entries, mapping to last entry",
                    season.index,
                )

            entry_anilist_id = entry["anilist_id"]
            entry_title = entry.get("display_title", "")

            # Store the season-level mapping
            source_id = f"{rating_key}:S{season.index}"
            await self._db.upsert_media_mapping(
                source="plex",
                source_id=source_id,
                source_title=f"{title} - Season {season.index}",
                anilist_id=entry_anilist_id,
                anilist_title=entry_title,
                match_confidence=confidence,
                match_method="fuzzy",
                series_group_id=group_id,
                season_number=season.index,
            )

            # Write season title and poster to Plex
            if not dry_run and entry_title:
                await self._apply_season_metadata(
                    season.rating_key,
                    entry_anilist_id,
                    entry_title,
                    dry_run,
                    force_refresh=force_refresh,
                )

        # Also store the show-level mapping pointing to the first entry
        first_entry = tv_entries[0]
        await self._db.upsert_media_mapping(
            source="plex",
            source_id=rating_key,
            source_title=title,
            anilist_id=first_entry["anilist_id"],
            anilist_title=first_entry.get("display_title", ""),
            match_confidence=confidence,
            match_method="fuzzy",
            series_group_id=group_id,
            season_number=1,
        )

        if not dry_run:
            # Apply metadata from the first entry to the show level
            await self._apply_anilist_metadata(
                rating_key,
                title,
                first_entry["anilist_id"],
                confidence,
                "fuzzy",
                dry_run,
                force_refresh=force_refresh,
            )

        logger.info(
            "  [structure B] Mapped %d seasons for '%s'",
            len(real_seasons),
            title,
        )

    async def _apply_season_metadata(
        self,
        season_rating_key: str,
        anilist_id: int,
        season_title: str,
        dry_run: bool,
        force_refresh: bool = False,
    ) -> None:
        """Write title and poster to a single Plex season.

        The *season_title* is used as a fallback; the actual title written
        respects the user's ``app.title_display`` preference (romaji vs
        english), resolved from the AniList metadata for this entry.
        """
        try:
            metadata = await self._get_anilist_metadata(
                anilist_id, force_refresh=force_refresh
            )

            # Resolve title using the same preference as show-level
            resolved_title = season_title
            if metadata:
                title_display = (
                    await self._db.get_setting("app.title_display") or "romaji"
                )
                title_obj = metadata.get("title", {})
                romaji = title_obj.get("romaji") or ""
                english = title_obj.get("english") or ""

                if title_display in ("english", "both_english_primary"):
                    resolved_title = english or romaji or season_title
                else:
                    resolved_title = romaji or english or season_title

            params: dict[str, str] = {
                "title.value": resolved_title,
                "title.locked": "1",
            }

            if metadata:
                description = metadata.get("description", "")
                if description:
                    from src.Clients.PlexClient import _strip_html

                    params["summary.value"] = _strip_html(description)
                    params["summary.locked"] = "1"

            if dry_run:
                logger.info(
                    "  [dry-run] Would rename season %s to '%s'",
                    season_rating_key,
                    resolved_title,
                )
                return

            await self._plex.update_show_metadata(season_rating_key, params)

            # Upload season poster
            if metadata:
                cover_url = metadata.get("coverImage", {}).get("large", "")
                if cover_url:
                    try:
                        await self._plex.upload_poster(season_rating_key, cover_url)
                    except Exception:
                        logger.debug(
                            "  Failed to upload season poster for %s",
                            season_rating_key,
                        )

            logger.info("  [season] Renamed to '%s'", resolved_title)
        except Exception:
            logger.exception(
                "  Failed to apply season metadata for %s", season_rating_key
            )

    # ------------------------------------------------------------------
    # Metadata application
    # ------------------------------------------------------------------

    async def _apply_anilist_metadata(
        self,
        rating_key: str,
        plex_title: str,
        anilist_id: int,
        confidence: float,
        method: str,
        dry_run: bool,
        force_refresh: bool = False,
    ) -> None:
        """Fetch AniList metadata and write it to Plex."""
        # Try cache first
        metadata = await self._get_anilist_metadata(
            anilist_id, force_refresh=force_refresh
        )
        if not metadata:
            logger.warning("  Could not fetch AniList metadata for %d", anilist_id)
            return

        # Resolve primary / secondary title based on user preference
        title_display = await self._db.get_setting("app.title_display") or "romaji"
        title_obj = metadata.get("title", {})
        romaji = title_obj.get("romaji") or ""
        english = title_obj.get("english") or ""

        al_title: str
        original_title: str | None = None

        if title_display in ("english", "both_english_primary"):
            al_title = english or romaji or plex_title
            if romaji and romaji != al_title:
                original_title = romaji
        else:  # "romaji", "both_romaji_primary" (default)
            al_title = romaji or english or plex_title
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
                "  [dry-run] Would apply to '%s': title='%s', "
                "originalTitle='%s', genres=%s, "
                "rating=%s, studio=%s, poster=%s",
                plex_title,
                al_title,
                original_title or "",
                genres,
                rating,
                studio_name,
                bool(cover_url),
            )
            return

        # Write metadata to Plex
        params = PlexClient.build_metadata_params(
            title=al_title,
            original_title=original_title,
            summary=description,
            genres=genres,
            rating=rating,
            studio=studio_name,
        )

        if params:
            await self._plex.update_show_metadata(rating_key, params)

        if cover_url:
            try:
                await self._plex.upload_poster(rating_key, cover_url)
            except Exception:
                logger.warning("  Failed to upload poster for %s", plex_title)

        logger.info("  [applied] Metadata written to '%s'", plex_title)

    async def _get_anilist_metadata(
        self, anilist_id: int, force_refresh: bool = False
    ) -> dict[str, Any] | None:
        """Fetch metadata from cache or AniList API.

        When *force_refresh* is True the local cache is bypassed and a fresh
        request is made to AniList (the result is still stored in cache).
        """
        if force_refresh:
            await self._db.delete_cached_metadata(anilist_id)

        # Check DB cache
        cached = await self._db.get_cached_metadata(anilist_id)
        if cached:
            # Reconstruct the dict from cached fields
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

        # Fetch from API
        metadata = await self._anilist.get_anime_by_id(anilist_id)
        if not metadata:
            return None

        # Cache the result — coalesce None→"" for NOT NULL columns
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
