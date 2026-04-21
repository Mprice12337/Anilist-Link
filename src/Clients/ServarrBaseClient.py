"""Base client for Servarr-family APIs (Sonarr, Radarr).

Consolidates the shared HTTP plumbing, quality-profile/root-folder queries,
naming-config CRUD, webhook registration, and common CRUD helpers so that
SonarrClient and RadarrClient only contain resource-specific logic.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ServarrBaseClient:
    """Shared async client for Sonarr/Radarr API v3."""

    # Subclasses set these for logging and webhook fallback payload
    _service_name: str = "Servarr"
    _webhook_info_link: str = ""
    _webhook_fallback_events: dict[str, bool] = {}

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
        """Return system status or raise on failure."""
        resp = await self._http.get(self._endpoint("system/status"))
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
        """Return naming configuration."""
        resp = await self._http.get(self._endpoint("config/naming"))
        resp.raise_for_status()
        return resp.json()

    async def push_naming_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Update naming configuration."""
        resp = await self._http.put(self._endpoint("config/naming"), json=config)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Release grab (identical across Sonarr/Radarr)
    # ------------------------------------------------------------------

    async def grab_release(self, guid: str, indexer_id: int) -> dict[str, Any]:
        """Instruct the service to grab a specific release."""
        resp = await self._http.post(
            self._endpoint("release"),
            json={"guid": guid, "indexerId": indexer_id},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Notifications / webhooks
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
        """Register a webhook; no-op if the name already exists."""
        for n in await self.get_notifications():
            if n.get("name") == name:
                return n

        # Build payload from notification schema if available, else use fallback
        schema: dict[str, Any] = {}
        try:
            resp = await self._http.get(self._endpoint("notification/schema"))
            resp.raise_for_status()
            for s in resp.json():
                if s.get("implementation") == "Webhook":
                    schema = s
                    break
        except Exception:
            pass

        if schema:
            schema.pop("id", None)
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
                **self._webhook_fallback_events,
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
                "infoLink": self._webhook_info_link,
                "tags": [],
            }

        resp = await self._http.post(self._endpoint("notification"), json=payload)
        if resp.status_code >= 400:
            logger.warning(
                "%s webhook registration failed (%d): %s",
                self._service_name,
                resp.status_code,
                resp.text[:500],
            )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Protected helpers for resource-specific CRUD
    # ------------------------------------------------------------------

    async def _get_by_id(
        self, resource: str, resource_id: int
    ) -> dict[str, Any] | None:
        """GET a single resource by ID, returning None on 404."""
        try:
            resp = await self._http.get(self._endpoint(f"{resource}/{resource_id}"))
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def _get_all(self, resource: str) -> list[dict[str, Any]]:
        """GET all items of a resource type."""
        resp = await self._http.get(self._endpoint(resource))
        resp.raise_for_status()
        return resp.json()

    async def _update_path(
        self, resource: str, resource_id: int, new_path: str
    ) -> dict[str, Any]:
        """Fetch a resource by ID, set its path, and PUT it back."""
        item = await self._get_by_id(resource, resource_id)
        if not item:
            raise ValueError(
                f"{resource} {resource_id} not found in {self._service_name}"
            )
        item["path"] = new_path
        resp = await self._http.put(
            self._endpoint(f"{resource}/{resource_id}"), json=item
        )
        resp.raise_for_status()
        return resp.json()

    async def _move_root_folder(
        self, resource: str, resource_id: int, new_root_folder: str
    ) -> dict[str, Any]:
        """Move a resource to a new root folder, moving files."""
        from pathlib import Path as _Path

        item = await self._get_by_id(resource, resource_id)
        if not item:
            raise ValueError(
                f"{resource} {resource_id} not found in {self._service_name}"
            )
        old_path = item.get("path", "")
        folder_name = _Path(old_path).name if old_path else ""
        item["rootFolderPath"] = new_root_folder
        if folder_name:
            item["path"] = str(_Path(new_root_folder) / folder_name)
        resp = await self._http.put(
            self._endpoint(f"{resource}/{resource_id}"),
            json=item,
            params={"moveFiles": "true"},
        )
        resp.raise_for_status()
        return resp.json()

    async def _rescan(
        self, command_name: str, id_field: str, resource_id: int
    ) -> dict[str, Any]:
        """Trigger a disk rescan command."""
        payload = {"name": command_name, id_field: resource_id}
        resp = await self._http.post(self._endpoint("command"), json=payload)
        resp.raise_for_status()
        return resp.json()

    async def _get_file_by_id(
        self, file_resource: str, file_id: int
    ) -> dict[str, Any] | None:
        """GET a single file record by ID, returning None on 404."""
        return await self._get_by_id(file_resource, file_id)

    async def _update_file(
        self, file_resource: str, file_id: int, relative_path: str, path: str
    ) -> dict[str, Any]:
        """Update stored paths for a file record."""
        file_obj = await self._get_file_by_id(file_resource, file_id)
        if not file_obj:
            raise ValueError(
                f"{file_resource} {file_id} not found in {self._service_name}"
            )
        file_obj["relativePath"] = relative_path
        file_obj["path"] = path
        resp = await self._http.put(
            self._endpoint(f"{file_resource}/{file_id}"), json=file_obj
        )
        resp.raise_for_status()
        return resp.json()

    async def _search_releases(
        self, id_param: str, id_value: int, timeout: float = 30.0
    ) -> list[dict[str, Any]]:
        """Search for available releases using a resource ID parameter."""
        resp = await self._http.get(
            self._endpoint("release"),
            params={id_param: id_value},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    async def _push_release(
        self,
        title: str,
        download_url: str,
        protocol: str,
        publish_date: str,
        id_field: str | None,
        id_value: int | None,
    ) -> dict[str, Any]:
        """Push a release URL directly to the service."""
        payload: dict[str, Any] = {
            "title": title,
            "downloadUrl": download_url,
            "protocol": protocol,
        }
        if publish_date:
            payload["publishDate"] = publish_date
        if id_field and id_value:
            payload[id_field] = id_value
        resp = await self._http.post(self._endpoint("release/push"), json=payload)
        resp.raise_for_status()
        return resp.json()

    async def _update_monitor(
        self, resource: str, resource_id: int, monitored: bool
    ) -> dict[str, Any]:
        """Toggle the monitored flag for an existing resource."""
        item = await self._get_by_id(resource, resource_id)
        if not item:
            raise ValueError(
                f"{resource} {resource_id} not found in {self._service_name}"
            )
        item["monitored"] = monitored
        resp = await self._http.put(
            self._endpoint(f"{resource}/{resource_id}"), json=item
        )
        resp.raise_for_status()
        return resp.json()
