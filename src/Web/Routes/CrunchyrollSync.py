"""Crunchyroll preview & history routes — Phase C."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.Clients.CrunchyrollClient import CrunchyrollClient
from src.Matching.TitleMatcher import TitleMatcher, get_primary_title
from src.Sync.CrunchyrollPreviewRunner import (
    CrunchyrollPreviewProgress,
    CrunchyrollPreviewRunner,
)
from src.Utils.Config import load_config_from_db_settings
from src.Web.App import spawn_background_task

logger = logging.getLogger(__name__)

router = APIRouter(tags=["crunchyroll"])

# ---------------------------------------------------------------------------
# Hub page (unified view)
# ---------------------------------------------------------------------------


@router.get("/crunchyroll", response_class=HTMLResponse)
async def crunchyroll_hub(
    request: Request, run_id: str = "", tab: str = "preview"
) -> HTMLResponse:
    """Unified Crunchyroll hub — preview + history in one tabbed view."""
    db = request.app.state.db
    templates = request.app.state.templates

    users = await db.get_users_by_service("anilist")
    user = users[0] if users else None

    rows: list[dict] = []
    active_run_id = run_id
    runs: list[dict] = []
    log_entries: list[dict] = []
    latest_sync_run_id = ""

    if user:
        runs = await db.get_cr_preview_runs(user["user_id"])
        if not active_run_id and runs:
            active_run_id = runs[0]["run_id"]
        if active_run_id:
            rows = await db.get_cr_preview_run(active_run_id)
        log_entries = await db.get_cr_sync_log(user_id=user["user_id"], limit=500)
        if log_entries:
            latest_sync_run_id = log_entries[0]["sync_run_id"]

    return templates.TemplateResponse(
        "crunchyroll.html",
        {
            "request": request,
            "rows": rows,
            "run_id": active_run_id,
            "runs": runs,
            "log_entries": log_entries,
            "latest_sync_run_id": latest_sync_run_id,
            "user": user,
            "active_tab": tab,
            "version": "0.1.0",
        },
    )


# ---------------------------------------------------------------------------
# Pages (legacy — kept for backwards compatibility)
# ---------------------------------------------------------------------------


@router.get("/crunchyroll/preview", response_class=HTMLResponse)
async def cr_preview_page(request: Request, run_id: str = "") -> HTMLResponse:
    """Render the Crunchyroll sync preview page."""
    db = request.app.state.db
    templates = request.app.state.templates

    users = await db.get_users_by_service("anilist")
    user = users[0] if users else None

    rows: list[dict] = []
    active_run_id = run_id
    runs: list[dict] = []

    if user:
        runs = await db.get_cr_preview_runs(user["user_id"])
        if not active_run_id and runs:
            active_run_id = runs[0]["run_id"]
        if active_run_id:
            rows = await db.get_cr_preview_run(active_run_id)

    return templates.TemplateResponse(
        "crunchyroll_preview.html",
        {
            "request": request,
            "rows": rows,
            "run_id": active_run_id,
            "runs": runs,
            "user": user,
            "version": "0.1.0",
        },
    )


@router.get("/crunchyroll/history", response_class=HTMLResponse)
async def cr_history_page(request: Request) -> HTMLResponse:
    """Render the Crunchyroll sync history page."""
    db = request.app.state.db
    templates = request.app.state.templates

    users = await db.get_users_by_service("anilist")
    user = users[0] if users else None

    log_entries: list[dict] = []
    if user:
        log_entries = await db.get_cr_sync_log(user_id=user["user_id"], limit=200)

    return templates.TemplateResponse(
        "crunchyroll_history.html",
        {
            "request": request,
            "log_entries": log_entries,
            "user": user,
            "version": "0.1.0",
        },
    )


# ---------------------------------------------------------------------------
# Manual sync trigger
# ---------------------------------------------------------------------------


@router.post("/api/sync/crunchyroll/run")
async def manual_cr_sync(request: Request) -> JSONResponse:
    """Manually trigger a Crunchyroll sync run now, ignoring the auto-sync schedule.

    Respects ``crunchyroll.auto_approve``:
    - ``True``  → runs ``WatchSyncer`` and applies changes to AniList directly.
    - ``False`` → runs ``CrunchyrollPreviewRunner`` and writes rows to the
      preview table for manual review at ``/crunchyroll``.
    """
    app_state = request.app.state
    db = app_state.db
    db_settings = await db.get_all_settings()

    from src.Utils.Config import load_config_from_db_settings

    config = load_config_from_db_settings(db_settings)

    if not config.crunchyroll.email or not config.crunchyroll.password:
        return JSONResponse(
            {"ok": False, "error": "Crunchyroll credentials not configured"},
            status_code=400,
        )

    users = await db.get_users_by_service("anilist")
    if not users:
        return JSONResponse(
            {"ok": False, "error": "No AniList account linked"}, status_code=400
        )

    if config.crunchyroll.auto_approve:
        spawn_background_task(app_state, app_state.cr_sync_task())
        return JSONResponse({"ok": True, "mode": "apply"})
    else:
        spawn_background_task(app_state, app_state.cr_preview_task())
        return JSONResponse({"ok": True, "mode": "preview"})


# ---------------------------------------------------------------------------
# Preview scan (start dry-run)
# ---------------------------------------------------------------------------


@router.post("/api/crunchyroll/preview-scan")
async def start_preview_scan(request: Request) -> JSONResponse:
    """Start a Crunchyroll dry-run preview scan as a background task.

    Returns immediately with {"ok": true, "running": true}.
    Poll GET /api/crunchyroll/preview-scan/status for completion.
    """
    app_state = request.app.state
    db = app_state.db
    # Always load config fresh from DB so GUI-saved credentials are picked up
    db_settings = await db.get_all_settings()
    config = load_config_from_db_settings(db_settings)

    # Reject if already running
    existing = getattr(app_state, "cr_preview_progress", None)
    if existing and existing.status == "scanning":
        return JSONResponse(
            {"ok": False, "error": "Scan already in progress"}, status_code=409
        )

    users = await db.get_users_by_service("anilist")
    if not users:
        return JSONResponse(
            {"ok": False, "error": "No AniList account linked"}, status_code=400
        )

    user = users[0]
    progress = CrunchyrollPreviewProgress(
        status="authenticating", detail="Authenticating with Crunchyroll…"
    )
    app_state.cr_preview_progress = progress  # type: ignore[attr-defined]

    cr_client = CrunchyrollClient(
        email=config.crunchyroll.email,
        password=config.crunchyroll.password,
        headless=config.crunchyroll.headless,
        flaresolverr_url=config.crunchyroll.flaresolverr_url,
        max_pages=config.crunchyroll.max_pages,
        db=db,
    )

    # Authenticate before launching the background task — auth is blocking
    # (browser/FlareSolverr) so we do it in a thread, but we need to know
    # if it succeeded before we can proceed.
    if not config.crunchyroll.email or not config.crunchyroll.password:
        progress.status = "error"
        progress.error = "Crunchyroll credentials not configured. Add them in Settings."
        return JSONResponse({"ok": False, "error": progress.error}, status_code=401)

    auth_ok = await cr_client.authenticate()
    if not auth_ok:
        progress.status = "error"
        progress.error = (
            "Crunchyroll authentication failed. Check credentials in Settings."
        )
        return JSONResponse({"ok": False, "error": progress.error}, status_code=401)

    title_matcher = TitleMatcher()
    runner = CrunchyrollPreviewRunner(
        db, app_state.anilist_client, title_matcher, cr_client, config, progress
    )

    spawn_background_task(app_state, runner.run_preview(user))
    return JSONResponse({"ok": True, "running": True})


@router.get("/api/crunchyroll/preview-scan/status")
async def preview_scan_status(request: Request) -> JSONResponse:
    """Poll for the status of the current/last CR preview scan."""
    progress: CrunchyrollPreviewProgress | None = getattr(
        request.app.state, "cr_preview_progress", None
    )
    if progress is None:
        return JSONResponse({"status": "idle"})

    return JSONResponse(
        {
            "status": progress.status,
            "current_page": progress.current_page,
            "max_pages": progress.max_pages,
            "entries_found": progress.entries_found,
            "run_id": progress.run_id,
            "detail": progress.detail,
            "error": progress.error,
        }
    )


# ---------------------------------------------------------------------------
# Preview data (JSON)
# ---------------------------------------------------------------------------


@router.get("/api/crunchyroll/preview/{run_id}")
async def get_preview_data(request: Request, run_id: str) -> JSONResponse:
    """Return JSON data for a preview run."""
    db = request.app.state.db
    rows = await db.get_cr_preview_run(run_id)
    return JSONResponse({"ok": True, "rows": rows})


# ---------------------------------------------------------------------------
# Approve entries
# ---------------------------------------------------------------------------


@router.post("/api/crunchyroll/preview/{run_id}/approve")
async def approve_preview_entries(request: Request, run_id: str) -> JSONResponse:
    """Set approved=1 for a list of entry ids, or all entries in run."""
    db = request.app.state.db
    body = await request.json()
    entry_ids: list[int] | None = body.get("entry_ids")
    approve_all: bool = body.get("approve_all", False)
    approved: bool = body.get("approved", True)

    if approve_all:
        rows = await db.get_cr_preview_run(run_id)
        entry_ids = [r["id"] for r in rows if r["action"] != "skip"]
    elif not entry_ids:
        return JSONResponse(
            {"ok": False, "error": "entry_ids required"}, status_code=400
        )

    await db.set_cr_preview_approved(entry_ids or [], approved)
    return JSONResponse({"ok": True, "updated": len(entry_ids or [])})


# ---------------------------------------------------------------------------
# Apply approved entries
# ---------------------------------------------------------------------------


@router.post("/api/crunchyroll/preview/{run_id}/apply")
async def apply_preview_run(request: Request, run_id: str) -> JSONResponse:
    """Apply all approved entries: write to AniList + record in cr_sync_log."""
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    users = await db.get_users_by_service("anilist")
    if not users:
        return JSONResponse(
            {"ok": False, "error": "No AniList account linked"}, status_code=400
        )

    user = users[0]
    access_token = user["access_token"]
    anilist_user_id = user["anilist_id"]
    user_id = user["user_id"]

    rows = await db.get_cr_preview_run(run_id)
    approved = [r for r in rows if r["approved"] and r["action"] != "skip"]

    if not approved:
        return JSONResponse(
            {"ok": False, "error": "No approved entries to apply"}, status_code=400
        )

    applied = 0
    errors = 0

    for row in approved:
        try:
            anilist_id = row["anilist_id"]
            proposed_status = row["proposed_status"]
            proposed_progress = row["proposed_progress"]

            # Fetch current state for before_ values
            existing = await anilist_client.get_anime_list_entry(
                anilist_id, access_token, anilist_user_id
            )
            before_status = (existing or {}).get("status") or ""
            before_progress = (existing or {}).get("progress") or 0
            current_repeat = (existing or {}).get("repeat") or 0

            # Determine repeat count
            if (
                proposed_status == "CURRENT"
                and before_status == "COMPLETED"
                and proposed_progress <= 3
            ):
                new_repeat = current_repeat + 1
            else:
                new_repeat = current_repeat

            resp = await anilist_client.update_anime_progress(
                anilist_id,
                access_token,
                proposed_progress,
                proposed_status,
                new_repeat,
            )

            if resp:
                await db.insert_cr_sync_log_entry(
                    user_id=user_id,
                    anilist_id=anilist_id,
                    show_title=row["anilist_title"],
                    before_status=before_status,
                    before_progress=before_progress,
                    after_status=proposed_status,
                    after_progress=proposed_progress,
                    sync_run_id=run_id,
                    cr_sync_preview_id=row["id"],
                )
                applied += 1
            else:
                errors += 1

        except Exception as exc:
            logger.error(
                "Apply error for anilist_id %s: %s", row.get("anilist_id"), exc
            )
            errors += 1

    if applied > 0:
        spawn_background_task(
            request.app.state, request.app.state.watchlist_refresh_task()
        )

    return JSONResponse(
        {"ok": True, "applied": applied, "errors": errors, "total": len(approved)}
    )


# ---------------------------------------------------------------------------
# Update entry (manual rematch)
# ---------------------------------------------------------------------------


@router.post("/api/crunchyroll/preview/{run_id}/update-entry")
async def update_preview_entry(request: Request, run_id: str) -> JSONResponse:
    """Update a preview row with a manually selected AniList match."""
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    users = await db.get_users_by_service("anilist")
    user = users[0] if users else None

    body = await request.json()
    entry_id: int = body.get("entry_id")
    anilist_id: int = body.get("anilist_id")

    # Fetch AniList metadata for the new match
    anime = await anilist_client.get_anime_by_id(anilist_id)
    if not anime:
        return JSONResponse(
            {"ok": False, "error": "AniList entry not found"}, status_code=404
        )

    anilist_title = get_primary_title(anime)
    total_episodes = anime.get("episodes")
    confidence = 1.0  # manual = full confidence

    # Determine current state if user is linked
    current_status = ""
    current_progress = 0
    proposed_progress = body.get("proposed_progress", 1)
    proposed_status = (
        "COMPLETED"
        if (total_episodes and proposed_progress >= total_episodes)
        else "CURRENT"
    )

    if user:
        existing = await anilist_client.get_anime_list_entry(
            anilist_id, user["access_token"], user["anilist_id"]
        )
        current_status = (existing or {}).get("status") or ""
        current_progress = (existing or {}).get("progress") or 0

    if not current_status:
        action = "add"
    elif current_progress >= proposed_progress and current_status in (
        "COMPLETED",
        "CURRENT",
    ):
        action = "skip"
    else:
        action = "update"

    await db.update_cr_preview_entry(
        entry_id=entry_id,
        anilist_id=anilist_id,
        anilist_title=anilist_title,
        confidence=confidence,
        proposed_status=proposed_status,
        proposed_progress=proposed_progress,
        action=action,
    )

    return JSONResponse(
        {
            "ok": True,
            "anilist_id": anilist_id,
            "anilist_title": anilist_title,
            "proposed_status": proposed_status,
            "proposed_progress": proposed_progress,
            "action": action,
        }
    )


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


@router.post("/api/crunchyroll/undo/{log_id}")
async def undo_cr_sync_entry(request: Request, log_id: int) -> JSONResponse:
    """Undo a single cr_sync_log entry by reverting progress/status on AniList."""
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    entry = await db.get_cr_sync_log_entry(log_id)
    if not entry:
        return JSONResponse(
            {"ok": False, "error": "Log entry not found"}, status_code=404
        )

    if entry.get("undone_at"):
        return JSONResponse({"ok": False, "error": "Already undone"}, status_code=400)

    users = await db.get_users_by_service("anilist")
    user = next((u for u in users if u["user_id"] == entry["user_id"]), None)
    if not user:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)

    access_token = user["access_token"]
    before_status = entry["before_status"]
    before_progress = entry["before_progress"]
    anilist_id = entry["anilist_id"]

    try:
        if not before_status:
            # Was a new add — revert to PLANNING with 0 progress as best approximation.
            resp = await anilist_client.update_anime_progress(
                anilist_id, access_token, 0, "PLANNING"
            )
        else:
            resp = await anilist_client.update_anime_progress(
                anilist_id, access_token, before_progress, before_status
            )

        if resp:
            await db.mark_cr_sync_log_undone(log_id)
            return JSONResponse({"ok": True})
        else:
            return JSONResponse(
                {"ok": False, "error": "AniList update failed"}, status_code=500
            )

    except Exception as exc:
        logger.exception("Undo failed for log_id %s", log_id)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# AniList search (for rematch modal)
# ---------------------------------------------------------------------------


@router.get("/api/crunchyroll/search")
async def cr_anilist_search(request: Request, q: str = "") -> JSONResponse:
    """Search AniList for manual rematch."""
    if not q or len(q) < 2:
        return JSONResponse({"ok": True, "results": []})

    anilist_client = request.app.state.anilist_client
    results = await anilist_client.search_anime(q) or []

    simplified = [
        {
            "id": r["id"],
            "title": get_primary_title(r),
            "title_english": (r.get("title") or {}).get("english") or "",
            "episodes": r.get("episodes"),
            "format": r.get("format") or "",
            "cover_image": ((r.get("coverImage") or {}).get("medium") or ""),
        }
        for r in results[:10]
    ]

    return JSONResponse({"ok": True, "results": simplified})
