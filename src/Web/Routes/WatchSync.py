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


async def _enrich_log_entries(db: object, entries: list[dict]) -> list[dict]:
    """Add full metadata to log entries for title display and the media
    detail modal.

    Tries ``anilist_cache`` first (TTL ignored — titles are stable), then
    falls back to ``user_watchlist`` which has titles for anything on the
    user's AniList (covers Crunchyroll-synced entries that were never
    scanned into the metadata cache).
    """
    if not entries:
        return entries
    anilist_ids = list({e["anilist_id"] for e in entries if e.get("anilist_id")})
    if not anilist_ids:
        return entries

    placeholders = ",".join("?" for _ in anilist_ids)

    # Primary source: anilist_cache (has description, genres, studio, rating)
    cache_rows = await db.fetch_all(  # type: ignore[attr-defined]
        f"SELECT * FROM anilist_cache WHERE anilist_id IN ({placeholders})",
        tuple(anilist_ids),
    )
    meta_map: dict[int, dict] = {r["anilist_id"]: dict(r) for r in cache_rows}

    # Fallback: user_watchlist (has titles, cover, format, episodes, year)
    missing = [aid for aid in anilist_ids if aid not in meta_map]
    if missing:
        wl_ph = ",".join("?" for _ in missing)
        wl_rows = await db.fetch_all(  # type: ignore[attr-defined]
            f"SELECT * FROM user_watchlist WHERE anilist_id IN ({wl_ph})",
            tuple(missing),
        )
        for r in wl_rows:
            aid = r["anilist_id"]
            if aid not in meta_map:
                meta_map[aid] = dict(r)

    enriched = []
    for e in entries:
        row = dict(e)
        m = meta_map.get(e.get("anilist_id", 0)) or {}
        row["title_romaji"] = m.get("title_romaji", "")
        row["title_english"] = m.get("title_english", "")
        row["cover_image"] = m.get("cover_image", "")
        # anilist_cache uses "episodes"; user_watchlist uses "anilist_episodes"
        row["anilist_episodes"] = m.get("episodes") or m.get("anilist_episodes")
        row["anilist_format"] = m.get("anilist_format", "")
        row["start_year"] = m.get("year") or m.get("start_year") or 0
        row["airing_status"] = m.get("status") or m.get("airing_status", "")
        row["genres"] = m.get("genres", "[]")
        row["rating"] = m.get("rating")
        row["studio"] = m.get("studio", "")
        row["description"] = m.get("description", "")
        enriched.append(row)
    return enriched


@router.get("/watch-sync")
async def watch_sync_page(request: Request):  # type: ignore[return]
    """Render the watch sync account linking and status page."""
    db = request.app.state.db
    config = request.app.state.config
    templates = request.app.state.templates

    jellyfin_user = await db.get_jellyfin_user()
    plex_user = await db.get_plex_user()
    sync_log = await db.get_watch_sync_log(limit=100)

    # Crunchyroll data
    cr_configured = bool(config.crunchyroll.email and config.crunchyroll.password)
    cr_auto_approve = config.crunchyroll.auto_approve
    cr_sync_log = await db.get_cr_sync_log(limit=100)

    # Enrich with cached AniList titles for dual-language display
    sync_log = await _enrich_log_entries(db, sync_log)
    cr_sync_log = await _enrich_log_entries(db, cr_sync_log)

    title_display = await db.get_setting("app.title_display") or "romaji"

    return templates.TemplateResponse(
        "watch_sync.html",
        {
            "request": request,
            "jellyfin_user": jellyfin_user,
            "plex_user": plex_user,
            "plex_watch_sync_enabled": config.plex.watch_sync_enabled,
            "jellyfin_watch_sync_enabled": config.jellyfin.watch_sync_enabled,
            "jellyfin_configured": bool(
                config.jellyfin.url and config.jellyfin.api_key
            ),
            "plex_configured": bool(config.plex.url and config.plex.token),
            "sync_log": sync_log,
            "cr_configured": cr_configured,
            "cr_auto_approve": cr_auto_approve,
            "cr_sync_log": cr_sync_log,
            "title_display": title_display,
        },
    )


# ---------------------------------------------------------------------------
# Enable / disable toggles
# ---------------------------------------------------------------------------


@router.post("/api/watch-sync/plex/toggle")
async def toggle_plex_watch_sync(request: Request) -> JSONResponse:
    """Enable or disable the scheduled Plex → AniList watch sync."""
    db = request.app.state.db
    body = await request.json()
    enabled: bool = bool(body.get("enabled", False))
    value = "true" if enabled else "false"
    await db.set_setting("plex.watch_sync_enabled", value)
    # Refresh in-memory config
    db_settings = await db.get_all_settings()
    from src.Utils.Config import load_config_from_db_settings

    request.app.state.config = load_config_from_db_settings(db_settings)
    logger.info("Plex watch sync %s", "enabled" if enabled else "disabled")
    return JSONResponse({"ok": True, "enabled": enabled})


@router.post("/api/watch-sync/jellyfin/toggle")
async def toggle_jellyfin_watch_sync(request: Request) -> JSONResponse:
    """Enable or disable the scheduled Jellyfin → AniList watch sync."""
    db = request.app.state.db
    body = await request.json()
    enabled: bool = bool(body.get("enabled", False))
    value = "true" if enabled else "false"
    await db.set_setting("jellyfin.watch_sync_enabled", value)
    db_settings = await db.get_all_settings()
    from src.Utils.Config import load_config_from_db_settings

    request.app.state.config = load_config_from_db_settings(db_settings)
    logger.info("Jellyfin watch sync %s", "enabled" if enabled else "disabled")
    return JSONResponse({"ok": True, "enabled": enabled})


# ---------------------------------------------------------------------------
# Watch sync log (history + undo)
# ---------------------------------------------------------------------------


@router.get("/api/watch-sync/log")
async def get_watch_sync_log(
    request: Request,
    source: str = "",
    limit: int = 200,
) -> JSONResponse:
    """Return watch_sync_log entries, optionally filtered by source."""
    db = request.app.state.db
    entries = await db.get_watch_sync_log(
        source=source or None,
        limit=min(limit, 500),
    )
    return JSONResponse({"ok": True, "entries": entries})


@router.post("/api/watch-sync/undo/{log_id}")
async def undo_watch_sync_entry(request: Request, log_id: int) -> JSONResponse:
    """Undo a single watch_sync_log entry by reverting progress/status on AniList."""
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    entry = await db.get_watch_sync_log_entry(log_id)
    if not entry:
        return JSONResponse(
            {"ok": False, "error": "Log entry not found"}, status_code=404
        )

    if entry.get("undone_at"):
        return JSONResponse({"ok": False, "error": "Already undone"}, status_code=400)

    users = await db.get_users_by_service("anilist")
    user = next((u for u in users if u["user_id"] == entry["user_id"]), None)
    if not user:
        # Fall back to first linked account
        user = users[0] if users else None
    if not user:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)

    access_token = user["access_token"]
    before_status = entry["before_status"]
    before_progress = entry["before_progress"]
    anilist_id = entry["anilist_id"]

    try:
        if not before_status:
            resp = await anilist_client.update_anime_progress(
                anilist_id, access_token, 0, "PLANNING"
            )
        else:
            resp = await anilist_client.update_anime_progress(
                anilist_id, access_token, before_progress, before_status
            )

        if resp:
            await db.mark_watch_sync_log_undone(log_id)
            return JSONResponse({"ok": True})
        else:
            return JSONResponse(
                {"ok": False, "error": "AniList update failed"}, status_code=500
            )

    except Exception as exc:
        logger.exception("Undo failed for watch_sync_log id %s", log_id)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
