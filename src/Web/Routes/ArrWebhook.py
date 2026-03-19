"""Webhook endpoints for Sonarr and Radarr download events.

Sonarr/Radarr POST to these endpoints after a download completes.
The post-processor moves the file to the AniList-structured path and
updates the arr service's file record so it stays linked — no rescan needed.

Webhook URLs to register in Sonarr/Radarr:
  Sonarr: http://<your-app-host>:9876/api/webhook/sonarr
  Radarr: http://<your-app-host>:9876/api/webhook/radarr
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.Download.ArrPostProcessor import ArrPostProcessor

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.post("/api/webhook/sonarr")
async def sonarr_webhook(request: Request) -> JSONResponse:
    """Receive Sonarr download/upgrade events and post-process file paths."""
    payload: dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    config = request.app.state.config
    if not config.download_sync.arr_enabled:
        logger.debug("Sonarr webhook received but arr_enabled=false — ignored")
        return JSONResponse({"ok": True})

    db = request.app.state.db
    processor = ArrPostProcessor(db=db, config=config)

    # Fire-and-forget — respond immediately so Sonarr doesn't time out
    asyncio.create_task(processor.process_sonarr_download(payload))
    return JSONResponse({"ok": True})


@router.post("/api/webhook/radarr")
async def radarr_webhook(request: Request) -> JSONResponse:
    """Receive Radarr download/upgrade events and post-process file paths."""
    payload: dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    config = request.app.state.config
    if not config.download_sync.arr_enabled:
        logger.debug("Radarr webhook received but arr_enabled=false — ignored")
        return JSONResponse({"ok": True})

    db = request.app.state.db
    processor = ArrPostProcessor(db=db, config=config)

    asyncio.create_task(processor.process_radarr_download(payload))
    return JSONResponse({"ok": True})


@router.get("/api/webhook/info")
async def webhook_info(request: Request) -> JSONResponse:
    """Return the webhook URLs to register in Sonarr/Radarr."""
    base_url = request.app.state.config.app.base_url.rstrip("/")
    return JSONResponse(
        {
            "sonarr": f"{base_url}/api/webhook/sonarr",
            "radarr": f"{base_url}/api/webhook/radarr",
        }
    )
