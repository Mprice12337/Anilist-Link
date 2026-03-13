"""Manual grab — search Prowlarr and send torrents to qBittorrent."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.Clients.ProwlarrClient import ProwlarrClient
from src.Clients.QBittorrentClient import QBittorrentClient
from src.Utils.NamingTranslator import get_all_titles

logger = logging.getLogger(__name__)

router = APIRouter(tags=["manual-grab"])

_DEFAULT_SAVE_PATH = "/data/anime"


@router.get("/grab/{anilist_id}", response_class=HTMLResponse)
async def grab_page(request: Request, anilist_id: int) -> HTMLResponse:
    """Render the manual grab search page for a given AniList entry."""
    db = request.app.state.db
    templates = request.app.state.templates

    # Try to get title from watchlist first, fall back to anilist_cache
    title = ""
    anilist_format = ""
    cover_image = ""

    users = await db.get_users_by_service("anilist")
    if users:
        entry = await db.get_watchlist_entry(users[0]["user_id"], anilist_id)
        if entry:
            title = entry.get("anilist_title", "")
            anilist_format = entry.get("anilist_format", "")
            cover_image = entry.get("cover_image", "")

    if not title:
        cached = await db.get_cached_metadata(anilist_id)
        if cached:
            title = (
                cached.get("title_romaji")
                or cached.get("title_english")
                or cached.get("title_native")
                or ""
            )
            cover_image = cached.get("cover_image", "")

    return templates.TemplateResponse(
        "manual_grab.html",
        {
            "request": request,
            "anilist_id": anilist_id,
            "anilist_title": title or f"AniList #{anilist_id}",
            "anilist_format": anilist_format,
            "cover_image": cover_image,
        },
    )


@router.get("/api/grab/search")
async def grab_search(request: Request) -> JSONResponse:
    """Search Prowlarr for releases for a given AniList entry.

    Query params: anilist_id (required)
    """
    db = request.app.state.db
    config = request.app.state.config
    anilist_client = request.app.state.anilist_client

    anilist_id_str = request.query_params.get("anilist_id", "")
    if not anilist_id_str or not anilist_id_str.isdigit():
        return JSONResponse({"error": "anilist_id is required"}, status_code=400)
    anilist_id = int(anilist_id_str)

    if not config.prowlarr.url or not config.prowlarr.api_key:
        return JSONResponse({"error": "Prowlarr not configured"}, status_code=503)

    # Gather title and synonyms
    main_title = ""
    alt_titles: list[str] = []

    users = await db.get_users_by_service("anilist")
    if users:
        entry = await db.get_watchlist_entry(users[0]["user_id"], anilist_id)
        if entry:
            main_title = entry.get("anilist_title", "")

    if not main_title:
        cached = await db.get_cached_metadata(anilist_id)
        if cached:
            main_title = (
                cached.get("title_romaji")
                or cached.get("title_english")
                or cached.get("title_native")
                or ""
            )

    # Fetch synonyms from AniList API if possible
    if main_title:
        try:
            media = await anilist_client.get_anime_by_id(anilist_id)
            if media:
                all_t = get_all_titles(media)
                alt_titles = [t for t in all_t if t != main_title]
        except Exception:
            logger.debug("Could not fetch AniList synonyms for id=%d", anilist_id)

    prowlarr = ProwlarrClient(
        url=config.prowlarr.url,
        api_key=config.prowlarr.api_key,
    )
    try:
        results = await prowlarr.search_anime(
            query=main_title or f"AniList {anilist_id}",
            titles=alt_titles,
        )
    except Exception as exc:
        logger.error("Prowlarr search failed: %s", exc)
        await prowlarr.close()
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await prowlarr.close()

    result_dicts = [
        {
            "guid": r.guid,
            "title": r.title,
            "size_mb": round(r.size / (1024 * 1024), 1) if r.size else 0,
            "seeders": r.seeders,
            "leechers": r.leechers,
            "indexer": r.indexer,
            "quality": r.quality,
            "is_torrent": r.is_torrent,
            "download_url": r.download_url,
            "magnet_url": r.magnet_url,
            "publish_date": r.publish_date,
        }
        for r in results
    ]

    return JSONResponse(
        {
            "ok": True,
            "anilist_title": main_title,
            "results": result_dicts,
        }
    )


@router.post("/api/grab/download")
async def grab_download(request: Request) -> JSONResponse:
    """Send a release to qBittorrent.

    Body JSON:
      anilist_id, download_url_or_magnet, title,
      save_path (optional), category (optional)
    """
    db = request.app.state.db
    config = request.app.state.config

    if not config.qbittorrent.url:
        return JSONResponse({"error": "qBittorrent not configured"}, status_code=503)

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    url_or_magnet: str = body.get("download_url_or_magnet") or body.get(
        "magnet_url", ""
    )
    if not url_or_magnet:
        return JSONResponse(
            {"error": "download_url_or_magnet is required"}, status_code=400
        )

    # Determine save_path
    save_path: str = body.get("save_path", "")
    if not save_path:
        save_path = (
            await db.get_setting("restructure.local_path_prefix") or _DEFAULT_SAVE_PATH
        )

    category: str = body.get("category", "anilist-link")

    qbit = QBittorrentClient(
        url=config.qbittorrent.url,
        username=config.qbittorrent.username,
        password=config.qbittorrent.password,
    )
    try:
        ok = await qbit.add_torrent(
            url_or_magnet=url_or_magnet,
            save_path=save_path,
            category=category,
            name=body.get("title"),
        )
        if ok:
            logger.info(
                "Sent torrent to qBittorrent: anilist_id=%s, url=%s…",
                body.get("anilist_id", "?"),
                url_or_magnet[:60],
            )
            return JSONResponse({"ok": True})
        else:
            return JSONResponse(
                {"error": "qBittorrent rejected the request"}, status_code=500
            )
    except Exception as exc:
        logger.error("qBittorrent add failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await qbit.close()


@router.get("/api/grab/qbit/status")
async def qbit_status(request: Request) -> JSONResponse:
    """Return active torrents from qBittorrent."""
    config = request.app.state.config

    if not config.qbittorrent.url:
        return JSONResponse({"error": "qBittorrent not configured"}, status_code=503)

    qbit = QBittorrentClient(
        url=config.qbittorrent.url,
        username=config.qbittorrent.username,
        password=config.qbittorrent.password,
    )
    try:
        torrents_raw = await qbit.get_torrents(category="anilist-link")
        torrents = [
            {
                "name": t.get("name", ""),
                "state": t.get("state", ""),
                "progress": round(t.get("progress", 0) * 100, 1),
                "size": t.get("size", 0),
                "speed": t.get("dlspeed", 0),
                "hash": t.get("hash", ""),
            }
            for t in torrents_raw
        ]
        return JSONResponse({"ok": True, "torrents": torrents})
    except Exception as exc:
        logger.error("qBittorrent status check failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await qbit.close()
