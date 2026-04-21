"""Sonarr API v3 client for adding and managing series."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.Clients.ServarrBaseClient import ServarrBaseClient

logger = logging.getLogger(__name__)


class SeriesAlreadyExistsError(Exception):
    """Raised when a series already exists in Sonarr."""


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


class SonarrClient(ServarrBaseClient):
    """Async Sonarr API v3 client."""

    _service_name = "Sonarr"
    _webhook_info_link = "https://wiki.servarr.com/sonarr/supported#webhook"
    _webhook_fallback_events = {
        "onRename": False,
        "onSeriesDelete": False,
        "onEpisodeFileDelete": False,
        "onEpisodeFileDeleteForUpgrade": False,
        "onHealthIssue": False,
        "onApplicationUpdate": False,
        "onManualInteractionRequired": False,
        "onSeriesAdd": False,
    }

    # ------------------------------------------------------------------
    # Series
    # ------------------------------------------------------------------

    async def get_all_series(self) -> list[dict[str, Any]]:
        """Return all series in Sonarr."""
        return await self._get_all("series")

    async def get_series_by_id(self, series_id: int) -> dict[str, Any] | None:
        """Return a single series by Sonarr series ID."""
        return await self._get_by_id("series", series_id)

    async def get_series_by_tvdb_id(self, tvdb_id: int) -> dict[str, Any] | None:
        """Find a series in Sonarr by TVDB ID."""
        all_series = await self.get_all_series()
        for s in all_series:
            if s.get("tvdbId") == tvdb_id:
                return s
        return None

    async def lookup_series(
        self, term: str, *, timeout: float | None = 10.0
    ) -> list[dict[str, Any]]:
        """Search for a series by title via Sonarr lookup."""
        resp = await self._http.get(
            self._endpoint("series/lookup"),
            params={"term": term},
            timeout=timeout,
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
        tags: list[int] | None = None,
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
            "tags": tags or [],
            "addOptions": {
                "searchForMissingEpisodes": monitored and search_immediately,
                "monitor": monitor_strategy,
            },
        }
        resp = await self._http.post(self._endpoint("series"), json=payload)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Release search
    # ------------------------------------------------------------------

    async def search_releases(self, series_id: int) -> list[dict[str, Any]]:
        """Search for available releases for a series already in Sonarr."""
        return await self._search_releases("seriesId", series_id)

    async def search_releases_long(
        self, series_id: int, timeout: float = 90.0
    ) -> list[dict[str, Any]]:
        """Search for releases with a longer timeout (indexer queries can be slow)."""
        return await self._search_releases("seriesId", series_id, timeout=timeout)

    async def push_release(
        self,
        title: str,
        download_url: str,
        protocol: str,
        publish_date: str = "",
        series_id: int | None = None,
    ) -> dict[str, Any]:
        """Push a release URL directly to Sonarr without going through its search.

        Useful when Prowlarr found a release that Sonarr's tvsearch missed.
        Pass ``series_id`` to skip Sonarr's title-based series matching (avoids
        "Unknown Series" rejections when the release title format is unrecognised).
        """
        return await self._push_release(
            title, download_url, protocol, publish_date, "seriesId", series_id
        )

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    async def get_or_create_tag(self, label: str) -> int:
        """Return the ID of a tag by label, creating it if it doesn't exist."""
        resp = await self._http.get(self._endpoint("tag"))
        resp.raise_for_status()
        for tag in resp.json():
            if tag.get("label", "").lower() == label.lower():
                return int(tag["id"])
        resp = await self._http.post(self._endpoint("tag"), json={"label": label})
        resp.raise_for_status()
        return int(resp.json()["id"])

    # ------------------------------------------------------------------
    # Episode files
    # ------------------------------------------------------------------

    async def get_episode_file(self, file_id: int) -> dict[str, Any] | None:
        """Return a single episode file record by ID."""
        return await self._get_file_by_id("episodefile", file_id)

    async def get_episode_files(self, series_id: int) -> list[dict[str, Any]]:
        """Return all episode files for a series."""
        resp = await self._http.get(
            self._endpoint("episodefile"), params={"seriesId": series_id}
        )
        resp.raise_for_status()
        return resp.json()

    async def update_episode_file(
        self, file_id: int, relative_path: str, path: str
    ) -> dict[str, Any]:
        """Update stored paths for an episode file; caller moves the file on disk."""
        return await self._update_file("episodefile", file_id, relative_path, path)

    # ------------------------------------------------------------------
    # Series path / rescan
    # ------------------------------------------------------------------

    async def move_series_root_folder(
        self, series_id: int, new_root_folder: str
    ) -> dict[str, Any]:
        """Move a series to a new root folder path, instructing Sonarr to move files."""
        return await self._move_root_folder("series", series_id, new_root_folder)

    async def update_series_path(self, series_id: int, new_path: str) -> dict[str, Any]:
        """Update the root path for a series in Sonarr."""
        return await self._update_path("series", series_id, new_path)

    async def rescan_series(self, series_id: int) -> dict[str, Any]:
        """Trigger a disk rescan for a series so Sonarr discovers moved files."""
        return await self._rescan("RescanSeries", "seriesId", series_id)

    # ------------------------------------------------------------------
    # Series monitoring
    # ------------------------------------------------------------------

    async def update_series_monitor(
        self, series_id: int, monitored: bool
    ) -> dict[str, Any]:
        """Toggle the monitored flag for an existing series."""
        return await self._update_monitor("series", series_id, monitored)

    async def push_alt_titles(
        self, series_id: int, titles: list[str]
    ) -> dict[str, Any]:
        """Merge extra search titles into the series' alternateTitles and PUT back.

        Sonarr uses all alternate titles when querying indexers, so adding
        AniList synonyms here improves release matching significantly.
        """
        series = await self.get_series_by_id(series_id)
        if not series:
            raise ValueError(f"Series {series_id} not found in Sonarr")

        known: set[str] = {series.get("title", "").lower()}
        for t in series.get("alternateTitles", []):
            known.add(t.get("title", "").lower())

        new_alts = list(series.get("alternateTitles", []))
        for title in titles:
            if title and title.lower() not in known:
                new_alts.append({"title": title, "seasonNumber": -1})
                known.add(title.lower())

        series["alternateTitles"] = new_alts
        resp = await self._http.put(self._endpoint(f"series/{series_id}"), json=series)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Episode monitoring / search commands
    # ------------------------------------------------------------------

    async def monitor_all_episodes(self, series_id: int) -> None:
        """Set all seasons and episodes for a series to monitored."""
        series = await self.get_series_by_id(series_id)
        if not series:
            raise ValueError(f"Series {series_id} not found in Sonarr")
        series["monitored"] = True
        for season in series.get("seasons", []):
            if season.get("seasonNumber", 0) > 0:
                season["monitored"] = True
        await self._http.put(self._endpoint(f"series/{series_id}"), json=series)

        episodes = await self.get_episodes(series_id)
        episode_ids = [ep["id"] for ep in episodes if not ep.get("monitored", True)]
        if episode_ids:
            resp = await self._http.put(
                self._endpoint("episode/monitor"),
                json={"episodeIds": episode_ids, "monitored": True},
            )
            resp.raise_for_status()

    async def monitor_season_episodes(self, series_id: int, season_number: int) -> None:
        """Set a specific season and its episodes to monitored.

        Only touches the target season — other seasons are left unchanged.
        """
        series = await self.get_series_by_id(series_id)
        if not series:
            raise ValueError(f"Series {series_id} not found in Sonarr")
        series["monitored"] = True
        for season in series.get("seasons", []):
            if season.get("seasonNumber") == season_number:
                season["monitored"] = True
                break
        await self._http.put(self._endpoint(f"series/{series_id}"), json=series)

        episodes = await self.get_episodes(series_id)
        episode_ids = [
            ep["id"]
            for ep in episodes
            if ep.get("seasonNumber") == season_number and not ep.get("monitored", True)
        ]
        if episode_ids:
            resp = await self._http.put(
                self._endpoint("episode/monitor"),
                json={"episodeIds": episode_ids, "monitored": True},
            )
            resp.raise_for_status()

    async def trigger_series_search(self, series_id: int) -> dict[str, Any]:
        """Tell Sonarr to search for all missing episodes in the series."""
        resp = await self._http.post(
            self._endpoint("command"),
            json={"name": "SeriesSearch", "seriesId": series_id},
        )
        resp.raise_for_status()
        return resp.json()

    async def trigger_season_search(
        self, series_id: int, season_number: int
    ) -> dict[str, Any]:
        """Tell Sonarr to search for missing episodes in a specific season."""
        resp = await self._http.post(
            self._endpoint("command"),
            json={
                "name": "SeasonSearch",
                "seriesId": series_id,
                "seasonNumber": season_number,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def update_season_monitor(
        self, series_id: int, season_number: int, monitored: bool
    ) -> dict[str, Any]:
        """Toggle monitoring for a specific season of a series."""
        series = await self.get_series_by_id(series_id)
        if not series:
            raise ValueError(f"Series {series_id} not found in Sonarr")
        for season in series.get("seasons", []):
            if season.get("seasonNumber") == season_number:
                season["monitored"] = monitored
                break
        resp = await self._http.put(self._endpoint(f"series/{series_id}"), json=series)
        resp.raise_for_status()
        return resp.json()

    async def get_episodes(self, series_id: int) -> list[dict[str, Any]]:
        """Return all episode records for a series."""
        resp = await self._http.get(
            self._endpoint("episode"), params={"seriesId": series_id}
        )
        resp.raise_for_status()
        return resp.json()
