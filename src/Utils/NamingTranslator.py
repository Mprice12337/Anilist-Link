"""Translation utilities for mapping AniList data to external service identifiers.

Handles TVDB/TMDB ID resolution for Sonarr/Radarr integration using AniList
external links and title-based fallback searches.
"""

from __future__ import annotations

import logging
from typing import Any

from src.Clients.AnilistClient import AniListClient

logger = logging.getLogger(__name__)

# AniList external link site names (as returned by the API)
_TVDB_SITE_NAMES = {"The TVDB", "TheTVDB"}
_TMDB_SITE_NAMES = {"The Movie Database", "TMDB"}
_IMDB_SITE_NAMES = {"Internet Movie Database", "IMDb"}


GET_EXTERNAL_LINKS_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english native }
    format
    externalLinks {
      site
      url
      id
    }
  }
}
"""


async def resolve_tvdb_id(anilist_id: int, anilist_client: AniListClient) -> int | None:
    """Attempt to resolve a TVDB ID from AniList external links.

    Returns the TVDB numeric ID if found, else None.
    """
    try:
        data = await anilist_client._execute_query(
            GET_EXTERNAL_LINKS_QUERY, {"id": anilist_id}
        )
        media = data.get("Media", {})
        for link in media.get("externalLinks", []):
            if link.get("site") in _TVDB_SITE_NAMES:
                url = link.get("url", "")
                # Extract numeric ID from URL like
                # https://www.thetvdb.com/series/attack-on-titan or
                # https://thetvdb.com/?tab=series&id=267440
                # Try the id field first (most reliable)
                link_id = link.get("id")
                if link_id and str(link_id).isdigit():
                    return int(link_id)
                # Parse from URL query param
                if "id=" in url:
                    try:
                        part = url.split("id=")[1].split("&")[0]
                        if part.isdigit():
                            return int(part)
                    except (IndexError, ValueError):
                        pass
        return None
    except Exception:
        logger.warning("Failed to resolve TVDB ID for anilist_id=%d", anilist_id)
        return None


async def resolve_tmdb_id(anilist_id: int, anilist_client: AniListClient) -> int | None:
    """Attempt to resolve a TMDB ID from AniList external links.

    Returns the TMDB numeric ID if found, else None.
    """
    try:
        data = await anilist_client._execute_query(
            GET_EXTERNAL_LINKS_QUERY, {"id": anilist_id}
        )
        media = data.get("Media", {})
        for link in media.get("externalLinks", []):
            if link.get("site") in _TMDB_SITE_NAMES:
                url = link.get("url", "")
                link_id = link.get("id")
                if link_id and str(link_id).isdigit():
                    return int(link_id)
                if "/movie/" in url or "/tv/" in url:
                    try:
                        part = url.rstrip("/").split("/")[-1]
                        if part.isdigit():
                            return int(part)
                    except (IndexError, ValueError):
                        pass
        return None
    except Exception:
        logger.warning("Failed to resolve TMDB ID for anilist_id=%d", anilist_id)
        return None


def get_preferred_title(media: dict[str, Any]) -> str:
    """Return the best display title from an AniList media object."""
    title = media.get("title", {})
    return (
        title.get("english") or title.get("romaji") or title.get("native") or "Unknown"
    )


def get_all_titles(media: dict[str, Any]) -> list[str]:
    """Return all non-empty titles and synonyms for a media entry."""
    title = media.get("title", {})
    titles: list[str] = []
    for key in ("english", "romaji", "native"):
        val = title.get(key)
        if val:
            titles.append(val)
    for syn in media.get("synonyms", []):
        if syn and syn not in titles:
            titles.append(syn)
    return titles


def is_movie_format(anilist_format: str) -> bool:
    """Return True if the AniList format should be sent to Radarr rather than Sonarr."""
    return anilist_format in ("MOVIE",)
