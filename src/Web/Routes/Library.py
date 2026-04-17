"""Library Manager routes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import Response

from src.Clients.JellyfinClient import JellyfinClient
from src.Clients.PlexClient import PlexClient
from src.Matching.TitleMatcher import TitleMatcher, get_primary_title
from src.Scanner.JellyfinMetadataScanner import JellyfinMetadataScanner
from src.Scanner.LibraryRestructurer import LibraryRestructurer, RestructureProgress
from src.Scanner.LibraryScanner import LibraryScanner, LibraryScanProgress
from src.Scanner.LocalDirectoryScanner import LocalDirectoryScanner
from src.Scanner.MetadataScanner import MetadataScanner, ScanProgress
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Web.App import spawn_background_task

logger = logging.getLogger(__name__)

router = APIRouter(tags=["library"])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _get_scan_progress(app_state: Any) -> dict[int, LibraryScanProgress]:
    """Return the library_scan_progress dict, creating it if needed."""
    if not hasattr(app_state, "library_scan_progress"):
        app_state.library_scan_progress = {}
    return app_state.library_scan_progress


def _is_library_scan_busy(app_state: Any, library_id: int) -> bool:
    progress_map = _get_scan_progress(app_state)
    p = progress_map.get(library_id)
    return p is not None and p.status not in (
        "",
        "pending",
        "complete",
        "error",
        "cancelled",
    )


async def _run_library_scan(
    app_state: object, library_id: int, paths: list[str], force_rescan: bool
) -> None:
    """Background coroutine: scan a library."""
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]
    progress_map = _get_scan_progress(app_state)
    progress = progress_map[library_id]

    title_matcher = TitleMatcher(similarity_threshold=0.75)
    scanner = LibraryScanner(
        db=db, anilist_client=anilist_client, title_matcher=title_matcher
    )

    try:
        stats = await scanner.scan_library(
            library_id, paths, progress, force_rescan=force_rescan
        )
        progress.phase = (
            f"Done: {stats['matched']} matched, {stats['unmatched']} unmatched"
        )
    except asyncio.CancelledError:
        logger.info("Library scan cancelled for library %d", library_id)
        progress.status = "cancelled"
        progress.phase = "Cancelled by user"
        raise
    except Exception:
        logger.exception("Library scan failed for library %d", library_id)
        progress.status = "error"
        progress.error_message = "Scan failed unexpectedly"


# ------------------------------------------------------------------
# Page endpoints
# ------------------------------------------------------------------


@router.get("/library/{library_id}", response_class=HTMLResponse)
async def library_detail(request: Request, library_id: int) -> Response:
    """Browse a library's items."""
    db = request.app.state.db
    templates = request.app.state.templates

    library = await db.get_library(library_id)
    if not library:
        return RedirectResponse(url="/library?error=Library+not+found", status_code=303)

    items = await db.get_library_items_with_cache(library_id)
    counts = await db.get_library_item_counts(library_id)
    path_list = json.loads(library["paths"]) if library["paths"] else []
    title_display = await db.get_setting("app.title_display") or "romaji"

    # Check if a scan is running
    scan_running = _is_library_scan_busy(request.app.state, library_id)

    # Plex correlation: check if Plex is configured and find matching items
    config = request.app.state.config
    plex_configured = bool(config.plex.url and config.plex.token)
    plex_matches: dict = {}
    plex_matched_count = 0
    if plex_configured and items:
        folder_names = [it["folder_name"] for it in items if it.get("folder_name")]
        plex_matches = await db.get_plex_matches_for_folder_names(folder_names)
        plex_matched_count = len(plex_matches)

    # Jellyfin: check if configured and find matching items by folder_name
    jellyfin_configured = bool(config.jellyfin.url and config.jellyfin.api_key)
    jf_progress = getattr(request.app.state, "jellyfin_scan_progress", None)
    jellyfin_scan_running = bool(jf_progress and jf_progress.status == "running")
    jellyfin_matches: dict = {}
    jellyfin_matched_count = 0
    if jellyfin_configured and items:
        folder_names = [it["folder_name"] for it in items if it.get("folder_name")]
        jellyfin_matches = await db.get_jellyfin_matches_for_folder_names(folder_names)
        jellyfin_matched_count = len(jellyfin_matches)

    return templates.TemplateResponse(
        "library_detail.html",
        {
            "request": request,
            "library": library,
            "items": items,
            "counts": counts,
            "path_list": path_list,
            "title_display": title_display,
            "scan_running": scan_running,
            "plex_configured": plex_configured,
            "plex_matches": plex_matches,
            "plex_matched_count": plex_matched_count,
            "jellyfin_configured": jellyfin_configured,
            "jellyfin_scan_running": jellyfin_scan_running,
            "jellyfin_matches": jellyfin_matches,
            "jellyfin_matched_count": jellyfin_matched_count,
            "error": request.query_params.get("error", ""),
            "message": request.query_params.get("message", ""),
        },
    )


@router.get("/library/{library_id}/scan/progress", response_class=HTMLResponse)
async def library_scan_progress_page(request: Request, library_id: int) -> Response:
    """Render scan progress page."""
    templates = request.app.state.templates
    db = request.app.state.db
    library = await db.get_library(library_id)

    return templates.TemplateResponse(
        "library_scan_progress.html",
        {
            "request": request,
            "library_id": library_id,
            "library": library,
        },
    )


# ------------------------------------------------------------------
# Form / API endpoints
# ------------------------------------------------------------------


@router.post("/library/{library_id}/scan")
async def library_scan(request: Request, library_id: int) -> RedirectResponse:
    """Start a background library scan."""
    db = request.app.state.db

    if _is_library_scan_busy(request.app.state, library_id):
        return RedirectResponse(
            url=f"/library/{library_id}/scan/progress", status_code=303
        )

    library = await db.get_library(library_id)
    if not library:
        return RedirectResponse(url="/library?error=Library+not+found", status_code=303)

    form = await request.form()
    force_rescan = str(form.get("force_rescan", "")).lower() in (
        "on",
        "true",
        "1",
    )

    path_list = json.loads(library["paths"]) if library["paths"] else []
    if not path_list:
        return RedirectResponse(
            url=f"/library/{library_id}?error=No+paths+configured",
            status_code=303,
        )

    progress_map = _get_scan_progress(request.app.state)
    progress_map[library_id] = LibraryScanProgress()

    spawn_background_task(
        request.app.state,
        _run_library_scan(request.app.state, library_id, path_list, force_rescan),
        task_key=f"library_scan_{library_id}",
    )

    return RedirectResponse(url=f"/library/{library_id}/scan/progress", status_code=303)


@router.get("/api/library/{library_id}/changes")
async def library_changes(request: Request, library_id: int) -> JSONResponse:
    """Quick folder change detection."""
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    library = await db.get_library(library_id)
    if not library:
        return JSONResponse({"error": "not found"}, status_code=404)

    path_list = json.loads(library["paths"]) if library["paths"] else []
    title_matcher = TitleMatcher(similarity_threshold=0.75)
    scanner = LibraryScanner(
        db=db, anilist_client=anilist_client, title_matcher=title_matcher
    )
    changes = await scanner.detect_changes(library_id, path_list)
    return JSONResponse(changes)


@router.get("/api/library/{library_id}/scan/progress")
async def library_scan_progress_api(request: Request, library_id: int) -> JSONResponse:
    """Return current scan progress as JSON."""
    progress_map = _get_scan_progress(request.app.state)
    progress = progress_map.get(library_id)

    if not progress:
        return JSONResponse({"status": "idle"})

    elapsed = time.monotonic() - progress.started_at if progress.started_at > 0 else 0
    return JSONResponse(
        {
            "status": progress.status,
            "phase": progress.phase,
            "processed": progress.processed,
            "total": progress.total,
            "current_item": progress.current_item,
            "error_message": progress.error_message,
            "elapsed_seconds": round(elapsed, 1),
        }
    )


@router.post("/library/{library_id}/update-match")
async def library_update_match(request: Request, library_id: int) -> JSONResponse:
    """Manual AniList match for a library item (AJAX)."""
    db = request.app.state.db
    data = await request.json()

    item_id = data.get("item_id")
    anilist_id = data.get("anilist_id")
    if not item_id or not anilist_id:
        return JSONResponse({"error": "missing fields"}, status_code=400)

    # Fetch AniList metadata for the selected entry
    anilist_client = request.app.state.anilist_client
    try:
        candidates = await anilist_client.search_anime(str(anilist_id), per_page=1)
        # search by ID doesn't work; look up directly
        entry = None
        for c in candidates:
            if c.get("id") == anilist_id:
                entry = c
                break
        if not entry:
            # Fallback: use provided data minimally
            await db.update_library_item_match(
                item_id=item_id,
                anilist_id=anilist_id,
                match_method="manual",
            )
            return JSONResponse({"ok": True})

        title_obj = entry.get("title") or {}
        cover = (entry.get("coverImage") or {}).get("large", "")
        year = entry.get("seasonYear") or (
            (entry.get("startDate") or {}).get("year") or 0
        )

        await db.update_library_item_match(
            item_id=item_id,
            anilist_id=anilist_id,
            anilist_title=get_primary_title(entry),
            match_confidence=1.0,
            match_method="manual",
            cover_image=cover,
            anilist_format=entry.get("format", "") or "",
            anilist_episodes=entry.get("episodes"),
            year=year,
        )

        # Also cache metadata
        await db.set_cached_metadata(
            anilist_id=anilist_id,
            title_romaji=title_obj.get("romaji", ""),
            title_english=title_obj.get("english", "") or "",
            title_native=title_obj.get("native", "") or "",
            episodes=entry.get("episodes"),
            cover_image=cover,
            description=entry.get("description", "") or "",
            genres=json.dumps(entry.get("genres") or []),
            status=entry.get("status", ""),
            year=year,
        )
    except Exception:
        logger.exception("Failed to fetch AniList data for manual match")
        await db.update_library_item_match(
            item_id=item_id,
            anilist_id=anilist_id,
            match_method="manual",
        )

    return JSONResponse({"ok": True})


@router.post("/library/{library_id}/remove-match")
async def library_remove_match(request: Request, library_id: int) -> JSONResponse:
    """Remove AniList match from a library item (AJAX)."""
    db = request.app.state.db
    data = await request.json()
    item_id = data.get("item_id")
    if not item_id:
        return JSONResponse({"error": "missing item_id"}, status_code=400)

    await db.clear_library_item_match(item_id)
    return JSONResponse({"ok": True})


@router.get("/api/library/search")
async def library_search(request: Request) -> JSONResponse:
    """Search AniList for anime candidates (for rematch modal)."""
    q = request.query_params.get("q", "").strip()
    if not q:
        return JSONResponse([])

    anilist_client = request.app.state.anilist_client
    candidates = await anilist_client.search_anime(q, per_page=15)

    results = []
    for c in candidates:
        title_obj = c.get("title", {})
        start_date = c.get("startDate") or {}
        cover = c.get("coverImage") or {}
        results.append(
            {
                "id": c["id"],
                "title_romaji": title_obj.get("romaji") or "",
                "title_english": title_obj.get("english") or "",
                "year": c.get("seasonYear") or start_date.get("year"),
                "format": c.get("format") or "",
                "episodes": c.get("episodes"),
                "cover_image": cover.get("large") or cover.get("medium") or "",
                "season": c.get("season") or "",
            }
        )

    return JSONResponse(results)


# ------------------------------------------------------------------
# Plex integration endpoints
# ------------------------------------------------------------------


async def _apply_plex_metadata_for_item(
    db: Any,
    anilist_client: Any,
    config: Any,
    rating_key: str,
    plex_title: str,
    anilist_id: int,
    confidence: float,
    match_method: str,
) -> None:
    """Apply AniList metadata to a single Plex item (shared logic)."""
    plex_client = PlexClient(url=config.plex.url, token=config.plex.token)
    title_matcher = TitleMatcher(similarity_threshold=0.75)
    group_builder = SeriesGroupBuilder(db, anilist_client)
    scanner = MetadataScanner(
        db,
        anilist_client,
        title_matcher,
        plex_client,
        config,
        group_builder=group_builder,
    )
    try:
        group_id = None
        tv_entries: list[dict] = []
        structure = "A"
        try:
            group_id, group_entries = await group_builder.get_or_build_group(anilist_id)
            tv_entries = [
                e for e in group_entries if e.get("format", "") in ("TV", "TV_SHORT")
            ]
            if group_id and len(tv_entries) > 1:
                structure = await scanner._detect_structure(
                    rating_key, tv_entries, anilist_id
                )
        except Exception:
            logger.debug("Could not build series group for %s", plex_title)

        if structure == "B" and group_id and tv_entries:
            await scanner._store_structure_b_mappings(
                rating_key, plex_title, group_id, tv_entries, confidence, False
            )
        else:
            await scanner._apply_anilist_metadata(
                rating_key,
                plex_title,
                anilist_id,
                confidence,
                match_method,
                False,
            )
    finally:
        await plex_client.close()


@router.post("/library/{library_id}/plex-sync")
async def library_plex_sync(request: Request, library_id: int) -> JSONResponse:
    """Sync AniList metadata to Plex for a single library item (AJAX)."""
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    if not config.plex.url or not config.plex.token:
        return JSONResponse({"error": "Plex not configured"}, status_code=503)

    body = await request.json()
    item_id = body.get("item_id")
    if not item_id:
        return JSONResponse({"error": "missing item_id"}, status_code=400)

    item = await db.get_library_item(item_id)
    if not item or not item.get("anilist_id"):
        return JSONResponse({"error": "item not matched to AniList"}, status_code=400)

    # Find corresponding Plex entry by folder_name
    folder_name = item.get("folder_name", "")
    plex_matches = await db.get_plex_matches_for_folder_names([folder_name])
    plex_info = plex_matches.get(folder_name)
    if not plex_info:
        return JSONResponse({"error": "item not found in Plex"}, status_code=404)

    rating_key = plex_info["rating_key"]
    anilist_id = item["anilist_id"]

    try:
        await _apply_plex_metadata_for_item(
            db,
            anilist_client,
            config,
            rating_key,
            folder_name,
            anilist_id,
            item.get("match_confidence") or 1.0,
            item.get("match_method") or "manual",
        )
    except Exception:
        logger.exception("Failed to sync Plex metadata for item %s", item_id)
        return JSONResponse({"error": "sync failed"}, status_code=500)

    return JSONResponse({"ok": True, "title": folder_name})


async def _refresh_plex_media(db: Any, config: Any) -> None:
    """Trigger Plex library scan, wait for completion, and re-enumerate shows.

    This refreshes the plex_media table so it reflects the current state
    of Plex (important after file restructures).
    Only refreshes libraries that actually exist on the Plex server.
    """
    plex_client = PlexClient(url=config.plex.url, token=config.plex.token)
    try:
        libraries = await plex_client.get_libraries()
        show_libs = [lib for lib in libraries if lib.type in ("show", "movie")]

        # Trigger refresh on each library, skipping any that fail
        for lib in show_libs:
            try:
                await plex_client.refresh_library_and_wait(lib.key)
            except Exception:
                logger.warning(
                    "Could not refresh Plex library %s (%s), skipping",
                    lib.key,
                    lib.title,
                )

        # Re-enumerate shows and update plex_media
        for lib in show_libs:
            try:
                shows = await plex_client.get_library_shows(lib.key)
            except Exception:
                logger.warning(
                    "Could not enumerate Plex library %s (%s), skipping",
                    lib.key,
                    lib.title,
                )
                continue
            for show in shows:
                folder_name = show.folder_name
                # If folder_name falls back to title, try fetching real path
                if folder_name == show.title:
                    locs = await plex_client.get_show_locations(show.rating_key)
                    if locs:
                        real_name = os.path.basename(locs[0])
                        if real_name:
                            folder_name = real_name
                await db.upsert_plex_media(
                    rating_key=show.rating_key,
                    title=show.title,
                    year=show.year,
                    thumb=getattr(show, "thumb", "") or "",
                    summary=getattr(show, "summary", "") or "",
                    library_key=show.library_key,
                    library_title=lib.title,
                    folder_name=folder_name,
                )
    finally:
        await plex_client.close()


@router.post("/library/{library_id}/plex-apply-all")
async def library_plex_apply_all(request: Request, library_id: int) -> RedirectResponse:
    """Refresh Plex library data, then apply AniList metadata to all matched items."""
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    if not config.plex.url or not config.plex.token:
        return RedirectResponse(
            url=f"/library/{library_id}?error=Plex+not+configured", status_code=303
        )

    # Step 1: Refresh Plex library data (handles post-restructure changes)
    try:
        await _refresh_plex_media(db, config)
    except Exception:
        logger.exception("Failed to refresh Plex library data")
        return RedirectResponse(
            url=f"/library/{library_id}?error=Plex+library+refresh+failed",
            status_code=303,
        )

    # Step 2: Re-query with fresh plex_media data
    items = await db.get_library_items_with_cache(library_id)
    matched_items = [
        it for it in items if it.get("anilist_id") and it.get("folder_name")
    ]
    if not matched_items:
        return RedirectResponse(
            url=f"/library/{library_id}?error=No+matched+items", status_code=303
        )

    folder_names = [it["folder_name"] for it in matched_items]
    plex_matches = await db.get_plex_matches_for_folder_names(folder_names)

    # Step 3: Apply metadata
    applied = 0
    errors = 0
    for it in matched_items:
        plex_info = plex_matches.get(it["folder_name"])
        if not plex_info:
            continue
        try:
            await _apply_plex_metadata_for_item(
                db,
                anilist_client,
                config,
                plex_info["rating_key"],
                it["folder_name"],
                it["anilist_id"],
                it.get("match_confidence") or 1.0,
                it.get("match_method") or "manual",
            )
            applied += 1
        except Exception:
            logger.exception("Failed to apply Plex metadata for %s", it["folder_name"])
            errors += 1

    msg = f"Applied+metadata+to+{applied}+Plex+items"
    if errors:
        msg += f"+({errors}+errors)"
    return RedirectResponse(url=f"/library/{library_id}?message={msg}", status_code=303)


# ------------------------------------------------------------------
# Jellyfin integration endpoints
# ------------------------------------------------------------------


async def _refresh_jellyfin_media(db: Any, config: Any) -> None:
    """Re-enumerate Jellyfin libraries and refresh the jellyfin_media table."""
    jellyfin_client = JellyfinClient(
        url=config.jellyfin.url, api_key=config.jellyfin.api_key
    )
    try:
        libraries = await jellyfin_client.get_libraries()
        eligible = [
            lib for lib in libraries if lib.type in ("tvshows", "movies", "mixed", "")
        ]
        for lib in eligible:
            try:
                shows = await jellyfin_client.get_library_shows(lib.id)
            except Exception:
                logger.warning(
                    "Could not enumerate Jellyfin library %s (%s), skipping",
                    lib.id,
                    lib.name,
                )
                continue
            for show in shows:
                folder_name = os.path.basename(show.path) if show.path else show.name
                await db.upsert_jellyfin_media(
                    item_id=show.item_id,
                    title=show.name,
                    year=show.year,
                    path=show.path or "",
                    library_id=lib.id,
                    library_name=lib.name,
                    folder_name=folder_name,
                )
    finally:
        await jellyfin_client.close()


@router.post("/library/{library_id}/jellyfin-sync")
async def library_jellyfin_sync(request: Request, library_id: int) -> JSONResponse:
    """Sync AniList metadata to Jellyfin for a single library item (AJAX)."""
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    if not config.jellyfin.url or not config.jellyfin.api_key:
        return JSONResponse({"error": "Jellyfin not configured"}, status_code=503)

    body = await request.json()
    item_id = body.get("item_id")
    if not item_id:
        return JSONResponse({"error": "missing item_id"}, status_code=400)

    item = await db.get_library_item(item_id)
    if not item or not item.get("anilist_id"):
        return JSONResponse({"error": "item not matched to AniList"}, status_code=400)

    folder_name = item.get("folder_name", "")
    jellyfin_matches = await db.get_jellyfin_matches_for_folder_names([folder_name])
    jf_info = jellyfin_matches.get(folder_name)
    if not jf_info:
        return JSONResponse({"error": "item not found in Jellyfin"}, status_code=404)

    jellyfin_item_id = jf_info["item_id"]
    anilist_id = item["anilist_id"]

    jellyfin_client = JellyfinClient(
        url=config.jellyfin.url, api_key=config.jellyfin.api_key
    )
    title_matcher = TitleMatcher(similarity_threshold=0.75)
    group_builder = SeriesGroupBuilder(db, anilist_client)
    scanner = JellyfinMetadataScanner(
        db, anilist_client, title_matcher, jellyfin_client, config, group_builder
    )
    try:
        await scanner._apply_anilist_metadata(
            jellyfin_item_id,
            folder_name,
            anilist_id,
            item.get("match_confidence") or 1.0,
            item.get("match_method") or "manual",
            False,
        )
    except Exception:
        logger.exception("Failed to sync Jellyfin metadata for item %s", item_id)
        await jellyfin_client.close()
        return JSONResponse({"error": "sync failed"}, status_code=500)

    await jellyfin_client.close()
    return JSONResponse({"ok": True, "title": folder_name})


async def _run_library_jellyfin_apply_all(
    app_state: object,
    matched_items: list[dict],
    jellyfin_matches: dict,
) -> None:
    """Background coroutine: apply AniList metadata to all Jellyfin items that
    map to the given library_items rows.
    """
    config = app_state.config  # type: ignore[attr-defined]
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]
    progress: ScanProgress = app_state.jellyfin_apply_progress  # type: ignore[attr-defined]

    jellyfin_client = JellyfinClient(
        url=config.jellyfin.url, api_key=config.jellyfin.api_key
    )
    title_matcher = TitleMatcher(similarity_threshold=0.75)
    group_builder = SeriesGroupBuilder(db, anilist_client)
    scanner = JellyfinMetadataScanner(
        db, anilist_client, title_matcher, jellyfin_client, config, group_builder
    )

    applied = 0
    errors = 0
    try:
        for it in matched_items:
            jf_info = jellyfin_matches.get(it["folder_name"])
            if not jf_info:
                continue
            progress.current_title = it["folder_name"]
            try:
                await scanner._apply_anilist_metadata(
                    jf_info["item_id"],
                    it["folder_name"],
                    it["anilist_id"],
                    it.get("match_confidence") or 1.0,
                    it.get("match_method") or "manual",
                    False,
                )
                applied += 1
            except Exception:
                logger.exception(
                    "Failed to apply Jellyfin metadata for %s", it["folder_name"]
                )
                errors += 1
            finally:
                progress.scanned = applied + errors

        progress.status = "complete"
        progress.current_title = f"Applied metadata to {applied} Jellyfin items" + (
            f" ({errors} errors)" if errors else ""
        )
    except asyncio.CancelledError:
        logger.info("Library Jellyfin apply-all cancelled after %d items", applied)
        progress.status = "cancelled"
        progress.current_title = f"Cancelled after {applied} items"
        raise
    except Exception:
        logger.exception("Library Jellyfin apply-all failed unexpectedly")
        progress.status = "error"
        progress.error_message = "Apply-all failed unexpectedly"
    finally:
        await jellyfin_client.close()


@router.post("/library/{library_id}/jellyfin-apply-all")
async def library_jellyfin_apply_all(
    request: Request, library_id: int
) -> RedirectResponse:
    """Kick off a background task to apply AniList metadata to all Jellyfin
    items that map to items in this local library.  Progress is reported via
    ``/api/progress`` so the floating widget can display it.
    """
    config = request.app.state.config
    db = request.app.state.db

    if not config.jellyfin.url or not config.jellyfin.api_key:
        return RedirectResponse(
            url=f"/library/{library_id}?error=Jellyfin+not+configured",
            status_code=303,
        )

    existing = getattr(request.app.state, "jellyfin_apply_progress", None)
    if existing and existing.status not in (
        "",
        "pending",
        "complete",
        "error",
        "cancelled",
    ):
        return RedirectResponse(
            url=(
                f"/library/{library_id}?message="
                "Apply+already+running+%E2%80%94+see+progress+widget"
            ),
            status_code=303,
        )

    try:
        await _refresh_jellyfin_media(db, config)
    except Exception:
        logger.exception("Failed to refresh Jellyfin library data")
        return RedirectResponse(
            url=f"/library/{library_id}?error=Jellyfin+library+refresh+failed",
            status_code=303,
        )

    items = await db.get_library_items_with_cache(library_id)
    matched_items = [
        it for it in items if it.get("anilist_id") and it.get("folder_name")
    ]
    if not matched_items:
        return RedirectResponse(
            url=f"/library/{library_id}?error=No+matched+items", status_code=303
        )

    folder_names = [it["folder_name"] for it in matched_items]
    jellyfin_matches = await db.get_jellyfin_matches_for_folder_names(folder_names)

    request.app.state.jellyfin_apply_progress = ScanProgress(
        status="running",
        total=sum(1 for it in matched_items if jellyfin_matches.get(it["folder_name"])),
        current_title="Starting…",
    )
    spawn_background_task(
        request.app.state,
        _run_library_jellyfin_apply_all(
            request.app.state, matched_items, jellyfin_matches
        ),
        task_key="jellyfin_apply",
    )
    return RedirectResponse(
        url=(
            f"/library/{library_id}?message="
            "Applying+metadata+in+background+%E2%80%94+see+progress+widget"
        ),
        status_code=303,
    )


async def _run_library_reindex_all(app_state: object) -> None:
    """Background coroutine: rescan every configured local library."""
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]
    progress: RestructureProgress = app_state.library_reindex_progress  # type: ignore[attr-defined]

    libraries = await db.get_all_libraries()
    if not libraries:
        progress.status = "complete"
        progress.phase = "No libraries configured"
        return

    title_matcher = TitleMatcher(similarity_threshold=0.75)
    group_builder = SeriesGroupBuilder(db, anilist_client)
    restructurer = LibraryRestructurer(db=db, group_builder=group_builder)
    dir_scanner = LocalDirectoryScanner(
        db=db, anilist_client=anilist_client, title_matcher=title_matcher
    )

    total_seeded = 0
    total_groups = 0
    try:
        for library in libraries:
            raw = library.get("paths") or "[]"
            try:
                library_paths: list[str] = json.loads(raw)
            except Exception:
                continue
            if not library_paths:
                continue

            progress.phase = f"Scanning {library.get('name') or 'library'}"
            all_shows = []
            for path in library_paths:
                shows = await dir_scanner.scan_directory(path, progress)
                all_shows.extend(shows)

            if not all_shows:
                continue

            progress.phase = f"Indexing {library.get('name') or 'library'}"
            plan = await restructurer.analyze(
                all_shows, progress, level="full_restructure"
            )

            # Clear stale rows before re-seeding
            await db.execute(
                "DELETE FROM library_items WHERE library_id = ?", (library["id"],)
            )

            seeded = await restructurer.seed_library_items(
                plan, library["id"], from_source=True
            )
            total_seeded += seeded
            total_groups += plan.total_groups

        progress.status = "complete"
        progress.phase = f"Re-indexed {total_seeded} items across {total_groups} groups"
        logger.info(
            "Library rescan complete: %d items, %d groups", total_seeded, total_groups
        )
    except asyncio.CancelledError:
        logger.info(
            "Library rescan cancelled after %d items / %d groups",
            total_seeded,
            total_groups,
        )
        progress.status = "cancelled"
        progress.phase = f"Cancelled — {total_seeded} items seeded"
        raise
    except Exception as exc:
        logger.exception("Library reindex-all failed")
        progress.status = "error"
        progress.error_message = str(exc)


@router.post("/api/library/reindex-all")
async def library_reindex_all(request: Request) -> JSONResponse:
    """Kick off a background rescan of all configured local libraries.

    The actual work runs in a background task so the HTTP request returns
    immediately and the floating progress widget can display status.
    """
    app_state = request.app.state

    existing = getattr(app_state, "library_reindex_progress", None)
    if existing and existing.status not in (
        "",
        "pending",
        "complete",
        "error",
        "cancelled",
    ):
        return JSONResponse(
            {"ok": False, "error": "A library rescan is already running"},
            status_code=409,
        )

    app_state.library_reindex_progress = RestructureProgress(
        status="running", phase="Starting rescan…"
    )
    spawn_background_task(
        app_state, _run_library_reindex_all(app_state), task_key="library_reindex"
    )
    return JSONResponse({"ok": True, "message": "Rescan started"})


@router.post("/api/library/{library_id}/reindex")
async def library_reindex(request: Request, library_id: int) -> JSONResponse:
    """Re-index a local library using the restructurer's analyze pipeline.

    Runs LocalDirectoryScanner + LibraryRestructurer.analyze() in read-only
    mode, then seeds library_items with series-group-aware per-season rows.
    Safe to run against an existing library — all upserts are non-destructive.
    """
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    library = await db.get_library(library_id)
    if not library:
        return JSONResponse(
            {"ok": False, "error": "Library not found"}, status_code=404
        )

    raw = library.get("paths") or "[]"
    try:
        library_paths: list[str] = json.loads(raw)
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "Invalid library paths"}, status_code=400
        )

    if not library_paths:
        return JSONResponse(
            {"ok": False, "error": "Library has no paths configured"}, status_code=400
        )

    title_matcher = TitleMatcher(similarity_threshold=0.75)
    group_builder = SeriesGroupBuilder(db, anilist_client)
    restructurer = LibraryRestructurer(db=db, group_builder=group_builder)
    dir_scanner = LocalDirectoryScanner(
        db=db, anilist_client=anilist_client, title_matcher=title_matcher
    )

    try:
        all_shows = []
        scan_progress = RestructureProgress(status="running")
        for path in library_paths:
            shows = await dir_scanner.scan_directory(path, scan_progress)
            all_shows.extend(shows)

        if not all_shows:
            return JSONResponse(
                {"ok": True, "seeded": 0, "message": "No shows found in library paths"}
            )

        progress = RestructureProgress(status="running")
        plan = await restructurer.analyze(all_shows, progress, level="full_restructure")

        # Clear stale rows before re-seeding so removed/renamed folders don't persist
        await db.execute(
            "DELETE FROM library_items WHERE library_id = ?", (library_id,)
        )

        seeded = await restructurer.seed_library_items(
            plan, library_id, from_source=True
        )

        return JSONResponse(
            {
                "ok": True,
                "seeded": seeded,
                "groups": plan.total_groups,
                "message": (
                    f"Re-indexed {seeded} items" f" across {plan.total_groups} groups"
                ),
            }
        )
    except Exception as exc:
        logger.exception("Library reindex failed for library %d", library_id)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
