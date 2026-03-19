"""Jellyfin API client."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class JellyfinLibrary:
    """Represents a Jellyfin virtual folder / library."""

    id: str
    name: str
    type: str  # "tvshows", "movies", "mixed", ""
    locations: list[str] = field(default_factory=list)


@dataclass
class JellyfinShow:
    """Represents a series item in a Jellyfin library."""

    item_id: str
    name: str
    original_title: str
    year: int | None
    path: str  # filesystem path reported by Jellyfin
    library_id: str
    overview: str = ""
    genres: list[str] = field(default_factory=list)


@dataclass
class JellyfinSeason:
    """Represents a season within a Jellyfin series."""

    item_id: str
    index: int  # season number (0 = Specials)
    name: str
    episode_count: int
    series_id: str


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class JellyfinClient:
    """Async HTTP client for the Jellyfin REST API.

    Auth uses an API key via the Authorization header:
    ``MediaBrowser Client="AnilistLink", Token="{api_key}"``
    """

    def __init__(self, url: str, api_key: str) -> None:
        base_url = url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": (
                    f'MediaBrowser Client="AnilistLink", Token="{api_key}"'
                ),
                "Accept": "application/json",
                "Content-Type": "application/json",
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
        resp = await self._http.get("/System/Info/Public")
        resp.raise_for_status()
        name = resp.json().get("ServerName", "Jellyfin")
        logger.info("Connected to Jellyfin server: %s", name)
        return name

    async def get_libraries(self) -> list[JellyfinLibrary]:
        """List all virtual folders (libraries) on the server."""
        resp = await self._http.get("/Library/VirtualFolders")
        resp.raise_for_status()
        libraries: list[JellyfinLibrary] = []
        for folder in resp.json():
            lib_id = str(folder.get("ItemId") or folder.get("Id") or "")
            libraries.append(
                JellyfinLibrary(
                    id=lib_id,
                    name=folder.get("Name", ""),
                    type=folder.get("CollectionType", "") or "",
                    locations=folder.get("Locations", []),
                )
            )
        return libraries

    async def get_library_shows(self, library_id: str) -> list[JellyfinShow]:
        """Get all series items in a library."""
        params = {
            "ParentId": library_id,
            "IncludeItemTypes": "Series",
            "Recursive": "true",
            "Fields": ("Path,Overview,Genres,Studios,OriginalTitle,ProductionYear"),
            "SortBy": "SortName",
            "SortOrder": "Ascending",
        }
        resp = await self._http.get("/Items", params=params)
        resp.raise_for_status()
        shows: list[JellyfinShow] = []
        for item in resp.json().get("Items", []):
            shows.append(
                JellyfinShow(
                    item_id=str(item["Id"]),
                    name=item.get("Name", ""),
                    original_title=item.get("OriginalTitle", "") or "",
                    year=item.get("ProductionYear"),
                    path=item.get("Path", "") or "",
                    library_id=library_id,
                    overview=item.get("Overview", "") or "",
                    genres=item.get("Genres", []) or [],
                )
            )
        return shows

    async def get_item(self, item_id: str) -> dict[str, Any] | None:
        """Fetch full item details (needed before a metadata update)."""
        try:
            resp = await self._http.get(
                f"/Items/{item_id}",
                params={
                    "Fields": (
                        "Path,Overview,Genres,Studios,OriginalTitle,"
                        "ProductionYear,LockedFields,LockData"
                    )
                },
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
        except Exception:
            logger.debug("Failed to fetch Jellyfin item %s", item_id)
            return None

    async def get_show_seasons(self, series_id: str) -> list[JellyfinSeason]:
        """Get all seasons for a series."""
        params = {"Fields": "ChildCount,Overview"}
        resp = await self._http.get(f"/Shows/{series_id}/Seasons", params=params)
        resp.raise_for_status()
        seasons: list[JellyfinSeason] = []
        for item in resp.json().get("Items", []):
            seasons.append(
                JellyfinSeason(
                    item_id=str(item["Id"]),
                    index=int(item.get("IndexNumber", 0) or 0),
                    name=item.get("Name", ""),
                    episode_count=int(item.get("ChildCount", 0) or 0),
                    series_id=series_id,
                )
            )
        return seasons

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def update_item_metadata(
        self,
        item_id: str,
        title: str | None = None,
        original_title: str | None = None,
        summary: str | None = None,
        genres: list[str] | None = None,
        rating: float | None = None,
        studio: str | None = None,
    ) -> None:
        """Write AniList metadata fields to a Jellyfin item.

        Fetches the current item state first so that unrelated fields are
        preserved, then merges the updated fields and POSTs the full body back.
        """
        current = await self.get_item(item_id)
        if not current:
            logger.warning("Cannot update Jellyfin item %s: not found", item_id)
            return

        body: dict[str, Any] = dict(current)

        if title is not None:
            body["Name"] = title
        if original_title is not None:
            body["OriginalTitle"] = original_title
        if summary is not None:
            body["Overview"] = _strip_html(summary)
        if genres is not None:
            body["Genres"] = genres
            body["GenreItems"] = [{"Name": g} for g in genres]
        if rating is not None:
            body["CommunityRating"] = round(rating, 1)
        if studio is not None:
            body["Studios"] = [{"Name": studio}]

        # Lock the fields we wrote so Jellyfin auto-refresh doesn't overwrite
        locked: list[str] = list(body.get("LockedFields") or [])
        for f in ("Name", "Overview", "Genres", "Studios"):
            if f not in locked:
                locked.append(f)
        body["LockData"] = True
        body["LockedFields"] = locked

        resp = await self._http.post(f"/Items/{item_id}", json=body)
        resp.raise_for_status()
        logger.debug("Updated Jellyfin metadata for item %s", item_id)

    async def upload_poster(self, item_id: str, image_url: str) -> None:
        """Set the primary poster by downloading a remote image URL."""
        resp = await self._http.post(
            f"/Items/{item_id}/RemoteImages/Download",
            params={"Type": "Primary", "ImageUrl": image_url},
        )
        resp.raise_for_status()
        logger.debug("Set poster for Jellyfin item %s", item_id)

    async def refresh_library(self) -> None:
        """Trigger a full Jellyfin library refresh."""
        try:
            resp = await self._http.post("/Library/Refresh")
            resp.raise_for_status()
            logger.info("Triggered Jellyfin library refresh")
        except Exception:
            logger.debug("Failed to trigger Jellyfin library refresh")

    async def _get_scan_task_id(self) -> str | None:
        """Return the task ID of the 'Scan Media Library' scheduled task.

        The task key ``RefreshLibrary`` is stable across Jellyfin versions;
        only the opaque ``Id`` varies per server instance, so we look it up
        dynamically and the caller can cache the result.
        """
        try:
            resp = await self._http.get("/ScheduledTasks")
            resp.raise_for_status()
            for task in resp.json():
                if task.get("Key") == "RefreshLibrary":
                    return str(task["Id"])
        except Exception:
            logger.debug("Failed to look up Jellyfin scan task ID")
        return None

    async def is_library_scanning(self) -> bool:
        """Return True if the Scan Media Library task is currently running."""
        task_id = await self._get_scan_task_id()
        if not task_id:
            return False
        try:
            resp = await self._http.get(f"/ScheduledTasks/{task_id}")
            resp.raise_for_status()
            return resp.json().get("State") == "Running"
        except Exception:
            logger.debug("Failed to check Jellyfin scan state")
            return False

    async def refresh_library_and_wait(
        self,
        poll_interval: float = 5.0,
        timeout: float = 600.0,
    ) -> bool:
        """Trigger a library refresh and poll until the scan task is idle.

        Returns True if the scan completed successfully, False if it timed out
        or the task ID could not be found.

        The ``started_running`` guard prevents a false-positive on the initial
        poll if the server hasn't transitioned to Running state yet.
        """
        task_id = await self._get_scan_task_id()
        if not task_id:
            logger.warning(
                "Jellyfin scan task ID not found — cannot poll for completion"
            )  # noqa: E501
            return False

        await self.refresh_library()
        # Brief pause so the server has time to transition to Running
        await asyncio.sleep(2.0)

        deadline = asyncio.get_event_loop().time() + timeout
        started_running = False

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                logger.warning(
                    "Jellyfin library scan did not complete within %ss", timeout
                )
                return False

            try:
                resp = await self._http.get(f"/ScheduledTasks/{task_id}")
                resp.raise_for_status()
                task = resp.json()
            except Exception:
                logger.debug("Failed to poll Jellyfin scan task")
                await asyncio.sleep(min(poll_interval, remaining))
                continue

            state = task.get("State", "Idle")
            progress = task.get("CurrentProgressPercentage") or 0.0

            if state == "Running":
                started_running = True
                logger.debug("Jellyfin scan running: %.1f%%", progress)
            elif state == "Idle" and started_running:
                result = task.get("LastExecutionResult") or {}
                status = result.get("Status", "Unknown")
                logger.info("Jellyfin library scan complete: %s", status)
                return status not in ("Failed", "Aborted")

            await asyncio.sleep(min(poll_interval, remaining))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()
