"""Watch sync account linking and manual trigger routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Jellyfin account linking
# ---------------------------------------------------------------------------


@router.get("/api/jellyfin/users")
async def list_jellyfin_server_users(request: Request) -> JSONResponse:
    """Return all users on the configured Jellyfin server.

    Used to populate the account-linking dropdown.
    """
    config = request.app.state.config
    if not config.jellyfin.url or not config.jellyfin.api_key:
        return JSONResponse(
            {"ok": False, "error": "Jellyfin not configured"}, status_code=400
        )

    from src.Clients.JellyfinClient import JellyfinClient

    client = JellyfinClient(url=config.jellyfin.url, api_key=config.jellyfin.api_key)
    try:
        users = await client.get_users()
        return JSONResponse(
            {
                "ok": True,
                "users": [
                    {"id": u.get("Id", ""), "name": u.get("Name", "")} for u in users
                ],
            }
        )
    except Exception as exc:
        logger.warning("Failed to list Jellyfin users: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    finally:
        await client.close()


@router.post("/api/watch-sync/jellyfin/link")
async def link_jellyfin_user(request: Request) -> JSONResponse:
    """Link a Jellyfin user account for watch sync.

    Expects JSON: ``{"jf_user_id": "...", "jf_username": "..."}``
    """
    db = request.app.state.db

    body = await request.json()
    jf_user_id: str = (body.get("jf_user_id") or "").strip()
    jf_username: str = (body.get("jf_username") or "").strip()

    if not jf_user_id or not jf_username:
        return JSONResponse(
            {"ok": False, "error": "jf_user_id and jf_username are required"},
            status_code=400,
        )

    # Resolve the linked AniList user_id (first linked account)
    anilist_users = await db.get_users_by_service("anilist")
    anilist_user_id = anilist_users[0]["user_id"] if anilist_users else ""

    # Single-user: clear any existing link before adding
    await db.clear_jellyfin_users()
    await db.upsert_jellyfin_user(
        jf_user_id=jf_user_id,
        jf_username=jf_username,
        anilist_user_id=anilist_user_id,
    )

    logger.info(
        "Linked Jellyfin user '%s' (%s) for watch sync", jf_username, jf_user_id
    )
    return JSONResponse({"ok": True})


@router.post("/api/watch-sync/jellyfin/unlink")
async def unlink_jellyfin_user(request: Request) -> JSONResponse:
    """Remove the linked Jellyfin user."""
    db = request.app.state.db
    await db.clear_jellyfin_users()
    logger.info("Unlinked Jellyfin user for watch sync")
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Plex account linking
# ---------------------------------------------------------------------------


@router.get("/api/plex/accounts")
async def list_plex_accounts(request: Request) -> JSONResponse:
    """Return all home accounts on the configured Plex server.

    Used to populate the account-linking dropdown.
    """
    config = request.app.state.config
    if not config.plex.url or not config.plex.token:
        return JSONResponse(
            {"ok": False, "error": "Plex not configured"}, status_code=400
        )

    from src.Clients.PlexClient import PlexClient

    client = PlexClient(url=config.plex.url, token=config.plex.token)
    try:
        accounts = await client.get_accounts()
        return JSONResponse({"ok": True, "accounts": accounts})
    except Exception as exc:
        logger.warning("Failed to list Plex accounts: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    finally:
        await client.close()


@router.post("/api/watch-sync/plex/link")
async def link_plex_user(request: Request) -> JSONResponse:
    """Link a Plex account for watch sync.

    Expects JSON: ``{"plex_username": "...", "plex_uuid": "..."}``
    ``plex_uuid`` is the numeric account ID returned by ``/accounts``.
    """
    db = request.app.state.db

    body = await request.json()
    plex_username: str = (body.get("plex_username") or "").strip()
    plex_uuid: str = str(body.get("plex_uuid") or "").strip()

    if not plex_username:
        return JSONResponse(
            {"ok": False, "error": "plex_username is required"}, status_code=400
        )

    anilist_users = await db.get_users_by_service("anilist")
    anilist_user_id = anilist_users[0]["user_id"] if anilist_users else ""

    await db.clear_plex_users()
    await db.upsert_plex_user(
        plex_username=plex_username,
        plex_uuid=plex_uuid,
        anilist_user_id=anilist_user_id,
    )

    logger.info("Linked Plex user '%s' for watch sync", plex_username)
    return JSONResponse({"ok": True})


@router.post("/api/watch-sync/plex/unlink")
async def unlink_plex_user(request: Request) -> JSONResponse:
    """Remove the linked Plex user."""
    db = request.app.state.db
    await db.clear_plex_users()
    logger.info("Unlinked Plex user for watch sync")
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Manual sync triggers
# ---------------------------------------------------------------------------


@router.post("/api/watch-sync/jellyfin/run")
async def trigger_jellyfin_sync(request: Request) -> JSONResponse:
    """Manually trigger a Jellyfin → AniList watch sync."""
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    if not config.jellyfin.url or not config.jellyfin.api_key:
        return JSONResponse(
            {"ok": False, "error": "Jellyfin not configured"}, status_code=400
        )

    from src.Clients.JellyfinClient import JellyfinClient
    from src.Sync.JellyfinWatchSyncer import JellyfinWatchSyncer
    from src.Web.App import spawn_background_task

    async def _run() -> None:
        jf_client = JellyfinClient(
            url=config.jellyfin.url, api_key=config.jellyfin.api_key
        )
        try:
            syncer = JellyfinWatchSyncer(
                db=db,
                anilist_client=anilist_client,
                jellyfin_client=jf_client,
            )
            results = await syncer.sync_to_anilist()
            logger.info("Manual Jellyfin → AniList sync results: %s", results)
        finally:
            await jf_client.close()

    spawn_background_task(request.app.state, _run())
    return JSONResponse({"ok": True, "message": "Jellyfin → AniList sync started"})


@router.post("/api/watch-sync/jellyfin/push")
async def trigger_jellyfin_push(request: Request) -> JSONResponse:
    """Manually trigger an AniList → Jellyfin watch sync (backfill)."""
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    if not config.jellyfin.url or not config.jellyfin.api_key:
        return JSONResponse(
            {"ok": False, "error": "Jellyfin not configured"}, status_code=400
        )

    from src.Clients.JellyfinClient import JellyfinClient
    from src.Sync.JellyfinWatchSyncer import JellyfinWatchSyncer
    from src.Web.App import spawn_background_task

    async def _run() -> None:
        jf_client = JellyfinClient(
            url=config.jellyfin.url, api_key=config.jellyfin.api_key
        )
        try:
            syncer = JellyfinWatchSyncer(
                db=db,
                anilist_client=anilist_client,
                jellyfin_client=jf_client,
            )
            results = await syncer.sync_to_jellyfin()
            logger.info("Manual AniList → Jellyfin sync results: %s", results)
        finally:
            await jf_client.close()

    spawn_background_task(request.app.state, _run())
    return JSONResponse({"ok": True, "message": "AniList → Jellyfin sync started"})


@router.post("/api/watch-sync/plex/run")
async def trigger_plex_sync(request: Request) -> JSONResponse:
    """Manually trigger a Plex → AniList watch sync."""
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    if not config.plex.url or not config.plex.token:
        return JSONResponse(
            {"ok": False, "error": "Plex not configured"}, status_code=400
        )

    from src.Clients.PlexClient import PlexClient
    from src.Sync.PlexWatchSyncer import PlexWatchSyncer
    from src.Web.App import spawn_background_task

    async def _run() -> None:
        plex_client = PlexClient(url=config.plex.url, token=config.plex.token)
        try:
            syncer = PlexWatchSyncer(
                db=db,
                anilist_client=anilist_client,
                plex_client=plex_client,
            )
            results = await syncer.sync_to_anilist()
            logger.info("Manual Plex → AniList sync results: %s", results)
        finally:
            await plex_client.close()

    spawn_background_task(request.app.state, _run())
    return JSONResponse({"ok": True, "message": "Plex → AniList sync started"})


@router.post("/api/watch-sync/plex/push")
async def trigger_plex_push(request: Request) -> JSONResponse:
    """Manually trigger an AniList → Plex watch sync (backfill)."""
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    if not config.plex.url or not config.plex.token:
        return JSONResponse(
            {"ok": False, "error": "Plex not configured"}, status_code=400
        )

    from src.Clients.PlexClient import PlexClient
    from src.Sync.PlexWatchSyncer import PlexWatchSyncer
    from src.Web.App import spawn_background_task

    async def _run() -> None:
        plex_client = PlexClient(url=config.plex.url, token=config.plex.token)
        try:
            syncer = PlexWatchSyncer(
                db=db,
                anilist_client=anilist_client,
                plex_client=plex_client,
            )
            results = await syncer.sync_to_plex()
            logger.info("Manual AniList → Plex sync results: %s", results)
        finally:
            await plex_client.close()

    spawn_background_task(request.app.state, _run())
    return JSONResponse({"ok": True, "message": "AniList → Plex sync started"})


# ---------------------------------------------------------------------------
# Watch sync status / management page
# ---------------------------------------------------------------------------


@router.get("/watch-sync")
async def watch_sync_page(request: Request):  # type: ignore[return]
    """Render the watch sync account linking and status page."""
    db = request.app.state.db
    templates = request.app.state.templates

    jellyfin_user = await db.get_jellyfin_user()
    plex_user = await db.get_plex_user()

    return templates.TemplateResponse(
        "watch_sync.html",
        {
            "request": request,
            "jellyfin_user": jellyfin_user,
            "plex_user": plex_user,
        },
    )
