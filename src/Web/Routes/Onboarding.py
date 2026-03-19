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
)
from src.Scanner.LibraryScanner import LibraryScanner, LibraryScanProgress
from src.Scanner.LocalDirectoryScanner import LocalDirectoryScanner
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder

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
        "radarr_url": await db.get_setting("radarr.url") or "",
        "radarr_api_key": await db.get_setting("radarr.api_key") or "",
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

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Restructure — multi-source analyze
# ---------------------------------------------------------------------------


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
        conflicts = LibraryRestructurer.detect_conflicts(plan)
        request.app.state.onboarding_restructure_conflicts = conflicts

        progress.status = "complete"
        progress.phase = (
            f"Analysis complete: {plan.total_groups} shows, {plan.total_files} files"
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
    task = asyncio.create_task(_run_onboarding_execute(app_state, conflict_resolutions))
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

        # Start library scan as a background task
        if not hasattr(app_state, "library_scan_progress"):
            app_state.library_scan_progress = {}  # type: ignore[attr-defined]

        scan_progress = LibraryScanProgress(status="scanning")
        app_state.library_scan_progress[library_id] = scan_progress  # type: ignore[attr-defined]

        title_matcher = TitleMatcher(similarity_threshold=0.75)
        scanner = LibraryScanner(
            db=db,  # type: ignore[arg-type]
            anilist_client=app_state.anilist_client,  # type: ignore[attr-defined]
            title_matcher=title_matcher,
        )

        scan_task = asyncio.create_task(
            scanner.scan_library(
                library_id, library_paths, scan_progress, force_rescan=True
            )
        )
        app_state.onboarding_library_scan_task = scan_task  # type: ignore[attr-defined]
        logger.info(
            "Onboarding auto-build: library scan started for library %d", library_id
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
