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
