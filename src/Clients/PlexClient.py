"""Plex Media Server API client."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CLIENT_IDENTIFIER = "anilist-link"


@dataclass
class PlexLibrary:
    """Represents a Plex library section."""

    key: str
    title: str
    type: str
    item_count: int


@dataclass
class PlexShow:
    """Represents a show in a Plex library."""

    rating_key: str
    title: str
    year: int | None
    thumb: str
    summary: str
    library_key: str
    locations: list[str] = field(default_factory=list)

    @property
    def folder_name(self) -> str:
        """Extract folder name from the first location path, falls back to title."""
        if self.locations:
            name = os.path.basename(self.locations[0])
            if name:
                return name
        return self.title


@dataclass
class PlexSeason:
    """Represents a season within a Plex show."""

    rating_key: str
    index: int  # season number (0 = Specials)
    title: str
    episode_count: int
    parent_rating_key: str


@dataclass
class PlexEpisode:
    """Represents an episode in a Plex library."""

    rating_key: str
    grandparent_title: str
    parent_index: int  # season number
    index: int  # episode number
    view_count: int


class PlexClient:
    """Async HTTP client for Plex Media Server REST API."""

    def __init__(self, url: str, token: str) -> None:
        base_url = url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "X-Plex-Token": token,
                "Accept": "application/json",
                "X-Plex-Client-Identifier": CLIENT_IDENTIFIER,
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def test_connection(self) -> str:
        """Verify server is reachable. Returns the server name."""
        resp = await self._http.get("/")
        resp.raise_for_status()
        data = resp.json()
        name = data.get("MediaContainer", {}).get("friendlyName", "Unknown")
        logger.info("Connected to Plex server: %s", name)
        return name

    async def get_libraries(self) -> list[PlexLibrary]:
        """List all libraries (sections) on the server."""
        resp = await self._http.get("/library/sections")
        resp.raise_for_status()
        container = resp.json().get("MediaContainer", {})
        directories = container.get("Directory", [])
        libraries: list[PlexLibrary] = []
        for d in directories:
            libraries.append(
                PlexLibrary(
                    key=str(d["key"]),
                    title=d.get("title", ""),
                    type=d.get("type", ""),
                    item_count=int(d.get("count", 0)),
                )
            )
        return libraries

    async def get_library_shows(self, library_key: str) -> list[PlexShow]:
        """Get all shows in a library section."""
        resp = await self._http.get(f"/library/sections/{library_key}/all")
        resp.raise_for_status()
        container = resp.json().get("MediaContainer", {})
        metadata = container.get("Metadata", [])
        shows: list[PlexShow] = []
        for m in metadata:
            locations = [
                loc["path"] for loc in m.get("Location", []) if loc.get("path")
            ]
            shows.append(
                PlexShow(
                    rating_key=str(m["ratingKey"]),
                    title=m.get("title", ""),
                    year=m.get("year"),
                    thumb=m.get("thumb", ""),
                    summary=m.get("summary", ""),
                    library_key=library_key,
                    locations=locations,
                )
            )
        return shows

    async def get_show_locations(self, rating_key: str) -> list[str]:
        """Fetch the file-system locations for a single show.

        The bulk ``/all`` endpoint omits Location data, so this fetches the
        individual metadata endpoint which always includes it.
        """
        try:
            resp = await self._http.get(f"/library/metadata/{rating_key}")
            resp.raise_for_status()
            container = resp.json().get("MediaContainer", {})
            metadata = container.get("Metadata", [])
            if metadata:
                return [
                    loc["path"]
                    for loc in metadata[0].get("Location", [])
                    if loc.get("path")
                ]
        except Exception:
            logger.debug("Failed to fetch locations for rating_key=%s", rating_key)
        return []

    async def get_show_episodes(self, rating_key: str) -> list[PlexEpisode]:
        """Get all episodes for a show."""
        resp = await self._http.get(f"/library/metadata/{rating_key}/allLeaves")
        resp.raise_for_status()
        container = resp.json().get("MediaContainer", {})
        metadata = container.get("Metadata", [])
        episodes: list[PlexEpisode] = []
        for m in metadata:
            episodes.append(
                PlexEpisode(
                    rating_key=str(m["ratingKey"]),
                    grandparent_title=m.get("grandparentTitle", ""),
                    parent_index=int(m.get("parentIndex", 0)),
                    index=int(m.get("index", 0)),
                    view_count=int(m.get("viewCount", 0)),
                )
            )
        return episodes

    async def get_show_seasons(self, rating_key: str) -> list[PlexSeason]:
        """Get all seasons for a show (via /library/metadata/{key}/children)."""
        resp = await self._http.get(f"/library/metadata/{rating_key}/children")
        resp.raise_for_status()
        container = resp.json().get("MediaContainer", {})
        metadata = container.get("Metadata", [])
        seasons: list[PlexSeason] = []
        for m in metadata:
            seasons.append(
                PlexSeason(
                    rating_key=str(m["ratingKey"]),
                    index=int(m.get("index", 0)),
                    title=m.get("title", ""),
                    episode_count=int(m.get("leafCount", 0)),
                    parent_rating_key=rating_key,
                )
            )
        return seasons

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def update_show_metadata(
        self, rating_key: str, fields: dict[str, Any]
    ) -> None:
        """Write metadata fields to a show.

        ``fields`` uses Plex form-style keys:
        - ``title.value``, ``summary.value``, ``rating.value``
        - ``genre[0].tag.tag``, ``genre[1].tag.tag``, ...
        - ``studio.value``
        """
        resp = await self._http.put(
            f"/library/metadata/{rating_key}",
            params=fields,
        )
        resp.raise_for_status()
        logger.debug("Updated metadata for %s: %s", rating_key, list(fields.keys()))

    async def upload_poster(self, rating_key: str, image_url: str) -> None:
        """Set the poster for a show from a URL."""
        resp = await self._http.post(
            f"/library/metadata/{rating_key}/posters",
            params={"url": image_url},
        )
        resp.raise_for_status()
        logger.debug("Uploaded poster for %s", rating_key)

    async def refresh_library(self, library_key: str) -> None:
        """Trigger a library scan/refresh for the given section key."""
        resp = await self._http.put(f"/library/sections/{library_key}/refresh")
        resp.raise_for_status()
        logger.info("Triggered refresh for library section %s", library_key)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def build_metadata_params(
        title: str | None = None,
        original_title: str | None = None,
        summary: str | None = None,
        genres: list[str] | None = None,
        rating: float | None = None,
        studio: str | None = None,
    ) -> dict[str, str]:
        """Build Plex PUT parameter dict from individual fields."""
        params: dict[str, str] = {}
        if title is not None:
            params["title.value"] = title
            params["title.locked"] = "1"
        if original_title is not None:
            params["originalTitle.value"] = original_title
            params["originalTitle.locked"] = "1"
        if summary is not None:
            params["summary.value"] = _strip_html(summary)
            params["summary.locked"] = "1"
        if genres is not None:
            for i, genre in enumerate(genres):
                params[f"genre[{i}].tag.tag"] = genre
            params["genre.locked"] = "1"
        if rating is not None:
            params["rating.value"] = str(round(rating, 1))
            params["rating.locked"] = "1"
        if studio is not None:
            params["studio.value"] = studio
            params["studio.locked"] = "1"
        return params


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()
