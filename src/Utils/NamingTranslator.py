"""Translation utilities for mapping AniList data to external service identifiers.

Handles TVDB/TMDB ID resolution for Sonarr/Radarr integration using AniList
external links and title-based fallback searches.
"""

from __future__ import annotations

import logging
from typing import Any

from rapidfuzz import fuzz as _fuzz

from src.Clients.AnilistClient import AniListClient

logger = logging.getLogger(__name__)

# AniList external link site names (as returned by the API)
_TVDB_SITE_NAMES = {"The TVDB", "TheTVDB"}
_TMDB_SITE_NAMES = {"The Movie Database", "TMDB"}
_IMDB_SITE_NAMES = {"Internet Movie Database", "IMDb"}

# Max number of Sonarr lookups per title-chain resolve attempt
_SEARCH_TITLE_LIMIT = 4


GET_EXTERNAL_LINKS_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english native }
    format
    externalLinks {
      site
      url
      siteId
    }
  }
}
"""

GET_FULL_MEDIA_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english native }
    synonyms
    format
    externalLinks {
      site
      url
      siteId
    }
  }
}
"""

GET_RELATIONS_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    relations {
      edges {
        relationType(version: 2)
        node {
          id
          type
        }
      }
    }
  }
}
"""

GET_RELATIONS_AND_LINKS_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    externalLinks {
      site
      siteId
      url
    }
    relations {
      edges {
        relationType(version: 2)
        node {
          id
          type
        }
      }
    }
  }
}
"""


async def get_sequel_prequel_ids(
    anilist_id: int, anilist_client: AniListClient
) -> list[int]:
    """Return AniList IDs of direct SEQUEL and PREQUEL anime relations.

    Used to find sibling entries that may map to the same Sonarr series
    (e.g. split-cour anime where both parts share a single TVDB entry).
    """
    try:
        data = await anilist_client._execute_query(
            GET_RELATIONS_QUERY, {"id": anilist_id}
        )
        ids: list[int] = []
        for edge in data.get("Media", {}).get("relations", {}).get("edges", []):
            if edge.get("relationType") in ("SEQUEL", "PREQUEL"):
                node = edge.get("node", {})
                if node.get("type") == "ANIME" and node.get("id"):
                    ids.append(int(node["id"]))
        return ids
    except Exception:
        logger.warning("Failed to fetch relations for anilist_id=%d", anilist_id)
        return []


async def fetch_relations_and_tvdb(
    anilist_id: int, anilist_client: AniListClient
) -> tuple[list[tuple[str, int]], int | None]:
    """Fetch SEQUEL/PREQUEL relations and TVDB ID in a single API call.

    Returns ([(relationType, related_anilist_id), ...], tvdb_id_or_None).
    """
    try:
        data = await anilist_client._execute_query(
            GET_RELATIONS_AND_LINKS_QUERY, {"id": anilist_id}
        )
        media = data.get("Media", {})
    except Exception:
        logger.warning("Failed to fetch relations+links for anilist_id=%d", anilist_id)
        return [], None

    # Extract TVDB ID
    tvdb_id: int | None = None
    for link in media.get("externalLinks", []):
        if link.get("site") in _TVDB_SITE_NAMES:
            site_id = link.get("siteId")
            if site_id and str(site_id).isdigit():
                tvdb_id = int(site_id)
                break
            url = link.get("url", "")
            if "id=" in url:
                try:
                    part = url.split("id=")[1].split("&")[0]
                    if part.isdigit():
                        tvdb_id = int(part)
                        break
                except (IndexError, ValueError):
                    pass

    # Extract SEQUEL/PREQUEL relations
    relations: list[tuple[str, int]] = []
    for edge in media.get("relations", {}).get("edges", []):
        rel_type = edge.get("relationType")
        node = edge.get("node", {})
        if (
            rel_type in ("SEQUEL", "PREQUEL")
            and node.get("type") == "ANIME"
            and node.get("id")
        ):
            relations.append((rel_type, int(node["id"])))

    return relations, tvdb_id


async def resolve_tvdb_via_prequel_chain(
    anilist_id: int, anilist_client: AniListClient, max_depth: int = 10
) -> tuple[int | None, int | None]:
    """Walk PREQUEL relations to find a root entry with a TVDB ID.

    Returns (tvdb_id, root_anilist_id) if found, else (None, None).
    Useful for sequels where only the first season has a TVDB link.
    """
    current = anilist_id
    visited: set[int] = {current}

    for _ in range(max_depth):
        relations, tvdb_id = await fetch_relations_and_tvdb(current, anilist_client)
        if tvdb_id:
            logger.info(
                "Resolved TVDB ID %d for anilist_id=%d via prequel chain (at %d)",
                tvdb_id,
                anilist_id,
                current,
            )
            return tvdb_id, current

        # Follow the PREQUEL edge (go backwards in time)
        prequel_id = None
        for rel_type, related_id in relations:
            if rel_type == "PREQUEL" and related_id not in visited:
                prequel_id = related_id
                break
        if not prequel_id:
            break
        visited.add(prequel_id)
        current = prequel_id

    return None, None


async def collect_series_chain(
    start_anilist_id: int,
    tvdb_id: int,
    anilist_client: AniListClient,
    max_entries: int = 20,
) -> list[int]:
    """BFS-traverse SEQUEL/PREQUEL relations, collecting all entries sharing tvdb_id.

    Returns the chain in chronological order via Kahn's topological sort on the
    directed sequel graph (SEQUEL A→B means A airs before B).

    The start entry is always included. All related entries are only included if
    their resolved TVDB ID also matches tvdb_id (same Sonarr series).
    """
    from collections import defaultdict

    # Cache: anilist_id -> (relations, tvdb_id)
    cache: dict[int, tuple[list[tuple[str, int]], int | None]] = {}

    async def _fetch(aid: int) -> tuple[list[tuple[str, int]], int | None]:
        if aid not in cache:
            cache[aid] = await fetch_relations_and_tvdb(aid, anilist_client)
        return cache[aid]

    visited: set[int] = {start_anilist_id}
    queue: list[int] = [start_anilist_id]
    # Directed edges: (A, B) means A airs before B
    edges: list[tuple[int, int]] = []

    while queue and len(visited) < max_entries:
        current = queue.pop(0)
        relations, _ = await _fetch(current)

        for rel_type, related_id in relations:
            # Record directed edge for later topological sort
            if rel_type == "SEQUEL":
                edges.append((current, related_id))
            else:  # PREQUEL: related is before current
                edges.append((related_id, current))

            if related_id in visited:
                continue

            # Include if TVDB matches OR if the entry has no TVDB link at all.
            # Many sequels/prequels on AniList don't repeat the TVDB link —
            # only the first season typically has it. A missing link is treated
            # as "same series", while a *different* TVDB ID definitively
            # excludes the entry (e.g. a spin-off with its own TVDB entry).
            _, related_tvdb = await _fetch(related_id)
            if related_tvdb == tvdb_id or related_tvdb is None:
                visited.add(related_id)
                queue.append(related_id)

    chain_set = visited

    # Kahn's topological sort (only over chain members)
    in_degree: dict[int, int] = {n: 0 for n in chain_set}
    adj: dict[int, list[int]] = defaultdict(list)
    for a, b in edges:
        if a in chain_set and b in chain_set:
            adj[a].append(b)
            in_degree[b] += 1

    topo_queue: list[int] = sorted(n for n in chain_set if in_degree[n] == 0)
    result: list[int] = []
    while topo_queue:
        node = topo_queue.pop(0)
        result.append(node)
        for neighbor in sorted(adj[node]):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                topo_queue.append(neighbor)
                topo_queue.sort()

    # Append any remaining nodes (cycle fallback — shouldn't occur in practice)
    remaining = sorted(n for n in chain_set if n not in result)
    result.extend(remaining)

    logger.debug(
        "collect_series_chain: tvdb_id=%d start=%d chain=%s",
        tvdb_id,
        start_anilist_id,
        result,
    )
    return result


async def resolve_tvdb_id(anilist_id: int, anilist_client: AniListClient) -> int | None:
    """Attempt to resolve a TVDB ID from AniList external links.

    Uses the ``siteId`` field (the actual TVDB series ID) as primary source,
    then falls back to parsing legacy ``?id=`` URL query parameters.
    Returns the TVDB numeric ID if found, else None.
    """
    try:
        data = await anilist_client._execute_query(
            GET_EXTERNAL_LINKS_QUERY, {"id": anilist_id}
        )
        media = data.get("Media", {})
        for link in media.get("externalLinks", []):
            if link.get("site") in _TVDB_SITE_NAMES:
                # siteId is the authoritative TVDB numeric series ID
                site_id = link.get("siteId")
                if site_id and str(site_id).isdigit():
                    logger.debug(
                        "Resolved TVDB ID %s for anilist_id=%d via siteId",
                        site_id,
                        anilist_id,
                    )
                    return int(site_id)
                # Legacy fallback: parse ?id= from URL
                url = link.get("url", "")
                if "id=" in url:
                    try:
                        part = url.split("id=")[1].split("&")[0]
                        if part.isdigit():
                            return int(part)
                    except (IndexError, ValueError):
                        pass
        logger.debug("No TVDB external link found for anilist_id=%d", anilist_id)
        return None
    except Exception:
        logger.warning("Failed to resolve TVDB ID for anilist_id=%d", anilist_id)
        return None


async def resolve_tmdb_id(anilist_id: int, anilist_client: AniListClient) -> int | None:
    """Attempt to resolve a TMDB ID from AniList external links.

    Uses the ``siteId`` field as primary source, then falls back to URL parsing.
    Returns the TMDB numeric ID if found, else None.
    """
    try:
        data = await anilist_client._execute_query(
            GET_EXTERNAL_LINKS_QUERY, {"id": anilist_id}
        )
        media = data.get("Media", {})
        for link in media.get("externalLinks", []):
            if link.get("site") in _TMDB_SITE_NAMES:
                site_id = link.get("siteId")
                if site_id and str(site_id).isdigit():
                    logger.debug(
                        "Resolved TMDB ID %s for anilist_id=%d via siteId",
                        site_id,
                        anilist_id,
                    )
                    return int(site_id)
                url = link.get("url", "")
                if "/movie/" in url or "/tv/" in url:
                    try:
                        part = url.rstrip("/").split("/")[-1]
                        if part.isdigit():
                            return int(part)
                    except (IndexError, ValueError):
                        pass
        logger.debug("No TMDB external link found for anilist_id=%d", anilist_id)
        return None
    except Exception:
        logger.warning("Failed to resolve TMDB ID for anilist_id=%d", anilist_id)
        return None


def get_preferred_title(media: dict[str, Any]) -> str:
    """Return the best display title from an AniList media object."""
    title = media.get("title", {})
    return (
        title.get("english") or title.get("romaji") or title.get("native") or "Unknown"
    )


def get_all_titles(media: dict[str, Any]) -> list[str]:
    """Return all non-empty titles and synonyms for a media entry."""
    title = media.get("title", {})
    titles: list[str] = []
    for key in ("english", "romaji", "native"):
        val = title.get(key)
        if val:
            titles.append(val)
    for syn in media.get("synonyms", []):
        if syn and syn not in titles:
            titles.append(syn)
    return titles


def is_movie_format(anilist_format: str) -> bool:
    """Return True if the AniList format should be sent to Radarr rather than Sonarr."""
    return anilist_format in ("MOVIE",)


def _is_ascii_dominant(text: str, threshold: float = 0.7) -> bool:
    """Return True if >= threshold fraction of characters are ASCII."""
    if not text:
        return False
    return sum(1 for c in text if ord(c) < 128) / len(text) >= threshold


def build_title_chain(media: dict[str, Any]) -> list[str]:
    """Return deduplicated title variants in search-priority order.

    Priority: english → ASCII-dominant synonyms → romaji
              → non-ASCII synonyms → native
    """
    title_obj = media.get("title", {}) or {}
    synonyms: list[str] = [s for s in (media.get("synonyms") or []) if s]

    seen: set[str] = set()
    chain: list[str] = []

    def _add(t: str | None) -> None:
        if t and t not in seen:
            seen.add(t)
            chain.append(t)

    _add(title_obj.get("english"))
    for s in synonyms:
        if _is_ascii_dominant(s):
            _add(s)
    _add(title_obj.get("romaji"))
    for s in synonyms:
        if not _is_ascii_dominant(s):
            _add(s)
    _add(title_obj.get("native"))

    return chain


def _score_candidate(sonarr_title: str, known_titles: list[str]) -> float:
    """Return the best WRatio score of sonarr_title against all known titles."""
    if not known_titles:
        return 0.0
    return max(_fuzz.WRatio(sonarr_title, t) for t in known_titles)


async def resolve_tvdb_via_title_chain(
    anilist_id: int,
    anilist_client: AniListClient,
    sonarr_client: Any,
    confidence_threshold: float = 90.0,
) -> tuple[int | None, list[dict[str, Any]]]:
    """Search Sonarr with a prioritized title chain to resolve a TVDB ID.

    Works through: english → ASCII synonyms → romaji → other synonyms → native.
    Scores each Sonarr result against all known titles using WRatio.

    Returns:
        (tvdb_id, candidates) — tvdb_id is set only if best score >= threshold.
        candidates is sorted by score descending (for the disambiguation modal).
    """
    try:
        data = await anilist_client._execute_query(
            GET_FULL_MEDIA_QUERY, {"id": anilist_id}
        )
        media = data.get("Media", {})
    except Exception:
        logger.warning(
            "Failed to fetch AniList media for title chain, anilist_id=%d", anilist_id
        )
        return None, []

    title_chain = build_title_chain(media)
    if not title_chain:
        return None, []

    # For sequel series, also include root (S1) entry's title variants.
    # TVDB typically registers multi-season shows under the S1 name.
    all_titles = list(title_chain)  # scoring pool (includes S1 titles)
    try:
        relations, _ = await fetch_relations_and_tvdb(anilist_id, anilist_client)
        has_prequel = any(rt == "PREQUEL" for rt, _ in relations)
        if has_prequel:
            # Walk to the root entry and get its titles
            root_id = anilist_id
            visited: set[int] = {root_id}
            for _ in range(10):
                rels, _ = await fetch_relations_and_tvdb(root_id, anilist_client)
                prequel = next(
                    (rid for rt, rid in rels if rt == "PREQUEL" and rid not in visited),
                    None,
                )
                if not prequel:
                    break
                visited.add(prequel)
                root_id = prequel
            if root_id != anilist_id:
                root_data = await anilist_client._execute_query(
                    GET_FULL_MEDIA_QUERY, {"id": root_id}
                )
                root_media = root_data.get("Media", {})
                root_titles = build_title_chain(root_media)
                # Add S1 titles to the search chain (high priority, after own english)
                seen = set(title_chain)
                for rt in root_titles:
                    if rt not in seen:
                        title_chain.append(rt)
                        all_titles.append(rt)
                        seen.add(rt)
                logger.info(
                    "Added %d root titles from anilist_id=%d for sequel search",
                    len(root_titles),
                    root_id,
                )
    except Exception as exc:
        logger.debug("Failed to fetch root titles for sequel: %s", exc)

    seen_tvdb: set[int] = set()
    scored: list[tuple[float, dict[str, Any]]] = []

    for search_title in title_chain[:_SEARCH_TITLE_LIMIT]:
        try:
            results = await sonarr_client.lookup_series(search_title)
        except Exception as exc:
            logger.debug("Sonarr lookup failed for %r: %s", search_title, exc)
            continue

        for r in results:
            tvdb_id = r.get("tvdbId")
            if not tvdb_id or tvdb_id in seen_tvdb:
                continue
            seen_tvdb.add(tvdb_id)

            score = _score_candidate(r.get("title", ""), all_titles)
            poster = r.get("remotePoster") or (
                r["images"][0].get("remoteUrl", "") if r.get("images") else ""
            )
            scored.append(
                (
                    score,
                    {
                        "tvdb_id": tvdb_id,
                        "title": r.get("title", ""),
                        "year": r.get("year"),
                        "status": r.get("status", ""),
                        "overview": (r.get("overview") or "")[:200],
                        "remote_poster": poster,
                        "score": round(score, 1),
                    },
                )
            )

        # Short-circuit once we have a confident match
        if scored and max(s for s, _ in scored) >= confidence_threshold:
            break

    scored.sort(key=lambda x: x[0], reverse=True)
    candidates = [c for _, c in scored]

    if candidates and scored[0][0] >= confidence_threshold:
        best = candidates[0]
        logger.info(
            "Title chain auto-resolved anilist_id=%d → tvdb_id=%d %r (score=%.1f)",
            anilist_id,
            best["tvdb_id"],
            best["title"],
            scored[0][0],
        )
        return best["tvdb_id"], candidates

    logger.debug(
        "Title chain: %d candidates for anilist_id=%d, best=%.1f (need %.1f for auto)",
        len(candidates),
        anilist_id,
        scored[0][0] if scored else 0.0,
        confidence_threshold,
    )
    return None, candidates
