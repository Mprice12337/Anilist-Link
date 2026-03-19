"""Plex library browser — browse scanned media and manage matches."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from src.Clients.PlexClient import PlexClient
from src.Matching.TitleMatcher import TitleMatcher
from src.Scanner.MetadataScanner import MetadataScanner, ScanProgress
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Web.Routes.PlexScan import _run_preview_scan

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plex-library"])


@router.get("/plex", response_class=HTMLResponse)
async def plex_library_page(request: Request) -> HTMLResponse:
    """Render the Plex library browser."""
    db = request.app.state.db
    config = request.app.state.config
    templates = request.app.state.templates

    # Read title display preference
    title_display = await db.get_setting("app.title_display") or "romaji"

    # Optional library filter
    library_key = request.query_params.get("library") or None

    # Fetch Plex libraries for the filter dropdown
    plex_libraries: list[dict[str, str]] = []
    plex_configured = bool(config.plex.url and config.plex.token)
    if plex_configured:
        try:
            plex_client = PlexClient(url=config.plex.url, token=config.plex.token)
            libs = await plex_client.get_libraries()
            plex_libraries = [
                {"key": lib.key, "title": lib.title}
                for lib in libs
                if lib.type in ("show", "movie")
            ]
            await plex_client.close()
        except Exception:
            logger.warning("Could not fetch Plex libraries for library browser")

    # Get media items with mapping/cache data
    items = await db.get_plex_media_with_mappings(library_key)

    # Scan/apply context for action bar
    has_scan_results = getattr(request.app.state, "plex_scan_results", None) is not None
    progress = getattr(request.app.state, "plex_scan_progress", None)
    scan_in_progress = bool(progress and progress.status == "running")
    matched_count = sum(1 for i in items if i.get("anilist_id"))

    # Flash messages
    message = request.query_params.get("message") or ""
    error = request.query_params.get("error") or ""

    # Anime library keys from settings (for scan form filtering)
    anime_library_keys: list[str] = list(config.plex.anime_library_keys)

    return templates.TemplateResponse(
        "plex_library.html",
        {
            "request": request,
            "items": items,
            "item_count": len(items),
            "plex_libraries": plex_libraries,
            "anime_library_keys": anime_library_keys,
            "selected_library": library_key or "",
            "title_display": title_display,
            "plex_configured": plex_configured,
            "has_scan_results": has_scan_results,
            "scan_in_progress": scan_in_progress,
            "matched_count": matched_count,
            "message": message,
            "error": error,
        },
    )


@router.get("/api/plex/thumb")
async def plex_thumb_proxy(request: Request) -> Response:
    """Proxy Plex thumbnail images to avoid exposing the token in <img> tags."""
    path = request.query_params.get("path", "")
    if not path:
        return Response(status_code=400)

    config = request.app.state.config
    if not config.plex.url or not config.plex.token:
        return Response(status_code=503)

    url = f"{config.plex.url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"X-Plex-Token": config.plex.token})
            resp.raise_for_status()
            return Response(
                content=resp.content,
                media_type=resp.headers.get("content-type", "image/jpeg"),
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except Exception:
        logger.debug("Failed to proxy Plex thumb: %s", path)
        return Response(status_code=502)


@router.post("/plex/update-match")
async def plex_update_match(request: Request) -> JSONResponse:
    """Save or update a manual AniList match for a Plex item."""
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client
    body = await request.json()

    rating_key = body.get("rating_key", "")
    anilist_id = body.get("anilist_id")
    plex_title = body.get("plex_title", "")

    if not rating_key or not anilist_id:
        return JSONResponse({"error": "missing fields"}, status_code=400)

    # Fetch and cache AniList metadata
    entry = await anilist_client.get_anime_by_id(int(anilist_id))
    if entry:
        title_obj = entry.get("title", {})
        year = entry.get("seasonYear") or (
            (entry.get("startDate") or {}).get("year") or 0
        )
        await db.set_cached_metadata(
            anilist_id=int(anilist_id),
            title_romaji=title_obj.get("romaji") or "",
            title_english=title_obj.get("english") or "",
            title_native=title_obj.get("native") or "",
            episodes=entry.get("episodes"),
            cover_image=(entry.get("coverImage") or {}).get("large") or "",
            description=entry.get("description") or "",
            genres=json.dumps(entry.get("genres") or []),
            status=entry.get("status") or "",
            year=year,
        )

    anilist_title = ""
    if entry:
        t = entry.get("title", {})
        anilist_title = t.get("romaji") or t.get("english") or ""

    await db.upsert_media_mapping(
        source="plex",
        source_id=rating_key,
        source_title=plex_title,
        anilist_id=int(anilist_id),
        anilist_title=anilist_title,
        match_confidence=1.0,
        match_method="manual",
    )

    return JSONResponse({"status": "ok"})


@router.post("/plex/remove-library")
async def plex_remove_library(request: Request) -> RedirectResponse:
    """Remove all database entries for a Plex library."""
    db = request.app.state.db
    form = await request.form()
    library_key = str(form.get("library_key", "")).strip()

    if not library_key:
        return RedirectResponse(url="/plex?error=No+library+specified", status_code=303)

    deleted = await db.delete_plex_library_data(library_key)
    logger.info("Removed library %s: %d items deleted", library_key, deleted)
    return RedirectResponse(
        url=f"/plex?message=Removed+{deleted}+items+from+library", status_code=303
    )


@router.post("/plex/remove-match")
async def plex_remove_match(request: Request) -> JSONResponse:
    """Remove the AniList match for a Plex item."""
    db = request.app.state.db
    body = await request.json()

    rating_key = body.get("rating_key", "")
    if not rating_key:
        return JSONResponse({"error": "missing rating_key"}, status_code=400)

    await db.delete_mapping_by_source("plex", rating_key)
    return JSONResponse({"status": "ok"})


# ------------------------------------------------------------------
# Scan endpoints (accessible from the Plex tab)
# ------------------------------------------------------------------


@router.post("/plex/scan/preview")
async def plex_scan_preview(request: Request) -> RedirectResponse:
    """Start a preview scan from the Plex tab, redirect to progress page."""
    config = request.app.state.config
    if not config.plex.url or not config.plex.token:
        return RedirectResponse(url="/plex?error=Plex+not+configured", status_code=303)

    form = await request.form()
    selected_keys = form.getlist("library_key")
    request.app.state.plex_scan_library_keys = (
        [str(k) for k in selected_keys] if selected_keys else None
    )

    request.app.state.plex_scan_progress = ScanProgress()
    request.app.state.plex_scan_results = None
    request.app.state.plex_scan_return_to = "/plex"

    asyncio.create_task(_run_preview_scan(request.app.state))

    return RedirectResponse(url="/plex/scan/progress", status_code=303)


@router.post("/plex/scan/live")
async def plex_scan_live(request: Request) -> RedirectResponse:
    """Start a live (non-preview) scan from the Plex tab."""
    config = request.app.state.config
    if not config.plex.url or not config.plex.token:
        return RedirectResponse(url="/plex?error=Plex+not+configured", status_code=303)

    form = await request.form()
    selected_keys = form.getlist("library_key")
    library_keys = [str(k) for k in selected_keys] if selected_keys else None

    progress = ScanProgress()
    request.app.state.plex_scan_progress = progress
    request.app.state.plex_scan_results = None
    request.app.state.plex_scan_return_to = "/plex"

    asyncio.create_task(_run_live_scan(request.app.state, library_keys, progress))

    return RedirectResponse(url="/plex/scan/progress", status_code=303)


async def _run_live_scan(
    app_state: object,
    library_keys: list[str] | None,
    progress: ScanProgress,
) -> None:
    """Background coroutine that runs a live (non-preview) scan."""
    config = app_state.config  # type: ignore[attr-defined]
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]

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

    if not library_keys:
        if config.plex.anime_library_keys:
            library_keys = list(config.plex.anime_library_keys)

    try:
        results = await scanner.run_scan(
            dry_run=False, library_keys=library_keys, progress=progress
        )
        app_state.plex_scan_results = results  # type: ignore[attr-defined]
    except Exception:
        logger.exception("Live scan failed")
        progress.status = "error"
        progress.error_message = "Live scan failed unexpectedly"
    finally:
        await plex_client.close()


@router.get("/plex/scan/progress", response_class=HTMLResponse)
async def plex_scan_progress_page(request: Request) -> HTMLResponse:
    """Render the progress page for scans started from the Plex tab."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "scan_progress.html",
        {
            "request": request,
            "results_url": "/plex/scan/results",
            "return_url": "/plex",
        },
    )


@router.get("/plex/scan/results", response_class=HTMLResponse)
async def plex_scan_results_page(request: Request) -> Response:
    """Render scan results, accessible from the Plex tab flow."""
    templates = request.app.state.templates
    results = getattr(request.app.state, "plex_scan_results", None)

    if not results:
        return RedirectResponse(
            url="/plex?error=No+scan+results+available", status_code=303
        )

    matched_items = [i for i in results.items if i.status == "matched"]
    skipped_items = [i for i in results.items if i.status == "skipped"]
    failed_items = [i for i in results.items if i.status == "failed"]

    return templates.TemplateResponse(
        "scan_preview.html",
        {
            "request": request,
            "results": results,
            "matched_items": matched_items,
            "skipped_items": skipped_items,
            "failed_items": failed_items,
            "return_url": "/plex",
        },
    )


# ------------------------------------------------------------------
# Apply endpoints
# ------------------------------------------------------------------


@router.post("/plex/apply-all")
async def plex_apply_all(request: Request) -> RedirectResponse:
    """Apply AniList metadata to all matched Plex items.

    For each item, builds the series group, detects structure, and writes
    both show-level and per-season metadata for Structure B shows.
    """
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    if not config.plex.url or not config.plex.token:
        return RedirectResponse(url="/plex?error=Plex+not+configured", status_code=303)

    items = await db.get_plex_media_with_mappings()
    matched = [i for i in items if i.get("anilist_id")]

    if not matched:
        return RedirectResponse(
            url="/plex?message=No+matched+items+to+apply", status_code=303
        )

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

    applied = 0
    errors = 0

    try:
        for item in matched:
            try:
                rating_key = item["rating_key"]
                plex_title = item["plex_title"]
                anilist_id = item["anilist_id"]
                confidence = item.get("match_confidence") or 1.0

                # Build series group and detect structure
                group_id = None
                tv_entries: list[dict] = []
                structure = "A"
                try:
                    group_id, group_entries = await group_builder.get_or_build_group(
                        anilist_id
                    )
                    tv_entries = [
                        e
                        for e in group_entries
                        if e.get("format", "") in ("TV", "TV_SHORT")
                    ]
                    if group_id and len(tv_entries) > 1:
                        structure = await scanner._detect_structure(
                            rating_key, tv_entries, anilist_id
                        )
                except Exception:
                    logger.debug("Could not build series group for %s", plex_title)

                if structure == "B" and group_id and tv_entries:
                    await scanner._store_structure_b_mappings(
                        rating_key,
                        plex_title,
                        group_id,
                        tv_entries,
                        confidence,
                        False,
                    )
                else:
                    await scanner._apply_anilist_metadata(
                        rating_key,
                        plex_title,
                        anilist_id,
                        confidence,
                        item.get("match_method") or "manual",
                        False,
                    )
                applied += 1
            except Exception:
                logger.exception("Failed to apply metadata for %s", item["plex_title"])
                errors += 1
    finally:
        await plex_client.close()

    msg = f"Applied+metadata+to+{applied}+items"
    if errors:
        msg += f"+({errors}+errors)"
    return RedirectResponse(url=f"/plex?message={msg}", status_code=303)


@router.post("/plex/apply-single")
async def plex_apply_single(request: Request) -> JSONResponse:
    """Apply AniList metadata to a single Plex item (AJAX).

    Builds the series group and, for Structure B shows, writes both
    show-level and per-season metadata to Plex.
    """
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    if not config.plex.url or not config.plex.token:
        return JSONResponse({"error": "Plex not configured"}, status_code=503)

    body = await request.json()
    rating_key = body.get("rating_key", "")
    if not rating_key:
        return JSONResponse({"error": "missing rating_key"}, status_code=400)

    mapping = await db.get_mapping_by_source("plex", rating_key)
    if not mapping:
        return JSONResponse({"error": "no mapping found"}, status_code=404)

    anilist_id = mapping["anilist_id"]
    plex_title = mapping.get("source_title") or ""
    confidence = mapping.get("match_confidence") or 1.0

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
        # Build series group and detect structure
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
                mapping.get("match_method") or "manual",
                False,
            )
    except Exception:
        logger.exception("Failed to apply metadata for rating_key=%s", rating_key)
        await plex_client.close()
        return JSONResponse({"error": "apply failed"}, status_code=500)

    await plex_client.close()
    return JSONResponse({"status": "ok", "title": plex_title or rating_key})
