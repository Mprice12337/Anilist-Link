"""Jellyfin webhook receiver.

Handles events from the Jellyfin Webhook plugin.  Currently listens for
TaskCompleted events to clean up virtual season folders that Jellyfin
creates when its metadata providers return season data for seasons that
don't exist on disk.

Webhook URL to configure in Jellyfin:
  http://<your-app-host>:9876/jellyfin/webhook
  Event type: TaskCompleted
  Recommended: enable "Send All Properties"
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.Clients.JellyfinClient import JellyfinClient
from src.Web.App import spawn_background_task

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jellyfin-webhook"])

LIBRARY_SCAN_TASK_NAMES = {
    "Scan Media Library",
    "RefreshLibrary",
    "Scan media library",
}


async def _cleanup_virtual_seasons(app_state: object) -> None:
    """Background task: delete virtual seasons from configured Jellyfin libraries."""
    config = app_state.config  # type: ignore[attr-defined]
    if not config.jellyfin.url or not config.jellyfin.api_key:
        return

    library_ids: list[str] = (
        list(config.jellyfin.anime_library_ids)
        if config.jellyfin.anime_library_ids
        else []
    )
    if not library_ids:
        logger.debug("Virtual season cleanup skipped — no library IDs configured")
        return

    jf = JellyfinClient(url=config.jellyfin.url, api_key=config.jellyfin.api_key)
    try:
        deleted = await jf.delete_virtual_seasons(library_ids)
        if deleted:
            logger.info("Webhook-triggered cleanup removed %d virtual seasons", deleted)
    except Exception:
        logger.exception("Virtual season cleanup failed")
    finally:
        await jf.close()


@router.get("/jellyfin/webhook")
async def jellyfin_webhook_test(request: Request) -> JSONResponse:
    """Test endpoint — verify the webhook URL is reachable."""
    return JSONResponse(
        {
            "status": "ok",
            "message": "Jellyfin webhook endpoint is reachable",
            "expected_method": "POST",
            "expected_events": ["TaskCompleted"],
        }
    )


@router.post("/jellyfin/webhook")
async def jellyfin_webhook(request: Request) -> JSONResponse:
    """Receive webhook events from the Jellyfin Webhook plugin."""
    payload: dict[str, Any] = {}
    try:
        body = await request.body()
        logger.info(
            "Jellyfin webhook raw body (%d bytes): %s",
            len(body),
            body[:500].decode(errors="replace"),
        )
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

    if notification_type == "TaskCompleted":
        task_name = payload.get("TaskName", "")
        result_status = payload.get("ResultStatus", "")
        logger.info(
            "Jellyfin TaskCompleted: task=%r status=%r",
            task_name,
            result_status,
        )

        if task_name in LIBRARY_SCAN_TASK_NAMES:
            logger.info(
                "Library scan completed (status=%s)"
                " — scheduling virtual season cleanup",
                result_status,
            )
            spawn_background_task(
                request.app.state,
                _cleanup_virtual_seasons(request.app.state),
                task_key="jellyfin_virtual_cleanup",
            )

    return JSONResponse({"ok": True})
