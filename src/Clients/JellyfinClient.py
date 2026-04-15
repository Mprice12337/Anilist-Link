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

    async def get_library_shows(
        self,
        library_id: str,
        by_season: bool = False,
    ) -> list[JellyfinShow]:
        """Get show containers from a library.

        Args:
            library_id: The Jellyfin library ID.
            by_season: When True, return Season+Movie items so each season
                subfolder is an independent scan target. After tvshow.nfo is
                written, Jellyfin reclassifies season subfolders from Movie →
                Season; this mode restores per-season granularity regardless of
                NFO status. Specials (IndexNumber == 0) are excluded.
                When False (default), return Series+Movie items — one entry per
                show root, suited for the restructure wizard and show-level ops.
        """
        if by_season:
            include_types = "Season,Movie"
            allowed_types = {"Season", "Movie"}
        else:
            include_types = "Series,Movie"
            allowed_types = {"Series", "Movie"}

        params = {
            "ParentId": library_id,
            "IncludeItemTypes": include_types,
            "Recursive": "true",
            "Fields": (
                "Path,Overview,Genres,Studios,OriginalTitle,ProductionYear,IndexNumber"
            ),
            "SortBy": "SortName",
            "SortOrder": "Ascending",
        }
        resp = await self._http.get("/Items", params=params)
        resp.raise_for_status()
        shows: list[JellyfinShow] = []
        for item in resp.json().get("Items", []):
            item_type = item.get("Type", "")
            if item_type not in allowed_types:
                logger.debug(
                    "Skipping item '%s' (Type=%s)",
                    item.get("Name", ""),
                    item_type,
                )
                continue
            # In by_season mode, skip the auto-generated Specials bucket.
            if by_season and item_type == "Season" and item.get("IndexNumber", 1) == 0:
                logger.debug("Skipping Specials season for '%s'", item.get("Name", ""))
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
                    media_type=item_type,
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
                        "ProductionYear,LockedFields,LockData,ParentId,"
                        "IsFolder,ProviderIds"
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

    async def get_series_ids_in_library(self, library_id: str) -> list[str]:
        """Return the Jellyfin item IDs of all Series in a library.

        Used to collect targets for a post-scan episode metadata refresh so
        we can call ``refresh_item_metadata`` on each series rather than on
        the virtual-folder container (which may not cascade correctly).
        """
        try:
            resp = await self._http.get(
                "/Items",
                params={
                    "ParentId": library_id,
                    "IncludeItemTypes": "Series",
                    "Recursive": "true",
                    "Fields": "Id",
                    "Limit": "5000",
                },
            )
            resp.raise_for_status()
            return [
                item["Id"]
                for item in resp.json().get("Items", [])
                if item.get("Id")
            ]
        except Exception as exc:
            logger.warning(
                "Could not fetch series IDs for library %s: %s", library_id, exc
            )
            return []

    async def refresh_item_metadata(
        self,
        item_id: str,
        recursive: bool = False,
        replace_all: bool = False,
    ) -> None:
        """Trigger a metadata (and image) refresh for a Jellyfin item.

        Pass ``recursive=True`` to cascade to all child items.  Items that
        have ``LockData=True`` (set by our NFO files) are immune — Jellyfin
        skips locked fields even with ``replace_all=True``.  This means a
        recursive replace-all refresh on a series container will leave our
        locked series/season metadata untouched while fully refreshing unlocked
        episode items from TMDB, TVDB, OMDB, and TVMaze.
        """
        try:
            params: dict[str, str] = {
                "MetadataRefreshMode": "FullRefresh",
                "ImageRefreshMode": "FullRefresh",
            }
            if replace_all:
                params["ReplaceAllMetadata"] = "true"
                params["ReplaceAllImages"] = "true"
            if recursive:
                params["Recursive"] = "true"
            resp = await self._http.post(
                f"/Items/{item_id}/Refresh", params=params
            )
            resp.raise_for_status()
            logger.info(
                "Triggered metadata refresh for item %s (recursive=%s replace=%s)",
                item_id,
                recursive,
                replace_all,
            )
        except Exception as exc:
            logger.warning(
                "Failed to trigger metadata refresh for item %s: %s", item_id, exc
            )

    async def _find_show_root_folder(self, item_id: str) -> dict[str, Any] | None:
        """Walk the Jellyfin item hierarchy to find the top-level show folder.

        Returns the Folder or Series item whose parent is a library root
        (CollectionFolder / UserView / AggregateFolder).  Handles:
        - Items that ARE the show folder/series (returned directly)
        - Season/Movie/Episode children nested inside a Series or Folder container
        - Double-nested layouts: Show(year)/Season(year)/episodes

        Returns None if the hierarchy cannot be resolved.
        """
        _LIBRARY_TYPES = {"CollectionFolder", "UserView", "AggregateFolder"}

        current = await self.get_item(item_id)
        if not current:
            return None

        logger.debug(
            "_find_show_root_folder: start item=%s type=%s parentId=%s",
            item_id,
            current.get("Type"),
            current.get("ParentId"),
        )

        seen: set[str] = {item_id}

        while True:
            current_type = current.get("Type", "")

            # Series is always the top-level show container — return immediately.
            if current_type == "Series":
                return current

            if current_type == "Folder":
                parent_id = current.get("ParentId", "")
                if not parent_id:
                    break
                parent = await self.get_item(parent_id)
                if not parent:
                    break
                parent_type = parent.get("Type", "")
                logger.debug(
                    "_find_show_root_folder: current=%s(%s) -> parent=%s(%s)",
                    current.get("Id"),
                    current_type,
                    parent.get("Id"),
                    parent_type,
                )
                if parent_type in _LIBRARY_TYPES:
                    # Parent is a library root → current IS the show folder.
                    return current
                if parent_type == "Folder":
                    # Check if the parent's parent is a library root — handles
                    # layouts where an intermediate plain Folder acts as the
                    # library directory (not a CollectionFolder).
                    grandparent_id = parent.get("ParentId", "")
                    if grandparent_id:
                        grandparent = await self.get_item(grandparent_id)
                        if grandparent and grandparent.get("Type") in _LIBRARY_TYPES:
                            return current
                if parent["Id"] in seen:
                    logger.debug(
                        "_find_show_root_folder: cycle detected at %s", parent["Id"]
                    )
                    break
                seen.add(parent["Id"])
                current = parent
            else:
                # Season, Movie, Episode, etc. — step up to parent container
                parent_id = current.get("ParentId", "")
                if not parent_id:
                    break
                parent = await self.get_item(parent_id)
                if not parent or parent["Id"] in seen:
                    break
                logger.debug(
                    "_find_show_root_folder: non-folder %s(%s) -> parent %s(%s)",
                    current.get("Id"),
                    current_type,
                    parent.get("Id"),
                    parent.get("Type"),
                )
                seen.add(parent["Id"])
                current = parent

        return None

    async def upload_poster_to_parent_folder(
        self, item_id: str, image_url: str
    ) -> None:
        """If the item's immediate parent is a Folder, also set its poster.

        In mixed Jellyfin libraries, show containers are plain Folders rather
        than Series items.  ``get_library_shows`` returns the child Movie/Series
        items, so without this step the visible library card (the Folder) never
        receives the AniList artwork.
        """
        top_folder = await self._find_show_root_folder(item_id)
        if not top_folder:
            logger.debug(
                "upload_poster_to_parent_folder: no top folder found for %s", item_id
            )
            return
        if top_folder.get("Id") == item_id:
            logger.debug(
                "upload_poster_to_parent_folder: item %s IS the top-level folder",
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

    # ------------------------------------------------------------------
    # Watch status operations
    # ------------------------------------------------------------------

    async def get_users(self) -> list[dict[str, Any]]:
        """Return all users on the Jellyfin server.

        Each dict has at minimum ``Id`` and ``Name`` keys.
        """
        try:
            resp = await self._http.get("/Users")
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
        except Exception:
            logger.debug("Failed to fetch Jellyfin users")
            return []

    async def get_series_episodes_with_userdata(
        self,
        series_id: str,
        user_id: str,
        season_item_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return episodes for a series, including per-user watch state.

        Uses ``GET /Items`` with ``ParentId`` and ``Recursive=true`` rather
        than ``GET /Shows/{id}/Episodes`` because the Shows endpoint silently
        returns nothing when the series has no explicit Season containers
        (common for double-nested folder layouts like
        ``/Show/Season1/ep.mkv``).

        If ``season_item_id`` (the Jellyfin season UUID) is provided, only
        episodes from that season container are returned by passing it as
        ``ParentId`` instead of the series ID.

        Episodes are sorted by season number then episode number so that
        ``episodes[:progress]`` always maps to the first N episodes in order.
        """
        episode_params: dict[str, str] = {
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "Fields": "UserData",
            "EnableUserData": "true",
            "SortBy": "ParentIndexNumber,IndexNumber",
            "SortOrder": "Ascending",
        }

        try:
            if season_item_id is not None:
                # Season-scoped: use the user-context endpoint with the season
                # UUID as ParentId — this is the most reliable approach for
                # fetching a specific season's episodes with played state.
                resp = await self._http.get(
                    f"/Users/{user_id}/Items",
                    params={**episode_params, "ParentId": season_item_id},
                )
                resp.raise_for_status()
                items: list[dict[str, Any]] = resp.json().get("Items", [])
                logger.debug(
                    "Episodes via user-Items season=%s → %d", season_item_id, len(items)
                )
                return items

            # ----------------------------------------------------------------
            # Series-level: first log the item's actual type so we know what
            # we're dealing with, then try multiple strategies.
            # ----------------------------------------------------------------
            diag = await self._http.get(
                "/Items",
                params={"Ids": series_id, "Fields": "Type,Name,Path"},
            )
            item_type = "unknown"
            if diag.status_code == 200:
                diag_items = diag.json().get("Items", [])
                if diag_items:
                    item_type = diag_items[0].get("Type", "unknown")
                    logger.debug(
                        "Item %s is Type=%s Name=%r",
                        series_id,
                        item_type,
                        diag_items[0].get("Name"),
                    )
                else:
                    logger.warning(
                        "series_id=%s not found in Jellyfin — "
                        "source_id may be stale; re-run the Jellyfin scanner",
                        series_id,
                    )
                    return []

            # Strategy 1: user-context endpoint with ParentId.
            # /Users/{id}/Items respects the item hierarchy in user-context
            # and is more reliable than the admin /Items endpoint for
            # filtering by non-Series container IDs (Folders, BoxSets, etc.)
            u_resp = await self._http.get(
                f"/Users/{user_id}/Items",
                params={**episode_params, "ParentId": series_id},
            )
            if u_resp.status_code not in (400, 404):
                u_resp.raise_for_status()
                items = u_resp.json().get("Items", [])
                total = u_resp.json().get("TotalRecordCount", len(items))
                logger.debug(
                    "Episodes via user-Items series=%s type=%s → %d (total=%d)",
                    series_id,
                    item_type,
                    len(items),
                    total,
                )
                # Sanity check: if we got back the whole library (~all episodes)
                # the filter was ignored — treat as 0 and fall through.
                if items and total < 5000:
                    return items
                if total >= 5000:
                    logger.debug(
                        "user-Items returned %d — filter likely ignored, trying /Shows",
                        total,
                    )

            # Strategy 2: canonical /Shows/{id}/Episodes (works for Series type).
            ep_resp = await self._http.get(
                f"/Shows/{series_id}/Episodes",
                params={
                    "UserId": user_id,
                    "Fields": "UserData",
                    "EnableUserData": "true",
                    "SortBy": "ParentIndexNumber,IndexNumber",
                    "SortOrder": "Ascending",
                },
            )
            if ep_resp.status_code not in (400, 404):
                ep_resp.raise_for_status()
                items = ep_resp.json().get("Items", [])
                if items:
                    logger.debug(
                        "Episodes via /Shows series=%s → %d", series_id, len(items)
                    )
                    return items

            logger.warning(
                "All episode strategies failed for series_id=%s type=%s — "
                "0 usable episodes found",
                series_id,
                item_type,
            )
            return []

        except Exception:
            logger.debug(
                "Failed to fetch episodes for Jellyfin series %s season %s",
                series_id,
                season_item_id,
                exc_info=True,
            )
            return []

    async def resolve_season_id(self, series_id: str, season_number: int) -> str | None:
        """Return the Jellyfin season item UUID for a given season number.

        ``season_number`` corresponds to ``IndexNumber`` in Jellyfin (1 for
        Season 1, 2 for Season 2, etc.; 0 = Specials).  Returns ``None`` if
        the season is not found.
        """
        seasons = await self.get_show_seasons(series_id)
        for season in seasons:
            if season.index == season_number:
                return season.item_id
        logger.debug(
            "Season %d not found for Jellyfin series %s (available: %s)",
            season_number,
            series_id,
            [s.index for s in seasons],
        )
        return None

    async def get_item_with_userdata(
        self, item_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """Fetch a single Jellyfin item including user-specific UserData.

        Uses the user-context endpoint ``/Users/{userId}/Items/{itemId}``
        which populates ``UserData.Played``, ``UserData.PlayCount``, etc.
        Returns ``None`` if the item is not found or the request fails.
        """
        try:
            resp = await self._http.get(
                f"/Users/{user_id}/Items/{item_id}",
                params={"Fields": "UserData"},
            )
            if resp.status_code in (400, 404):
                return None
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
        except Exception:
            logger.debug(
                "Failed to fetch item %s with userdata for user %s",
                item_id,
                user_id,
            )
            return None

    async def mark_episode_played(self, item_id: str, user_id: str) -> None:
        """Mark an episode or movie as played for the given Jellyfin user."""
        try:
            resp = await self._http.post(f"/Users/{user_id}/PlayedItems/{item_id}")
            resp.raise_for_status()
            logger.debug(
                "Marked Jellyfin item %s as played for user %s", item_id, user_id
            )
        except Exception:
            logger.debug(
                "Failed to mark item %s as played for user %s", item_id, user_id
            )

    async def mark_episode_unplayed(self, item_id: str, user_id: str) -> None:
        """Mark an episode as unplayed for the given Jellyfin user."""
        try:
            resp = await self._http.request(
                "DELETE", f"/Users/{user_id}/PlayedItems/{item_id}"
            )
            resp.raise_for_status()
            logger.debug(
                "Marked Jellyfin item %s as unplayed for user %s", item_id, user_id
            )
        except Exception:
            logger.debug(
                "Failed to mark item %s as unplayed for user %s", item_id, user_id
            )

    # AniList status values → Jellyfin/Kodi NFO status strings
    _ANILIST_STATUS_TO_NFO: dict[str, str] = {
        "FINISHED": "Ended",
        "RELEASING": "Continuing",
        "NOT_YET_RELEASED": "Upcoming",
        "CANCELLED": "Ended",
        "HIATUS": "Continuing",
    }

    @staticmethod
    def _xml_escape(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    async def write_tvshow_nfo(
        self,
        item_id: str,
        title: str,
        *,
        original_title: str | None = None,
        plot: str | None = None,
        genres: list[str] | None = None,
        studio: str | None = None,
        rating: float | None = None,
        year: int | None = None,
        status: str | None = None,
        anilist_id: int | None = None,
        tags: list[str] | None = None,
        imdb_id: str | None = None,
        tvdb_id: str | None = None,
        tvmaze_id: str | None = None,
        lock_data: bool = True,
    ) -> None:
        """Write a tvshow.nfo into the show's root directory.

        Writes AniList-sourced metadata so Jellyfin classifies the folder as a
        TV show and our custom series/season arrangement is preserved.
        ``<lockdata>true</lockdata>`` prevents Jellyfin's scheduled metadata
        refreshes (e.g. TVDB) from overwriting show-level data.

        Walks up the hierarchy via ``_find_show_root_folder`` so the NFO always
        lands at the top-level show folder regardless of which child item
        triggered the metadata apply.

        Episode-level metadata is intentionally left to TVDB — this NFO only
        covers the series container.
        """
        try:
            show_folder = await self._find_show_root_folder(item_id)
            if not show_folder or not show_folder.get("Path"):
                logger.debug(
                    "write_tvshow_nfo: could not resolve show root for item %s",
                    item_id,
                )
                return
            raw_path: str = show_folder["Path"]
            local_path = self._path_translator.translate(raw_path)
            folder_dir = local_path.rstrip("/").rstrip("\\")
            nfo_path = os.path.join(folder_dir, "tvshow.nfo")

            # Derive the TMDB series ID from the IMDB ID if not provided directly.
            # Jellyfin's TMDB provider supports IMDB-ID cross-referencing so
            # <uniqueid type="imdb"> is sufficient for episode matching even
            # without an explicit tmdb uniqueid.
            pass  # IDs arrive via imdb_id / tvdb_id params from the scanner

            lines: list[str] = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                "<tvshow>",
                f"  <title>{self._xml_escape(title)}</title>",
            ]
            if original_title:
                esc_ot = self._xml_escape(original_title)
                lines.append(f"  <originaltitle>{esc_ot}</originaltitle>")
            if plot:
                lines.append(f"  <plot>{self._xml_escape(plot)}</plot>")
            if year:
                lines.append(f"  <year>{year}</year>")
            if rating is not None:
                lines.append(f"  <rating>{rating}</rating>")
            if studio:
                lines.append(f"  <studio>{self._xml_escape(studio)}</studio>")
            if status:
                nfo_status = self._ANILIST_STATUS_TO_NFO.get(status, status)
                lines.append(f"  <status>{self._xml_escape(nfo_status)}</status>")
            for genre in genres or []:
                lines.append(f"  <genre>{self._xml_escape(genre)}</genre>")
            # Deduplicated title variant tags for searchability (romaji/english/native)
            for tag in sorted(set(tags or [])):
                lines.append(f"  <tag>{self._xml_escape(tag)}</tag>")
            if anilist_id is not None:
                lines.append(
                    f'  <uniqueid type="AniList" default="true">{anilist_id}</uniqueid>'
                )
            # Secondary provider IDs sourced from TVMaze — let episode providers
            # (TMDB, OMDB) retain their matching reference after our restructure
            # renames folders away from names those providers would recognise.
            if imdb_id:
                lines.append(f'  <uniqueid type="imdb">{imdb_id}</uniqueid>')
            if tvdb_id:
                lines.append(f'  <uniqueid type="tvdb">{tvdb_id}</uniqueid>')
            if tvmaze_id:
                lines.append(f'  <uniqueid type="TVmaze">{tvmaze_id}</uniqueid>')
            if lock_data:
                lines.append("  <lockdata>true</lockdata>")
            lines.append("</tvshow>")

            content = "\n".join(lines) + "\n"
            with open(nfo_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            logger.info("Wrote tvshow.nfo for '%s' at %s", title, nfo_path)
        except Exception as exc:
            logger.debug("Could not write tvshow.nfo for item %s: %s", item_id, exc)

    async def write_season_nfo(
        self,
        item_id: str,
        title: str,
        season_number: int,
        *,
        original_title: str | None = None,
        plot: str | None = None,
        year: int | None = None,
        anilist_id: int | None = None,
        genres: list[str] | None = None,
        studio: str | None = None,
        rating: float | None = None,
        tags: list[str] | None = None,
        series_imdb_id: str | None = None,
        series_tvdb_id: str | None = None,
        series_tvmaze_id: str | None = None,
        lock_data: bool = True,
    ) -> None:
        """Write a season.nfo into the item's own directory.

        Writes AniList metadata for this specific season entry so each season
        carries its own title, description, and AniList ID rather than
        inheriting the parent show's TVDB data.  ``<lockdata>true</lockdata>``
        prevents Jellyfin from overwriting season-level metadata on refresh.

        ``series_imdb_id``, ``series_tvdb_id``, and ``series_tvmaze_id`` are
        the parent series' provider IDs written as non-default uniqueid entries.
        They give TMDB, TVDB, OMDB, and TVMaze enough series context to resolve
        per-episode metadata even when the season numbering in our custom
        arrangement diverges from what those providers use.

        Uses the item's own ``Path`` (no hierarchy walk) so the file lands in
        the correct season subdirectory.  Episode-level metadata remains owned
        by TVDB.
        """
        try:
            item = await self.get_item(item_id)
            if not item or not item.get("Path"):
                logger.debug(
                    "write_season_nfo: no path for item %s", item_id
                )
                return
            raw_path: str = item["Path"]
            local_path = self._path_translator.translate(raw_path)
            # Path may point to a file (Movie/OVA) — use its parent directory
            if os.path.splitext(local_path)[1]:
                folder_dir = os.path.dirname(local_path)
            else:
                folder_dir = local_path.rstrip("/").rstrip("\\")
            nfo_path = os.path.join(folder_dir, "season.nfo")

            lines: list[str] = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                "<season>",
                f"  <title>{self._xml_escape(title)}</title>",
            ]
            if original_title:
                esc_ot = self._xml_escape(original_title)
                lines.append(f"  <originaltitle>{esc_ot}</originaltitle>")
            lines.append(f"  <seasonnumber>{season_number}</seasonnumber>")
            if plot:
                lines.append(f"  <plot>{self._xml_escape(plot)}</plot>")
            if year:
                lines.append(f"  <year>{year}</year>")
            if rating is not None:
                lines.append(f"  <rating>{rating}</rating>")
            if studio:
                lines.append(f"  <studio>{self._xml_escape(studio)}</studio>")
            for genre in genres or []:
                lines.append(f"  <genre>{self._xml_escape(genre)}</genre>")
            # Deduplicated title variant tags for searchability (romaji/english/native)
            for tag in sorted(set(tags or [])):
                lines.append(f"  <tag>{self._xml_escape(tag)}</tag>")
            if anilist_id is not None:
                lines.append(
                    f'  <uniqueid type="AniList" default="true">{anilist_id}</uniqueid>'
                )
            # Series-level provider IDs — non-default so they don't override
            # the per-season AniList source, but give episode providers the
            # series context they need for per-episode metadata lookups.
            if series_imdb_id:
                lines.append(
                    f'  <uniqueid type="imdb">{series_imdb_id}</uniqueid>'
                )
            if series_tvdb_id:
                lines.append(
                    f'  <uniqueid type="tvdb">{series_tvdb_id}</uniqueid>'
                )
            if series_tvmaze_id:
                lines.append(
                    f'  <uniqueid type="TVmaze">{series_tvmaze_id}</uniqueid>'
                )
            if lock_data:
                lines.append("  <lockdata>true</lockdata>")
            lines.append("</season>")

            content = "\n".join(lines) + "\n"
            with open(nfo_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            logger.info(
                "Wrote season.nfo for '%s' (season %d) at %s",
                title,
                season_number,
                nfo_path,
            )
        except Exception as exc:
            logger.debug(
                "Could not write season.nfo for item %s: %s", item_id, exc
            )

    async def refresh_library(
        self, library_ids: list[str] | None = None
    ) -> None:
        """Trigger a Jellyfin library refresh.

        If *library_ids* is provided, only those virtual-folder items are
        refreshed (``POST /Items/{id}/Refresh?Recursive=true``).
        Otherwise the global ``POST /Library/Refresh`` is used.
        """
        if library_ids:
            for lib_id in library_ids:
                try:
                    resp = await self._http.post(
                        f"/Items/{lib_id}/Refresh",
                        params={"Recursive": "true"},
                    )
                    resp.raise_for_status()
                    logger.info(
                        "Triggered Jellyfin library refresh for %s", lib_id
                    )
                except Exception:
                    logger.debug(
                        "Failed to trigger Jellyfin library refresh for %s",
                        lib_id,
                    )
        else:
            try:
                resp = await self._http.post("/Library/Refresh")
                resp.raise_for_status()
                logger.info("Triggered Jellyfin library refresh (all)")
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
        library_ids: list[str] | None = None,
    ) -> bool:
        """Trigger a library refresh and poll until the scan task is idle.

        If *library_ids* is provided, only those libraries are refreshed;
        otherwise all libraries are refreshed.

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

        await self.refresh_library(library_ids=library_ids)
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
