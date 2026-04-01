"""Onboarding wizard routes — Phase B."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.Matching.TitleMatcher import TitleMatcher
from src.Scanner.LibraryRestructurer import (
    LibraryRestructurer,
    RestructurePlan,
    RestructureProgress,
    ShowInput,
    _find_video_subdirs,
)
from src.Scanner.LocalDirectoryScanner import LocalDirectoryScanner
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Web.App import spawn_background_task

logger = logging.getLogger(__name__)

router = APIRouter(tags=["onboarding"])


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


@router.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request) -> HTMLResponse:
    """Render the onboarding wizard."""
    db = request.app.state.db
    templates = request.app.state.templates

    onboarding_status = await db.get_setting("onboarding.status") or "not_started"
    current_step_raw = await db.get_setting("onboarding.step") or "1"
    try:
        current_step = int(current_step_raw)
    except ValueError:
        current_step = 1

    # Collect relevant settings for pre-filling forms
    settings = {
        "plex_url": await db.get_setting("plex.url") or "",
        "plex_token": await db.get_setting("plex.token") or "",
        "plex_poll_interval": await db.get_setting("plex.poll_interval") or "5",
        "plex_sync_libraries": await db.get_setting("plex.sync_libraries") or "",
        "plex_libraries_json": await db.get_setting("plex.libraries_json") or "[]",
        "jellyfin_url": await db.get_setting("jellyfin.url") or "",
        "jellyfin_api_key": await db.get_setting("jellyfin.api_key") or "",
        "anilist_client_id": await db.get_setting("anilist.client_id") or "",
        "anilist_client_secret": await db.get_setting("anilist.client_secret") or "",
        "cr_email": await db.get_setting("crunchyroll.email") or "",
        "cr_flaresolverr_url": await db.get_setting("crunchyroll.flaresolverr_url")
        or "",
        "sonarr_url": await db.get_setting("sonarr.url") or "",
        "sonarr_api_key": await db.get_setting("sonarr.api_key") or "",
        "sonarr_anime_root_folder": await db.get_setting("sonarr.anime_root_folder")
        or "",
        "sonarr_path_prefix": await db.get_setting("sonarr.path_prefix") or "",
        "sonarr_local_path_prefix": await db.get_setting("sonarr.local_path_prefix")
        or "",
        "radarr_url": await db.get_setting("radarr.url") or "",
        "radarr_api_key": await db.get_setting("radarr.api_key") or "",
        "radarr_anime_root_folder": await db.get_setting("radarr.anime_root_folder")
        or "",
        "radarr_path_prefix": await db.get_setting("radarr.path_prefix") or "",
        "radarr_local_path_prefix": await db.get_setting("radarr.local_path_prefix")
        or "",
        "downloads_arr_enabled": await db.get_setting("downloads.arr_enabled") or "",
        "downloads_auto_statuses": await db.get_setting("downloads.auto_statuses")
        or "",
        "downloads_monitor_mode": await db.get_setting("downloads.monitor_mode")
        or "future",
        "downloads_auto_search": await db.get_setting("downloads.auto_search") or "",
    }

    # Connection status flags (set after successful tests)
    connected = {
        "plex": (await db.get_setting("plex.connected") or "") == "true",
        "jellyfin": (await db.get_setting("jellyfin.connected") or "") == "true",
        "anilist": (await db.get_setting("anilist.connected") or "") == "true",
        "crunchyroll": (await db.get_setting("crunchyroll.connected") or "") == "true",
        "sonarr": (await db.get_setting("sonarr.connected") or "") == "true",
        "radarr": (await db.get_setting("radarr.connected") or "") == "true",
    }

    # AniList linked account (from users table)
    anilist_user = await db.fetch_one(
        "SELECT username FROM users WHERE service='anilist' LIMIT 1"
    )
    anilist_username = (anilist_user or {}).get("username", "")

    # Plex extra state
    has_plexpass = (await db.get_setting("plex.has_plexpass") or "") == "true"

    return templates.TemplateResponse(
        "onboarding.html",
        {
            "request": request,
            "onboarding_status": onboarding_status,
            "current_step": current_step,
            "settings": settings,
            "connected": connected,
            "anilist_username": anilist_username,
            "has_plexpass": has_plexpass,
            "version": "0.1.0",
        },
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@router.post("/onboarding/status")
async def update_onboarding_status(request: Request) -> JSONResponse:
    """Save onboarding step / completion status."""
    db = request.app.state.db
    body = await request.json()

    status: str | None = body.get("status")
    step: int | None = body.get("step")

    if status:
        await db.set_setting("onboarding.status", status)
    if step is not None:
        await db.set_setting("onboarding.step", str(step))

    # When onboarding completes, kick off Plex/Jellyfin metadata scans
    # so the unified library has data on first visit.
    if status == "completed":
        # Clear stale restructure state so _auto_scan_media_servers
        # doesn't think a restructure is pending (e.g. from a previous
        # onboarding attempt that was abandoned or skipped).
        exec_prog = getattr(request.app.state, "restructure_exec_progress", None)
        restructure_done = (
            exec_prog is not None and getattr(exec_prog, "status", "") == "complete"
        )
        if not restructure_done:
            request.app.state.restructure_progress = None  # type: ignore[attr-defined]
            request.app.state.restructure_plan = None  # type: ignore[attr-defined]
            request.app.state.onboarding_restructure_plan = None  # type: ignore[attr-defined]

        spawn_background_task(
            request.app.state, _auto_scan_media_servers(request.app.state)
        )

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Auto-scan Plex/Jellyfin after onboarding completes
# ---------------------------------------------------------------------------


async def _auto_scan_media_servers(app_state: object) -> None:
    """Background task: run preview scans for configured Plex/Jellyfin servers.

    Scans run in preview mode so metadata is NOT written automatically.
    On completion a persistent notification is created telling the user
    to review and apply the results.

    If a restructure is in progress, waits for it to finish and then
    triggers a Plex/Jellyfin library refresh so the media server picks
    up the new file structure before we scan.
    """
    from src.Clients.JellyfinClient import JellyfinClient
    from src.Clients.PlexClient import PlexClient
    from src.Scanner.JellyfinMetadataScanner import JellyfinMetadataScanner
    from src.Scanner.MetadataScanner import MetadataScanner, ScanProgress

    config = app_state.config  # type: ignore[attr-defined]
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]

    # If a restructure was started during onboarding (analyze running,
    # plan built, or execute in progress), skip scans entirely.
    # Scans will be triggered automatically after the user reviews and
    # executes via _run_execution in Restructure.py.
    restructure_in_flight = (
        getattr(app_state, "restructure_progress", None) is not None
        or getattr(app_state, "restructure_plan", None) is not None
        or getattr(app_state, "onboarding_restructure_plan", None) is not None
    )
    if restructure_in_flight:
        exec_prog = getattr(app_state, "restructure_exec_progress", None)
        restructure_ran = exec_prog is not None and exec_prog.status == "complete"
        if not restructure_ran:
            logger.info(
                "Restructure in progress or pending review — deferring"
                " media server scans until restructure is applied"
            )
            return
    else:
        restructure_ran = False

    if restructure_ran and config.plex.url and config.plex.token:
        logger.info("Triggering Plex library refresh post-restructure")
        plex_refresh = PlexClient(url=config.plex.url, token=config.plex.token)
        try:
            keys = (
                list(config.plex.anime_library_keys)
                if config.plex.anime_library_keys
                else None
            )
            if keys:
                for key in keys:
                    await plex_refresh.refresh_library_and_wait(key, poll_interval=3.0)
            else:
                # No specific keys — attempt a refresh of all libraries
                libs = await plex_refresh.get_libraries()
                for lib in libs:
                    await plex_refresh.refresh_library_and_wait(
                        lib.key, poll_interval=3.0
                    )
        except Exception:
            logger.exception("Plex post-restructure refresh failed")
        finally:
            await plex_refresh.close()

    if restructure_ran and config.jellyfin.url and config.jellyfin.api_key:
        logger.info("Triggering Jellyfin library refresh post-restructure")
        jf_refresh = JellyfinClient(
            url=config.jellyfin.url, api_key=config.jellyfin.api_key
        )
        try:
            await jf_refresh.refresh_library_and_wait(
                poll_interval=5.0, inactivity_timeout=120.0
            )
        except Exception:
            logger.exception("Jellyfin post-restructure refresh failed")
        finally:
            await jf_refresh.close()

    # Index local library — either run it now or wait for the background
    # scan that was kicked off when the user selected their media directory.
    already_seeded = getattr(app_state, "library_already_seeded", False)
    lib_scan = getattr(app_state, "library_scan_progress", None)
    lib_scan_running = (
        lib_scan is not None and getattr(lib_scan, "status", "") == "running"
    )

    if lib_scan_running:
        # Background scan from save-media-dirs is still running — wait
        # for it instead of starting a duplicate.
        logger.info("Waiting for background local scan to finish")
        while getattr(lib_scan, "status", "") == "running":
            await asyncio.sleep(2)
        logger.info("Background local scan finished")
    elif not restructure_ran and not already_seeded:
        # No background scan was started — run inline now.
        await _auto_index_local_libraries(app_state)

    # Plex preview scan
    if config.plex.url and config.plex.token:
        logger.info("Onboarding complete — starting Plex preview scan")
        plex_client = PlexClient(url=config.plex.url, token=config.plex.token)
        title_matcher = TitleMatcher(similarity_threshold=0.75)
        group_builder = SeriesGroupBuilder(db, anilist_client)
        scanner = MetadataScanner(
            db, anilist_client, title_matcher, plex_client, config, group_builder
        )
        progress = ScanProgress()
        app_state.plex_scan_progress = progress  # type: ignore[attr-defined]
        try:
            library_keys = (
                list(config.plex.anime_library_keys)
                if config.plex.anime_library_keys
                else None
            )
            results = await scanner.run_scan(
                preview=True, library_keys=library_keys, progress=progress
            )
            app_state.plex_scan_results = results  # type: ignore[attr-defined]
            matched = results.matched if results else 0
            logger.info("Plex preview scan complete — %d matches", matched)
            await db.add_notification(
                notification_type="success",
                message=(
                    f"Plex scan complete — {matched} matches found."
                    " Review results before applying."
                ),
                action_url="/scan/plex/results",
                action_label="Review Results",
            )
        except Exception:
            logger.exception("Automatic Plex preview scan failed")
            await db.add_notification(
                notification_type="warning",
                message="Plex scan failed. Check logs for details.",
                action_url="/plex",
                action_label="Go to Plex",
            )
        finally:
            await plex_client.close()

    # Jellyfin preview scan
    if config.jellyfin.url and config.jellyfin.api_key:
        logger.info("Onboarding complete — starting Jellyfin preview scan")
        jf_client = JellyfinClient(
            url=config.jellyfin.url, api_key=config.jellyfin.api_key
        )
        title_matcher = TitleMatcher(similarity_threshold=0.75)
        group_builder = SeriesGroupBuilder(db, anilist_client)
        jf_scanner = JellyfinMetadataScanner(
            db, anilist_client, title_matcher, jf_client, config, group_builder
        )
        jf_progress = ScanProgress()
        app_state.jellyfin_scan_progress = jf_progress  # type: ignore[attr-defined]
        try:
            library_ids = (
                list(config.jellyfin.anime_library_ids)
                if config.jellyfin.anime_library_ids
                else None
            )
            jf_results = await jf_scanner.run_scan(
                preview=True, library_ids=library_ids, progress=jf_progress
            )
            app_state.jellyfin_scan_results = jf_results  # type: ignore[attr-defined]
            matched = jf_results.matched if jf_results else 0
            logger.info("Jellyfin preview scan complete — %d matches", matched)
            await db.add_notification(
                notification_type="success",
                message=(
                    f"Jellyfin scan complete — {matched} matches"
                    " found. Review results before applying."
                ),
                action_url="/jellyfin/scan/results",
                action_label="Review Results",
            )
        except Exception:
            logger.exception("Automatic Jellyfin preview scan failed")
            await db.add_notification(
                notification_type="warning",
                message="Jellyfin scan failed. Check logs for details.",
                action_url="/jellyfin",
                action_label="Go to Jellyfin",
            )
        finally:
            await jf_client.close()


# ---------------------------------------------------------------------------
# Local library index helper (skip / rename path)
# ---------------------------------------------------------------------------


async def _auto_index_local_libraries(app_state: object) -> None:
    """Index local library_items from existing folder structure without restructuring.

    Called on onboarding complete/skip.  Scans each directory directly —
    for root folders that contain named season subdirs (Structure B), each
    subdir is scanned individually so it gets its own AniList match.  This
    avoids the restructure analyze() pipeline entirely, which was designed
    to *plan file moves* and used lossy fuzzy matching to map subdirs to
    series group entries.

    After scanning, series groups are built for matched shows so the
    unified library can display group membership.
    """
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]

    libraries = await db.get_all_libraries()  # type: ignore[attr-defined]
    if not libraries:
        # Fall back to source dirs captured during onboarding analyze
        source_dirs: list[str] = getattr(app_state, "onboarding_source_dirs", []) or []
        if not source_dirs:
            logger.debug(
                "_auto_index_local_libraries: no libraries or source dirs, skipping"
            )
            return
        library_id = await db.create_library(  # type: ignore[attr-defined]
            "My Library", json.dumps(source_dirs)
        )
        library_paths = source_dirs
    else:
        library_id = libraries[0]["id"]
        raw = libraries[0].get("paths") or "[]"
        try:
            library_paths = json.loads(raw)
        except Exception:
            library_paths = []

    if not library_paths:
        logger.debug("_auto_index_local_libraries: no library paths, skipping")
        return

    logger.info(
        "_auto_index_local_libraries: indexing library %d paths=%s",
        library_id,
        library_paths,
    )

    title_matcher = TitleMatcher(similarity_threshold=0.75)
    group_builder = SeriesGroupBuilder(db, anilist_client)
    dir_scanner = LocalDirectoryScanner(
        db=db, anilist_client=anilist_client, title_matcher=title_matcher
    )

    scan_progress = RestructureProgress(status="running")
    scan_progress.phase = "Scanning local directories"
    app_state.library_scan_progress = scan_progress  # type: ignore[attr-defined]

    try:
        # Phase 1: Scan root-level folders to identify shows
        root_shows: list[ShowInput] = []
        for path in library_paths:
            shows = await dir_scanner.scan_directory(path, scan_progress)
            root_shows.extend(shows)

        if not root_shows:
            logger.info("_auto_index_local_libraries: no shows found")
            scan_progress.status = "complete"
            return

        # Phase 2: For Structure B roots (named season subdirs), scan
        # inside each root to get per-season AniList matches directly.
        # This replaces the old approach of fuzzy-matching subdir names
        # against series group entry display_titles.
        scan_progress.phase = "Scanning season subdirectories"
        all_items: list[tuple[ShowInput, str]] = []  # (show, root_folder_name)

        for root_show in root_shows:
            if not root_show.local_path:
                continue
            root_name = os.path.basename(root_show.local_path.rstrip("/"))
            video_subdirs = _find_video_subdirs(root_show.local_path)

            if len(video_subdirs) >= 2:
                # Structure B: scan each subdir as its own show
                subdir_shows = await dir_scanner.scan_directory(
                    root_show.local_path, scan_progress
                )
                for sub_show in subdir_shows:
                    all_items.append((sub_show, root_name))
                logger.info(
                    "Structure B: scanned %d subdirs in '%s'",
                    len(subdir_shows),
                    root_name,
                )
            else:
                # Single-season / movie / OVA — use the root match directly
                all_items.append((root_show, root_name))

        # Phase 3: Build series groups for matched shows
        scan_progress.phase = "Building series groups"
        # Map anilist_id → series_group_id for library_item creation
        group_ids: dict[int, int] = {}
        seen_groups: set[int] = set()
        for show, _ in all_items:
            if not show.anilist_id or show.anilist_id in seen_groups:
                continue
            seen_groups.add(show.anilist_id)
            try:
                group_id, _entries = await group_builder.build_group(show.anilist_id)
                if group_id:
                    entries = await db.get_series_group_entries(group_id)
                    for entry in entries:
                        group_ids[entry["anilist_id"]] = group_id
            except Exception:
                logger.debug(
                    "Series group build failed for anilist_id=%d", show.anilist_id
                )

        # Phase 4: Create library_items directly from scan results
        scan_progress.phase = "Saving library items"
        upserted = 0
        for show, root_name in all_items:
            if not show.anilist_id:
                continue
            cached = await db.get_cached_metadata(show.anilist_id)
            cover = (cached.get("cover_image") or "") if cached else ""
            year = (cached.get("year") or 0) if cached else 0
            fmt = (cached.get("format") or "") if cached else ""
            eps = cached.get("episodes") if cached else None

            await db.upsert_library_item(
                library_id=library_id,
                folder_path=show.local_path,
                folder_name=root_name,
                anilist_id=show.anilist_id,
                anilist_title=show.anilist_title or show.title,
                match_confidence=1.0,
                match_method="local_scan",
                series_group_id=group_ids.get(show.anilist_id),
                cover_image=cover,
                year=year,
                anilist_format=fmt,
                anilist_episodes=eps,
            )
            upserted += 1

        logger.info(
            "_auto_index_local_libraries: seeded %d items for library %d",
            upserted,
            library_id,
        )
        scan_progress.status = "complete"
        app_state.library_already_seeded = True  # type: ignore[attr-defined]
    except Exception:
        logger.exception("_auto_index_local_libraries: failed")
        scan_progress.status = "error"


# ---------------------------------------------------------------------------
# Restructure — multi-source analyze
# ---------------------------------------------------------------------------


@router.post("/onboarding/save-media-dirs")
async def save_media_dirs(request: Request) -> JSONResponse:
    """Persist local media directories when the user skips restructuring.

    Creates (or updates) a library row in the DB so that
    ``_auto_index_local_libraries`` can find and index the paths after
    onboarding completes.
    """
    body = await request.json()
    source_dirs: list[str] = body.get("source_dirs") or []
    if not source_dirs:
        return JSONResponse(
            {"ok": False, "error": "No source directories provided"},
            status_code=400,
        )

    # Store in app_state for the current session
    request.app.state.onboarding_source_dirs = source_dirs

    # Persist to DB so the library survives a container restart
    db = request.app.state.db
    libraries = await db.get_all_libraries()
    if not libraries:
        await db.create_library("My Library", json.dumps(source_dirs))
    else:
        lib = libraries[0]
        await db.update_library(lib["id"], lib["name"], json.dumps(source_dirs))

    logger.info("Saved media directories (skip path): %s", source_dirs)

    # Start local library indexing in the background so it runs while
    # the user configures services on step 3.  The completion handler
    # (_auto_scan_media_servers) checks library_scan_progress to avoid
    # starting a duplicate scan.
    spawn_background_task(
        request.app.state, _auto_index_local_libraries(request.app.state)
    )

    return JSONResponse({"ok": True, "saved": len(source_dirs)})


@router.post("/onboarding/restructure/analyze")
async def onboarding_restructure_analyze(request: Request) -> JSONResponse:
    """Run multi-source restructure analysis and return results (conflicts, file count).

    Runs synchronously so the frontend can receive results immediately.
    Progress is tracked in app_state.restructure_progress for the floating widget.
    """
    body = await request.json()
    source_dirs: list[str] = body.get("source_dirs") or []
    output_dir: str = (body.get("output_dir") or "").strip()

    # Map onboarding UI values to internal level identifiers
    _level_map = {"full": "full_restructure", "quick": "folder_file_rename"}
    level_raw: str = body.get("level") or "full"
    level: str = _level_map.get(level_raw, level_raw)

    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    # Save any template overrides (sent as nested templates object from onboarding)
    templates: dict = body.get("templates") or {}
    for key, setting_key in [
        ("episode", "naming.file_template"),
        ("folder", "naming.folder_template"),
        ("season", "naming.season_folder_template"),
        ("movie", "naming.movie_file_template"),
        ("illegal_char_replacement", "naming.illegal_char_replacement"),
    ]:
        val = (templates.get(key) or "").strip()
        if val:
            await db.set_setting(setting_key, val)

    logger.info(
        "Onboarding analyze request — level_raw=%r -> level=%r, "
        "source_dirs=%s, output_dir=%r, templates=%s",
        level_raw,
        level,
        source_dirs,
        output_dir or "(none)",
        {k: v for k, v in templates.items() if v},
    )

    if not source_dirs:
        return JSONResponse(
            {"ok": False, "error": "At least one source directory is required"},
            status_code=400,
        )

    file_tmpl = await db.get_setting("naming.file_template") or ""
    folder_tmpl = await db.get_setting("naming.folder_template") or ""
    season_tmpl = await db.get_setting("naming.season_folder_template") or ""
    movie_tmpl = await db.get_setting("naming.movie_file_template") or ""
    title_pref = await db.get_setting("app.title_display") or "romaji"
    illegal_char_repl = await db.get_setting("naming.illegal_char_replacement") or ""

    logger.info(
        "Onboarding analyze — effective templates: file=%r folder=%r season=%r "
        "movie=%r illegal_char=%r title_pref=%r",
        file_tmpl or "(default)",
        folder_tmpl or "(default)",
        season_tmpl or "(default)",
        movie_tmpl or "(default)",
        illegal_char_repl or "(remove)",
        title_pref,
    )

    group_builder = SeriesGroupBuilder(db, anilist_client)
    restructurer = LibraryRestructurer(
        db=db,
        group_builder=group_builder,
        file_template=file_tmpl,
        folder_template=folder_tmpl,
        season_folder_template=season_tmpl,
        movie_file_template=movie_tmpl,
        title_pref=title_pref,
        illegal_char_replacement=illegal_char_repl,
    )
    title_matcher = TitleMatcher(similarity_threshold=0.75)
    scanner = LocalDirectoryScanner(
        db=db, anilist_client=anilist_client, title_matcher=title_matcher
    )

    # Clear any previous plan / notification from a prior analyze run
    # (user may have gone back to change naming settings).
    request.app.state.restructure_plan = None
    request.app.state.onboarding_restructure_plan = None
    await db.dismiss_notifications_by_url("/restructure/preview")
    await db.clear_dismissed_notifications()

    # Store for library auto-build after execute
    request.app.state.onboarding_source_dirs = source_dirs
    request.app.state.onboarding_output_dir = output_dir

    progress = RestructureProgress(status="running")
    request.app.state.restructure_progress = progress

    try:
        all_shows = []
        for src_dir in source_dirs:
            progress.phase = f"Scanning {src_dir}"
            logger.info("Onboarding scan: starting directory %r", src_dir)
            shows = await scanner.scan_directory(src_dir, progress)
            matched = sum(1 for s in shows if s.anilist_id)
            unmatched = len(shows) - matched
            logger.info(
                "Onboarding scan: %r -> %d folders (%d matched, %d unmatched)",
                src_dir,
                len(shows),
                matched,
                unmatched,
            )
            if unmatched:
                for s in shows:
                    if not s.anilist_id:
                        logger.warning(
                            "Onboarding scan: no AniList match for %r", s.title
                        )
            all_shows.extend(shows)

        logger.info(
            "Onboarding analyze: total shows=%d (matched=%d, unmatched=%d), "
            "calling restructurer.analyze level=%r output_dir=%r",
            len(all_shows),
            sum(1 for s in all_shows if s.anilist_id),
            sum(1 for s in all_shows if not s.anilist_id),
            level,
            output_dir or "(alongside source)",
        )

        plan = await restructurer.analyze(
            all_shows, progress, level=level, output_dir=output_dir or None
        )
        request.app.state.onboarding_restructure_plan = plan

        # Create/reuse a library for post-restructure seeding
        lib_paths = [output_dir] if output_dir else list(source_dirs)
        libraries = await db.get_all_libraries()
        if libraries:
            lib_id = libraries[0]["id"]
            await db.update_library(lib_id, libraries[0]["name"], json.dumps(lib_paths))
        else:
            lib_id = await db.create_library("My Library", json.dumps(lib_paths))
        request.app.state.onboarding_library_id = lib_id

        # Only gate Plex/Jellyfin scans behind the restructure plan
        # if there are actually changes to apply.  An empty plan
        # (0 groups) means nothing to move — proceed straight to scans.
        if plan.total_groups > 0:
            request.app.state.restructure_plan = plan
            request.app.state.restructure_source_mode = "local"
            request.app.state.restructure_library_keys = []
            request.app.state.restructure_library_id = lib_id

        conflicts = LibraryRestructurer.detect_conflicts(plan)
        request.app.state.onboarding_restructure_conflicts = conflicts

        progress.status = "complete"
        progress.phase = (
            f"Analysis complete: {plan.total_groups} shows,"
            f" {plan.total_files} files"
        )

        # Create a notification only if there's a plan to review
        if plan.total_groups > 0:
            await db.add_notification(
                notification_type="success",
                message=(
                    f"Library scan complete — {plan.total_groups} shows,"
                    f" {plan.total_files} files ready to organize."
                    " Review before applying."
                ),
                action_url="/restructure/preview",
                action_label="Review Plan",
            )
        logger.info(
            "Onboarding analysis complete: %d groups, %d files, %d conflicts",
            plan.total_groups,
            plan.total_files,
            len(conflicts),
        )
        if conflicts:
            for c in conflicts:
                logger.warning(
                    "Onboarding conflict: %s | %s -> %s (%s)",
                    c.get("group"),
                    c.get("source"),
                    c.get("destination"),
                    c.get("conflict_type"),
                )

        return JSONResponse(
            {
                "ok": True,
                "total_groups": plan.total_groups,
                "file_count": plan.total_files,
                "conflicts": [
                    {
                        "show": c["group"],
                        "group_key": c["group_key"],
                        "source": c["source"],
                        "target": c["destination"],
                        "conflict_type": c["conflict_type"],
                    }
                    for c in conflicts
                ],
            }
        )
    except Exception as exc:
        logger.exception("Onboarding restructure analysis failed")
        progress.status = "error"
        progress.error_message = str(exc)
        return JSONResponse(
            {"ok": False, "error": f"Analysis failed: {exc}"}, status_code=500
        )


# ---------------------------------------------------------------------------
# Restructure — execute
# ---------------------------------------------------------------------------


async def _run_onboarding_execute(
    app_state: object,
    conflict_resolutions: dict[str, str],
) -> None:
    """Background coroutine: execute restructure plan with conflict resolutions."""
    db = app_state.db  # type: ignore[attr-defined]
    plan: RestructurePlan = app_state.onboarding_restructure_plan  # type: ignore[attr-defined]

    # Apply conflict resolutions: disable groups marked "skip",
    # mark "merge" groups so execute skips existing files.
    for group in plan.groups:
        resolution = conflict_resolutions.get(group.group_key, "overwrite")
        if resolution == "skip":
            group.enabled = False
        elif resolution == "merge":
            # Filter out file moves where destination already exists
            group.file_moves = [
                fm for fm in group.file_moves if not os.path.exists(fm.destination)
            ]

    exec_progress = RestructureProgress(status="running")
    app_state.restructure_exec_progress = exec_progress  # type: ignore[attr-defined]

    file_tmpl = await db.get_setting("naming.file_template") or ""
    folder_tmpl = await db.get_setting("naming.folder_template") or ""
    season_tmpl = await db.get_setting("naming.season_folder_template") or ""
    movie_tmpl = await db.get_setting("naming.movie_file_template") or ""
    title_pref = await db.get_setting("app.title_display") or "romaji"
    illegal_char_repl = await db.get_setting("naming.illegal_char_replacement") or ""

    group_builder = SeriesGroupBuilder(db, app_state.anilist_client)  # type: ignore[attr-defined]
    restructurer = LibraryRestructurer(
        db=db,
        group_builder=group_builder,
        file_template=file_tmpl,
        folder_template=folder_tmpl,
        season_folder_template=season_tmpl,
        movie_file_template=movie_tmpl,
        title_pref=title_pref,
        illegal_char_replacement=illegal_char_repl,
    )

    enabled_groups = [g for g in plan.groups if getattr(g, "enabled", True)]
    total_files = sum(len(g.file_moves) for g in enabled_groups)
    logger.info(
        "Onboarding execute start: %d groups enabled (%d disabled), %d files to move, "
        "level=%r",
        len(enabled_groups),
        len(plan.groups) - len(enabled_groups),
        total_files,
        plan.operation_level,
    )

    try:
        stats = await restructurer.execute(plan, exec_progress)
        exec_progress.status = "complete"
        moved = stats.get("files_moved", 0)
        errors = stats.get("errors", 0)
        exec_progress.phase = f"Done: {moved} moved, {errors} errors"
        exec_progress.files_moved = moved  # type: ignore[attr-defined]
        exec_progress.errors = errors  # type: ignore[attr-defined]
        logger.info(
            "Onboarding execute complete: files_moved=%d errors=%d full_stats=%s",
            moved,
            errors,
            stats,
        )

        # Auto-build library from output directory
        await _auto_build_library(app_state, db, plan)

    except Exception:
        logger.exception("Onboarding restructure execute failed")
        exec_progress.status = "error"
        exec_progress.error_message = "Execute failed — see logs"


@router.post("/onboarding/restructure/execute")
async def onboarding_restructure_execute(request: Request) -> JSONResponse:
    """Execute the pending restructure plan with conflict resolutions."""
    body = await request.json()
    conflict_resolutions: dict[str, str] = body.get("conflict_resolutions") or {}

    app_state = request.app.state
    plan: RestructurePlan | None = getattr(
        app_state, "onboarding_restructure_plan", None
    )
    if not plan:
        return JSONResponse(
            {"ok": False, "error": "No restructure plan available — run analyze first"},
            status_code=400,
        )

    # Store the task reference — asyncio only holds a weak ref, so without this
    # the GC can collect the task before it ever runs.
    task = spawn_background_task(
        app_state, _run_onboarding_execute(app_state, conflict_resolutions)
    )
    app_state.restructure_exec_task = task
    return JSONResponse({"ok": True, "message": "Restructure execution started"})


# ---------------------------------------------------------------------------
# Library auto-build helper
# ---------------------------------------------------------------------------


async def _auto_build_library(
    app_state: object,
    db: object,
    plan: RestructurePlan,
) -> None:
    """Create/update a library from the restructure output dirs and start a scan."""
    try:
        output_dir: str = getattr(app_state, "onboarding_output_dir", "") or ""
        source_dirs: list[str] = getattr(app_state, "onboarding_source_dirs", []) or []

        if output_dir:
            library_paths = [output_dir]
        elif source_dirs:
            library_paths = list(source_dirs)
        else:
            # Derive from plan: parent dirs of all target folders
            parents: set[str] = {
                os.path.dirname(g.target_folder) for g in plan.groups if g.target_folder
            }
            library_paths = list(parents) if parents else []

        if not library_paths:
            logger.warning("Onboarding auto-build: could not determine library paths")
            return

        # Create or reuse the first existing library
        libraries = await db.get_all_libraries()  # type: ignore[attr-defined]
        if libraries:
            library_id = libraries[0]["id"]
            await db.update_library(  # type: ignore[attr-defined]
                library_id, libraries[0]["name"], json.dumps(library_paths)
            )
            logger.info(
                "Onboarding auto-build: updated library %d paths=%s",
                library_id,
                library_paths,
            )
        else:
            library_id = await db.create_library(  # type: ignore[attr-defined]
                "My Library", json.dumps(library_paths)
            )
            logger.info(
                "Onboarding auto-build: created library %d paths=%s",
                library_id,
                library_paths,
            )

        app_state.onboarding_library_id = library_id  # type: ignore[attr-defined]

        # Seed library_items from the plan (series-group-aware, post-execute)
        file_tmpl = await db.get_setting("naming.file_template") or ""  # type: ignore[attr-defined]
        folder_tmpl = await db.get_setting("naming.folder_template") or ""  # type: ignore[attr-defined]
        season_tmpl = await db.get_setting("naming.season_folder_template") or ""  # type: ignore[attr-defined]
        movie_tmpl = await db.get_setting("naming.movie_file_template") or ""  # type: ignore[attr-defined]
        title_pref = await db.get_setting("app.title_display") or "romaji"  # type: ignore[attr-defined]
        illegal_char_repl = (  # type: ignore[attr-defined]
            await db.get_setting("naming.illegal_char_replacement") or ""
        )
        group_builder = SeriesGroupBuilder(db, app_state.anilist_client)  # type: ignore[attr-defined]
        restructurer = LibraryRestructurer(
            db=db,  # type: ignore[arg-type]
            group_builder=group_builder,
            file_template=file_tmpl,
            folder_template=folder_tmpl,
            season_folder_template=season_tmpl,
            movie_file_template=movie_tmpl,
            title_pref=title_pref,
            illegal_char_replacement=illegal_char_repl,
        )
        seeded = await restructurer.seed_library_items(
            plan, library_id, from_source=False
        )
        logger.info(
            "Onboarding auto-build: seeded %d library_items for library %d",
            seeded,
            library_id,
        )
    except Exception:
        logger.exception("Onboarding auto-build: failed to create library / start scan")


# ---------------------------------------------------------------------------
# Exec status polling endpoint
# ---------------------------------------------------------------------------


@router.get("/api/onboarding/exec-status")
async def onboarding_exec_status(request: Request) -> JSONResponse:
    """Return current execute + library-scan progress for frontend polling."""
    app_state = request.app.state
    exec_progress = getattr(app_state, "restructure_exec_progress", None)
    library_id: int | None = getattr(app_state, "onboarding_library_id", None)

    if not exec_progress:
        return JSONResponse({"status": "not_started"})

    result: dict = {
        "status": exec_progress.status,
        "phase": getattr(exec_progress, "phase", ""),
        "files_moved": getattr(exec_progress, "files_moved", 0),
        "errors": getattr(exec_progress, "errors", 0),
        "library_id": library_id,
    }

    if library_id is not None:
        progress_map: dict = getattr(app_state, "library_scan_progress", {})
        lib_scan = progress_map.get(library_id)
        if lib_scan:
            result["library_scan_status"] = lib_scan.status
            result["library_scan_phase"] = getattr(lib_scan, "phase", "")

    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Restructure report endpoint
# ---------------------------------------------------------------------------


@router.get("/api/restructure/report")
async def restructure_report(request: Request) -> JSONResponse:
    """Return recent restructure log entries for the Done step report."""
    db = request.app.state.db
    limit = min(int(request.query_params.get("limit", "1000")), 5000)
    entries = await db.get_restructure_log(limit=limit)

    moved = sum(1 for e in entries if e["status"] == "success")
    errors = sum(1 for e in entries if e["status"] == "error")

    return JSONResponse(
        {
            "total": len(entries),
            "moved": moved,
            "errors": errors,
            "entries": [dict(e) for e in entries],
        }
    )
