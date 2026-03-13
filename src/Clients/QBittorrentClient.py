"""qBittorrent Web API v2 client."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class QBittorrentClient:
    """Async qBittorrent Web API v2 client.

    Authentication is session-based (cookie). Call ``authenticate()`` before
    making other requests, or ``test_connection()`` which authenticates first.
    """

    def __init__(
        self, url: str, username: str = "admin", password: str = "adminadmin"
    ) -> None:
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._http = httpx.AsyncClient(timeout=30.0)
        self._authenticated = False

    async def close(self) -> None:
        await self._http.aclose()

    def _endpoint(self, path: str) -> str:
        return f"{self._url}/api/v2/{path.lstrip('/')}"

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def authenticate(self) -> bool:
        """POST credentials to qBittorrent and persist the session cookie.

        Returns True if authentication succeeded.
        """
        try:
            resp = await self._http.post(
                self._endpoint("auth/login"),
                data={"username": self._username, "password": self._password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            ok = resp.text.strip() == "Ok."
            self._authenticated = ok
            if not ok:
                logger.warning(
                    "qBittorrent auth failed — unexpected response: %s", resp.text[:50]
                )
            return ok
        except Exception as exc:
            logger.error("qBittorrent authentication error: %s", exc)
            return False

    async def test_connection(self) -> dict[str, Any]:
        """Authenticate and return app version info."""
        if not self._authenticated:
            ok = await self.authenticate()
            if not ok:
                raise RuntimeError("qBittorrent authentication failed")
        resp = await self._http.get(self._endpoint("app/version"))
        resp.raise_for_status()
        return {"version": resp.text.strip()}

    # ------------------------------------------------------------------
    # Torrents
    # ------------------------------------------------------------------

    async def add_torrent(
        self,
        url_or_magnet: str,
        save_path: str,
        category: str = "",
        tags: list[str] | None = None,
        name: str | None = None,
    ) -> bool:
        """Add a torrent by URL or magnet link.

        Returns True if successfully submitted.
        """
        if not self._authenticated:
            await self.authenticate()

        data: dict[str, Any] = {
            "urls": url_or_magnet,
            "savepath": save_path,
        }
        if category:
            data["category"] = category
        if tags:
            data["tags"] = ",".join(tags)
        if name:
            data["rename"] = name

        resp = await self._http.post(
            self._endpoint("torrents/add"),
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.text.strip().lower() == "ok."

    async def add_torrent_file(
        self,
        content: bytes,
        save_path: str,
        category: str = "",
        name: str | None = None,
    ) -> bool:
        """Add a torrent from raw .torrent file bytes.

        Returns True if successfully submitted.
        """
        if not self._authenticated:
            await self.authenticate()

        files: dict[str, Any] = {
            "torrents": ("upload.torrent", content, "application/x-bittorrent")
        }
        data: dict[str, str] = {"savepath": save_path}
        if category:
            data["category"] = category
        if name:
            data["rename"] = name

        resp = await self._http.post(
            self._endpoint("torrents/add"),
            files=files,
            data=data,
        )
        resp.raise_for_status()
        return resp.text.strip().lower() == "ok."

    async def get_torrents(self, category: str | None = None) -> list[dict[str, Any]]:
        """Return a list of torrent info dicts, optionally filtered by category."""
        if not self._authenticated:
            await self.authenticate()

        params: dict[str, Any] = {}
        if category:
            params["category"] = category

        resp = await self._http.get(self._endpoint("torrents/info"), params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_torrent_info(self, torrent_hash: str) -> dict[str, Any] | None:
        """Return info for a single torrent by hash, or None if not found."""
        if not self._authenticated:
            await self.authenticate()

        resp = await self._http.get(
            self._endpoint("torrents/info"), params={"hashes": torrent_hash}
        )
        resp.raise_for_status()
        results: list[dict[str, Any]] = resp.json()
        return results[0] if results else None
