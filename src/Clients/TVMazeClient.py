"""TVMaze API client for resolving IMDB and TVDB IDs by show title.

TVMaze is a free, unauthenticated API used solely to obtain external provider
IDs (IMDB, TVDB) for a matched series.  These IDs are written into tvshow.nfo
so Jellyfin's TMDB and OMDB providers can still resolve per-episode metadata
even after our file restructure renames folders away from names those providers
would recognise on their own.

No API key or account required.  Rate limit is generous (20 req/10s) and we
only call this on a cache miss — subsequent runs read from anilist_cache.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

_TVMAZE_SEARCH = "https://api.tvmaze.com/search/shows"

# Minimum rapidfuzz token_sort_ratio score to accept the top TVMaze result.
# Keeps clearly wrong matches (different show, similar words) from being stored.
_MIN_CONFIDENCE = 60


class TVMazeClient:
    """Thin client for TVMaze show search."""

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(timeout=10.0)
        self._owns_http = http is None

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "TVMazeClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def search_show(self, title: str) -> dict[str, str | None] | None:
        """Search TVMaze for *title* and return external IDs if confident.

        Returns a dict with keys ``imdb_id`` and ``tvdb_id`` (either may be
        ``None`` if TVMaze doesn't have that ID for the matched show), or
        ``None`` if no result exceeded the confidence threshold.

        The top result's name is fuzzy-matched against *title* using
        token_sort_ratio so minor formatting differences (punctuation, articles,
        subtitle separators) don't cause a miss, while clearly wrong matches
        are rejected.
        """
        if not title or not title.strip():
            return None

        try:
            resp = await self._http.get(_TVMAZE_SEARCH, params={"q": title.strip()})
            resp.raise_for_status()
            results: list[dict[str, Any]] = resp.json()
        except Exception as exc:
            logger.debug("TVMaze search failed for '%s': %s", title, exc)
            return None

        if not results:
            logger.debug("TVMaze: no results for '%s'", title)
            return None

        top = results[0]
        show = top.get("show") or {}
        show_name: str = show.get("name") or ""

        score = fuzz.token_sort_ratio(title.lower(), show_name.lower())
        logger.debug(
            "TVMaze top result for '%s': '%s' (score=%d)",
            title,
            show_name,
            score,
        )

        if score < _MIN_CONFIDENCE:
            logger.debug(
                "TVMaze: rejected '%s' for query '%s' (score %d < %d)",
                show_name,
                title,
                score,
                _MIN_CONFIDENCE,
            )
            return None

        externals: dict[str, Any] = show.get("externals") or {}
        imdb_id: str | None = externals.get("imdb") or None
        tvdb_raw = externals.get("thetvdb")
        tvdb_id: str | None = str(tvdb_raw) if tvdb_raw is not None else None
        tvmaze_raw = show.get("id")
        tvmaze_id: str | None = str(tvmaze_raw) if tvmaze_raw is not None else None

        logger.info(
            "TVMaze matched '%s' → '%s' (score=%d) imdb=%s tvdb=%s tvmaze=%s",
            title,
            show_name,
            score,
            imdb_id,
            tvdb_id,
            tvmaze_id,
        )
        return {"imdb_id": imdb_id, "tvdb_id": tvdb_id, "tvmaze_id": tvmaze_id}
