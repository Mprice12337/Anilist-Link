"""Jellyfin API client."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.Utils.PathTranslator import PathTranslator

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
    """Represents a series or movie item in a Jellyfin library."""

    item_id: str
    name: str
    original_title: str
    year: int | None
    path: str  # filesystem path reported by Jellyfin
    library_id: str
    overview: str = ""
    genres: list[str] = field(default_factory=list)
    media_type: str = "Series"  # "Series" or "Movie"


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
        self._path_translator: PathTranslator = PathTranslator.identity()
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

    def set_path_translator(self, translator: PathTranslator) -> None:
        """Replace the path translator used for filesystem operations."""
        self._path_translator = translator

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
        """Get all Series and Movie items in a library."""
        params = {
            "ParentId": library_id,
            "IncludeItemTypes": "Series,Movie",
            "Recursive": "true",
            "Fields": ("Path,Overview,Genres,Studios,OriginalTitle,ProductionYear"),
            "SortBy": "SortName",
            "SortOrder": "Ascending",
        }
        resp = await self._http.get("/Items", params=params)
        resp.raise_for_status()
        shows: list[JellyfinShow] = []
        for item in resp.json().get("Items", []):
            # Only process top-level containers — Series and Movie.
            # Episodes, Seasons, and other child types are excluded.
            if item.get("Type") not in ("Series", "Movie"):
                logger.debug(
                    "Skipping non-Series/Movie item '%s' (Type=%s)",
                    item.get("Name", ""),
                    item.get("Type", "unknown"),
                )
                continue
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
                    media_type=item.get("Type", "Series"),
                )
            )
        return shows

    async def get_item(self, item_id: str) -> dict[str, Any] | None:
        """Fetch full item details (needed before a metadata update).

        Uses the bulk ``/Items?Ids=`` endpoint rather than the singular
        ``/Items/{id}`` form because the latter requires user context in
        some Jellyfin versions and may return 404 with an admin API key.
        """
        try:
            resp = await self._http.get(
                "/Items",
                params={
                    "Ids": item_id,
                    "Fields": (
                        "Path,Overview,Genres,Studios,OriginalTitle,"
                        "ProductionYear,LockedFields,LockData,ParentId,IsFolder"
                    ),
                },
            )
            resp.raise_for_status()
            items = resp.json().get("Items", [])
            if not items:
                logger.debug("Jellyfin item %s not found in response", item_id)
                return None
            return items[0]  # type: ignore[no-any-return]
        except Exception:
            logger.debug("Failed to fetch Jellyfin item %s", item_id)
            return None

    async def get_show_seasons(self, series_id: str) -> list[JellyfinSeason]:
        """Get all seasons for a series.

        Returns an empty list (rather than raising) for Movie items or items
        that Jellyfin does not recognise as a Series — callers should treat
        an empty result as Structure A.
        """
        params = {"Fields": "ChildCount,Overview"}
        resp = await self._http.get(f"/Shows/{series_id}/Seasons", params=params)
        if resp.status_code in (400, 404):
            logger.debug(
                "Jellyfin item %s has no seasons (status %d) — not a Series?",
                series_id,
                resp.status_code,
            )
            return []
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

        Fetches the current item state first so unrelated fields are preserved,
        then POSTs a clean ``UpdateItemByIdRequest`` body.  Jellyfin 10.8+
        rejects the full ``BaseItemDto`` format (400 Bad Request), so only the
        fields accepted by the update endpoint are sent — no ``ImageTags``,
        ``MediaStreams``, ``UserData``, ``GenreItems``, etc.
        """
        current = await self.get_item(item_id)
        if not current:
            logger.warning("Cannot update Jellyfin item %s: not found", item_id)
            return

        # Build a clean UpdateItemByIdRequest body — only fields Jellyfin 10.8+
        # accepts on POST /Items/{id}.
        body: dict[str, Any] = {
            "Name": current.get("Name") or "",
            "OriginalTitle": current.get("OriginalTitle") or "",
            "Overview": current.get("Overview") or "",
            "Genres": list(current.get("Genres") or []),
            "Tags": list(current.get("Tags") or []),
            "Studios": list(current.get("Studios") or []),
            "CommunityRating": current.get("CommunityRating"),
            "OfficialRating": current.get("OfficialRating") or "",
            "LockData": bool(current.get("LockData", False)),
            "LockedFields": list(current.get("LockedFields") or []),
            "ProviderIds": dict(current.get("ProviderIds") or {}),
            "People": list(current.get("People") or []),
            "Taglines": list(current.get("Taglines") or []),
        }

        if title is not None:
            body["Name"] = title
        if original_title is not None:
            body["OriginalTitle"] = original_title
        if summary is not None:
            body["Overview"] = _strip_html(summary)
        if genres is not None:
            body["Genres"] = genres
        if rating is not None:
            body["CommunityRating"] = round(rating, 1)
        if studio is not None:
            body["Studios"] = [{"Name": studio}]

        # Lock the fields we wrote so Jellyfin auto-refresh doesn't overwrite
        locked: list[str] = list(body["LockedFields"])
        for f in ("Name", "Overview", "Genres", "Studios"):
            if f not in locked:
                locked.append(f)
        body["LockData"] = True
        body["LockedFields"] = locked

        resp = await self._http.post(f"/Items/{item_id}", json=body)
        resp.raise_for_status()
        logger.debug("Updated Jellyfin metadata for item %s", item_id)

    async def lock_item(self, item_id: str) -> None:
        """Set LockData=True on an item without changing any other metadata.

        Prevents Jellyfin's scheduled metadata/image refresh from overwriting
        data we have already applied.
        """
        await self.update_item_metadata(item_id)

    async def upload_poster(self, item_id: str, image_url: str) -> None:
        """Set the primary poster for a Jellyfin item.

        Strategy:
        1. Download the image bytes from *image_url* and push directly via
           ``POST /Items/{id}/Images/Primary``.
        2. If Jellyfin returns 5xx (common for Folder-type items and WebP
           images on builds without WebP Skia support), write ``folder.jpg``
           into the item's filesystem directory and trigger an image refresh.
        3. If the filesystem write fails (path not accessible), fall back to
           ``RemoteImages/Download``.
        """
        async with httpx.AsyncClient(timeout=30.0) as dl:
            img_resp = await dl.get(image_url)
            img_resp.raise_for_status()
            image_bytes = img_resp.content
            content_type = img_resp.headers.get("content-type", "image/jpeg").split(
                ";"
            )[0]

        logger.debug(
            "Uploading poster for %s (%s, %d bytes)",
            item_id,
            content_type,
            len(image_bytes),
        )

        resp = await self._http.post(
            f"/Items/{item_id}/Images/Primary",
            content=image_bytes,
            headers={"Content-Type": content_type},
        )

        if resp.is_server_error or resp.status_code == 404:
            logger.warning(
                "Direct image upload returned %d for %s — body: %s",
                resp.status_code,
                item_id,
                resp.text[:300],
            )

            # Try writing folder.jpg to the item's filesystem directory.
            # For Folder items we try two candidates: the item's own path,
            # then the parent directory (handles double-nested show layouts
            # like Show(year)/Show(year)/episodes where Jellyfin reports the
            # inner path but only the outer directory exists on disk).
            item = await self.get_item(item_id)
            if item and item.get("Path"):
                item_path: str = self._path_translator.translate(item["Path"])
                if item_path != item["Path"]:
                    logger.debug(
                        "upload_poster: translated path %s -> %s",
                        item["Path"],
                        item_path,
                    )
                if item.get("IsFolder"):
                    inner = item_path.rstrip("/")
                    outer = os.path.dirname(inner)
                    candidates = [inner, outer] if outer != inner else [inner]
                else:
                    # For Movie/Episode files: try the immediate parent dir,
                    # then one level further up (handles double-nested layouts
                    # like Show(year)/Show(year)/ep.mkv where the inner dir
                    # may not exist but the outer show dir does).
                    inner = os.path.dirname(item_path)
                    outer = os.path.dirname(inner)
                    candidates = [inner, outer] if outer != inner else [inner]
                wrote_folder_jpg = False
                for folder_dir in candidates:
                    folder_jpg = os.path.join(folder_dir, "folder.jpg")
                    try:
                        with open(folder_jpg, "wb") as fh:
                            fh.write(image_bytes)
                        await self._refresh_item_images(item_id)
                        logger.debug(
                            "Wrote folder.jpg for %s at %s", item_id, folder_jpg
                        )
                        wrote_folder_jpg = True
                        break
                    except Exception as exc:
                        logger.warning(
                            "Failed to write folder.jpg for %s (%s): %s",
                            item_id,
                            folder_jpg,
                            exc,
                        )
                if wrote_folder_jpg:
                    return

            # Last resort: ask Jellyfin to fetch the image URL itself.
            # RemoteImages/Download applies the image immediately — no separate
            # refresh needed (and a full image refresh would overwrite it).
            fallback = await self._http.post(
                f"/Items/{item_id}/RemoteImages/Download",
                params={"Type": "Primary", "ImageUrl": image_url},
            )
            fallback.raise_for_status()
            logger.debug("Set poster for Jellyfin item %s (remote fallback)", item_id)
            return

        resp.raise_for_status()
        logger.debug("Set poster for Jellyfin item %s", item_id)

    async def _refresh_item_images(self, item_id: str) -> None:
        """Trigger an image-only refresh for a single Jellyfin item.

        Uses ``FullRefresh`` so Jellyfin re-scans local image files (e.g.
        ``folder.jpg``) and replaces whatever was there before.
        """
        try:
            resp = await self._http.post(
                f"/Items/{item_id}/Refresh",
                params={
                    "MetadataRefreshMode": "None",
                    "ImageRefreshMode": "FullRefresh",
                    "ReplaceAllImages": "true",
                },
            )
            resp.raise_for_status()
            logger.debug("Triggered image refresh for Jellyfin item %s", item_id)
        except Exception:
            logger.debug("Failed to trigger image refresh for item %s", item_id)

    async def upload_poster_to_parent_folder(
        self, item_id: str, image_url: str
    ) -> None:
        """If the item's immediate parent is a Folder, also set its poster.

        In mixed Jellyfin libraries, show containers are plain Folders rather
        than Series items.  ``get_library_shows`` returns the child Movie/Series
        items, so without this step the visible library card (the Folder) never
        receives the AniList artwork.
        """
        # Walk up the item hierarchy to find the top-level show folder —
        # the Folder whose parent is a library root (CollectionFolder / UserView).
        # This handles double-nested layouts like Show (year)/Season (year)/episodes.
        _LIBRARY_TYPES = {"CollectionFolder", "UserView", "AggregateFolder"}

        current = await self.get_item(item_id)
        if not current:
            logger.debug("upload_poster_to_parent_folder: item %s not found", item_id)
            return

        logger.debug(
            "upload_poster_to_parent_folder: start item=%s type=%s parentId=%s",
            item_id,
            current.get("Type"),
            current.get("ParentId"),
        )

        top_folder: dict[str, Any] | None = None
        seen: set[str] = {item_id}

        while True:
            if current.get("Type") == "Folder":
                parent_id = current.get("ParentId", "")
                if not parent_id:
                    logger.debug(
                        "upload_poster_to_parent_folder: no parent_id on %s, stopping",
                        current.get("Id"),
                    )
                    break
                parent = await self.get_item(parent_id)
                if not parent:
                    logger.debug(
                        "upload_poster_to_parent_folder: parent %s not found", parent_id
                    )
                    break
                parent_type = parent.get("Type", "")
                logger.debug(
                    "upload_poster_to_parent_folder: current=%s(%s) -> parent=%s(%s)",
                    current.get("Id"),
                    current.get("Type"),
                    parent.get("Id"),
                    parent_type,
                )
                if parent_type in _LIBRARY_TYPES:
                    # Parent is library root → current IS the show folder.
                    top_folder = current
                    break
                if parent_type == "Folder":
                    # Parent is a plain folder — check if IT is a library container
                    # (i.e. its parent is a library root).  This handles layouts like:
                    #   AggregateFolder → library_dir [Folder] → Show [Folder] → items
                    # where the intermediate library_dir is not a CollectionFolder.
                    grandparent_id = parent.get("ParentId", "")
                    if grandparent_id:
                        grandparent = await self.get_item(grandparent_id)
                        if grandparent and grandparent.get("Type") in _LIBRARY_TYPES:
                            # parent is the library container; current is the show folder.
                            top_folder = current
                            break
                if parent["Id"] in seen:
                    logger.debug(
                        "upload_poster_to_parent_folder: cycle detected at %s",
                        parent["Id"],
                    )
                    break  # cycle guard
                seen.add(parent["Id"])
                current = parent
            else:
                # Non-folder item (Movie, Episode, etc.) — step up once
                parent_id = current.get("ParentId", "")
                if not parent_id:
                    break
                parent = await self.get_item(parent_id)
                if not parent or parent["Id"] in seen:
                    break
                logger.debug(
                    "upload_poster_to_parent_folder: non-folder %s(%s) -> parent %s(%s)",
                    current.get("Id"),
                    current.get("Type"),
                    parent.get("Id"),
                    parent.get("Type"),
                )
                seen.add(parent["Id"])
                current = parent

        if not top_folder:
            logger.debug(
                "upload_poster_to_parent_folder: no top folder found for %s", item_id
            )
            return
        if top_folder.get("Id") == item_id:
            logger.debug(
                "upload_poster_to_parent_folder: item %s IS the top-level folder (no separate parent to target)",
                item_id,
            )
            return

        logger.info(
            "Setting poster on top-level folder '%s' (%s) for item %s",
            top_folder.get("Name"),
            top_folder.get("Id"),
            item_id,
        )
        folder_id = top_folder["Id"]
        try:
            await self.upload_poster(folder_id, image_url)
        except Exception:
            logger.warning(
                "Failed to set poster on top-level folder %s",
                folder_id,
                exc_info=True,
            )
        # Lock the folder so Jellyfin's scheduled scans don't overwrite our image.
        try:
            await self.lock_item(folder_id)
            logger.debug("Locked top-level folder %s", folder_id)
        except Exception:
            logger.warning(
                "Failed to lock top-level folder %s", folder_id, exc_info=True
            )

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
        inactivity_timeout: float = 120.0,
    ) -> bool:
        """Trigger a library refresh and poll until the scan task is idle.

        Returns True if the scan completed successfully, False if it timed out
        or the task ID could not be found.

        The timeout is based on *inactivity* — the timer resets whenever
        progress changes, so large libraries won't time out as long as
        Jellyfin is still making progress.
        """
        task_id = await self._get_scan_task_id()
        if not task_id:
            logger.warning(
                "Jellyfin scan task ID not found — cannot poll for completion"
            )
            return False

        await self.refresh_library()
        await asyncio.sleep(2.0)

        started_running = False
        last_progress: float = -1.0
        last_activity = asyncio.get_event_loop().time()

        while True:
            now = asyncio.get_event_loop().time()
            if now - last_activity > inactivity_timeout:
                logger.warning(
                    "Jellyfin library scan stalled (no progress for %.0fs)",
                    inactivity_timeout,
                )
                return False

            try:
                resp = await self._http.get(f"/ScheduledTasks/{task_id}")
                resp.raise_for_status()
                task = resp.json()
            except Exception:
                logger.debug("Failed to poll Jellyfin scan task")
                await asyncio.sleep(poll_interval)
                continue

            state = task.get("State", "Idle")
            progress = task.get("CurrentProgressPercentage") or 0.0

            if state == "Running":
                started_running = True
                if progress != last_progress:
                    last_activity = now
                    last_progress = progress
                logger.debug("Jellyfin scan running: %.1f%%", progress)
            elif state == "Idle" and started_running:
                result = task.get("LastExecutionResult") or {}
                status = result.get("Status", "Unknown")
                logger.info("Jellyfin library scan complete: %s", status)
                return status not in ("Failed", "Aborted")

            await asyncio.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()
