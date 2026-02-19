"""Dashboard and statistics endpoints."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.Clients.PlexClient import PlexClient
from src.Scheduler.Jobs import JOB_CRUNCHYROLL_SYNC

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, error: str | None = None) -> HTMLResponse:
    """Render the main dashboard page."""
    db = request.app.state.db
    config = request.app.state.config
    scheduler = request.app.state.scheduler
    templates = request.app.state.templates

    users = await db.get_all_users()
    mapping_count = await db.get_mapping_count()
    job_status = scheduler.get_job_status()

    plex_configured = bool(config.plex.url and config.plex.token)
    plex_libraries: list = []
    selected_library_keys: list[str] = list(config.plex.anime_library_keys)

    if plex_configured:
        try:
            plex_client = PlexClient(url=config.plex.url, token=config.plex.token)
            all_libs = await plex_client.get_libraries()
            plex_libraries = [lib for lib in all_libs if lib.type in ("show", "movie")]
            await plex_client.close()
        except Exception:
            logger.warning("Could not fetch Plex libraries for dashboard")

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "users": users,
            "mapping_count": mapping_count,
            "job_status": job_status,
            "anilist_configured": bool(config.anilist.client_id),
            "plex_configured": plex_configured,
            "plex_libraries": plex_libraries,
            "selected_library_keys": selected_library_keys,
            "error": error,
            "message": request.query_params.get("message"),
            "version": "0.1.0",
        },
    )


@router.get("/api/status")
async def api_status(request: Request) -> JSONResponse:
    """Return system status as JSON."""
    db = request.app.state.db
    scheduler = request.app.state.scheduler

    mapping_count = await db.get_mapping_count()
    user_count = await db.get_user_count()
    jobs = scheduler.get_job_status()

    return JSONResponse(
        {
            "status": "running",
            "version": "0.1.0",
            "mapping_count": mapping_count,
            "user_count": user_count,
            "jobs": jobs,
        }
    )


@router.post("/api/sync", response_model=None)
async def trigger_sync(request: Request) -> RedirectResponse | JSONResponse:
    """Manually trigger a Crunchyroll sync job."""
    scheduler = request.app.state.scheduler
    triggered = scheduler.trigger_job(JOB_CRUNCHYROLL_SYNC)
    is_browser = "text/html" in request.headers.get("accept", "")

    if triggered:
        logger.info("Manual Crunchyroll sync triggered")
        if is_browser:
            return RedirectResponse(url="/?message=Sync+triggered", status_code=303)
        return JSONResponse({"status": "ok", "message": "Sync triggered"})

    if is_browser:
        return RedirectResponse(url="/?error=sync_not_registered", status_code=303)
    return JSONResponse(
        {"status": "error", "message": "Sync job not registered"},
        status_code=404,
    )


@router.post("/api/sync/dry-run", response_model=None)
async def trigger_dry_run_sync(request: Request) -> RedirectResponse | JSONResponse:
    """Trigger a dry-run sync that logs intended changes without mutating AniList."""
    sync_fn = getattr(request.app.state, "cr_sync_task", None)
    is_browser = "text/html" in request.headers.get("accept", "")

    if not sync_fn:
        if is_browser:
            return RedirectResponse(url="/?error=sync_not_configured", status_code=303)
        return JSONResponse(
            {"status": "error", "message": "Sync task not configured"},
            status_code=404,
        )

    asyncio.create_task(sync_fn(dry_run=True))
    logger.info("Dry-run Crunchyroll sync triggered")
    if is_browser:
        return RedirectResponse(
            url="/?message=Dry+run+started+%E2%80%94+check+logs", status_code=303
        )
    return JSONResponse({"status": "ok", "message": "Dry run started — check logs"})


@router.post("/api/scan/plex", response_model=None)
async def trigger_plex_scan(request: Request) -> RedirectResponse | JSONResponse:
    """Manually trigger a Plex metadata scan."""
    scan_fn = getattr(request.app.state, "plex_scan_task", None)
    is_browser = "text/html" in request.headers.get("accept", "")

    if not scan_fn:
        if is_browser:
            return RedirectResponse(url="/?error=plex_not_configured", status_code=303)
        return JSONResponse(
            {"status": "error", "message": "Plex scan not configured"},
            status_code=404,
        )

    # Read form-selected library keys (from dashboard checkboxes)
    form = await request.form()
    selected_keys = form.getlist("library_key")
    library_keys = [str(k) for k in selected_keys] if selected_keys else None

    asyncio.create_task(scan_fn(dry_run=False, library_keys=library_keys))
    logger.info("Manual Plex metadata scan triggered")
    if is_browser:
        return RedirectResponse(
            url="/?message=Plex+scan+started+%E2%80%94+check+logs", status_code=303
        )
    return JSONResponse({"status": "ok", "message": "Plex scan started — check logs"})


@router.post("/api/scan/plex/dry-run", response_model=None)
async def trigger_plex_dry_run(request: Request) -> RedirectResponse | JSONResponse:
    """Trigger a dry-run Plex scan that logs matches without writing to Plex."""
    scan_fn = getattr(request.app.state, "plex_scan_task", None)
    is_browser = "text/html" in request.headers.get("accept", "")

    if not scan_fn:
        if is_browser:
            return RedirectResponse(url="/?error=plex_not_configured", status_code=303)
        return JSONResponse(
            {"status": "error", "message": "Plex scan not configured"},
            status_code=404,
        )

    # Read form-selected library keys (from dashboard checkboxes)
    form = await request.form()
    selected_keys = form.getlist("library_key")
    library_keys = [str(k) for k in selected_keys] if selected_keys else None

    asyncio.create_task(scan_fn(dry_run=True, library_keys=library_keys))
    logger.info("Dry-run Plex metadata scan triggered")
    if is_browser:
        return RedirectResponse(
            url="/?message=Plex+dry+run+started+%E2%80%94+check+logs",
            status_code=303,
        )
    return JSONResponse(
        {"status": "ok", "message": "Plex dry run started — check logs"}
    )
