"""AniList GraphQL client with OAuth2 support and token-bucket rate limiting."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GRAPHQL_ENDPOINT = "https://graphql.anilist.co"
OAUTH_AUTHORIZE_URL = "https://anilist.co/api/v2/oauth/authorize"
OAUTH_TOKEN_URL = "https://anilist.co/api/v2/oauth/token"

MAX_RETRIES = 3
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
    relations {
      edges {
        relationType
        node {
          id
          title { romaji english }
          type
          format
          status
          episodes
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


class TokenBucket:
    """Proactive rate limiter: 90 capacity, 1.5 tokens/sec refill."""

    def __init__(self, capacity: float = 90.0, refill_rate: float = 1.5) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._capacity,
                self._tokens + elapsed * self._refill_rate,
            )
            self._last_refill = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._refill_rate
                logger.debug("Rate limiter: sleeping %.2fs", wait)
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


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
        self._bucket = TokenBucket()
        self._rate_limit_remaining: int | None = None

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

    async def get_anime_relations(self, anime_id: int) -> list[dict[str, Any]]:
        data = await self._execute_query(GET_ANIME_RELATIONS_QUERY, {"id": anime_id})
        media = data.get("Media")
        if not media:
            return []
        return media.get("relations", {}).get("edges", [])

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
        await self._bucket.acquire()

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        for attempt in range(MAX_RETRIES):
            try:
                resp = await self._http.post(
                    GRAPHQL_ENDPOINT,
                    json={"query": query, "variables": variables},
                    headers=headers,
                )
                self._update_rate_limit(resp.headers)

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    logger.warning("Rate limited. Waiting %ds", retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                if resp.status_code >= 500:
                    wait = BACKOFF_BASE**attempt
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
                if attempt < MAX_RETRIES - 1:
                    wait = BACKOFF_BASE**attempt
                    logger.warning("Transport error: %s, retrying in %.1fs", exc, wait)
                    await asyncio.sleep(wait)
                else:
                    raise

        return {}

    def _update_rate_limit(self, headers: httpx.Headers) -> None:
        remaining = headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            self._rate_limit_remaining = int(remaining)
            if self._rate_limit_remaining < 10:
                logger.warning(
                    "AniList rate limit low: %d remaining",
                    self._rate_limit_remaining,
                )
