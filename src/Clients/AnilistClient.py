"""AniList GraphQL client with OAuth2 support and token-bucket rate limiting."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GRAPHQL_ENDPOINT = "https://graphql.anilist.co"
OAUTH_AUTHORIZE_URL = "https://anilist.co/api/v2/oauth/authorize"
OAUTH_TOKEN_URL = "https://anilist.co/api/v2/oauth/token"

MAX_RETRIES = 3  # retries for 5xx / transport errors only
MAX_RATE_LIMIT_WAITS = 10  # separate budget for 429s (not counted as errors)
BACKOFF_BASE = 2.0

# ---------------------------------------------------------------------------
# GraphQL query / mutation strings
# ---------------------------------------------------------------------------

SEARCH_ANIME_QUERY = """
query ($search: String, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { total currentPage lastPage hasNextPage }
    media(search: $search, type: ANIME) {
      id
      title { romaji english native }
      synonyms
      episodes
      status
      format
      startDate { year month day }
      season
      seasonYear
      coverImage { large medium }
      description
      genres
      averageScore
      studios(isMain: true) { nodes { name } }
    }
  }
}
"""

GET_ANIME_BY_ID_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english native }
    episodes
    status
    format
    startDate { year month day }
    season
    seasonYear
    coverImage { large medium }
    description
    genres
    averageScore
    studios(isMain: true) { nodes { name } }
    staff(sort: RELEVANCE, perPage: 10) {
      edges { role node { name { full } } }
    }
  }
}
"""

GET_ANIME_RELATIONS_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english }
    format
    episodes
    status
    startDate { year month day }
    relations {
      edges {
        relationType(version: 2)
        node {
          id
          title { romaji english }
          type
          format
          status
          episodes
          startDate { year month day }
        }
      }
    }
  }
}
"""

VIEWER_QUERY = """
query {
  Viewer {
    id
    name
    avatar { large medium }
  }
}
"""

USER_ANIME_LIST_QUERY = """
query ($userId: Int) {
  MediaListCollection(userId: $userId, type: ANIME) {
    lists {
      name
      status
      entries {
        id
        mediaId
        status
        progress
        repeat
        media {
          id
          title { romaji english native }
          episodes
        }
      }
    }
  }
}
"""

USER_WATCHLIST_QUERY = """
query ($userId: Int) {
  MediaListCollection(userId: $userId, type: ANIME) {
    lists {
      status
      entries {
        mediaId
        status
        progress
        score
        media {
          id
          title { romaji english native }
          format
          episodes
          status
          startDate { year }
          coverImage { medium }
        }
      }
    }
  }
}
"""

GET_ANIME_LIST_ENTRY_QUERY = """
query ($mediaId: Int, $userId: Int) {
  MediaList(mediaId: $mediaId, userId: $userId) {
    id
    status
    progress
    repeat
    media {
      id
      title { romaji english }
      episodes
    }
  }
}
"""

UPDATE_PROGRESS_MUTATION = """
mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus, $repeat: Int) {
  SaveMediaListEntry(
    mediaId: $mediaId, progress: $progress,
    status: $status, repeat: $repeat
  ) {
    id
    status
    progress
    repeat
  }
}
"""


# ---------------------------------------------------------------------------
# Token bucket rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Header-aware rate limiter for AniList API.

    Reads ``X-RateLimit-Limit``, ``X-RateLimit-Remaining``, and
    ``X-RateLimit-Reset`` from every response to adapt to whatever limit
    AniList is currently enforcing (90/min normally, 30/min when degraded).

    Paces requests with a dynamic gap computed from the actual limit and
    proactively sleeps when the remaining budget is nearly exhausted.
    """

    def __init__(self) -> None:
        self._limit: int = 30  # conservative default until first response
        self._remaining: int = 30
        self._reset_at: float = 0.0  # monotonic time when window resets
        self._last_response: float = 0.0  # monotonic time of last response
        self._lock = asyncio.Lock()

    @property
    def _min_gap(self) -> float:
        """Dynamic gap: 60s / limit with 10% headroom."""
        return 60.0 / max(1, self._limit) * 1.1

    def update_from_headers(self, headers: httpx.Headers) -> None:
        """Feed response headers back into the limiter."""
        raw_limit = headers.get("X-RateLimit-Limit")
        if raw_limit is not None:
            self._limit = int(raw_limit)

        raw_remaining = headers.get("X-RateLimit-Remaining")
        if raw_remaining is not None:
            self._remaining = int(raw_remaining)

        raw_reset = headers.get("X-RateLimit-Reset")
        if raw_reset is not None:
            # AniList sends a UNIX epoch timestamp
            reset_epoch = int(raw_reset)
            # Convert to monotonic-relative for reliable comparison
            wall_now = time.time()
            mono_now = time.monotonic()
            self._reset_at = mono_now + max(0, reset_epoch - wall_now)

        # Record when the last response arrived so ``acquire`` can
        # measure the gap from response→next-request (not request→request).
        self._last_response = time.monotonic()

        logger.debug(
            "Rate headers: limit=%d, remaining=%d, " "reset_in=%.0fs, gap=%.1fs",
            self._limit,
            self._remaining,
            max(0, self._reset_at - self._last_response),
            self._min_gap,
        )

    async def acquire(self) -> None:
        """Wait if necessary before making a request."""
        async with self._lock:
            now = time.monotonic()

            # 1. If near-exhausted, wait for the window to reset
            if self._remaining <= 3 and self._reset_at > now:
                wait = self._reset_at - now
                logger.info(
                    "Rate limiter: %d/%d remaining, waiting %.1fs for reset",
                    self._remaining,
                    self._limit,
                    wait,
                )
                await asyncio.sleep(wait)
                now = time.monotonic()

            # 2. Enforce minimum gap since the last response came back.
            #    This prevents bursting even when network round-trips are fast.
            gap = self._min_gap
            since_last = now - self._last_response
            if since_last < gap:
                await asyncio.sleep(gap - since_last)


# ---------------------------------------------------------------------------
# AniList Client
# ---------------------------------------------------------------------------


class AniListClient:
    """Async GraphQL client for AniList API with OAuth2 and rate limiting."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str = "",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = httpx.AsyncClient(timeout=30.0)
        self._limiter = RateLimiter()
        self.on_rate_limit_wait: Callable[[int], None] | None = None

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # OAuth2
    # ------------------------------------------------------------------

    def get_authorize_url(self, redirect_uri: str | None = None) -> str:
        uri = redirect_uri or self._redirect_uri
        return (
            f"{OAUTH_AUTHORIZE_URL}"
            f"?client_id={self._client_id}"
            f"&redirect_uri={uri}"
            f"&response_type=code"
        )

    async def exchange_code_for_token(
        self, auth_code: str, redirect_uri: str | None = None
    ) -> dict[str, Any]:
        uri = redirect_uri or self._redirect_uri
        resp = await self._http.post(
            OAUTH_TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": uri,
                "code": auth_code,
            },
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Public queries (no auth)
    # ------------------------------------------------------------------

    async def search_anime(
        self, query: str, page: int = 1, per_page: int = 10
    ) -> list[dict[str, Any]]:
        data = await self._execute_query(
            SEARCH_ANIME_QUERY,
            {"search": query, "page": page, "perPage": per_page},
        )
        return data.get("Page", {}).get("media", [])

    async def get_anime_by_id(self, anime_id: int) -> dict[str, Any] | None:
        data = await self._execute_query(GET_ANIME_BY_ID_QUERY, {"id": anime_id})
        return data.get("Media")

    async def get_anime_relations(
        self, anime_id: int
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Fetch an anime's own data and its relation edges.

        Returns ``(root_media_data, relation_edges)`` where
        ``root_media_data`` contains the queried entry's own fields
        (id, title, format, episodes, startDate, status).
        """
        data = await self._execute_query(GET_ANIME_RELATIONS_QUERY, {"id": anime_id})
        media = data.get("Media")
        if not media:
            return None, []
        edges = media.get("relations", {}).get("edges", [])
        # Build root data dict (everything except the relations sub-object)
        root_data = {k: v for k, v in media.items() if k != "relations"}
        return root_data, edges

    # ------------------------------------------------------------------
    # Authenticated queries
    # ------------------------------------------------------------------

    async def get_viewer(self, access_token: str) -> dict[str, Any]:
        data = await self._execute_query(VIEWER_QUERY, {}, access_token)
        return data.get("Viewer", {})

    async def get_user_anime_list(
        self, access_token: str, user_id: int
    ) -> list[dict[str, Any]]:
        data = await self._execute_query(
            USER_ANIME_LIST_QUERY, {"userId": user_id}, access_token
        )
        collection = data.get("MediaListCollection", {})
        return collection.get("lists", [])

    async def get_user_watchlist(
        self, anilist_user_id: int, access_token: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch the user's full anime list and return a flat list of entries.

        Each entry has shape:
        {anilist_id, list_status, progress, score, title, format,
         episodes, airing_status, start_year, cover_image}
        """
        data = await self._execute_query(
            USER_WATCHLIST_QUERY,
            {"userId": anilist_user_id},
            access_token,
        )
        collection = data.get("MediaListCollection", {})
        lists = collection.get("lists", [])

        flat: list[dict[str, Any]] = []
        for lst in lists:
            for entry in lst.get("entries", []):
                media = entry.get("media") or {}
                title_obj = media.get("title") or {}
                start_date = media.get("startDate") or {}
                flat.append(
                    {
                        "anilist_id": media.get("id") or entry.get("mediaId", 0),
                        "list_status": entry.get("status", ""),
                        "progress": entry.get("progress", 0),
                        "score": entry.get("score", 0.0),
                        "title": (
                            title_obj.get("romaji")
                            or title_obj.get("english")
                            or title_obj.get("native")
                            or ""
                        ),
                        "format": media.get("format", ""),
                        "episodes": media.get("episodes"),
                        "airing_status": media.get("status", ""),
                        "start_year": start_date.get("year"),
                        "cover_image": (media.get("coverImage") or {}).get(
                            "medium", ""
                        ),
                    }
                )
        return flat

    async def get_anime_list_entry(
        self, anime_id: int, access_token: str, user_id: int
    ) -> dict[str, Any] | None:
        try:
            data = await self._execute_query(
                GET_ANIME_LIST_ENTRY_QUERY,
                {"mediaId": anime_id, "userId": user_id},
                access_token,
            )
            return data.get("MediaList")
        except httpx.HTTPStatusError:
            return None

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def update_anime_progress(
        self,
        anime_id: int,
        access_token: str,
        progress: int,
        status: str = "CURRENT",
        repeat: int | None = None,
    ) -> dict[str, Any]:
        variables: dict[str, Any] = {
            "mediaId": anime_id,
            "progress": progress,
            "status": status,
        }
        if repeat is not None:
            variables["repeat"] = repeat
        data = await self._execute_query(
            UPDATE_PROGRESS_MUTATION, variables, access_token
        )
        return data.get("SaveMediaListEntry", {})

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_query(
        self,
        query: str,
        variables: dict[str, Any],
        access_token: str | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        error_retries = 0
        rate_limit_waits = 0

        while True:
            await self._limiter.acquire()

            try:
                resp = await self._http.post(
                    GRAPHQL_ENDPOINT,
                    json={"query": query, "variables": variables},
                    headers=headers,
                )
                self._limiter.update_from_headers(resp.headers)

                if resp.status_code == 429:
                    rate_limit_waits += 1
                    if rate_limit_waits > MAX_RATE_LIMIT_WAITS:
                        logger.error(
                            "Exceeded %d rate-limit waits, giving up",
                            MAX_RATE_LIMIT_WAITS,
                        )
                        return {}

                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    retry_after = max(retry_after, 5)
                    logger.warning(
                        "Rate limited (wait %d/%d). Sleeping %ds. "
                        "Headers: remaining=%s, limit=%s, reset=%s",
                        rate_limit_waits,
                        MAX_RATE_LIMIT_WAITS,
                        retry_after,
                        resp.headers.get("X-RateLimit-Remaining", "?"),
                        resp.headers.get("X-RateLimit-Limit", "?"),
                        resp.headers.get("X-RateLimit-Reset", "?"),
                    )
                    if self.on_rate_limit_wait:
                        self.on_rate_limit_wait(retry_after)
                    await asyncio.sleep(retry_after)
                    # After sleeping, assume the window has refreshed so
                    # acquire() doesn't double-wait.
                    self._limiter._remaining = self._limiter._limit
                    self._limiter._reset_at = 0.0
                    self._limiter._last_response = time.monotonic()
                    continue

                if resp.status_code >= 500:
                    error_retries += 1
                    if error_retries > MAX_RETRIES:
                        logger.error("Exceeded %d server-error retries", MAX_RETRIES)
                        return {}
                    wait = BACKOFF_BASE**error_retries
                    logger.warning(
                        "Server error %d, retrying in %.1fs",
                        resp.status_code,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                body = resp.json()

                if "errors" in body and body["errors"]:
                    logger.error("GraphQL errors: %s", body["errors"])

                return body.get("data", {})

            except httpx.TransportError as exc:
                error_retries += 1
                if error_retries > MAX_RETRIES:
                    raise
                wait = BACKOFF_BASE**error_retries
                logger.warning("Transport error: %s, retrying in %.1fs", exc, wait)
                await asyncio.sleep(wait)

    @property
    def rate_limit_remaining(self) -> int:
        """Current remaining requests in the AniList rate limit window."""
        return self._limiter._remaining
