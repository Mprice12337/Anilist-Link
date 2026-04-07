"""P4 Download Manager routes — Sonarr/Radarr add requests."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.Clients.RadarrClient import RadarrClient
from src.Clients.SonarrClient import SonarrClient
from src.Download.ArrPostProcessor import ArrPostProcessor
from src.Download.DownloadManager import DownloadManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["downloads"])


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@router.get("/download", response_class=HTMLResponse)
async def download_page(request: Request) -> HTMLResponse:
    """Render the P4 download manager page."""
    db = request.app.state.db
    templates = request.app.state.templates
    config = request.app.state.config

    recent = await db.get_download_requests(limit=50)

    sonarr_configured = bool(config.sonarr.url and config.sonarr.api_key)
    radarr_configured = bool(config.radarr.url and config.radarr.api_key)

    return templates.TemplateResponse(
        "download_manager.html",
        {
            "request": request,
            "recent_requests": recent,
            "sonarr_configured": sonarr_configured,
            "radarr_configured": radarr_configured,
        },
    )


# ---------------------------------------------------------------------------
# API — search
# ---------------------------------------------------------------------------


@router.get("/api/download/search")
async def search_anilist(request: Request, q: str = "") -> JSONResponse:
    """Search AniList for anime matching *q* and return with TVDB/TMDB hints."""
    if not q or len(q) < 2:
        return JSONResponse({"results": []})

    anilist_client = request.app.state.anilist_client
    try:
        results = await anilist_client.search_anime(q, per_page=15)
    except Exception as exc:
        logger.warning("AniList search failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    items: list[dict[str, Any]] = []
    for media in results:
        title_obj = media.get("title", {})
        title = (
            title_obj.get("english")
            or title_obj.get("romaji")
            or title_obj.get("native")
            or ""
        )
        fmt = media.get("format", "")
        # Determine which service this would go to
        movie_formats = {"MOVIE", "SPECIAL", "MUSIC"}
        suggested_service = "radarr" if fmt in movie_formats else "sonarr"

        cover = (media.get("coverImage") or {}).get("medium", "")
        items.append(
            {
                "id": media.get("id"),
                "title": title,
                "format": fmt,
                "episodes": media.get("episodes"),
                "year": (media.get("startDate") or {}).get("year"),
                "status": media.get("status", ""),
                "cover": cover,
                "suggested_service": suggested_service,
            }
        )

    return JSONResponse({"results": items})


# ---------------------------------------------------------------------------
# API — quality profiles & root folders
# ---------------------------------------------------------------------------


@router.get("/api/download/sonarr/options")
async def sonarr_options(request: Request) -> JSONResponse:
    """Return Sonarr quality profiles and root folders."""
    config = request.app.state.config
    if not config.sonarr.url or not config.sonarr.api_key:
        return JSONResponse(
            {"ok": False, "error": "Sonarr not configured"}, status_code=400
        )

    client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
    try:
        profiles = await client.get_quality_profiles()
        folders = await client.get_root_folders()
        await client.close()
        return JSONResponse(
            {
                "ok": True,
                "quality_profiles": [
                    {"id": p["id"], "name": p["name"]} for p in profiles
                ],
                "root_folders": [{"id": f["id"], "path": f["path"]} for f in folders],
            }
        )
    except Exception as exc:
        await client.close()
        logger.warning("Failed to fetch Sonarr options: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/api/download/radarr/options")
async def radarr_options(request: Request) -> JSONResponse:
    """Return Radarr quality profiles and root folders."""
    config = request.app.state.config
    if not config.radarr.url or not config.radarr.api_key:
        return JSONResponse(
            {"ok": False, "error": "Radarr not configured"}, status_code=400
        )

    client = RadarrClient(url=config.radarr.url, api_key=config.radarr.api_key)
    try:
        profiles = await client.get_quality_profiles()
        folders = await client.get_root_folders()
        await client.close()
        return JSONResponse(
            {
                "ok": True,
                "quality_profiles": [
                    {"id": p["id"], "name": p["name"]} for p in profiles
                ],
                "root_folders": [{"id": f["id"], "path": f["path"]} for f in folders],
            }
        )
    except Exception as exc:
        await client.close()
        logger.warning("Failed to fetch Radarr options: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# API — reprocess & root folder migration
# ---------------------------------------------------------------------------


@router.post("/api/download/sonarr/{sonarr_id}/reprocess")
async def reprocess_sonarr(
    sonarr_id: int, request: Request, dry_run: bool = False
) -> JSONResponse:
    """Restructure all existing files for a Sonarr series into AniList subfolders."""
    config = request.app.state.config
    if not config.sonarr.url or not config.sonarr.api_key:
        return JSONResponse(
            {"ok": False, "error": "Sonarr not configured"}, status_code=400
        )
    processor = ArrPostProcessor(db=request.app.state.db, config=config)
    result = await processor.reprocess_sonarr_series(sonarr_id, dry_run=dry_run)
    status = 200 if result.get("ok") else 500
    return JSONResponse(result, status_code=status)


@router.post("/api/download/radarr/{radarr_id}/reprocess")
async def reprocess_radarr(
    radarr_id: int, request: Request, dry_run: bool = False
) -> JSONResponse:
    """Restructure the existing file for a Radarr movie into its AniList subfolder."""
    config = request.app.state.config
    if not config.radarr.url or not config.radarr.api_key:
        return JSONResponse(
            {"ok": False, "error": "Radarr not configured"}, status_code=400
        )
    processor = ArrPostProcessor(db=request.app.state.db, config=config)
    result = await processor.reprocess_radarr_movie(radarr_id, dry_run=dry_run)
    status = 200 if result.get("ok") else 500
    return JSONResponse(result, status_code=status)


@router.post("/api/download/sonarr/migrate-root-folder")
async def migrate_sonarr_root_folder(request: Request) -> JSONResponse:
    """Move all tracked Sonarr series to the configured anime root folder."""
    config = request.app.state.config
    if not config.sonarr.url or not config.sonarr.api_key:
        return JSONResponse(
            {"ok": False, "error": "Sonarr not configured"}, status_code=400
        )
    if not config.sonarr.anime_root_folder:
        return JSONResponse(
            {"ok": False, "error": "sonarr.anime_root_folder not configured"},
            status_code=400,
        )

    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT DISTINCT sonarr_id FROM anilist_sonarr_mapping WHERE in_sonarr=1"
    )
    if not rows:
        return JSONResponse({"ok": True, "moved": 0, "skipped": 0, "errors": 0})

    client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
    target_root = config.sonarr.anime_root_folder.rstrip("/")
    details: list[dict[str, Any]] = []
    try:
        for row in rows:
            sonarr_id: int = row["sonarr_id"]
            try:
                series = await client.get_series_by_id(sonarr_id)
                if not series:
                    details.append(
                        {
                            "id": sonarr_id,
                            "title": "?",
                            "status": "not_found",
                            "from": "",
                        }
                    )
                    continue
                title: str = series.get("title", str(sonarr_id))
                current_root = series.get("rootFolderPath", "").rstrip("/")
                if current_root == target_root:
                    details.append(
                        {
                            "id": sonarr_id,
                            "title": title,
                            "status": "skipped",
                            "from": current_root,
                        }
                    )
                    continue
                await client.move_series_root_folder(sonarr_id, target_root)
                details.append(
                    {
                        "id": sonarr_id,
                        "title": title,
                        "status": "moved",
                        "from": current_root,
                    }
                )
                logger.info(
                    "Moved Sonarr series %d (%s) %s → %s",
                    sonarr_id,
                    title,
                    current_root,
                    target_root,
                )
            except Exception as exc:
                logger.error("Failed to migrate sonarr_id=%d: %s", sonarr_id, exc)
                details.append(
                    {
                        "id": sonarr_id,
                        "title": "?",
                        "status": "error",
                        "from": "",
                        "error": str(exc),
                    }
                )
    finally:
        await client.close()

    moved = sum(1 for d in details if d["status"] == "moved")
    skipped = sum(1 for d in details if d["status"] == "skipped")
    errors = sum(1 for d in details if d["status"] in ("error", "not_found"))
    return JSONResponse(
        {
            "ok": True,
            "moved": moved,
            "skipped": skipped,
            "errors": errors,
            "target": target_root,
            "details": details,
        }
    )


@router.post("/api/download/radarr/migrate-root-folder")
async def migrate_radarr_root_folder(request: Request) -> JSONResponse:
    """Move all tracked Radarr movies to the configured anime root folder."""
    config = request.app.state.config
    if not config.radarr.url or not config.radarr.api_key:
        return JSONResponse(
            {"ok": False, "error": "Radarr not configured"}, status_code=400
        )
    if not config.radarr.anime_root_folder:
        return JSONResponse(
            {"ok": False, "error": "radarr.anime_root_folder not configured"},
            status_code=400,
        )

    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT DISTINCT radarr_id FROM anilist_radarr_mapping WHERE in_radarr=1"
    )
    if not rows:
        return JSONResponse({"ok": True, "moved": 0, "skipped": 0, "errors": 0})

    client = RadarrClient(url=config.radarr.url, api_key=config.radarr.api_key)
    target_root = config.radarr.anime_root_folder.rstrip("/")
    details: list[dict[str, Any]] = []
    try:
        for row in rows:
            radarr_id: int = row["radarr_id"]
            try:
                movie = await client.get_movie_by_id(radarr_id)
                if not movie:
                    details.append(
                        {
                            "id": radarr_id,
                            "title": "?",
                            "status": "not_found",
                            "from": "",
                        }
                    )
                    continue
                title: str = movie.get("title", str(radarr_id))
                current_root = str(movie.get("rootFolderPath", "")).rstrip("/")
                if current_root == target_root:
                    details.append(
                        {
                            "id": radarr_id,
                            "title": title,
                            "status": "skipped",
                            "from": current_root,
                        }
                    )
                    continue
                await client.move_movie_root_folder(radarr_id, target_root)
                details.append(
                    {
                        "id": radarr_id,
                        "title": title,
                        "status": "moved",
                        "from": current_root,
                    }
                )
                logger.info(
                    "Moved Radarr movie %d (%s) %s → %s",
                    radarr_id,
                    title,
                    current_root,
                    target_root,
                )
            except Exception as exc:
                logger.error("Failed to migrate radarr_id=%d: %s", radarr_id, exc)
                details.append(
                    {
                        "id": radarr_id,
                        "title": "?",
                        "status": "error",
                        "from": "",
                        "error": str(exc),
                    }
                )
    finally:
        await client.close()

    moved = sum(1 for d in details if d["status"] == "moved")
    skipped = sum(1 for d in details if d["status"] == "skipped")
    errors = sum(1 for d in details if d["status"] in ("error", "not_found"))
    return JSONResponse(
        {
            "ok": True,
            "moved": moved,
            "skipped": skipped,
            "errors": errors,
            "target": target_root,
            "details": details,
        }
    )


# ---------------------------------------------------------------------------
# API — add requests
# ---------------------------------------------------------------------------


@router.post("/api/download/add")
async def add_download(request: Request) -> JSONResponse:
    """Add an AniList entry to Sonarr or Radarr.

    Expects JSON body:
      {
        "anilist_id": 123,
        "service": "sonarr" | "radarr",
        "quality_profile_id": 1,
        "root_folder": "/data/anime",
        "monitored": true
      }
    """
    body = await request.json()
    anilist_id: int = int(body.get("anilist_id", 0))
    service: str = str(body.get("service", "")).lower()
    quality_profile_id: int = int(body.get("quality_profile_id", 0))
    root_folder: str = str(body.get("root_folder", "")).strip()
    monitored: bool = bool(body.get("monitored", True))
    monitor_strategy: str = str(body.get("monitor_strategy", "future")).strip()
    search_immediately: bool = bool(body.get("search_immediately", False))

    if (
        not anilist_id
        or service not in ("sonarr", "radarr")
        or not quality_profile_id
        or not root_folder
    ):
        return JSONResponse(
            {
                "ok": False,
                "error": (
                    "anilist_id, service, quality_profile_id,"
                    " and root_folder are required"
                ),
            },
            status_code=400,
        )

    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client
    manager = DownloadManager(db=db, anilist_client=anilist_client)

    if service == "sonarr":
        if not config.sonarr.url or not config.sonarr.api_key:
            return JSONResponse(
                {"ok": False, "error": "Sonarr not configured"}, status_code=400
            )
        sonarr_client = SonarrClient(
            url=config.sonarr.url, api_key=config.sonarr.api_key
        )
        try:
            result = await manager.add_to_sonarr(
                anilist_id=anilist_id,
                sonarr_client=sonarr_client,
                quality_profile_id=quality_profile_id,
                root_folder_path=root_folder,
                monitored=monitored,
                monitor_strategy=monitor_strategy,
                search_immediately=search_immediately,
            )
        finally:
            await sonarr_client.close()
    else:
        if not config.radarr.url or not config.radarr.api_key:
            return JSONResponse(
                {"ok": False, "error": "Radarr not configured"}, status_code=400
            )
        radarr_client = RadarrClient(
            url=config.radarr.url, api_key=config.radarr.api_key
        )
        try:
            result = await manager.add_to_radarr(
                anilist_id=anilist_id,
                radarr_client=radarr_client,
                quality_profile_id=quality_profile_id,
                root_folder_path=root_folder,
                monitored=monitored,
                search_immediately=search_immediately,
            )
        finally:
            await radarr_client.close()

    return JSONResponse(
        {
            "ok": result.ok,
            "status": result.status,
            "service": result.service,
            "anilist_title": result.anilist_title,
            "external_id": result.external_id,
            "tvdb_id": result.tvdb_id,
            "tmdb_id": result.tmdb_id,
            "error": result.error,
            "download_request_id": result.download_request_id,
        }
    )
