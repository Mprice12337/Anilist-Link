"""Sonarr sync routes — add/manage AniList→Sonarr series mappings."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.Clients.RadarrClient import RadarrClient
from src.Clients.SonarrClient import SonarrClient
from src.Download.MappingResolver import MappingResolver

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sonarr-sync"])


# ------------------------------------------------------------------
# Sonarr status & config
# ------------------------------------------------------------------


@router.get("/api/sonarr/status")
async def sonarr_status(request: Request) -> JSONResponse:
    """Return Sonarr connection status."""
    config = request.app.state.config
    if not config.sonarr.url or not config.sonarr.api_key:
        return JSONResponse({"configured": False})

    client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
    try:
        info = await client.test_connection()
        return JSONResponse({"configured": True, "ok": True, "info": info})
    except Exception as exc:
        return JSONResponse({"configured": True, "ok": False, "error": str(exc)})
    finally:
        await client.close()


@router.get("/api/sonarr/quality-profiles")
async def sonarr_quality_profiles(request: Request) -> JSONResponse:
    """Return Sonarr quality profiles."""
    config = request.app.state.config
    if not config.sonarr.url or not config.sonarr.api_key:
        return JSONResponse({"error": "Sonarr not configured"}, status_code=503)

    client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
    try:
        profiles = await client.get_quality_profiles()
        return JSONResponse({"profiles": profiles})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await client.close()


@router.get("/api/sonarr/root-folders")
async def sonarr_root_folders(request: Request) -> JSONResponse:
    """Return Sonarr root folders."""
    config = request.app.state.config
    if not config.sonarr.url or not config.sonarr.api_key:
        return JSONResponse({"error": "Sonarr not configured"}, status_code=503)

    client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
    try:
        folders = await client.get_root_folders()
        return JSONResponse({"folders": folders})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await client.close()


# ------------------------------------------------------------------
# Radarr status & config
# ------------------------------------------------------------------


@router.get("/api/radarr/status")
async def radarr_status(request: Request) -> JSONResponse:
    """Return Radarr connection status."""
    config = request.app.state.config
    if not config.radarr.url or not config.radarr.api_key:
        return JSONResponse({"configured": False})

    client = RadarrClient(url=config.radarr.url, api_key=config.radarr.api_key)
    try:
        info = await client.test_connection()
        return JSONResponse({"configured": True, "ok": True, "info": info})
    except Exception as exc:
        return JSONResponse({"configured": True, "ok": False, "error": str(exc)})
    finally:
        await client.close()


@router.get("/api/radarr/quality-profiles")
async def radarr_quality_profiles(request: Request) -> JSONResponse:
    """Return Radarr quality profiles."""
    config = request.app.state.config
    if not config.radarr.url or not config.radarr.api_key:
        return JSONResponse({"error": "Radarr not configured"}, status_code=503)

    client = RadarrClient(url=config.radarr.url, api_key=config.radarr.api_key)
    try:
        profiles = await client.get_quality_profiles()
        return JSONResponse({"profiles": profiles})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await client.close()


@router.get("/api/radarr/root-folders")
async def radarr_root_folders(request: Request) -> JSONResponse:
    """Return Radarr root folders."""
    config = request.app.state.config
    if not config.radarr.url or not config.radarr.api_key:
        return JSONResponse({"error": "Radarr not configured"}, status_code=503)

    client = RadarrClient(url=config.radarr.url, api_key=config.radarr.api_key)
    try:
        folders = await client.get_root_folders()
        return JSONResponse({"folders": folders})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await client.close()


# ------------------------------------------------------------------
# Add to Sonarr/Radarr
# ------------------------------------------------------------------


@router.post("/api/arr/add")
async def arr_add(request: Request) -> JSONResponse:
    """Add an AniList entry to Sonarr or Radarr.

    Body JSON:
      anilist_id, anilist_format, title, quality_profile_id,
      root_folder_path, monitored (bool), monitor_strategy,
      search_immediately (bool)
    """
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    anilist_id: int = int(body.get("anilist_id", 0))
    if not anilist_id:
        return JSONResponse({"error": "anilist_id is required"}, status_code=400)

    anilist_format: str = body.get("anilist_format", "TV")
    title: str = body.get("title", "")
    quality_profile_id: int = int(body.get("quality_profile_id", 1))
    root_folder_path: str = body.get("root_folder_path", "")
    monitored: bool = bool(body.get("monitored", True))
    monitor_strategy: str = body.get("monitor_strategy", "future")
    search_immediately: bool = bool(body.get("search_immediately", False))

    sonarr_client: SonarrClient | None = None
    radarr_client: RadarrClient | None = None

    if config.sonarr.url and config.sonarr.api_key:
        sonarr_client = SonarrClient(
            url=config.sonarr.url, api_key=config.sonarr.api_key
        )
    if config.radarr.url and config.radarr.api_key:
        radarr_client = RadarrClient(
            url=config.radarr.url, api_key=config.radarr.api_key
        )

    resolver = MappingResolver(
        db=db,
        anilist_client=anilist_client,
        sonarr_client=sonarr_client,
        radarr_client=radarr_client,
    )

    media: dict[str, Any] = {
        "title": {"romaji": title},
        "synonyms": [],
    }

    try:
        result = await resolver.resolve_and_add(
            anilist_id=anilist_id,
            anilist_format=anilist_format,
            anilist_media=media,
            quality_profile_id=quality_profile_id,
            root_folder_path=root_folder_path,
            monitored=monitored,
            monitor_strategy=monitor_strategy,
            search_immediately=search_immediately,
        )
        if result.ok:
            return JSONResponse(
                {
                    "ok": True,
                    "service": result.service,
                    "external_id": result.external_id,
                    "arr_id": result.arr_id,
                }
            )
        else:
            return JSONResponse({"ok": False, "error": result.error}, status_code=422)
    except Exception as exc:
        logger.exception("arr_add failed for anilist_id=%d", anilist_id)
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        if sonarr_client:
            await sonarr_client.close()
        if radarr_client:
            await radarr_client.close()


# ------------------------------------------------------------------
# Mapping inspection
# ------------------------------------------------------------------


@router.get("/api/arr/mappings")
async def arr_mappings(request: Request) -> JSONResponse:
    """Return all Sonarr and Radarr mappings."""
    db = request.app.state.db

    sonarr_rows = await db.fetch_all(
        "SELECT * FROM anilist_sonarr_mapping ORDER BY updated_at DESC"
    )
    radarr_rows = await db.fetch_all(
        "SELECT * FROM anilist_radarr_mapping ORDER BY updated_at DESC"
    )

    return JSONResponse(
        {
            "sonarr": sonarr_rows,
            "radarr": radarr_rows,
            "sonarr_count": len(sonarr_rows),
            "radarr_count": len(radarr_rows),
        }
    )


@router.get("/api/arr/mapping/{anilist_id}")
async def arr_mapping_detail(request: Request, anilist_id: int) -> JSONResponse:
    """Return the Sonarr/Radarr mapping for a specific AniList ID."""
    db = request.app.state.db

    sonarr_row = await db.fetch_one(
        "SELECT * FROM anilist_sonarr_mapping WHERE anilist_id=?",
        (anilist_id,),
    )
    radarr_row = await db.fetch_one(
        "SELECT * FROM anilist_radarr_mapping WHERE anilist_id=?",
        (anilist_id,),
    )

    return JSONResponse(
        {
            "anilist_id": anilist_id,
            "sonarr": sonarr_row,
            "radarr": radarr_row,
        }
    )
