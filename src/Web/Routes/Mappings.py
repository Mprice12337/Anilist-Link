"""Manual override management endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.responses import Response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mappings"])


@router.get("/mappings", response_model=None)
async def mappings_page(request: Request) -> Response:
    """List all manual overrides."""
    db = request.app.state.db
    templates = request.app.state.templates

    # Redirect to unified library — overrides now managed inline there
    if not request.query_params.get("legacy"):
        return RedirectResponse(url="/library", status_code=302)

    overrides = await db.get_all_overrides()

    return templates.TemplateResponse(
        "mappings.html",
        {
            "request": request,
            "overrides": overrides,
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@router.post("/mappings/add")
async def mappings_add(request: Request) -> RedirectResponse:
    """Add a new manual override."""
    db = request.app.state.db
    form = await request.form()

    source = str(form.get("source", "")).strip()
    source_id = str(form.get("source_id", "")).strip()
    source_title = str(form.get("source_title", "")).strip()
    anilist_id_str = str(form.get("anilist_id", "")).strip()

    if not source or not source_id or not anilist_id_str:
        return RedirectResponse(
            url="/mappings?error=Source,+source+ID,+and+AniList+ID+are+required",
            status_code=303,
        )

    try:
        anilist_id = int(anilist_id_str)
    except ValueError:
        return RedirectResponse(
            url="/mappings?error=Invalid+AniList+ID", status_code=303
        )

    # Check for duplicate
    existing = await db.get_override(source, source_id)
    if existing:
        return RedirectResponse(
            url="/mappings?error=An+override+for+this+source+ID+already+exists",
            status_code=303,
        )

    await db.add_override(
        source=source,
        source_id=source_id,
        source_title=source_title,
        anilist_id=anilist_id,
    )

    logger.info(
        "Added manual override: %s/%s -> AniList %d", source, source_id, anilist_id
    )
    return RedirectResponse(url="/mappings?message=Override+added", status_code=303)


@router.post("/mappings/{override_id}/delete")
async def mappings_delete(request: Request, override_id: int) -> RedirectResponse:
    """Delete a manual override by ID."""
    db = request.app.state.db
    await db.delete_override(override_id)
    logger.info("Deleted manual override %d", override_id)
    return RedirectResponse(url="/mappings?message=Override+deleted", status_code=303)


@router.get("/api/mappings/search")
async def mappings_search(request: Request) -> JSONResponse:
    """Search AniList for anime candidates (for the add-override form)."""
    q = request.query_params.get("q", "").strip()
    if not q:
        return JSONResponse([])

    anilist_client = request.app.state.anilist_client
    candidates = await anilist_client.search_anime(q, per_page=10)

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
            }
        )

    return JSONResponse(results)
