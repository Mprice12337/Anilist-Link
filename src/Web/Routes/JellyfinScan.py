"""Jellyfin scan background tasks and API endpoints."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from src.Clients.AnilistClient import AniListClient
from src.Clients.JellyfinClient import JellyfinClient
from src.Matching.TitleMatcher import TitleMatcher, get_primary_title
from src.Scanner.JellyfinMetadataScanner import JellyfinMetadataScanner
from src.Scanner.MetadataScanner import ScanItemDetail, ScanProgress, ScanResults
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jellyfin-scan"])


# ---------------------------------------------------------------------------
# Background coroutines (imported by JellyfinLibrary)
# ---------------------------------------------------------------------------


async def _run_jellyfin_preview_scan(app_state: object) -> None:
    """Background coroutine: preview scan against Jellyfin."""
    config = app_state.config  # type: ignore[attr-defined]
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client: AniListClient = app_state.anilist_client  # type: ignore[attr-defined]
    progress: ScanProgress = app_state.jellyfin_scan_progress  # type: ignore[attr-defined]

    jellyfin_client = JellyfinClient(
        url=config.jellyfin.url, api_key=config.jellyfin.api_key
    )
    title_matcher = TitleMatcher(similarity_threshold=0.75)
    group_builder = SeriesGroupBuilder(db, anilist_client)
    scanner = JellyfinMetadataScanner(
        db, anilist_client, title_matcher, jellyfin_client, config, group_builder
    )

    library_ids = getattr(app_state, "jellyfin_scan_library_ids", None)
    if not library_ids and config.jellyfin.anime_library_ids:
        library_ids = list(config.jellyfin.anime_library_ids)

    try:
        # Refresh only the selected libraries so item IDs are stable before
        # we attempt any matching or metadata writes.
        if progress:
            progress.current_title = "Waiting for Jellyfin library refresh..."
        await jellyfin_client.refresh_library_and_wait(
            inactivity_timeout=120.0, library_ids=library_ids or None
        )

        results = await scanner.run_scan(
            preview=True, library_ids=library_ids, progress=progress
        )
        app_state.jellyfin_scan_results = results  # type: ignore[attr-defined]
    except Exception:
        logger.exception("Jellyfin preview scan failed")
        progress.status = "error"
        progress.error_message = "Preview scan failed unexpectedly"
    finally:
        await jellyfin_client.close()


async def _run_jellyfin_live_scan(app_state: object) -> None:
    """Background coroutine: live scan — match and write metadata to Jellyfin."""
    config = app_state.config  # type: ignore[attr-defined]
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client: AniListClient = app_state.anilist_client  # type: ignore[attr-defined]
    progress: ScanProgress = app_state.jellyfin_scan_progress  # type: ignore[attr-defined]

    jellyfin_client = JellyfinClient(
        url=config.jellyfin.url, api_key=config.jellyfin.api_key
    )
    title_matcher = TitleMatcher(similarity_threshold=0.75)
    group_builder = SeriesGroupBuilder(db, anilist_client)
    scanner = JellyfinMetadataScanner(
        db, anilist_client, title_matcher, jellyfin_client, config, group_builder
    )

    library_ids = getattr(app_state, "jellyfin_scan_library_ids", None)
    if not library_ids and config.jellyfin.anime_library_ids:
        library_ids = list(config.jellyfin.anime_library_ids)

    try:
        # Refresh only the selected libraries so item IDs are stable before
        # we attempt any matching or metadata writes.
        if progress:
            progress.current_title = "Waiting for Jellyfin library refresh..."
        await jellyfin_client.refresh_library_and_wait(
            inactivity_timeout=120.0, library_ids=library_ids or None
        )

        results = await scanner.run_scan(
            preview=False, library_ids=library_ids, progress=progress
        )
        app_state.jellyfin_scan_results = results  # type: ignore[attr-defined]

        # Second refresh so Jellyfin picks up the NFO files and metadata
        # changes that were just written.
        if progress:
            progress.current_title = "Triggering Jellyfin re-index..."
        await jellyfin_client.refresh_library_and_wait(
            inactivity_timeout=120.0, library_ids=library_ids or None
        )
    except Exception:
        logger.exception("Jellyfin live scan failed")
        progress.status = "error"
        progress.error_message = "Live scan failed unexpectedly"
    finally:
        await jellyfin_client.close()


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@router.get("/api/scan/jellyfin/progress")
async def jellyfin_scan_progress_api(request: Request) -> JSONResponse:
    """Return current scan progress as JSON."""
    progress: ScanProgress | None = getattr(
        request.app.state, "jellyfin_scan_progress", None
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


@router.get("/api/scan/jellyfin/search")
async def jellyfin_scan_search(request: Request) -> JSONResponse:
    """Search AniList for anime candidates (for the Fix Match modal)."""
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


@router.post("/scan/jellyfin/apply")
async def jellyfin_scan_apply(request: Request) -> RedirectResponse:
    """Apply selected preview matches — store mappings and write metadata."""
    config = request.app.state.config
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    if not config.jellyfin.url or not config.jellyfin.api_key:
        return RedirectResponse(
            url="/jellyfin?error=Jellyfin+not+configured", status_code=303
        )

    form = await request.form()
    apply_items = form.getlist("apply_item")

    if not apply_items:
        return RedirectResponse(
            url="/jellyfin?message=No+items+to+apply", status_code=303
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

    # Use the library IDs that were active during the scan (or settings fallback)
    scan_library_ids: list[str] | None = getattr(
        request.app.state, "jellyfin_scan_library_ids", None
    )
    if not scan_library_ids and config.jellyfin.anime_library_ids:
        scan_library_ids = list(config.jellyfin.anime_library_ids)

    try:
        # Refresh only the selected libraries before writing so IDs are stable.
        await jellyfin_client.refresh_library_and_wait(
            inactivity_timeout=120.0, library_ids=scan_library_ids or None
        )

        for item_str in apply_items:
            # Format: "item_id|anilist_id|confidence|title"
            parts = str(item_str).split("|", 3)
            if len(parts) < 4:
                logger.warning("Malformed apply_item: %s", item_str)
                errors += 1
                continue

            item_id, anilist_id_str, confidence_str, title = parts
            try:
                anilist_id = int(anilist_id_str)
                confidence = float(confidence_str)
            except ValueError:
                errors += 1
                continue

            group_id = None
            group_entries: list[dict] = []
            try:
                group_id, group_entries = await group_builder.get_or_build_group(
                    anilist_id
                )
            except Exception:
                logger.debug("Could not build series group for %s", title)

            root_anilist_id = (
                group_entries[0]["anilist_id"] if group_entries else anilist_id
            )

            # Determine season number from position in group (chronological order)
            season_number: int | None = None
            if group_entries:
                for i, entry in enumerate(group_entries):
                    if entry.get("anilist_id") == anilist_id:
                        season_number = i + 1
                        break
                if season_number is None:
                    season_number = 1

            await db.upsert_media_mapping(
                source="jellyfin",
                source_id=item_id,
                source_title=title,
                anilist_id=anilist_id,
                anilist_title="",
                match_confidence=confidence,
                match_method="fuzzy",
                series_group_id=group_id,
                season_number=1,
            )
            await scanner._apply_anilist_metadata(
                item_id,
                title,
                anilist_id,
                confidence,
                "fuzzy",
                False,
                parent_anilist_id=root_anilist_id,
                season_number=season_number,
            )
            applied += 1

        # Trigger a re-index so Jellyfin picks up the NFO files just written.
        await jellyfin_client.refresh_library_and_wait(
            inactivity_timeout=120.0, library_ids=scan_library_ids or None
        )
    except Exception:
        logger.exception("Error during Jellyfin apply")
        errors += 1
    finally:
        await jellyfin_client.close()

    # Auto-dismiss the scan notification now that results have been applied
    await db.dismiss_notifications_by_url("/jellyfin/scan/results")
    await db.clear_dismissed_notifications()

    msg = f"Applied+metadata+to+{applied}+shows"
    if errors:
        msg += f"+({errors}+errors)"
    return_to = getattr(request.app.state, "jellyfin_scan_return_to", "/jellyfin")
    return RedirectResponse(url=f"{return_to}?message={msg}", status_code=303)


@router.post("/scan/jellyfin/rematch")
async def jellyfin_scan_rematch(request: Request) -> RedirectResponse:
    """Re-match a single item using a directly-selected AniList ID."""
    form = await request.form()
    item_id = str(form.get("rating_key", ""))
    anilist_id_str = str(form.get("anilist_id", "")).strip()
    title = str(form.get("plex_title", ""))
    year_str = str(form.get("plex_year", ""))
    library_title = str(form.get("library_title", ""))
    folder_name = str(form.get("folder_name", ""))

    if not item_id or not anilist_id_str:
        return RedirectResponse(url="/jellyfin/scan/results", status_code=303)

    year: int | None = None
    if year_str:
        try:
            year = int(year_str)
        except ValueError:
            pass

    try:
        anilist_id = int(anilist_id_str)
    except ValueError:
        return RedirectResponse(url="/jellyfin/scan/results", status_code=303)

    anilist_client = request.app.state.anilist_client
    results: ScanResults | None = getattr(
        request.app.state, "jellyfin_scan_results", None
    )
    if not results:
        return RedirectResponse(
            url="/jellyfin?error=No+scan+results+available", status_code=303
        )

    entry = await anilist_client.get_anime_by_id(anilist_id)

    if entry:
        anilist_title = get_primary_title(entry)
        title_obj = entry.get("title", {})
        start_date = entry.get("startDate") or {}

        changes: dict[str, str] = {}
        al_title = title_obj.get("english") or title_obj.get("romaji") or ""
        if al_title and al_title != title:
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
            rating_key=item_id,
            plex_title=title,
            plex_year=year,
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
            rating_key=item_id,
            plex_title=title,
            plex_year=year,
            library_title=library_title,
            status="failed",
            reason=f"AniList ID {anilist_id} not found",
            folder_name=folder_name,
        )

    _replace_item_in_results(results, item_id, new_item)
    return RedirectResponse(url="/jellyfin/scan/results", status_code=303)


def _replace_item_in_results(
    results: ScanResults, item_id: str, new_item: ScanItemDetail
) -> None:
    for i, item in enumerate(results.items):
        if item.rating_key == item_id:
            old_status = item.status
            results.items[i] = new_item
            if old_status == "matched":
                results.matched -= 1
            elif old_status == "failed":
                results.failed -= 1
            elif old_status == "skipped":
                results.skipped -= 1
            if new_item.status == "matched":
                results.matched += 1
            elif new_item.status == "failed":
                results.failed += 1
            elif new_item.status == "skipped":
                results.skipped += 1
            return
    results.items.append(new_item)
    if new_item.status == "matched":
        results.matched += 1
    elif new_item.status == "failed":
        results.failed += 1
