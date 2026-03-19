"""Manual grab — search Sonarr/Radarr indexers and send releases to *arr."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.Clients.RadarrClient import RadarrClient
from src.Clients.SonarrClient import SonarrClient
from src.Utils.NamingTranslator import is_movie_format

logger = logging.getLogger(__name__)

router = APIRouter(tags=["manual-grab"])


@router.get("/grab/{anilist_id}", response_class=HTMLResponse)
async def grab_page(request: Request, anilist_id: int) -> HTMLResponse:
    """Render the manual grab search page for a given AniList entry."""
    db = request.app.state.db
    templates = request.app.state.templates

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
    """Search Sonarr or Radarr for releases for a given AniList entry.

    The entry must already be added to Sonarr/Radarr (via the Download Manager
    or auto-sync). Uses the *arr's configured indexers — no Prowlarr needed.

    Query params: anilist_id (required)
    """
    db = request.app.state.db
    config = request.app.state.config

    anilist_id_str = request.query_params.get("anilist_id", "")
    if not anilist_id_str or not anilist_id_str.isdigit():
        return JSONResponse({"error": "anilist_id is required"}, status_code=400)
    anilist_id = int(anilist_id_str)

    # Determine format (series vs movie)
    anilist_format = ""
    users = await db.get_users_by_service("anilist")
    if users:
        entry = await db.get_watchlist_entry(users[0]["user_id"], anilist_id)
        if entry:
            anilist_format = entry.get("anilist_format", "") or ""

    if not anilist_format:
        cached = await db.get_cached_metadata(anilist_id)
        if cached:
            anilist_format = cached.get("format", "") or ""

    is_movie = is_movie_format(anilist_format)

    if is_movie:
        return await _search_radarr(anilist_id, config, db)
    else:
        return await _search_sonarr(anilist_id, config, db)


async def _search_sonarr(anilist_id: int, config: Any, db: Any) -> JSONResponse:
    if not config.sonarr.url or not config.sonarr.api_key:
        return JSONResponse({"error": "Sonarr not configured"}, status_code=503)

    mapping = await db.fetch_one(
        "SELECT sonarr_id FROM anilist_sonarr_mapping"
        " WHERE anilist_id=? AND in_sonarr=1",
        (anilist_id,),
    )
    if not mapping:
        return JSONResponse(
            {
                "error": "Not in Sonarr yet. Add it via the Download Manager first.",
                "needs_add": True,
                "service": "sonarr",
            },
            status_code=404,
        )

    sonarr_id: int = mapping["sonarr_id"]
    client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
    try:
        raw = await client.search_releases(sonarr_id)
    except Exception as exc:
        logger.error("Sonarr release search failed: %s", exc)
        await client.close()
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await client.close()

    return JSONResponse(
        {"ok": True, "service": "sonarr", "results": _normalise_releases(raw)}
    )


async def _search_radarr(anilist_id: int, config: Any, db: Any) -> JSONResponse:
    if not config.radarr.url or not config.radarr.api_key:
        return JSONResponse({"error": "Radarr not configured"}, status_code=503)

    mapping = await db.fetch_one(
        "SELECT radarr_id FROM anilist_radarr_mapping"
        " WHERE anilist_id=? AND in_radarr=1",
        (anilist_id,),
    )
    if not mapping:
        return JSONResponse(
            {
                "error": "Not in Radarr yet. Add it via the Download Manager first.",
                "needs_add": True,
                "service": "radarr",
            },
            status_code=404,
        )

    radarr_id: int = mapping["radarr_id"]
    client = RadarrClient(url=config.radarr.url, api_key=config.radarr.api_key)
    try:
        raw = await client.search_releases(radarr_id)
    except Exception as exc:
        logger.error("Radarr release search failed: %s", exc)
        await client.close()
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await client.close()

    return JSONResponse(
        {"ok": True, "service": "radarr", "results": _normalise_releases(raw)}
    )


def _normalise_releases(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise Sonarr/Radarr release objects into a consistent shape."""
    out = []
    for r in raw:
        quality_obj = r.get("quality", {})
        quality_name = (
            quality_obj.get("quality", {}).get("name", "")
            if isinstance(quality_obj, dict)
            else ""
        )
        size_bytes: int = r.get("size", 0) or 0
        out.append(
            {
                "guid": r.get("guid", ""),
                "indexer_id": r.get("indexerId", 0),
                "title": r.get("title", ""),
                "size_mb": round(size_bytes / (1024 * 1024), 1) if size_bytes else 0,
                "quality": quality_name,
                "protocol": r.get("protocol", ""),
                "indexer": r.get("indexer", ""),
                "seeders": r.get("seeders"),
                "leechers": r.get("leechers"),
                "publish_date": r.get("publishDate", ""),
                "approved": r.get("approved", False),
                "rejections": r.get("rejections", []),
                "release_group": r.get("releaseGroup", ""),
            }
        )
    return out


@router.post("/api/grab/download")
async def grab_download(request: Request) -> JSONResponse:
    """Tell Sonarr or Radarr to grab a specific release.

    Body JSON: { guid, indexer_id, service ("sonarr"|"radarr") }
    """
    config = request.app.state.config

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    guid: str = body.get("guid", "")
    indexer_id: int = int(body.get("indexer_id", 0))
    service: str = body.get("service", "sonarr")

    if not guid:
        return JSONResponse({"error": "guid is required"}, status_code=400)

    if service == "radarr":
        if not config.radarr.url or not config.radarr.api_key:
            return JSONResponse({"error": "Radarr not configured"}, status_code=503)
        client: SonarrClient | RadarrClient = RadarrClient(
            url=config.radarr.url, api_key=config.radarr.api_key
        )
    else:
        if not config.sonarr.url or not config.sonarr.api_key:
            return JSONResponse({"error": "Sonarr not configured"}, status_code=503)
        client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)

    try:
        result = await client.grab_release(guid=guid, indexer_id=indexer_id)
        logger.info("Grabbed release via %s: guid=%s", service, guid[:40])
        return JSONResponse({"ok": True, "title": result.get("title", "")})
    except Exception as exc:
        logger.error("%s grab failed: %s", service, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await client.close()
