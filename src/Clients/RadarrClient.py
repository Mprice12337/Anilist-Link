"""Radarr API v3 client for adding and managing movies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.Clients.ServarrBaseClient import ServarrBaseClient

logger = logging.getLogger(__name__)


class MovieAlreadyExistsError(Exception):
    """Raised when a movie already exists in Radarr."""


@dataclass
class RadarrMovie:
    """Represents a movie in Radarr."""

    id: int
    title: str
    tmdb_id: int
    imdb_id: str
    year: int
    status: str
    monitored: bool
    path: str


class RadarrClient(ServarrBaseClient):
    """Async Radarr API v3 client."""

    _service_name = "Radarr"
    _webhook_info_link = "https://wiki.servarr.com/radarr/supported#webhook"
    _webhook_fallback_events = {
        "onMovieAdded": False,
        "onMovieDelete": False,
        "onMovieFileDelete": False,
        "onMovieFileDeleteForUpgrade": False,
        "onHealthIssue": False,
        "onApplicationUpdate": False,
        "onManualInteractionRequired": False,
    }

    # ------------------------------------------------------------------
    # Movies
    # ------------------------------------------------------------------

    async def get_all_movies(self) -> list[dict[str, Any]]:
        """Return all movies in Radarr."""
        return await self._get_all("movie")

    async def get_movie_by_id(self, movie_id: int) -> dict[str, Any] | None:
        """Return a single movie by Radarr movie ID."""
        return await self._get_by_id("movie", movie_id)

    async def get_movie_by_tmdb_id(self, tmdb_id: int) -> dict[str, Any] | None:
        """Find a movie in Radarr by TMDB ID."""
        all_movies = await self.get_all_movies()
        for m in all_movies:
            if m.get("tmdbId") == tmdb_id:
                return m
        return None

    async def lookup_movie(self, term: str) -> list[dict[str, Any]]:
        """Search for a movie by title via Radarr lookup."""
        resp = await self._http.get(
            self._endpoint("movie/lookup"), params={"term": term}
        )
        resp.raise_for_status()
        return resp.json()

    async def lookup_movie_by_tmdb(self, tmdb_id: int) -> dict[str, Any] | None:
        """Look up a movie by TMDB ID via Radarr."""
        results = await self.lookup_movie(f"tmdb:{tmdb_id}")
        if results:
            return results[0]
        return None

    async def add_movie(
        self,
        title: str,
        tmdb_id: int,
        quality_profile_id: int,
        root_folder_path: str,
        monitored: bool = True,
        search_immediately: bool = False,
        minimum_availability: str = "announced",
    ) -> dict[str, Any]:
        """Add a movie to Radarr."""
        payload: dict[str, Any] = {
            "title": title,
            "tmdbId": tmdb_id,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder_path,
            "monitored": monitored,
            "minimumAvailability": minimum_availability,
            "addOptions": {
                "searchForMovie": monitored and search_immediately,
            },
        }
        resp = await self._http.post(self._endpoint("movie"), json=payload)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Movie path / rescan
    # ------------------------------------------------------------------

    async def update_movie_path(self, movie_id: int, new_path: str) -> dict[str, Any]:
        """Update the folder path for a movie in Radarr."""
        return await self._update_path("movie", movie_id, new_path)

    async def rescan_movie(self, movie_id: int) -> dict[str, Any]:
        """Trigger a disk rescan for a movie so Radarr discovers moved files."""
        return await self._rescan("RescanMovie", "movieId", movie_id)

    async def move_movie_root_folder(
        self, movie_id: int, new_root_folder: str
    ) -> dict[str, Any]:
        """Move a movie to a new root folder path, instructing Radarr to move files."""
        return await self._move_root_folder("movie", movie_id, new_root_folder)

    # ------------------------------------------------------------------
    # Movie monitoring
    # ------------------------------------------------------------------

    async def update_movie_monitor(
        self, movie_id: int, monitored: bool
    ) -> dict[str, Any]:
        """Toggle the monitored flag for an existing movie."""
        return await self._update_monitor("movie", movie_id, monitored)

    # ------------------------------------------------------------------
    # Release search
    # ------------------------------------------------------------------

    async def search_releases(self, movie_id: int) -> list[dict[str, Any]]:
        """Search for available releases for a movie already in Radarr."""
        return await self._search_releases("movieId", movie_id)

    async def search_releases_long(
        self, movie_id: int, timeout: float = 90.0
    ) -> list[dict[str, Any]]:
        """Search for releases with a longer timeout (indexer queries can be slow)."""
        return await self._search_releases("movieId", movie_id, timeout=timeout)

    async def push_release(
        self,
        title: str,
        download_url: str,
        protocol: str,
        publish_date: str = "",
        movie_id: int | None = None,
    ) -> dict[str, Any]:
        """Push a release URL directly to Radarr without going through its search.

        Pass ``movie_id`` to skip Radarr's title-based movie matching.
        """
        return await self._push_release(
            title, download_url, protocol, publish_date, "movieId", movie_id
        )

    # ------------------------------------------------------------------
    # Movie files
    # ------------------------------------------------------------------

    async def get_movie_file(self, file_id: int) -> dict[str, Any] | None:
        """Return a single movie file record by ID."""
        return await self._get_file_by_id("moviefile", file_id)

    async def get_movie_files(self, movie_id: int) -> list[dict[str, Any]]:
        """Return all movie files for a movie."""
        resp = await self._http.get(
            self._endpoint("moviefile"), params={"movieId": movie_id}
        )
        resp.raise_for_status()
        return resp.json()

    async def update_movie_file(
        self, file_id: int, relative_path: str, path: str
    ) -> dict[str, Any]:
        """Update stored paths for a movie file (no disk move — caller handles that)."""
        return await self._update_file("moviefile", file_id, relative_path, path)
