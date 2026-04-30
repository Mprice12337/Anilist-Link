"""Unified library browser — shows local, Plex, and Jellyfin items in one view."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import Response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["unified-library"])


def _make_local_entry(
    item: dict[str, Any],
    lib: dict[str, Any],
    anilist_id: int | None = None,
    title: str = "",
    cover_image: str | None = None,
) -> dict[str, Any]:
    """Build a unified-library local entry dict from a library_items row.

    When *anilist_id* differs from the item's own anilist_id (virtual entry
    for a related season), cover_url uses the explicitly supplied *cover_image*
    (typically from anilist_cache via series_group_entries JOIN).
    """
    aid = anilist_id if anilist_id is not None else item.get("anilist_id")
    own = aid == item.get("anilist_id")
    cover = (
        (item.get("display_cover") or item.get("cover_image") or None)
        if own
        else (cover_image or None)
    )
    return {
        "source": "local",
        "sources": ["local"],
        "source_id": str(item.get("id", "")),
        "folder_name": item.get("folder_name", ""),
        "folder_path": item.get("folder_path", ""),
        "title": title or item.get("anilist_title") or item.get("folder_name", ""),
        "cover_url": cover,
        "anilist_id": aid,
        "title_romaji": item.get("title_romaji") or "",
        "title_english": item.get("title_english") or "",
        "match_confidence": item.get("match_confidence"),
        "match_method": item.get("match_method") or "",
        "episodes": item.get("episodes"),
        "anilist_status": item.get("anilist_status") or "",
        "year": item.get("anilist_year") or item.get("year") or None,
        "library_name": lib.get("name", ""),
        "virtual": False,
    }


async def _get_local_items(db: Any) -> list[dict[str, Any]]:
    """Return all local library items across all libraries.

    Each library_items row is emitted as one local entry.  Multi-season shows
    that went through Structure B detection already have a separate DB row per
    season subdir (each with the correct per-season anilist_id), so no
    expansion is needed here.  _aggregate_by_anilist_id handles deduplication
    of any rows that share an anilist_id (e.g. a Specials folder that fell
    back to the series S1 id).

    When an item has a series_group_id, virtual entries are emitted for every
    entry in that series group so that Plex/Jellyfin items matched to
    individual group members (e.g. Evangelion Rebuild movies) can merge with
    the local presence via _aggregate_by_anilist_id.
    """
    libraries = await db.get_all_libraries()
    result: list[dict[str, Any]] = []

    # Pre-load series group entries referenced by library items to avoid N+1
    _sg_cache: dict[int, list[dict[str, Any]]] = {}

    for lib in libraries:
        items = await db.get_library_items_with_cache(lib["id"])
        for item in items:
            result.append(_make_local_entry(item, lib))

            # Expand series group: emit a virtual local entry per group member
            # whose anilist_id differs from the item's own id.
            sg_id = item.get("series_group_id")
            if not sg_id:
                continue
            if sg_id not in _sg_cache:
                _sg_cache[sg_id] = await db.fetch_all(
                    "SELECT sge.anilist_id, sge.display_title,"
                    "       sge.format, sge.episodes, sge.start_date,"
                    "       ac.cover_image, ac.title_romaji,"
                    "       ac.title_english, ac.year AS anilist_year"
                    "  FROM series_group_entries sge"
                    "  LEFT JOIN anilist_cache ac"
                    "    ON ac.anilist_id = sge.anilist_id"
                    "   AND ac.expires_at > datetime('now')"
                    " WHERE sge.group_id = ?",
                    (sg_id,),
                )
            own_aid = item.get("anilist_id")
            for sge in _sg_cache[sg_id]:
                if sge["anilist_id"] == own_aid:
                    continue  # already emitted above
                virtual_entry = _make_local_entry(
                    item,
                    lib,
                    anilist_id=sge["anilist_id"],
                    title=sge.get("display_title") or "",
                    cover_image=sge.get("cover_image") or None,
                )
                virtual_entry["virtual"] = True
                # Override romaji/english from anilist_cache so the
                # virtual entry displays its own title, not the parent's.
                virtual_entry["title_romaji"] = sge.get("title_romaji") or ""
                virtual_entry["title_english"] = sge.get("title_english") or ""
                virtual_entry["year"] = sge.get("anilist_year") or None
                result.append(virtual_entry)
    return result


async def _get_plex_items(db: Any, plex_url: str = "") -> list[dict[str, Any]]:
    """Return Plex media items from the DB cache."""
    items = await db.get_plex_media_with_mappings(None)
    result: list[dict[str, Any]] = []
    for item in items:
        # Prefer AniList cover; fall back to Plex thumb proxy
        cover_url = item.get("cover_image") or None
        thumb = item.get("thumb") or ""
        if not cover_url and thumb:
            cover_url = f"/api/plex/thumb?path={quote(thumb)}"
        result.append(
            {
                "source": "plex",
                "sources": ["plex"],
                "source_id": str(item.get("rating_key", "")),
                "folder_name": item.get("folder_name", ""),
                "folder_path": "",
                "title": item.get("plex_title") or item.get("folder_name", ""),
                "cover_url": cover_url,
                "anilist_id": item.get("anilist_id"),
                "title_romaji": item.get("title_romaji") or "",
                "title_english": item.get("title_english") or "",
                "match_confidence": item.get("match_confidence"),
                "match_method": item.get("match_method") or "",
                "episodes": item.get("episodes"),
                "anilist_status": item.get("anilist_status") or "",
                "year": item.get("anilist_year") or item.get("plex_year"),
                "library_name": item.get("library_title") or "Plex",
            }
        )
    return result


async def _get_jellyfin_items(db: Any, jellyfin_url: str = "") -> list[dict[str, Any]]:
    """Return Jellyfin media items from the DB cache."""
    items = await db.get_jellyfin_media_with_mappings(None)
    result: list[dict[str, Any]] = []
    base = jellyfin_url.rstrip("/") if jellyfin_url else ""
    for item in items:
        cover_url = item.get("cover_image") or None
        item_id = item.get("item_id") or ""
        if not cover_url and item_id and base:
            cover_url = f"{base}/Items/{item_id}/Images/Primary?maxHeight=300"
        result.append(
            {
                "source": "jellyfin",
                "sources": ["jellyfin"],
                "source_id": str(item_id),
                "folder_name": item.get("folder_name", ""),
                "folder_path": item.get("path", ""),
                "title": (item.get("jellyfin_title") or item.get("folder_name", "")),
                "cover_url": cover_url,
                "anilist_id": item.get("anilist_id"),
                "title_romaji": item.get("title_romaji") or "",
                "title_english": item.get("title_english") or "",
                "match_confidence": item.get("match_confidence"),
                "match_method": item.get("match_method") or "",
                "episodes": item.get("episodes"),
                "anilist_status": item.get("anilist_status") or "",
                "year": item.get("anilist_year") or item.get("jellyfin_year"),
                "library_name": item.get("library_name") or "Jellyfin",
            }
        )
    return result


def _aggregate_by_anilist_id(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    When showing all sources, merge items that share the same anilist_id
    so one card shows multiple source badges.
    Unmatched items (anilist_id=None) remain separate, keyed by folder_name.
    """
    by_key: dict[str, dict[str, Any]] = {}
    unmatched: list[dict[str, Any]] = []

    for item in items:
        aid = item.get("anilist_id")
        if aid:
            key = str(aid)
            if key in by_key:
                existing = by_key[key]
                # Merge sources list (deduplicate)
                for s in item["sources"]:
                    if s not in existing["sources"]:
                        existing["sources"].append(s)
                # If a non-virtual item merges in, result is non-virtual
                if not item.get("virtual", False):
                    existing["virtual"] = False
                # Prefer AniList cover; keep first non-null cover
                if not existing["cover_url"] and item["cover_url"]:
                    existing["cover_url"] = item["cover_url"]
                # Keep highest confidence
                ec = existing.get("match_confidence") or 0
                ic = item.get("match_confidence") or 0
                if ic > ec:
                    existing["match_confidence"] = ic
                    existing["match_method"] = item.get("match_method") or ""
            else:
                by_key[key] = dict(item)
                by_key[key]["source"] = "multi"
        else:
            # Group unmatched by folder_name
            fn = item.get("folder_name", "")
            if fn:
                existing = next(
                    (u for u in unmatched if u.get("folder_name") == fn), {}
                )
                if existing:
                    for s in item["sources"]:
                        if s not in existing["sources"]:
                            existing["sources"].append(s)
                else:
                    unmatched.append(dict(item))
            else:
                unmatched.append(dict(item))

    result = list(by_key.values()) + unmatched
    result.sort(key=lambda x: (x.get("title_romaji") or x.get("title") or "").lower())
    return result


@router.get("/library", response_class=HTMLResponse, response_model=None)
async def unified_library(
    request: Request,
    source: str = "all",
) -> Response:
    """Unified library browser across local, Plex, and Jellyfin sources."""
    db = request.app.state.db
    config = request.app.state.config
    templates = request.app.state.templates

    plex_configured = bool(config.plex.url and config.plex.token)
    jellyfin_configured = bool(config.jellyfin.url and config.jellyfin.api_key)
    plex_url = config.plex.url or ""
    jellyfin_url = config.jellyfin.url or ""

    raw_items: list[dict[str, Any]] = []

    try:
        if source in ("all", "local"):
            raw_items.extend(await _get_local_items(db))
        if source in ("all", "plex") and plex_configured:
            raw_items.extend(await _get_plex_items(db, plex_url))
        if source in ("all", "jellyfin") and jellyfin_configured:
            raw_items.extend(await _get_jellyfin_items(db, jellyfin_url))
    except Exception:
        logger.exception("Error loading unified library items")

    # Always deduplicate — source-filtered views can still have multiple
    # virtual entries sharing the same anilist_id (e.g. from series-group
    # expansion), and _aggregate_by_anilist_id is safe to run on any subset.
    items = _aggregate_by_anilist_id(raw_items)

    # Remove phantom virtual entries that didn't merge with any non-local
    # source.  These are series-group expansions for anime the user doesn't
    # actually have on disk — they only exist to enable cross-source merging.
    items = [
        i
        for i in items
        if not i.get("virtual") or set(i.get("sources", [])) != {"local"}
    ]

    # Enrich with AniList watch status from user_watchlist
    users = await db.get_users_by_service("anilist")
    if users:
        wl_user_id = users[0]["user_id"]
        wl_rows = await db.fetch_all(
            "SELECT anilist_id, list_status, progress"
            " FROM user_watchlist WHERE user_id=?",
            (wl_user_id,),
        )
        wl_map = {r["anilist_id"]: r for r in wl_rows}
        for item in items:
            aid = item.get("anilist_id")
            if aid and aid in wl_map:
                item["list_status"] = wl_map[aid]["list_status"]
                item["progress"] = wl_map[aid]["progress"]

    matched_count = sum(1 for i in items if i.get("anilist_id"))
    title_display = await db.get_setting("app.title_display") or "romaji"

    return templates.TemplateResponse(
        "unified_library.html",
        {
            "request": request,
            "items": items,
            "item_count": len(items),
            "matched_count": matched_count,
            "source": source,
            "plex_configured": plex_configured,
            "jellyfin_configured": jellyfin_configured,
            "title_display": title_display,
            "message": request.query_params.get("message") or "",
            "error": request.query_params.get("error") or "",
            "version": "0.1.0",
        },
    )


@router.post("/library/update-match")
async def library_update_match(request: Request) -> JSONResponse:
    """Update the AniList match for a library item (any source)."""
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client
    body = await request.json()

    source = str(body.get("source", "")).strip()
    source_id = str(body.get("source_id", "")).strip()
    anilist_id = body.get("anilist_id")

    if not source or not source_id or not anilist_id:
        return JSONResponse({"ok": False, "error": "Missing fields"}, status_code=400)

    try:
        anilist_id = int(anilist_id)
    except (ValueError, TypeError):
        return JSONResponse(
            {"ok": False, "error": "Invalid anilist_id"}, status_code=400
        )

    entry = await anilist_client.get_anime_by_id(anilist_id)
    anilist_title = ""
    if entry:
        title_obj = entry.get("title", {})
        anilist_title = title_obj.get("romaji") or title_obj.get("english") or ""
        year = entry.get("seasonYear") or (
            (entry.get("startDate") or {}).get("year") or 0
        )
        await db.set_cached_metadata(
            anilist_id=anilist_id,
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

    await db.upsert_media_mapping(
        source=source,
        source_id=source_id,
        source_title=body.get("source_title", ""),
        anilist_id=anilist_id,
        anilist_title=anilist_title,
        match_confidence=1.0,
        match_method="manual",
    )
    return JSONResponse({"ok": True})


@router.get("/api/search/anilist")
async def anilist_search(request: Request, q: str = "") -> JSONResponse:
    """Consolidated AniList title search for all library match modals."""
    q = q.strip()
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
