"""Tools overview page — links to all operational tools with status."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tools"])


@router.get("/tools", response_class=HTMLResponse)
async def tools_page(request: Request) -> HTMLResponse:
    """Render the tools overview page."""
    db = request.app.state.db
    config = request.app.state.config
    templates = request.app.state.templates

    # Last restructure timestamp
    last_restructure: str | None = None
    try:
        row = await db.fetch_one("SELECT MAX(executed_at) AS ts FROM restructure_log")
        last_restructure = row["ts"] if row else None
    except Exception:
        pass

    # Last CR sync timestamp
    last_cr_sync: str | None = None
    try:
        row = await db.fetch_one("SELECT MAX(applied_at) AS ts FROM cr_sync_log")
        last_cr_sync = row["ts"] if row else None
    except Exception:
        pass

    # Download request count
    download_count: int = 0
    try:
        row = await db.fetch_one("SELECT COUNT(*) AS cnt FROM download_requests")
        download_count = row["cnt"] if row else 0
    except Exception:
        pass

    # Service configured flags
    plex_configured = bool(config.plex.url and config.plex.token)
    jellyfin_configured = bool(config.jellyfin.url and config.jellyfin.api_key)
    crunchyroll_configured = bool(
        await db.get_setting("crunchyroll.email")
        or getattr(config, "crunchyroll", None)
        and getattr(config.crunchyroll, "email", None)
    )
    sonarr_configured = bool(config.sonarr.url and config.sonarr.api_key)
    radarr_configured = bool(config.radarr.url and config.radarr.api_key)
    downloads_configured = sonarr_configured or radarr_configured

    return templates.TemplateResponse(
        "tools.html",
        {
            "request": request,
            "last_restructure": last_restructure,
            "last_cr_sync": last_cr_sync,
            "download_count": download_count,
            "plex_configured": plex_configured,
            "jellyfin_configured": jellyfin_configured,
            "crunchyroll_configured": crunchyroll_configured,
            "sonarr_configured": sonarr_configured,
            "radarr_configured": radarr_configured,
            "downloads_configured": downloads_configured,
            "version": "0.1.0",
        },
    )
