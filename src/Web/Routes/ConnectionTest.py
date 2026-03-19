"""Connection test endpoints and filesystem browser for Phase A."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.Clients.JellyfinClient import JellyfinClient
from src.Clients.PlexClient import PlexClient
from src.Clients.RadarrClient import RadarrClient
from src.Clients.SonarrClient import SonarrClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["connection-test"])


# ---------------------------------------------------------------------------
# Bulk settings save (used by onboarding service tabs)
# ---------------------------------------------------------------------------


@router.post("/api/settings/bulk")
async def bulk_save_settings(request: Request) -> JSONResponse:
    """Save multiple app_settings key/value pairs in one call."""
    body = await request.json()
    settings: dict[str, str] = body.get("settings") or {}
    if not settings:
        return JSONResponse(
            {"ok": False, "error": "No settings provided"}, status_code=400
        )

    db = request.app.state.db
    for key, value in settings.items():
        await db.set_setting(str(key), str(value))

    return JSONResponse({"ok": True, "saved": len(settings)})


# ---------------------------------------------------------------------------
# Filesystem browser
# ---------------------------------------------------------------------------


@router.post("/api/fs/mkdir")
async def fs_mkdir(request: Request) -> JSONResponse:
    """Create a new directory inside *parent* and return its path."""
    body = await request.json()
    parent = (body.get("parent") or "").strip()
    name = (body.get("name") or "").strip()

    if not parent or not name:
        return JSONResponse({"error": "parent and name are required"}, status_code=400)

    # Reject names with path separators or traversal attempts
    if "/" in name or "\\" in name or name in (".", ".."):
        return JSONResponse({"error": "Invalid folder name"}, status_code=400)

    target = Path(parent).resolve() / name
    if target.exists():
        return JSONResponse({"error": f"'{name}' already exists"}, status_code=409)

    try:
        target.mkdir(parents=False)
    except PermissionError:
        return JSONResponse(
            {"error": f"Permission denied creating directory in: {parent}"},
            status_code=403,
        )
    except OSError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    return JSONResponse({"ok": True, "path": str(target)})


@router.get("/api/fs/browse")
async def fs_browse(path: str = "/data") -> JSONResponse:
    """Return immediate child directories of *path*.

    The response is a JSON object:
      {
        "path": "/data",
        "parent": "/",
        "dirs": [{"name": "anime", "path": "/data/anime"}, ...]
      }
    """
    target = Path(path).resolve()

    if not target.exists() or not target.is_dir():
        return JSONResponse(
            {"error": f"Path not found or not a directory: {path}"}, status_code=404
        )

    dirs: list[dict[str, str]] = []
    try:
        for entry in sorted(target.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                dirs.append({"name": entry.name, "path": str(entry)})
    except PermissionError:
        return JSONResponse(
            {"error": f"Permission denied reading: {path}"}, status_code=403
        )

    parent = str(target.parent) if target != target.parent else str(target)

    return JSONResponse(
        {
            "path": str(target),
            "parent": parent,
            "dirs": dirs,
        }
    )


# ---------------------------------------------------------------------------
# Global progress API
# ---------------------------------------------------------------------------


@router.get("/api/progress")
async def get_progress(request: Request) -> JSONResponse:
    """Return all active background task states.

    Each task dict has at minimum:
      { "id": str, "type": str, "label": str, "status": str,
        "percent": int, "detail": str }
    """
    app_state = request.app.state
    tasks: list[dict[str, Any]] = []

    # Restructure analysis
    progress = getattr(app_state, "restructure_progress", None)
    if progress and progress.status not in ("", "pending", "complete", "error"):
        total = getattr(progress, "total", 0)
        done = getattr(progress, "processed", 0)
        pct = int(done / total * 100) if total else 0
        tasks.append(
            {
                "id": "restructure_analysis",
                "type": "Restructure",
                "label": getattr(progress, "phase", "Analysing library…")
                or "Analysing library…",
                "status": progress.status,
                "percent": pct,
                "detail": getattr(progress, "current_item", ""),
            }
        )

    # Restructure execution
    exec_progress = getattr(app_state, "restructure_exec_progress", None)
    if exec_progress and exec_progress.status not in (
        "",
        "pending",
        "complete",
        "error",
    ):
        total = getattr(exec_progress, "total", 0)
        done = getattr(
            exec_progress, "processed", 0
        )  # RestructureProgress uses .processed
        pct = int(done / total * 100) if total else 0
        tasks.append(
            {
                "id": "restructure_exec",
                "type": "Restructure",
                "label": getattr(exec_progress, "phase", "Restructuring files…")
                or "Restructuring files…",
                "status": exec_progress.status,
                "percent": pct,
                "detail": (
                    f"{done}/{total}" if total else getattr(exec_progress, "phase", "")
                ),
            }
        )

    # Plex metadata scan
    scan_progress = getattr(app_state, "scan_progress", None)
    if scan_progress and getattr(scan_progress, "status", "") not in (
        "",
        "complete",
        "error",
    ):
        total = getattr(scan_progress, "total", 0)
        done = getattr(scan_progress, "scanned", 0)
        pct = int(done / total * 100) if total else 0
        tasks.append(
            {
                "id": "plex_scan",
                "type": "Metadata Scan",
                "label": f"Plex: {getattr(scan_progress, 'library_title', 'scan')}",
                "status": scan_progress.status,
                "percent": pct,
                "detail": f"{done}/{total}",
            }
        )

    # Jellyfin metadata scan
    jf_scan = getattr(app_state, "jellyfin_scan_progress", None)
    if jf_scan and getattr(jf_scan, "status", "") not in ("", "complete", "error"):
        total = getattr(jf_scan, "total", 0)
        done = getattr(jf_scan, "scanned", 0)
        pct = int(done / total * 100) if total else 0
        tasks.append(
            {
                "id": "jellyfin_scan",
                "type": "Metadata Scan",
                "label": f"Jellyfin: {getattr(jf_scan, 'library_title', 'scan')}",
                "status": jf_scan.status,
                "percent": pct,
                "detail": f"{done}/{total}",
            }
        )

    # Library scanner (local directory)
    # May be stored as a dict {library_id: LibraryScanProgress} (from onboarding)
    # or as a single LibraryScanProgress object (from direct library scan routes).
    lib_scan_raw = getattr(app_state, "library_scan_progress", None)
    lib_scan_candidates: list[Any] = []
    if isinstance(lib_scan_raw, dict):
        lib_scan_candidates = list(lib_scan_raw.values())
    elif lib_scan_raw is not None:
        lib_scan_candidates = [lib_scan_raw]

    for i, lib_scan in enumerate(lib_scan_candidates):
        status = getattr(lib_scan, "status", "")
        if status not in ("", "complete", "error"):
            total = getattr(lib_scan, "total", 0)
            done = getattr(lib_scan, "processed", 0)
            pct = int(done / total * 100) if total else 0
            tasks.append(
                {
                    "id": f"library_scan_{i}",
                    "type": "Library Scan",
                    "label": getattr(lib_scan, "phase", "Library scan")
                    or "Library scan",
                    "status": status,
                    "percent": pct,
                    "detail": (
                        f"{done}/{total}"
                        if total
                        else getattr(lib_scan, "current_item", "")
                    ),
                }
            )

    # Crunchyroll preview scan
    cr_preview = getattr(app_state, "cr_preview_progress", None)
    if cr_preview and cr_preview.status == "scanning":
        page = getattr(cr_preview, "current_page", 0)
        max_pages = getattr(cr_preview, "max_pages", 0)
        pct = int(page / max_pages * 100) if max_pages else 0
        tasks.append(
            {
                "id": "cr_preview_scan",
                "type": "CR Preview",
                "label": "Crunchyroll preview scan",
                "status": f"Page {page}" + (f"/{max_pages}" if max_pages else ""),
                "percent": pct,
                "detail": f"{getattr(cr_preview, 'entries_found', 0)} entries found",
            }
        )

    return JSONResponse({"tasks": tasks})


# ---------------------------------------------------------------------------
# Connection test endpoints
# ---------------------------------------------------------------------------


@router.post("/api/test/plex")
async def test_plex(request: Request) -> JSONResponse:
    """Validate Plex URL + token. Returns server name, libraries, PlexPass status."""
    body = await request.json()
    url: str = (body.get("url") or "").strip()
    token: str = (body.get("token") or "").strip()

    if not url or not token:
        return JSONResponse(
            {"ok": False, "error": "url and token are required"}, status_code=400
        )

    try:
        client = PlexClient(url=url, token=token)
        server_name = await client.test_connection()
        libraries = await client.get_libraries()

        # Check PlexPass via Plex.tv account API
        has_plexpass = False
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                resp = await http.get(
                    "https://plex.tv/api/v2/user",
                    headers={
                        "X-Plex-Token": token,
                        "X-Plex-Client-Identifier": "anilist-link",
                        "Accept": "application/json",
                    },
                )
                if resp.status_code == 200:
                    user_data = resp.json()
                    subscription = user_data.get("subscription") or {}
                    logger.debug("Plex subscription payload: %s", subscription)
                    # Plex returns any combination of these depending on account type.
                    # "lifetime" plan is also a valid PlexPass subscription.
                    plan = str(subscription.get("plan", "")).lower()
                    has_plexpass = bool(
                        subscription.get("active")
                        or str(subscription.get("status", "")).lower() == "active"
                        or plan in ("plexpass", "lifetime")
                    )
                else:
                    logger.warning("Plex.tv /api/v2/user returned %s", resp.status_code)
        except Exception:
            logger.warning("Could not fetch PlexPass status from plex.tv")

        # Store PlexPass + connected status in DB
        db = request.app.state.db
        await db.set_setting("plex.has_plexpass", "true" if has_plexpass else "false")
        await db.set_setting("plex.connected", "true")

        await client.close()

        return JSONResponse(
            {
                "ok": True,
                "server_name": server_name,
                "has_plexpass": has_plexpass,
                "libraries": [
                    {"key": lib.key, "title": lib.title, "type": lib.type}
                    for lib in libraries
                ],
            }
        )
    except Exception as exc:
        logger.warning("Plex connection test failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)


@router.post("/api/test/jellyfin")
async def test_jellyfin(request: Request) -> JSONResponse:
    """Validate Jellyfin URL + API key. Returns server name and libraries."""
    body = await request.json()
    url: str = (body.get("url") or "").strip()
    api_key: str = (body.get("api_key") or "").strip()

    if not url or not api_key:
        return JSONResponse(
            {"ok": False, "error": "url and api_key are required"}, status_code=400
        )

    try:
        client = JellyfinClient(url=url, api_key=api_key)
        server_name = await client.test_connection()
        libraries = await client.get_libraries()
        await client.close()

        db = request.app.state.db
        await db.set_setting("jellyfin.connected", "true")

        return JSONResponse(
            {
                "ok": True,
                "server_name": server_name,
                "libraries": [
                    {"id": lib.id, "name": lib.name, "type": lib.type}
                    for lib in libraries
                ],
            }
        )
    except Exception as exc:
        logger.warning("Jellyfin connection test failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)


@router.post("/api/test/anilist")
async def test_anilist(request: Request) -> JSONResponse:
    """Validate AniList client credentials by attempting a public API call."""
    body = await request.json()
    client_id: str = (body.get("client_id") or "").strip()

    if not client_id:
        return JSONResponse(
            {"ok": False, "error": "client_id is required"}, status_code=400
        )

    # Validate by hitting the public AniList GraphQL endpoint
    query = "{ Viewer { id name } }"
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                "https://graphql.anilist.co",
                json={"query": query},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        # 200/400/401 = API reachable (401 = no token, expected for public ping).
        # 429 = rate limited — the API is definitely reachable, just busy right now
        # (often triggered by a background library scan running in parallel).
        reachable = resp.status_code in (200, 400, 401, 429)
        if reachable:
            db = request.app.state.db
            await db.set_setting("anilist.connected", "true")
            note = (
                "AniList API reachable. Credentials will be validated "
                "when a user completes OAuth."
            )
            if resp.status_code == 429:
                note = (
                    "AniList API reachable (rate limited — background scan running)."
                    " Credentials validated via OAuth."
                )
            return JSONResponse({"ok": True, "note": note})
        return JSONResponse(
            {"ok": False, "error": f"AniList returned {resp.status_code}"}
        )
    except Exception as exc:
        logger.warning("AniList connection test failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)


@router.post("/api/test/sonarr")
async def test_sonarr(request: Request) -> JSONResponse:
    """Validate Sonarr URL + API key. Returns version info."""
    body = await request.json()
    url: str = (body.get("url") or "").strip()
    api_key: str = (body.get("api_key") or "").strip()

    if not url or not api_key:
        return JSONResponse(
            {"ok": False, "error": "url and api_key are required"}, status_code=400
        )

    client = SonarrClient(url=url, api_key=api_key)
    try:
        status = await client.test_connection()
        await client.close()
        db = request.app.state.db
        await db.set_setting("sonarr.url", url)
        await db.set_setting("sonarr.api_key", api_key)
        await db.set_setting("sonarr.connected", "true")
        return JSONResponse({"ok": True, **status})
    except Exception as exc:
        await client.close()
        logger.warning("Sonarr connection test failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)


@router.post("/api/test/radarr")
async def test_radarr(request: Request) -> JSONResponse:
    """Validate Radarr URL + API key. Returns version info."""
    body = await request.json()
    url: str = (body.get("url") or "").strip()
    api_key: str = (body.get("api_key") or "").strip()

    if not url or not api_key:
        return JSONResponse(
            {"ok": False, "error": "url and api_key are required"}, status_code=400
        )

    client = RadarrClient(url=url, api_key=api_key)
    try:
        status = await client.test_connection()
        await client.close()
        db = request.app.state.db
        await db.set_setting("radarr.url", url)
        await db.set_setting("radarr.api_key", api_key)
        await db.set_setting("radarr.connected", "true")
        return JSONResponse({"ok": True, **status})
    except Exception as exc:
        await client.close()
        logger.warning("Radarr connection test failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)


@router.post("/api/test/crunchyroll")
async def test_crunchyroll(request: Request) -> JSONResponse:
    """Smoke-test Crunchyroll credentials (basic reachability check).

    Full browser-based auth is a long-running operation handled separately.
    This endpoint just verifies the FlareSolverr URL is reachable if provided.
    """
    body = await request.json()
    email: str = (body.get("email") or "").strip()
    password: str = body.get("password") or ""
    flaresolverr_url: str = (body.get("flaresolverr_url") or "").strip()

    if not email:
        return JSONResponse(
            {"ok": False, "error": "email is required"}, status_code=400
        )

    if flaresolverr_url:
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                resp = await http.get(flaresolverr_url)
            if resp.status_code not in (200, 405):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": f"FlareSolverr returned {resp.status_code}",
                    }
                )
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"FlareSolverr unreachable: {exc}"}
            )

    db = request.app.state.db
    await db.set_setting("crunchyroll.email", email)
    if password:
        await db.set_setting("crunchyroll.password", password)
    if flaresolverr_url:
        await db.set_setting("crunchyroll.flaresolverr_url", flaresolverr_url)
    await db.set_setting("crunchyroll.connected", "true")
    return JSONResponse(
        {
            "ok": True,
            "note": (
                "Credentials accepted. Full authentication runs in the background "
                "when you start a Crunchyroll scan."
            ),
        }
    )
