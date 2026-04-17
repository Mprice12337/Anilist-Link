"""Jellyfin webhook receiver.

Handles events from the Jellyfin Webhook plugin.  Logs all incoming
events for diagnostics.

Note: ``TaskCompleted`` events are broken server-side (Jellyfin never
emits the underlying event — see jellyfin-plugin-webhook#25).  Scan
completion is detected via the WebSocket ``ScheduledTasksInfo``
subscription in :class:`JellyfinEventListener` instead.

Webhook URL to configure in Jellyfin:
  http://<your-app-host>:9876/jellyfin/webhook
  Recommended: enable "Send All Properties"
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jellyfin-webhook"])


@router.get("/jellyfin/webhook")
async def jellyfin_webhook_test(request: Request) -> JSONResponse:
    """Test endpoint — verify the webhook URL is reachable."""
    return JSONResponse(
        {
            "status": "ok",
            "message": "Jellyfin webhook endpoint is reachable",
            "expected_method": "POST",
        }
    )


@router.post("/jellyfin/webhook")
async def jellyfin_webhook(request: Request) -> JSONResponse:
    """Receive webhook events from the Jellyfin Webhook plugin."""
    payload: dict[str, Any] = {}
    try:
        body = await request.body()
        payload = json.loads(body)
    except Exception:
        logger.warning("Jellyfin webhook: could not parse JSON body")
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    notification_type = payload.get("NotificationType", "")
    logger.info(
        "Jellyfin webhook received: %s (payload keys: %s)",
        notification_type,
        ", ".join(sorted(payload.keys())),
    )
    logger.debug("Jellyfin webhook payload: %s", json.dumps(payload, default=str))

    return JSONResponse({"ok": True})
