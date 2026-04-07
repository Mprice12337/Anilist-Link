"""Sonarr API v3 client for adding and managing series."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

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

    # ------------------------------------------------------------------
    # Release search / grab
    # ------------------------------------------------------------------

    async def search_releases(self, series_id: int) -> list[dict[str, Any]]:
        """Search for available releases for a series already in Sonarr."""
        resp = await self._http.get(
            self._endpoint("release"), params={"seriesId": series_id}
        )
        resp.raise_for_status()
        return resp.json()

    async def grab_release(self, guid: str, indexer_id: int) -> dict[str, Any]:
        """Instruct Sonarr to grab a specific release."""
        resp = await self._http.post(
            self._endpoint("release"),
            json={"guid": guid, "indexerId": indexer_id},
        )
        resp.raise_for_status()
        return resp.json()

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
        try:
            resp = await self._http.get(self._endpoint(f"episodefile/{file_id}"))
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

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
        file_obj = await self.get_episode_file(file_id)
        if not file_obj:
            raise ValueError(f"Episode file {file_id} not found in Sonarr")
        file_obj["relativePath"] = relative_path
        file_obj["path"] = path
        resp = await self._http.put(
            self._endpoint(f"episodefile/{file_id}"), json=file_obj
        )
        resp.raise_for_status()
        return resp.json()

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
        payload: dict[str, Any] = {
            "title": title,
            "downloadUrl": download_url,
            "protocol": protocol,
        }
        if publish_date:
            payload["publishDate"] = publish_date
        if series_id:
            payload["seriesId"] = series_id
        resp = await self._http.post(self._endpoint("release/push"), json=payload)
        resp.raise_for_status()
        return resp.json()

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

    async def search_releases_long(
        self, series_id: int, timeout: float = 90.0
    ) -> list[dict[str, Any]]:
        """Search for releases with a longer timeout (indexer queries can be slow)."""
        resp = await self._http.get(
            self._endpoint("release"),
            params={"seriesId": series_id},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    async def move_series_root_folder(
        self, series_id: int, new_root_folder: str
    ) -> dict[str, Any]:
        """Move a series to a new root folder path, instructing Sonarr to move files."""
        from pathlib import Path as _Path

        series = await self.get_series_by_id(series_id)
        if not series:
            raise ValueError(f"Series {series_id} not found in Sonarr")
        old_path = series.get("path", "")
        series_folder = _Path(old_path).name if old_path else ""
        series["rootFolderPath"] = new_root_folder
        if series_folder:
            series["path"] = str(_Path(new_root_folder) / series_folder)
        resp = await self._http.put(
            self._endpoint(f"series/{series_id}"),
            json=series,
            params={"moveFiles": "true"},
        )
        resp.raise_for_status()
        return resp.json()

    async def update_series_path(self, series_id: int, new_path: str) -> dict[str, Any]:
        """Update the root path for a series in Sonarr."""
        series = await self.get_series_by_id(series_id)
        if not series:
            raise ValueError(f"Series {series_id} not found in Sonarr")
        series["path"] = new_path
        resp = await self._http.put(self._endpoint(f"series/{series_id}"), json=series)
        resp.raise_for_status()
        return resp.json()

    async def rescan_series(self, series_id: int) -> dict[str, Any]:
        """Trigger a disk rescan for a series so Sonarr discovers moved files."""
        payload = {"name": "RescanSeries", "seriesId": series_id}
        resp = await self._http.post(self._endpoint("command"), json=payload)
        resp.raise_for_status()
        return resp.json()

    async def update_series_monitor(
        self, series_id: int, monitored: bool
    ) -> dict[str, Any]:
        """Toggle the monitored flag for an existing series."""
        series = await self.get_series_by_id(series_id)
        if not series:
            raise ValueError(f"Series {series_id} not found in Sonarr")
        series["monitored"] = monitored
        resp = await self._http.put(self._endpoint(f"series/{series_id}"), json=series)
        resp.raise_for_status()
        return resp.json()

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

    # ------------------------------------------------------------------
    # Webhook / notifications
    # ------------------------------------------------------------------

    async def get_notifications(self) -> list[dict[str, Any]]:
        """Return all configured notification connections."""
        resp = await self._http.get(self._endpoint("notification"))
        resp.raise_for_status()
        return resp.json()

    async def register_webhook(
        self,
        name: str,
        url: str,
        on_download: bool = True,
        on_upgrade: bool = True,
    ) -> dict[str, Any]:
        """Register a webhook in Sonarr; no-op if the name already exists."""
        for n in await self.get_notifications():
            if n.get("name") == name:
                return n

        # Build payload by cloning an existing webhook's schema if possible,
        # otherwise fall back to a known-good structure.
        # Use GET to discover required fields for this Sonarr version.
        schema: dict[str, Any] = {}
        try:
            resp = await self._http.get(
                self._endpoint("notification/schema"),
            )
            resp.raise_for_status()
            schemas = resp.json()
            for s in schemas:
                if s.get("implementation") == "Webhook":
                    schema = s
                    break
        except Exception:
            pass

        if schema:
            # Use the schema as the base — it has all required fields/defaults
            schema.pop("id", None)  # read-only on POST
            schema["name"] = name
            schema["onGrab"] = False
            schema["onDownload"] = on_download
            schema["onUpgrade"] = on_upgrade
            # Set URL and Method in fields (case-insensitive match)
            for f in schema.get("fields", []):
                fname = (f.get("name") or "").lower()
                if fname == "url":
                    f["value"] = url
                elif fname == "method":
                    f["value"] = 1  # POST
            payload = schema
        else:
            payload = {
                "onGrab": False,
                "onDownload": on_download,
                "onUpgrade": on_upgrade,
                "onRename": False,
                "onSeriesDelete": False,
                "onEpisodeFileDelete": False,
                "onEpisodeFileDeleteForUpgrade": False,
                "onHealthIssue": False,
                "onApplicationUpdate": False,
                "onManualInteractionRequired": False,
                "onSeriesAdd": False,
                "name": name,
                "fields": [
                    {"name": "Url", "value": url},
                    {"name": "Method", "value": 1},
                    {"name": "Username", "value": ""},
                    {"name": "Password", "value": ""},
                ],
                "implementationName": "Webhook",
                "implementation": "Webhook",
                "configContract": "WebhookSettings",
                "infoLink": "https://wiki.servarr.com/sonarr/supported#webhook",
                "tags": [],
            }

        resp = await self._http.post(self._endpoint("notification"), json=payload)
        if resp.status_code >= 400:
            logger.warning(
                "Sonarr webhook registration failed (%d): %s",
                resp.status_code,
                resp.text[:500],
            )
        resp.raise_for_status()
        return resp.json()
