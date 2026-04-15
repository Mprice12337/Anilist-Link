"""Jellyfin library browser — browse scanned media and manage matches."""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from src.Clients.JellyfinClient import JellyfinClient
from src.Matching.TitleMatcher import TitleMatcher
from src.Scanner.JellyfinMetadataScanner import JellyfinMetadataScanner
from src.Scanner.MetadataScanner import ScanProgress, ScanResults
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Web.App import spawn_background_task
from src.Web.Routes.JellyfinScan import (
    _run_jellyfin_live_scan,
    _run_jellyfin_preview_scan,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jellyfin-library"])


@router.get("/jellyfin", response_class=HTMLResponse)
async def jellyfin_library_page(request: Request) -> HTMLResponse:
    """Render the Jellyfin library browser."""
    db = request.app.state.db
    config = request.app.state.config
    templates = request.app.state.templates

    title_display = await db.get_setting("app.title_display") or "romaji"
    library_id = request.query_params.get("library") or None

    jellyfin_libraries: list[dict[str, str]] = []
    jellyfin_configured = bool(config.jellyfin.url and config.jellyfin.api_key)
    if jellyfin_configured:
        try:
            client = JellyfinClient(
                url=config.jellyfin.url, api_key=config.jellyfin.api_key
            )
            libs = await client.get_libraries()
            jellyfin_libraries = [
                {"id": lib.id, "name": lib.name, "type": lib.type}
                for lib in libs
                if lib.type in ("tvshows", "movies", "mixed", "")
            ]
            await client.close()
        except Exception:
            logger.warning("Could not fetch Jellyfin libraries for library browser")

    items = await db.get_jellyfin_media_with_mappings(library_id)

    has_scan_results = (
        getattr(request.app.state, "jellyfin_scan_results", None) is not None
    )
    progress = getattr(request.app.state, "jellyfin_scan_progress", None)
    scan_in_progress = bool(progress and progress.status == "running")
    matched_count = sum(1 for i in items if i.get("anilist_id"))

    message = request.query_params.get("message") or ""
    error = request.query_params.get("error") or ""

    return templates.TemplateResponse(
        "jellyfin_library.html",
        {
            "request": request,
            "items": items,
            "item_count": len(items),
            "jellyfin_libraries": jellyfin_libraries,
            "selected_library": library_id or "",
            "title_display": title_display,
            "jellyfin_configured": jellyfin_configured,
            "has_scan_results": has_scan_results,
            "scan_in_progress": scan_in_progress,
            "matched_count": matched_count,
            "message": message,
            "error": error,
        },
    )


@router.get("/api/jellyfin/thumb")
async def jellyfin_thumb_proxy(request: Request) -> Response:
    """Proxy Jellyfin item primary images to avoid exposing the API key."""
    item_id = request.query_params.get("item_id", "")
    if not item_id:
        return Response(status_code=400)

    config = request.app.state.config
    if not config.jellyfin.url or not config.jellyfin.api_key:
        return Response(status_code=503)

    url = f"{config.jellyfin.url.rstrip('/')}/Items/{item_id}/Images/Primary"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": (
                        f'MediaBrowser Client="AnilistLink",'
                        f' Token="{config.jellyfin.api_key}"'
                    )
                },
            )
            resp.raise_for_status()
            return Response(
                content=resp.content,
                media_type=resp.headers.get("content-type", "image/jpeg"),
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except Exception:
        logger.debug("Failed to proxy Jellyfin thumb for item: %s", item_id)
        return Response(status_code=502)


@router.post("/jellyfin/update-match")
async def jellyfin_update_match(request: Request) -> JSONResponse:
    """Save or update a manual AniList match for a Jellyfin item."""
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client
    body = await request.json()

    item_id = body.get("item_id", "")
    anilist_id = body.get("anilist_id")
    jellyfin_title = body.get("jellyfin_title", "")

    if not item_id or not anilist_id:
        return JSONResponse({"error": "missing fields"}, status_code=400)

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
        source="jellyfin",
        source_id=item_id,
        source_title=jellyfin_title,
        anilist_id=int(anilist_id),
        anilist_title=anilist_title,
        match_confidence=1.0,
        match_method="manual",
    )

    return JSONResponse({"status": "ok"})


@router.post("/jellyfin/remove-match")
async def jellyfin_remove_match(request: Request) -> JSONResponse:
    """Remove the AniList match for a Jellyfin item."""
    db = request.app.state.db
    body = await request.json()

    item_id = body.get("item_id", "")
    if not item_id:
        return JSONResponse({"error": "missing item_id"}, status_code=400)

    await db.delete_mapping_by_source("jellyfin", item_id)
    return JSONResponse({"status": "ok"})


@router.post("/jellyfin/remove-library")
async def jellyfin_remove_library(request: Request) -> RedirectResponse:
    """Remove all database entries for a Jellyfin library."""
    db = request.app.state.db
    form = await request.form()
    library_id = str(form.get("library_id", "")).strip()

    if not library_id:
        return RedirectResponse(
            url="/jellyfin?error=No+library+specified", status_code=303
        )

    deleted = await db.delete_jellyfin_library_data(library_id)
    logger.info("Removed Jellyfin library %s: %d items deleted", library_id, deleted)
    return RedirectResponse(
        url=f"/jellyfin?message=Removed+{deleted}+items+from+library", status_code=303
    )


@router.post("/jellyfin/clear-all")
async def jellyfin_clear_all(request: Request) -> RedirectResponse:
    """Remove ALL Jellyfin data from the database (all libraries)."""
    db = request.app.state.db
    total = 0
    rows = await db.fetch_all(
        "SELECT DISTINCT library_id FROM jellyfin_media WHERE library_id IS NOT NULL"
    )
    for row in rows:
        total += await db.delete_jellyfin_library_data(row["library_id"])
    # Clear any orphaned rows
    orphans = await db.fetch_one("SELECT COUNT(*) AS cnt FROM jellyfin_media")
    orphan_count = orphans["cnt"] if orphans else 0
    if orphan_count > 0:
        await db.execute("DELETE FROM media_mappings WHERE source='jellyfin'")
        await db.execute("DELETE FROM jellyfin_media")
        total += orphan_count
    logger.info("Cleared all Jellyfin data: %d items deleted", total)
    return RedirectResponse(
        url=f"/jellyfin?message=Cleared+all+Jellyfin+data+({total}+items)",
        status_code=303,
    )


@router.post("/jellyfin/apply-all")
async def jellyfin_apply_all(request: Request) -> RedirectResponse:
    """Apply AniList metadata to all matched Jellyfin items.

    Builds the series group, detects Structure B, and writes both show-level
    and per-season metadata for multi-season shows.

    When the form includes ``force_refresh=1`` the AniList metadata cache is
    bypassed so fresh data is fetched from AniList for every item.
    """
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    form = await request.form()
    force_refresh = form.get("force_refresh") == "1"

    if not config.jellyfin.url or not config.jellyfin.api_key:
        return RedirectResponse(
            url="/jellyfin?error=Jellyfin+not+configured", status_code=303
        )

    items = await db.get_jellyfin_media_with_mappings()
    matched = [i for i in items if i.get("anilist_id")]

    if not matched:
        return RedirectResponse(
            url="/jellyfin?message=No+matched+items+to+apply", status_code=303
        )

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

    # Scope the refresh to the configured anime libraries if available.
    apply_library_ids: list[str] | None = (
        list(config.jellyfin.anime_library_ids)
        if config.jellyfin.anime_library_ids
        else None
    )

    try:
        # Refresh only the selected libraries so item IDs are stable.
        await jellyfin_client.refresh_library_and_wait(
            inactivity_timeout=120.0, library_ids=apply_library_ids
        )

        for item in matched:
            try:
                item_id = item["item_id"]
                jellyfin_title = item["jellyfin_title"]
                anilist_id = item["anilist_id"]
                confidence = item.get("match_confidence") or 1.0

                # Build series group and detect Structure B
                group_id = None
                group_entries: list[dict] = []
                tv_entries: list[dict] = []
                is_structure_b = False
                jf_real_seasons = []
                try:
                    group_id, group_entries = await group_builder.get_or_build_group(
                        anilist_id
                    )
                    tv_entries = [
                        e
                        for e in group_entries
                        if e.get("format", "") in ("TV", "TV_SHORT")
                    ]
                except Exception:
                    logger.debug(
                        "Could not build series group for %s",
                        jellyfin_title,
                        exc_info=True,
                    )

                if group_id and len(tv_entries) > 1:
                    try:
                        jf_seasons = await jellyfin_client.get_show_seasons(item_id)
                        jf_real_seasons = sorted(
                            [s for s in jf_seasons if s.index > 0],
                            key=lambda s: s.index,
                        )
                        is_structure_b = len(jf_real_seasons) > 1
                    except Exception:
                        logger.debug(
                            "Could not fetch seasons for '%s' (%s),"
                            " treating as Structure A",
                            jellyfin_title,
                            item_id,
                            exc_info=True,
                        )

                if is_structure_b and tv_entries:
                    logger.info(
                        "  [structure B] '%s': %d seasons, %d group entries",
                        jellyfin_title,
                        len(jf_real_seasons),
                        len(tv_entries),
                    )
                    await scanner._apply_structure_b_metadata(
                        item_id,
                        jellyfin_title,
                        jf_real_seasons,
                        tv_entries,
                        confidence,
                        False,
                        force_refresh=force_refresh,
                    )
                else:
                    logger.info(
                        "  [structure A] '%s': single entry apply", jellyfin_title
                    )
                    root_anilist_id = (
                        group_entries[0]["anilist_id"] if group_entries else anilist_id
                    )
                    season_number: int | None = None
                    if group_entries:
                        for i, entry in enumerate(group_entries):
                            if entry.get("anilist_id") == anilist_id:
                                season_number = i + 1
                                break
                        if season_number is None:
                            season_number = 1
                    await scanner._apply_anilist_metadata(
                        item_id,
                        jellyfin_title,
                        anilist_id,
                        confidence,
                        item.get("match_method") or "manual",
                        False,
                        force_refresh=force_refresh,
                        parent_anilist_id=root_anilist_id,
                        season_number=season_number,
                    )
                applied += 1
            except Exception:
                logger.exception(
                    "Failed to apply metadata for %s", item["jellyfin_title"]
                )
                errors += 1

        # Trigger a re-index so Jellyfin picks up the NFO files just written.
        await jellyfin_client.refresh_library_and_wait(
            inactivity_timeout=120.0, library_ids=apply_library_ids
        )
    finally:
        await jellyfin_client.close()

    msg = f"Applied+metadata+to+{applied}+items"
    if errors:
        msg += f"+({errors}+errors)"
    return RedirectResponse(url=f"/jellyfin?message={msg}", status_code=303)


@router.post("/jellyfin/apply-single")
async def jellyfin_apply_single(request: Request) -> JSONResponse:
    """Apply AniList metadata to a single Jellyfin item (AJAX)."""
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    if not config.jellyfin.url or not config.jellyfin.api_key:
        return JSONResponse({"error": "Jellyfin not configured"}, status_code=503)

    body = await request.json()
    item_id = body.get("item_id", "")
    if not item_id:
        return JSONResponse({"error": "missing item_id"}, status_code=400)

    mapping = await db.get_mapping_by_source("jellyfin", item_id)
    if not mapping:
        return JSONResponse({"error": "no mapping found"}, status_code=404)

    anilist_id = mapping["anilist_id"]
    jellyfin_title = mapping.get("source_title") or ""
    confidence = mapping.get("match_confidence") or 1.0

    jellyfin_client = JellyfinClient(
        url=config.jellyfin.url, api_key=config.jellyfin.api_key
    )
    title_matcher = TitleMatcher(similarity_threshold=0.75)
    group_builder = SeriesGroupBuilder(db, anilist_client)
    scanner = JellyfinMetadataScanner(
        db, anilist_client, title_matcher, jellyfin_client, config, group_builder
    )

    try:
        # Detect Structure B (multi-season) before applying
        group_id = None
        group_entries: list[dict] = []
        tv_entries: list[dict] = []
        is_structure_b = False
        jf_real_seasons = []
        try:
            group_id, group_entries = await group_builder.get_or_build_group(anilist_id)
            tv_entries = [
                e for e in group_entries if e.get("format", "") in ("TV", "TV_SHORT")
            ]
        except Exception:
            logger.debug(
                "Could not build series group for %s", jellyfin_title, exc_info=True
            )

        if group_id and len(tv_entries) > 1:
            try:
                jf_seasons = await jellyfin_client.get_show_seasons(item_id)
                jf_real_seasons = sorted(
                    [s for s in jf_seasons if s.index > 0],
                    key=lambda s: s.index,
                )
                is_structure_b = len(jf_real_seasons) > 1
            except Exception:
                logger.debug(
                    "Could not fetch seasons for '%s', treating as Structure A",
                    jellyfin_title,
                    exc_info=True,
                )

        if is_structure_b and tv_entries:
            await scanner._apply_structure_b_metadata(
                item_id, jellyfin_title, jf_real_seasons, tv_entries, confidence, False
            )
        else:
            root_anilist_id = (
                group_entries[0]["anilist_id"] if group_entries else anilist_id
            )
            await scanner._apply_anilist_metadata(
                item_id,
                jellyfin_title,
                anilist_id,
                confidence,
                mapping.get("match_method") or "manual",
                False,
                parent_anilist_id=root_anilist_id,
            )
    except Exception:
        logger.exception("Failed to apply metadata for item_id=%s", item_id)
        await jellyfin_client.close()
        return JSONResponse({"error": "apply failed"}, status_code=500)

    await jellyfin_client.close()
    return JSONResponse({"status": "ok", "title": jellyfin_title or item_id})


# ------------------------------------------------------------------
# Scan routes (library-browser context, mirrors PlexLibrary scan routes)
# ------------------------------------------------------------------


@router.post("/jellyfin/scan/preview")
async def jellyfin_scan_preview(request: Request) -> RedirectResponse:
    """Start a preview scan from the Jellyfin browser."""
    config = request.app.state.config
    if not config.jellyfin.url or not config.jellyfin.api_key:
        return RedirectResponse(
            url="/jellyfin?error=Jellyfin+not+configured", status_code=303
        )

    form = await request.form()
    selected_ids = form.getlist("library_id")
    request.app.state.jellyfin_scan_library_ids = (
        [str(i) for i in selected_ids] if selected_ids else None
    )

    request.app.state.jellyfin_scan_progress = ScanProgress()
    request.app.state.jellyfin_scan_results = None
    request.app.state.jellyfin_scan_return_to = "/jellyfin"

    spawn_background_task(
        request.app.state, _run_jellyfin_preview_scan(request.app.state)
    )

    return RedirectResponse(url="/jellyfin/scan/progress", status_code=303)


@router.post("/jellyfin/scan/live")
async def jellyfin_scan_live(request: Request) -> RedirectResponse:
    """Start a live scan from the Jellyfin browser."""
    config = request.app.state.config
    if not config.jellyfin.url or not config.jellyfin.api_key:
        return RedirectResponse(
            url="/jellyfin?error=Jellyfin+not+configured", status_code=303
        )

    form = await request.form()
    selected_ids = form.getlist("library_id")
    request.app.state.jellyfin_scan_library_ids = (
        [str(i) for i in selected_ids] if selected_ids else None
    )

    request.app.state.jellyfin_scan_progress = ScanProgress()
    request.app.state.jellyfin_scan_results = None
    request.app.state.jellyfin_scan_return_to = "/jellyfin"

    spawn_background_task(request.app.state, _run_jellyfin_live_scan(request.app.state))

    return RedirectResponse(url="/jellyfin/scan/progress", status_code=303)


@router.get("/jellyfin/scan/progress", response_class=HTMLResponse)
async def jellyfin_scan_progress_page(request: Request) -> HTMLResponse:
    """Render the scan progress page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "scan_progress.html",
        {
            "request": request,
            "page_title": "Jellyfin Scan",
            "source_label": "Jellyfin",
            "scan_label": "Scanning Jellyfin library...",
            "progress_api_url": "/api/scan/jellyfin/progress",
            "results_url": "/jellyfin/scan/results",
        },
    )


@router.get("/jellyfin/scan/results", response_class=HTMLResponse)
async def jellyfin_scan_results_page(request: Request) -> Response:
    """Render scan results."""
    templates = request.app.state.templates
    results: ScanResults | None = getattr(
        request.app.state, "jellyfin_scan_results", None
    )

    if not results:
        return RedirectResponse(
            url="/jellyfin?error=No+scan+results+available", status_code=303
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
            "page_title": "Jellyfin Scan Preview",
            "source_label": "Jellyfin",
            "apply_url": "/scan/jellyfin/apply",
            "search_url": "/api/scan/jellyfin/search",
            "rematch_url": "/scan/jellyfin/rematch",
            "return_url": "/jellyfin",
        },
    )
