"""Dashboard and statistics endpoints."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "users": users,
            "mapping_count": mapping_count,
            "job_status": job_status,
            "anilist_configured": bool(config.anilist.client_id),
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
