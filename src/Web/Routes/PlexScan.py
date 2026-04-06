"""Plex scan preview and apply routes."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import Response

from src.Clients.PlexClient import PlexClient
from src.Matching.TitleMatcher import TitleMatcher, get_primary_title
from src.Scanner.MetadataScanner import (
    MetadataScanner,
    ScanItemDetail,
    ScanProgress,
    ScanResults,
)
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Web.App import spawn_background_task

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plex-scan"])


async def _run_preview_scan(
    app_state: object,
) -> None:
    """Background coroutine that runs the preview scan."""
    config = app_state.config  # type: ignore[attr-defined]
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]
    progress: ScanProgress = app_state.plex_scan_progress  # type: ignore[attr-defined]

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

    # Use form-selected keys if available, else fall back to config
    form_keys = getattr(app_state, "plex_scan_library_keys", None)
    if form_keys:
        library_keys = form_keys
    elif config.plex.anime_library_keys:
        library_keys = list(config.plex.anime_library_keys)
    else:
        library_keys = None

    try:
        results = await scanner.run_scan(
            preview=True, library_keys=library_keys, progress=progress
        )
        app_state.plex_scan_results = results  # type: ignore[attr-defined]
    except Exception:
        logger.exception("Preview scan failed")
        progress.status = "error"
        progress.error_message = "Preview scan failed unexpectedly"
    finally:
        await plex_client.close()


@router.post("/scan/plex/preview")
async def plex_scan_preview(request: Request) -> RedirectResponse:
    """Kick off a preview scan in the background, redirect to progress page."""
    config = request.app.state.config

    if not config.plex.url or not config.plex.token:
        return RedirectResponse(url="/?error=Plex+not+configured", status_code=303)

    # Read selected library keys from the form
    form = await request.form()
    selected_keys = form.getlist("library_key")
    request.app.state.plex_scan_library_keys = (
        [str(k) for k in selected_keys] if selected_keys else None
    )

    # Initialize progress tracking on app.state
    request.app.state.plex_scan_progress = ScanProgress()
    request.app.state.plex_scan_results = None

    # Launch background task
    spawn_background_task(request.app.state, _run_preview_scan(request.app.state))

    return RedirectResponse(url="/scan/plex/progress", status_code=303)


@router.get("/scan/plex/progress", response_class=HTMLResponse)
async def plex_scan_progress_page(request: Request) -> HTMLResponse:
    """Render the progress page that polls for scan status."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "scan_progress.html",
        {"request": request},
    )


@router.get("/api/scan/plex/progress")
async def plex_scan_progress_api(request: Request) -> JSONResponse:
    """Return current scan progress as JSON."""
    progress: ScanProgress | None = getattr(
        request.app.state, "plex_scan_progress", None
    )
    if not progress:
        return JSONResponse({"status": "idle"})

    elapsed = time.monotonic() - progress.started_at if progress.started_at > 0 else 0

    return JSONResponse(
        {
            "status": progress.status,
            "scanned": progress.scanned,
            "total": progress.total,
            "current_title": progress.current_title,
            "error_message": progress.error_message,
            "elapsed_seconds": round(elapsed, 1),
        }
    )


@router.get("/scan/plex/results", response_class=HTMLResponse)
async def plex_scan_results_page(request: Request) -> Response:
    """Render the preview results after a scan completes."""
    templates = request.app.state.templates
    results: ScanResults | None = getattr(request.app.state, "plex_scan_results", None)

    if not results:
        return RedirectResponse(
            url="/?error=No+scan+results+available", status_code=303
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
        },
    )


@router.post("/scan/plex/apply", response_model=None)
async def plex_scan_apply(request: Request) -> RedirectResponse:
    """Apply selected matches from a preview scan."""
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    if not config.plex.url or not config.plex.token:
        return RedirectResponse(url="/?error=Plex+not+configured", status_code=303)

    form = await request.form()
    apply_items = form.getlist("apply_item")

    if not apply_items:
        return RedirectResponse(url="/?message=No+items+to+apply", status_code=303)

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
        for item_str in apply_items:
            # Format: "rating_key|anilist_id|confidence|plex_title"
            parts = str(item_str).split("|", 3)
            if len(parts) < 4:
                logger.warning("Malformed apply_item: %s", item_str)
                errors += 1
                continue

            rating_key, anilist_id_str, confidence_str, plex_title = parts
            try:
                anilist_id = int(anilist_id_str)
                confidence = float(confidence_str)
            except ValueError:
                logger.warning("Invalid numeric values in: %s", item_str)
                errors += 1
                continue

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
                # Structure B: store season-level mappings + write season metadata
                await scanner._store_structure_b_mappings(
                    rating_key,
                    plex_title,
                    group_id,
                    tv_entries,
                    confidence,
                    False,
                )
            else:
                # Store the mapping (with group reference if available)
                await db.upsert_media_mapping(
                    source="plex",
                    source_id=rating_key,
                    source_title=plex_title,
                    anilist_id=anilist_id,
                    anilist_title="",
                    match_confidence=confidence,
                    match_method="fuzzy",
                    series_group_id=group_id,
                    season_number=1,
                )

                # Apply show-level metadata to Plex
                await scanner._apply_anilist_metadata(
                    rating_key, plex_title, anilist_id, confidence, "fuzzy", False
                )
            applied += 1

    except Exception:
        logger.exception("Error during apply")
        errors += 1
    finally:
        await plex_client.close()

    # Auto-dismiss the scan notification now that results have been applied
    await db.dismiss_notifications_by_url("/scan/plex/results")
    await db.clear_dismissed_notifications()

    msg = f"Applied+metadata+to+{applied}+shows"
    if errors:
        msg += f"+({errors}+errors)"
    return_to = getattr(request.app.state, "plex_scan_return_to", "/")
    return RedirectResponse(url=f"{return_to}?message={msg}", status_code=303)


@router.get("/api/scan/plex/search")
async def plex_scan_search(request: Request) -> JSONResponse:
    """Search AniList for anime candidates (AJAX endpoint for Fix Match modal)."""
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
                "season": c.get("season"),
                "format": c.get("format"),
                "episodes": c.get("episodes"),
                "cover_image": cover.get("medium") or cover.get("large") or "",
                "status": c.get("status"),
            }
        )
    return JSONResponse(results)


@router.post("/scan/plex/rematch")
async def plex_scan_rematch(request: Request) -> RedirectResponse:
    """Re-match a single item using a directly-selected AniList ID."""
    form = await request.form()
    rating_key = str(form.get("rating_key", ""))
    anilist_id_str = str(form.get("anilist_id", "")).strip()
    plex_title = str(form.get("plex_title", ""))
    plex_year_str = str(form.get("plex_year", ""))
    library_title = str(form.get("library_title", ""))
    folder_name = str(form.get("folder_name", ""))

    if not rating_key or not anilist_id_str:
        return RedirectResponse(url="/scan/plex/results", status_code=303)

    plex_year: int | None = None
    if plex_year_str:
        try:
            plex_year = int(plex_year_str)
        except ValueError:
            pass

    try:
        anilist_id = int(anilist_id_str)
    except ValueError:
        return RedirectResponse(url="/scan/plex/results", status_code=303)

    anilist_client = request.app.state.anilist_client
    results: ScanResults | None = getattr(request.app.state, "plex_scan_results", None)

    if not results:
        return RedirectResponse(
            url="/?error=No+scan+results+available", status_code=303
        )

    # Fetch the specific AniList entry by ID
    entry = await anilist_client.get_anime_by_id(anilist_id)

    new_item: ScanItemDetail
    if entry:
        anilist_title = get_primary_title(entry)
        title_obj = entry.get("title", {})
        start_date = entry.get("startDate") or {}

        changes: dict[str, str] = {}
        al_title = title_obj.get("english") or title_obj.get("romaji") or ""
        if al_title and al_title != plex_title:
            changes["title"] = al_title
        if entry.get("description"):
            changes["summary"] = "(will update)"
        if entry.get("genres"):
            changes["genres"] = ", ".join(entry["genres"])
        score = entry.get("averageScore")
        if score:
            changes["rating"] = str(round(score / 10, 1))
        cover = (entry.get("coverImage") or {}).get("large", "")
        if cover:
            changes["poster"] = "(will update)"

        new_item = ScanItemDetail(
            rating_key=rating_key,
            plex_title=plex_title,
            plex_year=plex_year,
            library_title=library_title,
            status="matched",
            reason="manual selection",
            anilist_id=anilist_id,
            anilist_title=anilist_title,
            anilist_title_romaji=title_obj.get("romaji") or None,
            anilist_title_english=title_obj.get("english") or None,
            confidence=1.0,
            match_method="manual",
            changes=changes,
            folder_name=folder_name,
            anilist_year=entry.get("seasonYear") or start_date.get("year"),
            anilist_season=entry.get("season"),
            anilist_format=entry.get("format"),
        )
    else:
        new_item = ScanItemDetail(
            rating_key=rating_key,
            plex_title=plex_title,
            plex_year=plex_year,
            library_title=library_title,
            status="failed",
            reason=f"AniList ID {anilist_id} not found",
            folder_name=folder_name,
        )

    # Replace item in results list and adjust counters
    _replace_item_in_results(results, rating_key, new_item)

    return RedirectResponse(url="/scan/plex/results", status_code=303)


def _replace_item_in_results(
    results: ScanResults, rating_key: str, new_item: ScanItemDetail
) -> None:
    """Replace an item in the results list by rating_key, adjusting counters."""
    for i, item in enumerate(results.items):
        if item.rating_key == rating_key:
            old_status = item.status
            results.items[i] = new_item

            # Decrement old counter
            if old_status == "matched":
                results.matched -= 1
            elif old_status == "failed":
                results.failed -= 1
            elif old_status == "skipped":
                results.skipped -= 1

            # Increment new counter
            if new_item.status == "matched":
                results.matched += 1
            elif new_item.status == "failed":
                results.failed += 1
            elif new_item.status == "skipped":
                results.skipped += 1
            return

    # Item not found — just append
    results.items.append(new_item)
    if new_item.status == "matched":
        results.matched += 1
    elif new_item.status == "failed":
        results.failed += 1
