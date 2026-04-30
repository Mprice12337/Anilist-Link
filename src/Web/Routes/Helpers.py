"""Shared helpers for route handlers.

Consolidates repeated service initialization, AniList metadata extraction,
and scan result formatting used across Plex/Jellyfin/Library routes.
"""

from __future__ import annotations

import json
from typing import Any

from src.Database.Connection import DatabaseManager
from src.Matching.TitleMatcher import TitleMatcher
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder

# Default threshold used by all scanners / route handlers
MATCH_THRESHOLD = 0.75


def create_title_matcher() -> TitleMatcher:
    """Create a TitleMatcher with the standard similarity threshold."""
    return TitleMatcher(similarity_threshold=MATCH_THRESHOLD)


def create_group_builder(
    db: DatabaseManager, anilist_client: Any
) -> SeriesGroupBuilder:
    """Create a SeriesGroupBuilder with standard dependencies."""
    return SeriesGroupBuilder(db, anilist_client)


def format_anilist_search_results(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Format raw AniList search results into the standard shape used by
    scan search and library search endpoints.
    """
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
    return results


def build_rematch_changes(entry: dict[str, Any], source_title: str) -> dict[str, str]:
    """Build the metadata changes dict from an AniList entry.

    Used by scan rematch and library rematch endpoints to show what
    metadata will be updated for a manual match selection.
    """
    title_obj = entry.get("title", {})
    changes: dict[str, str] = {}
    al_title = title_obj.get("english") or title_obj.get("romaji") or ""
    if al_title and al_title != source_title:
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
    return changes


async def cache_anilist_entry(db: DatabaseManager, entry: dict[str, Any]) -> None:
    """Cache an AniList entry's metadata into the database.

    Used by update-match endpoints to persist fetched metadata.
    """
    anilist_id = entry["id"]
    title_obj = entry.get("title", {})
    year = entry.get("seasonYear") or ((entry.get("startDate") or {}).get("year") or 0)
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


def get_anilist_display_title(entry: dict[str, Any]) -> str:
    """Get the preferred display title from an AniList entry."""
    t = entry.get("title", {})
    return t.get("romaji") or t.get("english") or ""


async def enrich_watchlist_entries(
    db: "DatabaseManager",
    raw_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add sources, folder_path, arr info to watchlist rows.

    Shared between the Watchlist page and the Dashboard so both have
    the same enriched data shape for the detail modal.
    """
    # Per-anilist_id source list and path from media_mappings + library_items
    source_map: dict[int, set[str]] = {}
    path_map: dict[int, str] = {}
    mapping_rows = await db.fetch_all("SELECT anilist_id, source FROM media_mappings")
    for row in mapping_rows:
        aid = row["anilist_id"]
        source_map.setdefault(aid, set()).add(row["source"])

    # Folder paths from library_items (local source has the filesystem path)
    lib_rows = await db.fetch_all(
        "SELECT anilist_id, folder_path FROM library_items"
        " WHERE anilist_id IS NOT NULL AND folder_path != ''"
    )
    for row in lib_rows:
        path_map.setdefault(row["anilist_id"], row["folder_path"])

    # Sonarr tracking
    sonarr_info: dict[int, dict] = {}
    sonarr_rows = await db.fetch_all(
        "SELECT anilist_id, sonarr_id, sonarr_season,"
        " sonarr_monitored, monitor_type"
        " FROM anilist_sonarr_mapping WHERE in_sonarr=1"
    )
    for row in sonarr_rows:
        sonarr_info[row["anilist_id"]] = {
            "sonarr_id": row["sonarr_id"],
            "sonarr_season": row["sonarr_season"],
            "sonarr_monitored": bool(row["sonarr_monitored"]),
            "monitor_type": row["monitor_type"] or "future",
        }

    # Radarr tracking
    radarr_info: dict[int, dict] = {}
    radarr_rows = await db.fetch_all(
        "SELECT anilist_id, radarr_id, radarr_monitored, monitor_type"
        " FROM anilist_radarr_mapping WHERE in_radarr=1"
    )
    for row in radarr_rows:
        radarr_info[row["anilist_id"]] = {
            "radarr_id": row["radarr_id"],
            "radarr_monitored": bool(row["radarr_monitored"]),
            "monitor_type": row["monitor_type"] or "future",
        }

    enriched: list[dict[str, Any]] = []
    for entry in raw_entries:
        aid = entry["anilist_id"]
        e = dict(entry)

        # Sources and local_status
        sources = sorted(source_map.get(aid, set()))
        e["sources"] = sources
        e["local_status"] = "have" if sources else "missing"
        e["folder_path"] = path_map.get(aid, "")

        # Sonarr / Radarr
        if aid in sonarr_info:
            si = sonarr_info[aid]
            e["arr_status"] = "monitored" if si["sonarr_monitored"] else "tracked"
            e["arr_service"] = "sonarr"
            e["sonarr_id"] = si["sonarr_id"]
            e["radarr_id"] = None
            e["sonarr_season"] = si["sonarr_season"]
            e["monitor_type"] = si["monitor_type"]
        elif aid in radarr_info:
            ri = radarr_info[aid]
            e["arr_status"] = "monitored" if ri["radarr_monitored"] else "tracked"
            e["arr_service"] = "radarr"
            e["sonarr_id"] = None
            e["radarr_id"] = ri["radarr_id"]
            e["sonarr_season"] = None
            e["monitor_type"] = ri["monitor_type"]
        else:
            e["arr_status"] = "untracked"
            e["arr_service"] = ""
            e["sonarr_id"] = None
            e["radarr_id"] = None
            e["sonarr_season"] = None
            e["monitor_type"] = ""

        enriched.append(e)

    return enriched
