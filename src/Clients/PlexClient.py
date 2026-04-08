"""Plex Media Server API client."""

from __future__ import annotations

import asyncio
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

    async def get_library_shows(
        self, library_key: str, library_type: str = "show"
    ) -> list[PlexShow]:
        """Get all shows and movies in a library section.

        Uses Plex ``type`` filters to avoid returning episodes/seasons:
        type=1 → movies, type=2 → TV shows.  For "show" libraries we
        fetch both types so anime movies in a TV library are included.
        """
        # Determine which Plex types to fetch
        if library_type == "movie":
            plex_types = ["1"]
        else:
            # Show libraries: fetch TV shows + movies (anime libs often mix both)
            plex_types = ["2", "1"]

        shows: list[PlexShow] = []
        seen_keys: set[str] = set()
        for plex_type in plex_types:
            resp = await self._http.get(
                f"/library/sections/{library_key}/all",
                params={"type": plex_type},
            )
            resp.raise_for_status()
            container = resp.json().get("MediaContainer", {})
            metadata = container.get("Metadata", [])
            for m in metadata:
                rk = str(m["ratingKey"])
                if rk in seen_keys:
                    continue
                seen_keys.add(rk)
                locations = [
                    loc["path"] for loc in m.get("Location", []) if loc.get("path")
                ]
                shows.append(
                    PlexShow(
                        rating_key=rk,
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

    async def get_accounts(self) -> list[dict[str, Any]]:
        """Return all home accounts on the Plex server.

        Each dict has at minimum ``id`` (numeric account ID) and ``name`` keys.
        Requires the admin token.  Returns an empty list if the endpoint is
        unavailable (e.g. non-PlexHome servers — in that case only the admin
        account exists and its viewCount data is already in the default view).
        """
        try:
            resp = await self._http.get("/accounts")
            resp.raise_for_status()
            accounts = resp.json().get("MediaContainer", {}).get("Account", [])
            return [
                {"id": str(a.get("id", "")), "name": a.get("name", "")}
                for a in accounts
            ]
        except Exception:
            logger.debug("Failed to fetch Plex accounts")
            return []

    async def mark_episode_watched(self, rating_key: str) -> None:
        """Mark an episode as watched (scrobble) on the Plex server.

        Uses the ``/:/scrobble`` endpoint which marks the item played and
        increments ``viewCount`` for the authenticated user's token.
        """
        try:
            resp = await self._http.get(
                "/:/scrobble",
                params={
                    "key": rating_key,
                    "identifier": "com.plexapp.plugins.library",
                },
            )
            resp.raise_for_status()
            logger.debug("Scrobbled Plex item %s as watched", rating_key)
        except Exception:
            logger.debug("Failed to scrobble Plex item %s", rating_key)

    async def mark_episode_unwatched(self, rating_key: str) -> None:
        """Mark an episode as unwatched on the Plex server."""
        try:
            resp = await self._http.get(
                "/:/unscrobble",
                params={
                    "key": rating_key,
                    "identifier": "com.plexapp.plugins.library",
                },
            )
            resp.raise_for_status()
            logger.debug("Unscrobbled Plex item %s as unwatched", rating_key)
        except Exception:
            logger.debug("Failed to unscrobble Plex item %s", rating_key)

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
        resp = await self._http.get(f"/library/sections/{library_key}/refresh")
        resp.raise_for_status()
        logger.info("Triggered refresh for library section %s", library_key)

    async def is_library_scanning(self, library_key: str) -> bool:
        """Check if a Plex library section is currently scanning."""
        try:
            resp = await self._http.get("/library/sections")
            resp.raise_for_status()
            for d in resp.json().get("MediaContainer", {}).get("Directory", []):
                if str(d.get("key")) == str(library_key):
                    return d.get("refreshing", False) or d.get("scanning", False)
        except Exception:
            logger.debug("Failed to check scan status for section %s", library_key)
        return False

    async def refresh_library_and_wait(
        self,
        library_key: str,
        poll_interval: float = 2.0,
        max_timeout: float = 600.0,
    ) -> bool:
        """Trigger a library refresh and wait for it to complete.

        Returns True if the scan completed, False if it timed out.
        Uses a generous max timeout (default 10 min) as a safety net;
        Plex doesn't report granular progress, so we rely on the
        scanning/not-scanning state transition.
        """
        await self.refresh_library(library_key)
        await asyncio.sleep(1.0)

        elapsed = 0.0
        while elapsed < max_timeout:
            if not await self.is_library_scanning(library_key):
                logger.info("Plex library %s scan complete", library_key)
                return True
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        logger.warning(
            "Plex library %s scan timed out after %.0fs", library_key, max_timeout
        )
        return False

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
