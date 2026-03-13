"""Radarr API v3 client for adding and managing movies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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


class RadarrClient:
    """Async Radarr API v3 client."""

    def __init__(self, url: str, api_key: str) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._http = httpx.AsyncClient(
            timeout=30.0,
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    def _endpoint(self, path: str) -> str:
        return f"{self._url}/api/v3/{path.lstrip('/')}"

    # ------------------------------------------------------------------
    # Connection / health
    # ------------------------------------------------------------------

    async def test_connection(self) -> dict[str, Any]:
        """Return Radarr system status or raise on failure."""
        resp = await self._http.get(self._endpoint("system/status"))
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Movies
    # ------------------------------------------------------------------

    async def get_all_movies(self) -> list[dict[str, Any]]:
        """Return all movies in Radarr."""
        resp = await self._http.get(self._endpoint("movie"))
        resp.raise_for_status()
        return resp.json()

    async def get_movie_by_id(self, movie_id: int) -> dict[str, Any] | None:
        """Return a single movie by Radarr movie ID."""
        try:
            resp = await self._http.get(self._endpoint(f"movie/{movie_id}"))
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

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
    # Quality Profiles / Root Folders
    # ------------------------------------------------------------------

    async def get_quality_profiles(self) -> list[dict[str, Any]]:
        """Return available quality profiles."""
        resp = await self._http.get(self._endpoint("qualityprofile"))
        resp.raise_for_status()
        return resp.json()

    async def get_root_folders(self) -> list[dict[str, Any]]:
        """Return configured root folders."""
        resp = await self._http.get(self._endpoint("rootfolder"))
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Naming config
    # ------------------------------------------------------------------

    async def get_naming_config(self) -> dict[str, Any]:
        """Return Radarr naming configuration."""
        resp = await self._http.get(self._endpoint("config/naming"))
        resp.raise_for_status()
        return resp.json()

    async def push_naming_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Update Radarr naming configuration."""
        resp = await self._http.put(self._endpoint("config/naming"), json=config)
        resp.raise_for_status()
        return resp.json()
