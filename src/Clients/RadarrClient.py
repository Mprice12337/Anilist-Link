"""Radarr API v3 client for adding and managing movies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

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

    async def update_movie_path(self, movie_id: int, new_path: str) -> dict[str, Any]:
        """Update the folder path for a movie in Radarr."""
        movie = await self.get_movie_by_id(movie_id)
        if not movie:
            raise ValueError(f"Movie {movie_id} not found in Radarr")
        movie["path"] = new_path
        resp = await self._http.put(self._endpoint(f"movie/{movie_id}"), json=movie)
        resp.raise_for_status()
        return resp.json()

    async def rescan_movie(self, movie_id: int) -> dict[str, Any]:
        """Trigger a disk rescan for a movie so Radarr discovers moved files."""
        payload = {"name": "RescanMovie", "movieId": movie_id}
        resp = await self._http.post(self._endpoint("command"), json=payload)
        resp.raise_for_status()
        return resp.json()

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

    async def move_movie_root_folder(
        self, movie_id: int, new_root_folder: str
    ) -> dict[str, Any]:
        """Move a movie to a new root folder path, instructing Radarr to move files."""
        from pathlib import Path as _Path

        movie = await self.get_movie_by_id(movie_id)
        if not movie:
            raise ValueError(f"Movie {movie_id} not found in Radarr")
        old_path = movie.get("path", "")
        movie_folder = _Path(old_path).name if old_path else ""
        movie["rootFolderPath"] = new_root_folder
        if movie_folder:
            movie["path"] = str(_Path(new_root_folder) / movie_folder)
        resp = await self._http.put(
            self._endpoint(f"movie/{movie_id}"),
            json=movie,
            params={"moveFiles": "true"},
        )
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

    # ------------------------------------------------------------------
    # Release search / grab
    # ------------------------------------------------------------------

    async def update_movie_monitor(
        self, movie_id: int, monitored: bool
    ) -> dict[str, Any]:
        """Toggle the monitored flag for an existing movie."""
        movie = await self.get_movie_by_id(movie_id)
        if not movie:
            raise ValueError(f"Movie {movie_id} not found in Radarr")
        movie["monitored"] = monitored
        resp = await self._http.put(self._endpoint(f"movie/{movie_id}"), json=movie)
        resp.raise_for_status()
        return resp.json()

    async def search_releases(self, movie_id: int) -> list[dict[str, Any]]:
        """Search for available releases for a movie already in Radarr."""
        resp = await self._http.get(
            self._endpoint("release"), params={"movieId": movie_id}
        )
        resp.raise_for_status()
        return resp.json()

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
        payload: dict[str, Any] = {
            "title": title,
            "downloadUrl": download_url,
            "protocol": protocol,
        }
        if publish_date:
            payload["publishDate"] = publish_date
        if movie_id:
            payload["movieId"] = movie_id
        resp = await self._http.post(self._endpoint("release/push"), json=payload)
        resp.raise_for_status()
        return resp.json()

    async def search_releases_long(
        self, movie_id: int, timeout: float = 90.0
    ) -> list[dict[str, Any]]:
        """Search for releases with a longer timeout (indexer queries can be slow)."""
        resp = await self._http.get(
            self._endpoint("release"),
            params={"movieId": movie_id},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    async def grab_release(self, guid: str, indexer_id: int) -> dict[str, Any]:
        """Instruct Radarr to grab a specific release."""
        resp = await self._http.post(
            self._endpoint("release"),
            json={"guid": guid, "indexerId": indexer_id},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Movie files
    # ------------------------------------------------------------------

    async def get_movie_file(self, file_id: int) -> dict[str, Any] | None:
        """Return a single movie file record by ID."""
        try:
            resp = await self._http.get(self._endpoint(f"moviefile/{file_id}"))
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

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
        file_obj = await self.get_movie_file(file_id)
        if not file_obj:
            raise ValueError(f"Movie file {file_id} not found in Radarr")
        file_obj["relativePath"] = relative_path
        file_obj["path"] = path
        resp = await self._http.put(
            self._endpoint(f"moviefile/{file_id}"), json=file_obj
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
        """Register a webhook in Radarr; no-op if the name already exists."""
        for n in await self.get_notifications():
            if n.get("name") == name:
                return n

        # Use schema endpoint to get all required fields for this Radarr version
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
            schema.pop("id", None)  # read-only on POST
            schema["name"] = name
            schema["onGrab"] = False
            schema["onDownload"] = on_download
            schema["onUpgrade"] = on_upgrade
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
                "onMovieAdded": False,
                "onMovieDelete": False,
                "onMovieFileDelete": False,
                "onMovieFileDeleteForUpgrade": False,
                "onHealthIssue": False,
                "onApplicationUpdate": False,
                "onManualInteractionRequired": False,
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
                "infoLink": "https://wiki.servarr.com/radarr/supported#webhook",
                "tags": [],
            }

        resp = await self._http.post(self._endpoint("notification"), json=payload)
        if resp.status_code >= 400:
            logger.warning(
                "Radarr webhook registration failed (%d): %s",
                resp.status_code,
                resp.text[:500],
            )
        resp.raise_for_status()
        return resp.json()
