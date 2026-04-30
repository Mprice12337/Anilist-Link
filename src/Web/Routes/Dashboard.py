"""Dashboard and statistics endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import Response

from src.Scheduler.Jobs import JOB_CRUNCHYROLL_SYNC
from src.Web.App import spawn_background_task
from src.Web.Routes.Helpers import enrich_watchlist_entries

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse, response_model=None)
async def dashboard(
    request: Request, error: str | None = None, skip_onboarding: str | None = None
) -> Response:
    """Render the main dashboard page.

    Redirects to /onboarding on first launch unless onboarding is complete
    or the caller passes ?skip_onboarding=1 (for development/testing).
    """
    db = request.app.state.db

    if not skip_onboarding:
        onboarding_status = await db.get_setting("onboarding.status") or "not_started"
        if onboarding_status != "completed":
            return RedirectResponse(url="/onboarding", status_code=302)
    config = request.app.state.config
    scheduler = request.app.state.scheduler
    templates = request.app.state.templates

    users = await db.get_all_users()
    mapping_count = await db.get_mapping_count()
    user_count = await db.get_user_count()
    job_status = scheduler.get_job_status()

    plex_configured = bool(config.plex.url and config.plex.token)
    jellyfin_configured = bool(config.jellyfin.url and config.jellyfin.api_key)

    # Currently watching — enriched with local/*arr status
    currently_watching: list = []
    anilist_user = next((u for u in users if u["service"] == "anilist"), None)
    if anilist_user:
        try:
            raw_watching = await db.get_watchlist(
                anilist_user["user_id"], list_statuses=["CURRENT"]
            )
            currently_watching = await enrich_watchlist_entries(db, raw_watching)
        except Exception:
            logger.warning("Could not fetch currently-watching list")

    # Recent activity
    recent_activity: list = []
    try:
        recent_activity = await db.get_recent_activity(limit=15)
    except Exception:
        logger.warning("Could not fetch recent activity")

    next_sync: str | None = None
    if job_status:
        next_sync = job_status[0].get("next_run_time")

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "users": users,
            "mapping_count": mapping_count,
            "user_count": user_count,
            "job_status": job_status,
            "anilist_configured": bool(config.anilist.client_id),
            "plex_configured": plex_configured,
            "jellyfin_configured": jellyfin_configured,
            "currently_watching": currently_watching,
            "recent_activity": recent_activity,
            "next_sync": next_sync,
            "arr_enabled": bool(config.sonarr.url or config.radarr.url),
            "title_display": await db.get_setting("app.title_display") or "romaji",
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
async def trigger_sync(request: Request) -> Response:
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
async def trigger_dry_run_sync(request: Request) -> Response:
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

    spawn_background_task(request.app.state, sync_fn(dry_run=True))
    logger.info("Dry-run Crunchyroll sync triggered")
    if is_browser:
        return RedirectResponse(
            url="/?message=Dry+run+started+%E2%80%94+check+logs", status_code=303
        )
    return JSONResponse({"status": "ok", "message": "Dry run started — check logs"})


@router.post("/api/scan/plex", response_model=None)
async def trigger_plex_scan(request: Request) -> Response:
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

    spawn_background_task(
        request.app.state, scan_fn(dry_run=False, library_keys=library_keys)
    )
    logger.info("Manual Plex metadata scan triggered")
    if is_browser:
        return RedirectResponse(
            url="/?message=Plex+scan+started+%E2%80%94+check+logs", status_code=303
        )
    return JSONResponse({"status": "ok", "message": "Plex scan started — check logs"})


@router.post("/api/scan/plex/dry-run", response_model=None)
async def trigger_plex_dry_run(request: Request) -> Response:
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

    spawn_background_task(
        request.app.state, scan_fn(dry_run=True, library_keys=library_keys)
    )
    logger.info("Dry-run Plex metadata scan triggered")
    if is_browser:
        return RedirectResponse(
            url="/?message=Plex+dry+run+started+%E2%80%94+check+logs",
            status_code=303,
        )
    return JSONResponse(
        {"status": "ok", "message": "Plex dry run started — check logs"}
    )
