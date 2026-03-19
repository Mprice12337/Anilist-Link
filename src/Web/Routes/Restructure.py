"""Library Restructuring Wizard routes."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import Response

from src.Clients.JellyfinClient import JellyfinClient
from src.Clients.PlexClient import PlexClient
from src.Matching.TitleMatcher import TitleMatcher
from src.Scanner.JellyfinShowProvider import JellyfinShowProvider
from src.Scanner.LibraryRestructurer import (
    LibraryRestructurer,
    RestructurePlan,
    RestructureProgress,
)
from src.Scanner.LibraryScanner import LibraryScanner, LibraryScanProgress
from src.Scanner.LocalDirectoryScanner import LocalDirectoryScanner
from src.Scanner.PlexShowProvider import PlexShowProvider
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Utils.NamingTemplate import NAMING_PRESETS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["restructure"])


def _is_restructure_busy(app_state: object) -> bool:
    """Return True if an analysis or execution is currently in-flight."""
    progress = getattr(app_state, "restructure_progress", None)
    if progress and progress.status not in ("", "pending", "complete", "error"):
        return True
    exec_progress = getattr(app_state, "restructure_exec_progress", None)
    if exec_progress and exec_progress.status not in (
        "",
        "pending",
        "complete",
        "error",
    ):
        return True
    return False


async def _auto_rescan_library(app_state: object) -> None:
    """Rescan the library that triggered this restructure, if any."""
    import json

    library_id: int | None = getattr(app_state, "restructure_library_id", None)
    if not library_id:
        return

    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]

    library = await db.get_library(library_id)
    if not library:
        logger.warning("Auto-rescan: library %d not found", library_id)
        return

    path_list = json.loads(library["paths"]) if library["paths"] else []
    if not path_list:
        return

    logger.info(
        "Auto-rescanning library %d (%s) after restructure", library_id, library["name"]
    )

    # Set up progress tracking so the library detail page shows scan status
    if not hasattr(app_state, "library_scan_progress"):
        app_state.library_scan_progress = {}  # type: ignore[attr-defined]
    progress = LibraryScanProgress()
    app_state.library_scan_progress[library_id] = progress  # type: ignore[attr-defined]

    title_matcher = TitleMatcher(similarity_threshold=0.75)
    scanner = LibraryScanner(
        db=db, anilist_client=anilist_client, title_matcher=title_matcher
    )

    try:
        stats = await scanner.scan_library(
            library_id, path_list, progress, force_rescan=False
        )
        progress.phase = (
            f"Done: {stats['matched']} matched, {stats['unmatched']} unmatched"
        )
        logger.info(
            "Auto-rescan complete for library %d: %d matched, %d unmatched, %d pruned",
            library_id,
            stats["matched"],
            stats["unmatched"],
            stats["pruned"],
        )
    except Exception:
        logger.exception("Auto-rescan failed for library %d", library_id)
        progress.status = "error"
        progress.error_message = "Auto-rescan after restructure failed"


async def _run_analysis(app_state: object) -> None:
    """Background coroutine: analyze libraries for restructuring."""
    config = app_state.config  # type: ignore[attr-defined]
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]
    progress: RestructureProgress = app_state.restructure_progress  # type: ignore[attr-defined]
    source_mode: str = app_state.restructure_source_mode  # type: ignore[attr-defined]
    level: str = getattr(app_state, "restructure_operation_level", "full_restructure")  # type: ignore[attr-defined]
    force_rescan: bool = getattr(app_state, "restructure_force_rescan", False)  # type: ignore[attr-defined]

    group_builder = SeriesGroupBuilder(db, anilist_client)

    # Load naming templates from settings
    file_tmpl = await db.get_setting("naming.file_template") or ""
    folder_tmpl = await db.get_setting("naming.folder_template") or ""
    season_tmpl = await db.get_setting("naming.season_folder_template") or ""
    movie_tmpl = await db.get_setting("naming.movie_file_template") or ""
    title_pref = await db.get_setting("app.title_display") or "romaji"
    illegal_char_repl = await db.get_setting("naming.illegal_char_replacement") or ""

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
    plex_client: PlexClient | None = None
    jellyfin_client: JellyfinClient | None = None

    try:
        if source_mode == "plex":
            library_keys: list[str] = app_state.restructure_library_keys  # type: ignore[attr-defined]
            plex_prefix = await db.get_setting("restructure.plex_path_prefix") or ""
            local_prefix = await db.get_setting("restructure.local_path_prefix") or ""

            plex_client = PlexClient(url=config.plex.url, token=config.plex.token)
            provider = PlexShowProvider(
                plex_client=plex_client,
                db=db,
                plex_path_prefix=plex_prefix,
                local_path_prefix=local_prefix,
            )
            show_inputs = await provider.get_shows(library_keys, progress)
        elif source_mode == "jellyfin":
            library_ids: list[str] = app_state.restructure_jellyfin_library_ids  # type: ignore[attr-defined]
            jf_prefix = await db.get_setting("restructure.jellyfin_path_prefix") or ""
            local_prefix = await db.get_setting("restructure.local_path_prefix") or ""

            jellyfin_client = JellyfinClient(
                url=config.jellyfin.url, api_key=config.jellyfin.api_key
            )
            provider = JellyfinShowProvider(  # type: ignore[assignment]
                jellyfin_client=jellyfin_client,
                db=db,
                jellyfin_path_prefix=jf_prefix,
                local_path_prefix=local_prefix,
            )
            show_inputs = await provider.get_shows(library_ids, progress)
        else:
            local_directory: str = app_state.restructure_local_directory  # type: ignore[attr-defined]
            title_matcher = TitleMatcher(similarity_threshold=0.75)
            scanner = LocalDirectoryScanner(
                db=db,
                anilist_client=anilist_client,
                title_matcher=title_matcher,
            )
            show_inputs = await scanner.scan_directory(
                local_directory, progress, force_rescan=force_rescan
            )

        plan = await restructurer.analyze(show_inputs, progress, level=level)
        app_state.restructure_plan = plan  # type: ignore[attr-defined]
    except Exception:
        logger.exception("Library analysis failed")
        progress.status = "error"
        progress.error_message = "Library analysis failed unexpectedly"
    finally:
        if plex_client:
            await plex_client.close()
        if jellyfin_client:
            await jellyfin_client.close()


async def _run_execution(app_state: object) -> None:
    """Background coroutine: execute restructuring and trigger rescan."""
    config = app_state.config  # type: ignore[attr-defined]
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]
    progress: RestructureProgress = app_state.restructure_exec_progress  # type: ignore[attr-defined]
    plan: RestructurePlan = app_state.restructure_plan  # type: ignore[attr-defined]
    source_mode: str = app_state.restructure_source_mode  # type: ignore[attr-defined]

    group_builder = SeriesGroupBuilder(db, anilist_client)

    # Load naming templates from settings (needed for season dir creation)
    file_tmpl = await db.get_setting("naming.file_template") or ""
    folder_tmpl = await db.get_setting("naming.folder_template") or ""
    season_tmpl = await db.get_setting("naming.season_folder_template") or ""
    movie_tmpl = await db.get_setting("naming.movie_file_template") or ""
    title_pref = await db.get_setting("app.title_display") or "romaji"
    illegal_char_repl = await db.get_setting("naming.illegal_char_replacement") or ""

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
    plex_client: PlexClient | None = None
    jellyfin_client: JellyfinClient | None = None

    try:
        # Phase 1: Move/rename files
        stats = await restructurer.execute(plan, progress)
        app_state.restructure_stats = stats  # type: ignore[attr-defined]

        # Phase 2: Pre-seed library_items so auto-rescan skips AniList API calls
        library_id: int | None = getattr(app_state, "restructure_library_id", None)
        if library_id:
            progress.phase = "Pre-seeding library mappings"
            for group in plan.groups:
                if not group.enabled or not group.anilist_id or not group.target_folder:
                    continue
                folder_name = os.path.basename(group.target_folder)
                await db.upsert_library_item(
                    library_id=library_id,
                    folder_path=group.target_folder,
                    folder_name=folder_name,
                    anilist_id=group.anilist_id,
                    anilist_title=group.display_title,
                    match_confidence=1.0,
                    match_method="restructure",
                    series_group_id=group.series_group_id or None,
                )

        # Phase 3: Media server refresh and wait
        if source_mode == "plex":
            library_keys: list[str] = app_state.restructure_library_keys  # type: ignore[attr-defined]
            plex_client = PlexClient(url=config.plex.url, token=config.plex.token)

            progress.phase = "Waiting for Plex to index"
            for key in library_keys:
                try:
                    await plex_client.refresh_library_and_wait(
                        key, poll_interval=2.0, timeout=120.0
                    )
                except Exception:
                    logger.exception("Failed to refresh Plex library %s", key)
            progress.phase = "Library indexed"

            if plan.operation_level == "full_restructure":
                # Delete old plex_media entries for source shows
                progress.phase = "Cleaning up old entries"
                for group in plan.groups:
                    if not group.enabled:
                        continue
                    for rk in group.source_rating_keys:
                        await db.delete_plex_media_by_rating_key(rk)

        # Phase 3: Jellyfin-specific post-execution
        elif (
            source_mode == "jellyfin"
            and config.jellyfin.url
            and config.jellyfin.api_key
        ):
            jellyfin_client = JellyfinClient(
                url=config.jellyfin.url, api_key=config.jellyfin.api_key
            )
            progress.phase = "Waiting for Jellyfin to index"
            try:
                await jellyfin_client.refresh_library_and_wait(
                    poll_interval=5.0, timeout=600.0
                )
            except Exception:
                logger.exception("Failed to refresh Jellyfin library")

        progress.status = "complete"
        progress.phase = "Operation complete"

        # Auto-rescan affected library if launched from library context
        await _auto_rescan_library(app_state)
    except Exception:
        logger.exception("Restructuring execution failed")
        progress.status = "error"
        progress.error_message = "Execution failed unexpectedly"
    finally:
        if plex_client:
            await plex_client.close()
        if jellyfin_client:
            await jellyfin_client.close()


@router.get("/restructure", response_class=HTMLResponse)
async def restructure_wizard(request: Request) -> HTMLResponse:
    """Render the restructure wizard landing page."""
    config = request.app.state.config
    db = request.app.state.db
    templates = request.app.state.templates

    plex_prefix = await db.get_setting("restructure.plex_path_prefix") or ""
    local_prefix = await db.get_setting("restructure.local_path_prefix") or ""

    # Check if coming from a library context
    library_context = None
    library_id_str = request.query_params.get("library_id", "")
    if library_id_str:
        try:
            library_id = int(library_id_str)
            library = await db.get_library(library_id)
            if library:
                import json

                library_context = {
                    "id": library["id"],
                    "name": library["name"],
                    "paths": json.loads(library["paths"]) if library["paths"] else [],
                }
        except (ValueError, TypeError):
            pass

    # Fetch Plex libraries for selection
    plex_libraries: list[dict[str, str]] = []
    plex_configured = bool(config.plex.url and config.plex.token)
    if plex_configured:
        try:
            plex_client = PlexClient(url=config.plex.url, token=config.plex.token)
            libs = await plex_client.get_libraries()
            plex_libraries = [
                {"key": lib.key, "title": lib.title}
                for lib in libs
                if lib.type in ("show", "movie")
            ]
            await plex_client.close()
        except Exception:
            logger.warning("Could not fetch Plex libraries")

    # Fetch Jellyfin libraries for selection
    jellyfin_libraries: list[dict[str, str]] = []
    jellyfin_configured = bool(config.jellyfin.url and config.jellyfin.api_key)
    jellyfin_prefix = await db.get_setting("restructure.jellyfin_path_prefix") or ""
    if jellyfin_configured:
        try:
            jellyfin_client = JellyfinClient(
                url=config.jellyfin.url, api_key=config.jellyfin.api_key
            )
            jf_libs = await jellyfin_client.get_libraries()
            jellyfin_libraries = [
                {"id": lib.id, "name": lib.name}
                for lib in jf_libs
                if lib.type in ("tvshows", "movies", "mixed", "")
            ]
            await jellyfin_client.close()
        except Exception:
            logger.warning("Could not fetch Jellyfin libraries")

    # Load current naming settings
    naming_values: dict[str, str] = {}
    for key in [
        "naming.file_template",
        "naming.movie_file_template",
        "naming.folder_template",
        "naming.season_folder_template",
        "app.title_display",
    ]:
        naming_values[key] = await db.get_setting(key) or ""

    return templates.TemplateResponse(
        "restructure_wizard.html",
        {
            "request": request,
            "plex_prefix": plex_prefix,
            "local_prefix": local_prefix,
            "plex_libraries": plex_libraries,
            "plex_configured": plex_configured,
            "jellyfin_libraries": jellyfin_libraries,
            "jellyfin_configured": jellyfin_configured,
            "jellyfin_prefix": jellyfin_prefix,
            "library_context": library_context,
            "naming_values": naming_values,
            "naming_presets": NAMING_PRESETS,
        },
    )


@router.post("/restructure/analyze")
async def restructure_analyze(request: Request) -> RedirectResponse:
    """Start background analysis, redirect to progress page."""
    if _is_restructure_busy(request.app.state):
        return RedirectResponse(
            url="/restructure?error=A+restructure+operation+is+already+running",
            status_code=303,
        )

    config = request.app.state.config
    form = await request.form()

    source_mode = str(form.get("source_mode", "plex"))
    if source_mode not in ("plex", "jellyfin", "local"):
        source_mode = "plex"

    operation_level = str(form.get("operation_level", "full_restructure"))
    if operation_level not in (
        "folder_rename",
        "folder_file_rename",
        "full_restructure",
    ):
        operation_level = "full_restructure"

    if source_mode == "plex":
        if not config.plex.url or not config.plex.token:
            return RedirectResponse(
                url="/restructure?error=Plex+not+configured", status_code=303
            )
        selected_keys = form.getlist("library_key")
        if not selected_keys:
            return RedirectResponse(
                url="/restructure?error=No+libraries+selected", status_code=303
            )
        request.app.state.restructure_library_keys = [str(k) for k in selected_keys]
    elif source_mode == "jellyfin":
        if not config.jellyfin.url or not config.jellyfin.api_key:
            return RedirectResponse(
                url="/restructure?error=Jellyfin+not+configured", status_code=303
            )
        selected_ids = form.getlist("jellyfin_library_id")
        if not selected_ids:
            return RedirectResponse(
                url="/restructure?error=No+Jellyfin+libraries+selected", status_code=303
            )
        request.app.state.restructure_jellyfin_library_ids = [
            str(i) for i in selected_ids
        ]
    else:
        local_directory = str(form.get("local_directory", "")).strip()
        if not local_directory or not os.path.isdir(local_directory):
            return RedirectResponse(
                url="/restructure?error=Invalid+directory+path", status_code=303
            )
        request.app.state.restructure_local_directory = local_directory

    force_rescan = str(form.get("force_rescan", "")).lower() in ("on", "true", "1")

    # Persist naming/display settings from the wizard form
    db = request.app.state.db
    naming_keys = [
        "naming.file_template",
        "naming.movie_file_template",
        "naming.folder_template",
        "naming.season_folder_template",
        "app.title_display",
    ]
    for key in naming_keys:
        val = str(form.get(key, "")).strip()
        if val:
            await db.set_setting(key, val)

    # Track library_id for auto-rescan after execution
    library_id_str = str(form.get("library_id", "")).strip()
    restructure_library_id: int | None = None
    if library_id_str:
        try:
            restructure_library_id = int(library_id_str)
        except (ValueError, TypeError):
            pass

    # Clear all previous-run state before starting fresh
    request.app.state.restructure_source_mode = source_mode
    request.app.state.restructure_operation_level = operation_level
    request.app.state.restructure_force_rescan = force_rescan
    request.app.state.restructure_library_id = restructure_library_id
    request.app.state.restructure_progress = RestructureProgress()
    request.app.state.restructure_plan = None
    request.app.state.restructure_exec_progress = None
    request.app.state.restructure_stats = None

    asyncio.create_task(_run_analysis(request.app.state))

    return RedirectResponse(url="/restructure/progress", status_code=303)


@router.get("/restructure/progress", response_class=HTMLResponse)
async def restructure_progress_page(request: Request) -> HTMLResponse:
    """Render progress page that polls for status."""
    templates = request.app.state.templates

    # Determine which progress to track (analysis or execution)
    exec_progress = getattr(request.app.state, "restructure_exec_progress", None)
    is_executing = exec_progress is not None and exec_progress.status not in (
        "complete",
        "error",
    )

    return templates.TemplateResponse(
        "restructure_progress.html",
        {
            "request": request,
            "is_executing": is_executing,
        },
    )


@router.get("/api/restructure/progress")
async def restructure_progress_api(request: Request) -> JSONResponse:
    """Return current progress as JSON."""
    # Check execution progress first
    exec_progress: RestructureProgress | None = getattr(
        request.app.state, "restructure_exec_progress", None
    )
    if exec_progress and exec_progress.status not in ("pending",):
        elapsed = (
            time.monotonic() - exec_progress.started_at
            if exec_progress.started_at > 0
            else 0
        )
        result_url = "/restructure/results"
        return JSONResponse(
            {
                "status": exec_progress.status,
                "phase": exec_progress.phase,
                "processed": exec_progress.processed,
                "total": exec_progress.total,
                "current_item": exec_progress.current_item,
                "error_message": exec_progress.error_message,
                "elapsed_seconds": round(elapsed, 1),
                "result_url": result_url,
            }
        )

    # Analysis progress
    progress: RestructureProgress | None = getattr(
        request.app.state, "restructure_progress", None
    )
    if not progress:
        return JSONResponse({"status": "idle"})

    elapsed = time.monotonic() - progress.started_at if progress.started_at > 0 else 0
    result_url = "/restructure/preview"

    return JSONResponse(
        {
            "status": progress.status,
            "phase": progress.phase,
            "processed": progress.processed,
            "total": progress.total,
            "current_item": progress.current_item,
            "error_message": progress.error_message,
            "elapsed_seconds": round(elapsed, 1),
            "result_url": result_url,
        }
    )


@router.get("/restructure/preview", response_class=HTMLResponse)
async def restructure_preview(request: Request) -> Response:
    """Render the preview page showing the restructure plan."""
    templates = request.app.state.templates
    plan: RestructurePlan | None = getattr(request.app.state, "restructure_plan", None)

    if not plan:
        return RedirectResponse(
            url="/restructure?error=No+analysis+results", status_code=303
        )

    return templates.TemplateResponse(
        "restructure_preview.html",
        {
            "request": request,
            "plan": plan,
            "operation_level": plan.operation_level,
        },
    )


@router.post("/restructure/execute")
async def restructure_execute(request: Request) -> RedirectResponse:
    """Execute selected restructure groups."""
    if _is_restructure_busy(request.app.state):
        return RedirectResponse(
            url="/restructure?error=A+restructure+operation+is+already+running",
            status_code=303,
        )

    plan: RestructurePlan | None = getattr(request.app.state, "restructure_plan", None)

    if not plan:
        return RedirectResponse(
            url="/restructure?error=No+plan+available", status_code=303
        )

    form = await request.form()
    enabled_keys = {str(v) for v in form.getlist("group_key")}

    # Update enabled state on groups
    for group in plan.groups:
        group.enabled = group.group_key in enabled_keys

    if not any(g.enabled for g in plan.groups):
        return RedirectResponse(
            url="/restructure/preview?error=No+groups+selected", status_code=303
        )

    request.app.state.restructure_exec_progress = RestructureProgress()
    request.app.state.restructure_stats = None

    asyncio.create_task(_run_execution(request.app.state))

    return RedirectResponse(url="/restructure/progress", status_code=303)


@router.get("/restructure/report", response_class=HTMLResponse)
async def restructure_report_page(request: Request) -> HTMLResponse:
    """Persistent restructure operation log — browseable at any time."""
    db = request.app.state.db
    templates = request.app.state.templates

    limit = int(request.query_params.get("limit", "1000"))
    status_filter = request.query_params.get("status", "")  # "", "success", "error"

    entries = await db.get_restructure_log(limit=limit)
    if status_filter:
        entries = [e for e in entries if e["status"] == status_filter]

    moved = sum(1 for e in entries if e["status"] == "success")
    errors = sum(1 for e in entries if e["status"] == "error")

    return templates.TemplateResponse(
        "restructure_report.html",
        {
            "request": request,
            "entries": entries,
            "moved": moved,
            "errors": errors,
            "total": len(entries),
            "status_filter": status_filter,
        },
    )


@router.get("/restructure/results", response_class=HTMLResponse)
async def restructure_results(request: Request) -> Response:
    """Render the results page after execution."""
    templates = request.app.state.templates
    stats: dict[str, int] | None = getattr(request.app.state, "restructure_stats", None)
    plan: RestructurePlan | None = getattr(request.app.state, "restructure_plan", None)
    source_mode: str = getattr(request.app.state, "restructure_source_mode", "plex")

    if not stats:
        return RedirectResponse(
            url="/restructure?error=No+results+available", status_code=303
        )

    return templates.TemplateResponse(
        "restructure_results.html",
        {
            "request": request,
            "stats": stats,
            "plan": plan,
            "operation_level": plan.operation_level if plan else "full_restructure",
            "source_mode": source_mode,
        },
    )
