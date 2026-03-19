"""Prowlarr API client for searching indexers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_QUALITY_PATTERNS: list[tuple[str, str]] = [
    (r"\b4K\b|2160p", "4K"),
    (r"1080p", "1080p"),
    (r"720p", "720p"),
    (r"480p", "480p"),
]


def _parse_quality(title: str) -> str:
    """Detect video quality from a release title string."""
    for pattern, label in _QUALITY_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return label
    return "Unknown"


@dataclass
class ReleaseResult:
    """A single search result from Prowlarr."""

    guid: str
    title: str
    size: int  # bytes
    seeders: int
    leechers: int
    indexer: str
    indexer_id: int
    download_url: str
    magnet_url: str
    publish_date: str
    quality: str  # "1080p", "720p", etc.
    is_torrent: bool  # True=torrent, False=NZB


class ProwlarrClient:
    """Async Prowlarr API v1 client."""

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
        return f"{self._url}/api/v1/{path.lstrip('/')}"

    # ------------------------------------------------------------------
    # Connection / health
    # ------------------------------------------------------------------

    async def test_connection(self) -> dict[str, Any]:
        """Return Prowlarr system status or raise on failure."""
        resp = await self._http.get(self._endpoint("system/status"))
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Indexers
    # ------------------------------------------------------------------

    async def get_indexers(self) -> list[dict[str, Any]]:
        """Return all configured indexers."""
        resp = await self._http.get(self._endpoint("indexer"))
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        categories: list[int] | None = None,
        indexer_ids: list[int] | None = None,
        limit: int = 100,
    ) -> list[ReleaseResult]:
        """Search Prowlarr indexers for releases.

        ``categories`` — Prowlarr category IDs (5070=anime, 2000=movies).
        """
        params: dict[str, Any] = {
            "query": query,
            "type": "search",
            "limit": limit,
        }
        if categories:
            params["categories"] = categories
        if indexer_ids:
            params["indexerIds"] = indexer_ids

        resp = await self._http.get(self._endpoint("search"), params=params)
        resp.raise_for_status()
        raw: list[dict[str, Any]] = resp.json()
        return [self._parse_result(r) for r in raw]

    async def grab_release(self, guid: str, indexer_id: int) -> dict[str, Any]:
        """Tell Prowlarr to grab a release.

        Routes it to the configured download client.
        """
        resp = await self._http.post(
            self._endpoint("release"),
            json={"guid": guid, "indexerId": indexer_id},
        )
        resp.raise_for_status()
        return resp.json()

    async def search_anime(
        self,
        query: str,
        titles: list[str] | None = None,
    ) -> list[ReleaseResult]:
        """Search with the main query and all alternate titles, deduplicating by guid.

        Returns merged results sorted by seeders descending.
        """
        seen_guids: set[str] = set()
        all_results: list[ReleaseResult] = []

        queries = [query]
        if titles:
            for t in titles:
                if t and t != query:
                    queries.append(t)

        for q in queries:
            try:
                results = await self.search(q, categories=[5070])
                for r in results:
                    if r.guid not in seen_guids:
                        seen_guids.add(r.guid)
                        all_results.append(r)
            except Exception:
                logger.warning("Prowlarr search failed for query: %s", q)

        all_results.sort(key=lambda r: r.seeders, reverse=True)
        return all_results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _parse_result(self, raw: dict[str, Any]) -> ReleaseResult:
        """Convert a raw Prowlarr search result to a ReleaseResult."""
        title = raw.get("title", "")
        download_url = raw.get("downloadUrl", "") or ""
        magnet_url = raw.get("magnetUrl", "") or ""
        is_torrent = bool(
            raw.get("protocol", "torrent").lower() == "torrent"
            or magnet_url
            or download_url.endswith(".torrent")
        )
        return ReleaseResult(
            guid=raw.get("guid", "") or "",
            title=title,
            size=raw.get("size", 0) or 0,
            seeders=raw.get("seeders", 0) or 0,
            leechers=raw.get("leechers", 0) or 0,
            indexer=raw.get("indexer", "") or "",
            indexer_id=raw.get("indexerId", 0) or 0,
            download_url=download_url,
            magnet_url=magnet_url,
            publish_date=raw.get("publishDate", "") or "",
            quality=_parse_quality(title),
            is_torrent=is_torrent,
        )
