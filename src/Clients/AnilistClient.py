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
SCAN_RESERVE_TOKENS = 3  # tokens reserved for auth/high-priority calls

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
    title { romaji english native }
    format
    episodes
    status
    seasonYear
    startDate { year month day }
    coverImage { large }
    description(asHtml: false)
    genres
    relations {
      edges {
        relationType(version: 2)
        node {
          id
          title { romaji english native }
          type
          format
          status
          episodes
          seasonYear
          startDate { year month day }
          coverImage { large }
          description(asHtml: false)
          genres
        }
      }
    }
  }
}
"""

GET_ANIME_EXTERNAL_LINKS_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english native }
    format
    episodes
    externalLinks {
      id
      externalId
      site
      url
      type
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
# Token-bucket rate limiter (adapted from original Phase 1 implementation)
# ---------------------------------------------------------------------------


class RateLimiter:
    """Token-bucket rate limiter for the AniList GraphQL API.

    Uses a classic token bucket that naturally refills over time.
    Adapts capacity/refill to whatever limit AniList reports via
    ``X-RateLimit-Limit`` (90/min normally, 30/min when degraded).

    This approach is self-healing: it doesn't depend on window-reset
    headers, which are unreliable during AniList degraded mode.

    Scan (low-priority) calls pause when only a few tokens remain,
    leaving headroom for auth/OAuth (high-priority) calls.
    """

    def __init__(self, capacity: float = 90.0) -> None:
        self._limit: int = int(capacity)
        self._capacity: float = capacity
        self._tokens: float = capacity
        self._refill_rate: float = capacity / 60.0  # tokens per second
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()
        # Exposed for external reads (e.g. rate_limit_remaining property)
        self._remaining: int = int(capacity)

    def update_from_headers(self, headers: httpx.Headers) -> None:
        """Adapt capacity/refill when AniList changes its rate limit."""
        raw_limit = headers.get("X-RateLimit-Limit")
        if raw_limit is not None:
            new_limit = int(raw_limit)
            if new_limit != self._limit:
                logger.info(
                    "AniList rate limit changed: %d -> %d", self._limit, new_limit
                )
                self._limit = new_limit
                self._capacity = float(new_limit)
                self._refill_rate = new_limit / 60.0
                self._tokens = min(self._tokens, self._capacity)

        raw_remaining = headers.get("X-RateLimit-Remaining")
        if raw_remaining is not None:
            self._remaining = int(raw_remaining)

        logger.debug(
            "Rate headers: limit=%d, remaining=%d, tokens=%.1f, refill=%.2f/s",
            self._limit,
            self._remaining,
            self._tokens,
            self._refill_rate,
        )

    async def acquire(self, high_priority: bool = False) -> None:
        """Wait if necessary before making a request.

        high_priority=True  — auth/OAuth calls: only need 1 token.
        high_priority=False — scan calls: need reserve+1 tokens so that
                               a few tokens remain for high-priority calls.
        """
        min_tokens = 1.0 if high_priority else float(SCAN_RESERVE_TOKENS + 1)

        async with self._lock:
            self._refill()

            if self._tokens < min_tokens:
                deficit = min_tokens - self._tokens
                wait = deficit / self._refill_rate
                logger.info(
                    "Rate limiter: %s waiting %.1fs "
                    "(tokens=%.1f, need=%.0f, refill=%.2f/s)",
                    "auth" if high_priority else "scan",
                    wait,
                    self._tokens,
                    min_tokens,
                    self._refill_rate,
                )
                await asyncio.sleep(wait)
                self._refill()

            self._tokens -= 1.0

    def _refill(self) -> None:
        """Add tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._capacity,
            self._tokens + elapsed * self._refill_rate,
        )
        self._last_refill = now


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
        self._http = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": "AnilistLink/1.0 (https://github.com/Mprice12337/Anilist-Link)",
                "Accept": "application/json",
            },
        )
        self._limiter = RateLimiter()
        self.on_rate_limit_wait: Callable[[int], None] | None = None

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # OAuth2
    # ------------------------------------------------------------------

    def get_authorize_url(
        self,
        redirect_uri: str | None = None,
        client_id: str | None = None,
    ) -> str:
        uri = redirect_uri or self._redirect_uri
        cid = client_id or self._client_id
        return (
            f"{OAUTH_AUTHORIZE_URL}"
            f"?client_id={cid}"
            f"&redirect_uri={uri}"
            f"&response_type=code"
        )

    async def exchange_code_for_token(
        self,
        auth_code: str,
        redirect_uri: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> dict[str, Any]:
        uri = redirect_uri or self._redirect_uri
        payload = {
            "grant_type": "authorization_code",
            "client_id": client_id or self._client_id,
            "client_secret": client_secret or self._client_secret,
            "redirect_uri": uri,
            "code": auth_code,
        }
        for attempt in range(4):
            resp = await self._http.post(OAUTH_TOKEN_URL, json=payload)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "10")) + 2
                logger.warning(
                    "OAuth token exchange rate-limited; waiting %ds (attempt %d)",
                    wait,
                    attempt + 1,
                )
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 400:
                body_snippet = resp.text[:300] if resp.text else "(empty)"
                logger.error(
                    "OAuth token exchange HTTP %d. Body: %s",
                    resp.status_code,
                    body_snippet,
                )
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(
            "AniList OAuth token exchange failed after retries (rate limited)"
        )

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

    async def get_anime_external_links(self, anime_id: int) -> dict[str, Any] | None:
        """Fetch external links (TVDB, TMDB, etc.) for a given AniList entry.

        Returns the Media object with ``externalLinks`` populated, or None.
        """
        data = await self._execute_query(
            GET_ANIME_EXTERNAL_LINKS_QUERY, {"id": anime_id}
        )
        return data.get("Media")

    def extract_tvdb_id(self, external_links: list[dict[str, Any]]) -> int | None:
        """Extract the TVDB series ID from AniList externalLinks.

        Checks ``externalId`` first (AniList's stored numeric ID for the site),
        then falls back to URL parsing. TVDB URLs are slug-based so URL parsing
        rarely succeeds; the Sonarr title-search fallback in DownloadManager
        handles that case.
        """
        for link in external_links:
            if link.get("site", "").lower() == "thetvdb":
                ext_id = link.get("externalId")
                if ext_id:
                    try:
                        return int(ext_id)
                    except (ValueError, TypeError):
                        pass
                # Fallback: parse numeric segment from URL
                url = link.get("url", "")
                for part in reversed(url.rstrip("/").split("/")):
                    if part.isdigit():
                        return int(part)
        return None

    def extract_tmdb_id(self, external_links: list[dict[str, Any]]) -> int | None:
        """Extract the TMDB ID from AniList externalLinks.

        Checks ``externalId`` first, then parses the URL (TMDB URLs are numeric
        but may carry a slug suffix like ``/12345-show-name``).
        """
        for link in external_links:
            site = link.get("site", "").lower()
            if site in ("themoviedb", "tmdb"):
                ext_id = link.get("externalId")
                if ext_id:
                    try:
                        return int(ext_id)
                    except (ValueError, TypeError):
                        pass
                # Fallback: TMDB URL may be /movie/12345 or /movie/12345-slug
                url = link.get("url", "")
                for part in reversed(url.rstrip("/").split("/")):
                    numeric = part.split("-")[0] if "-" in part else part
                    if numeric.isdigit():
                        return int(numeric)
        return None

    # ------------------------------------------------------------------
    # Authenticated queries
    # ------------------------------------------------------------------

    async def get_viewer(self, access_token: str) -> dict[str, Any]:
        data = await self._execute_query(
            VIEWER_QUERY, {}, access_token, high_priority=True
        )
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
            high_priority=True,
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
                high_priority=True,
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
            UPDATE_PROGRESS_MUTATION, variables, access_token, high_priority=True
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
        high_priority: bool = False,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        error_retries = 0
        rate_limit_waits = 0

        while True:
            await self._limiter.acquire(high_priority=high_priority)

            try:
                resp = await self._http.post(
                    GRAPHQL_ENDPOINT,
                    json={"query": query, "variables": variables},
                    headers=headers,
                )
                self._limiter.update_from_headers(resp.headers)

                if resp.status_code == 403:
                    error_retries += 1
                    body_snippet = resp.text[:300] if resp.text else "(empty)"
                    if error_retries > MAX_RETRIES:
                        logger.error(
                            "AniList 403 Forbidden — exceeded %d retries. " "Body: %s",
                            MAX_RETRIES,
                            body_snippet,
                        )
                        resp.raise_for_status()
                    wait = BACKOFF_BASE**error_retries
                    logger.warning(
                        "AniList 403 (attempt %d/%d), retrying in %.1fs. " "Body: %s",
                        error_retries,
                        MAX_RETRIES,
                        wait,
                        body_snippet,
                    )
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code == 429:
                    rate_limit_waits += 1
                    body_snippet = resp.text[:300] if resp.text else "(empty)"
                    if rate_limit_waits > MAX_RATE_LIMIT_WAITS:
                        logger.error(
                            "Exceeded %d rate-limit waits, giving up. " "Body: %s",
                            MAX_RATE_LIMIT_WAITS,
                            body_snippet,
                        )
                        return {}

                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    retry_after = max(retry_after, 5)
                    logger.warning(
                        "Rate limited (wait %d/%d). Sleeping %ds. "
                        "Headers: remaining=%s, limit=%s, reset=%s. "
                        "Body: %s",
                        rate_limit_waits,
                        MAX_RATE_LIMIT_WAITS,
                        retry_after,
                        resp.headers.get("X-RateLimit-Remaining", "?"),
                        resp.headers.get("X-RateLimit-Limit", "?"),
                        resp.headers.get("X-RateLimit-Reset", "?"),
                        body_snippet,
                    )
                    if self.on_rate_limit_wait:
                        self.on_rate_limit_wait(retry_after)
                    await asyncio.sleep(retry_after)
                    # After sleeping, restore token bucket so acquire()
                    # doesn't double-wait.
                    self._limiter._tokens = self._limiter._capacity
                    self._limiter._last_refill = time.monotonic()
                    continue

                if resp.status_code >= 500:
                    error_retries += 1
                    body_snippet = resp.text[:300] if resp.text else "(empty)"
                    if error_retries > MAX_RETRIES:
                        logger.error(
                            "Exceeded %d server-error retries (last %d). " "Body: %s",
                            MAX_RETRIES,
                            resp.status_code,
                            body_snippet,
                        )
                        return {}
                    wait = BACKOFF_BASE**error_retries
                    logger.warning(
                        "Server error %d, retrying in %.1fs. Body: %s",
                        resp.status_code,
                        wait,
                        body_snippet,
                    )
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 400:
                    body_snippet = resp.text[:300] if resp.text else "(empty)"
                    logger.error(
                        "AniList HTTP %d for query. Body: %s",
                        resp.status_code,
                        body_snippet,
                    )
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
