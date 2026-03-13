"""Watchlist library view — browse AniList watchlist with local/Sonarr status."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["library"])


@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request) -> HTMLResponse:
    """Render the watchlist library view."""
    db = request.app.state.db
    templates = request.app.state.templates

    # Get first linked AniList user
    users = await db.get_users_by_service("anilist")
    user = users[0] if users else None
    user_id: str = user["user_id"] if user else ""

    entries: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}

    if user_id:
        raw_entries = await db.get_watchlist(user_id)

        # Build sets for cross-referencing
        local_anilist_ids: set[int] = set()
        sonarr_monitored_ids: set[int] = set()
        sonarr_ids: set[int] = set()

        mappings = await db.fetch_all("SELECT DISTINCT anilist_id FROM media_mappings")
        for row in mappings:
            local_anilist_ids.add(row["anilist_id"])

        sonarr_rows = await db.fetch_all(
            "SELECT anilist_id, in_sonarr, sonarr_monitored"
            " FROM anilist_sonarr_mapping WHERE in_sonarr=1"
        )
        for row in sonarr_rows:
            sonarr_ids.add(row["anilist_id"])
            if row.get("sonarr_monitored"):
                sonarr_monitored_ids.add(row["anilist_id"])

        for entry in raw_entries:
            aid = entry["anilist_id"]
            # Determine local_status
            local_status = "have" if aid in local_anilist_ids else "missing"
            # Determine sonarr_status
            if aid in sonarr_monitored_ids:
                sonarr_status = "monitored"
            elif aid in sonarr_ids:
                sonarr_status = "in_sonarr"
            else:
                sonarr_status = "not_in_sonarr"

            enriched = dict(entry)
            enriched["local_status"] = local_status
            enriched["sonarr_status"] = sonarr_status
            entries.append(enriched)

        # Compute status counts
        for entry in entries:
            s = entry.get("list_status", "")
            status_counts[s] = status_counts.get(s, 0) + 1

    return templates.TemplateResponse(
        "watchlist_library.html",
        {
            "request": request,
            "entries": entries,
            "total_count": len(entries),
            "status_counts": status_counts,
            "user": user,
            "user_id": user_id,
        },
    )


@router.post("/api/library/watchlist/refresh")
async def refresh_watchlist(request: Request) -> JSONResponse:
    """Fetch user's AniList list and bulk-upsert into user_watchlist.

    Accepts optional ``user_id`` in JSON body; defaults to first linked user.
    """
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        pass

    user_id: str = body.get("user_id", "")
    if not user_id:
        users = await db.get_users_by_service("anilist")
        if not users:
            return JSONResponse({"error": "No linked AniList user"}, status_code=400)
        user_id = users[0]["user_id"]
        user_row = users[0]
    else:
        user_row = await db.get_user(user_id)
        if not user_row:
            return JSONResponse({"error": "User not found"}, status_code=404)

    anilist_user_id: int = user_row.get("anilist_id", 0)
    access_token: str = user_row.get("access_token", "")

    if not anilist_user_id:
        return JSONResponse({"error": "No AniList user ID on record"}, status_code=400)

    try:
        entries = await anilist_client.get_user_watchlist(
            anilist_user_id, access_token or None
        )
        count = await db.bulk_upsert_watchlist(user_id, entries)
        logger.info("Refreshed watchlist for user_id=%s: %d entries", user_id, count)
        return JSONResponse({"ok": True, "count": count})
    except Exception as exc:
        logger.exception("Watchlist refresh failed for user_id=%s", user_id)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/library/watchlist")
async def get_watchlist_json(request: Request) -> JSONResponse:
    """Return watchlist entries as JSON.

    Accepts ``?status=CURRENT,PLANNING`` for filtering (comma-separated).
    """
    db = request.app.state.db

    users = await db.get_users_by_service("anilist")
    if not users:
        return JSONResponse({"entries": [], "total": 0})

    user_id = users[0]["user_id"]

    status_param = request.query_params.get("status", "")
    list_statuses: list[str] | None = None
    if status_param:
        list_statuses = [s.strip() for s in status_param.split(",") if s.strip()]

    entries = await db.get_watchlist(user_id, list_statuses=list_statuses)
    return JSONResponse({"entries": entries, "total": len(entries)})
