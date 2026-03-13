"""Sonarr API v3 client for adding and managing series."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SonarrSeries:
    """Represents a series in Sonarr."""

    id: int
    title: str
    tvdb_id: int
    imdb_id: str
    year: int
    status: str
    monitored: bool
    path: str
    series_type: str


class SonarrClient:
    """Async Sonarr API v3 client."""

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
        """Return Sonarr system status or raise on failure."""
        resp = await self._http.get(self._endpoint("system/status"))
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Series
    # ------------------------------------------------------------------

    async def get_all_series(self) -> list[dict[str, Any]]:
        """Return all series in Sonarr."""
        resp = await self._http.get(self._endpoint("series"))
        resp.raise_for_status()
        return resp.json()

    async def get_series_by_id(self, series_id: int) -> dict[str, Any] | None:
        """Return a single series by Sonarr series ID."""
        try:
            resp = await self._http.get(self._endpoint(f"series/{series_id}"))
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def get_series_by_tvdb_id(self, tvdb_id: int) -> dict[str, Any] | None:
        """Find a series in Sonarr by TVDB ID."""
        all_series = await self.get_all_series()
        for s in all_series:
            if s.get("tvdbId") == tvdb_id:
                return s
        return None

    async def lookup_series(self, term: str) -> list[dict[str, Any]]:
        """Search for a series by title via Sonarr lookup."""
        resp = await self._http.get(
            self._endpoint("series/lookup"), params={"term": term}
        )
        resp.raise_for_status()
        return resp.json()

    async def lookup_series_by_tvdb(self, tvdb_id: int) -> dict[str, Any] | None:
        """Look up a series by TVDB ID via Sonarr (returns add-ready payload)."""
        results = await self.lookup_series(f"tvdb:{tvdb_id}")
        if results:
            return results[0]
        return None

    async def add_series(
        self,
        title: str,
        tvdb_id: int,
        quality_profile_id: int,
        root_folder_path: str,
        monitored: bool = True,
        monitor_strategy: str = "future",
        search_immediately: bool = False,
        series_type: str = "anime",
        season_folder: bool = True,
    ) -> dict[str, Any]:
        """Add a series to Sonarr.

        ``monitor_strategy`` controls which episodes Sonarr monitors:
        ``future``, ``all``, ``firstSeason``, ``latestSeason``, ``none``.
        """
        payload: dict[str, Any] = {
            "title": title,
            "tvdbId": tvdb_id,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder_path,
            "monitored": monitored,
            "seriesType": series_type,
            "seasonFolder": season_folder,
            "addOptions": {
                "searchForMissingEpisodes": monitored and search_immediately,
                "monitor": monitor_strategy,
            },
        }
        resp = await self._http.post(self._endpoint("series"), json=payload)
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
        """Return Sonarr naming configuration."""
        resp = await self._http.get(self._endpoint("config/naming"))
        resp.raise_for_status()
        return resp.json()

    async def push_naming_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Update Sonarr naming configuration."""
        resp = await self._http.put(self._endpoint("config/naming"), json=config)
        resp.raise_for_status()
        return resp.json()
