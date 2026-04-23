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
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

_TVMAZE_SEARCH = "https://api.tvmaze.com/search/shows"

# Minimum effective score to accept a TVMaze match.
_MIN_CONFIDENCE = 55

# Score at which we stop trying more title variants (clearly correct).
_EARLY_EXIT_SCORE = 90

# How many TVMaze results to evaluate per search query.
_RESULTS_PER_QUERY = 5

# Cross-reference bonus: points added per extra query that returns the same
# TVMaze show ID.  Capped at _MAX_XREF_BONUS.
_XREF_BONUS_PER_HIT = 8
_MAX_XREF_BONUS = 15

# Patterns to strip season/part indicators for base-title generation.
_SEASON_PART_RE = re.compile(
    r"""
    \s*-?\s*Season\s*\d+          |
    \s*-?\s*\d+(?:st|nd|rd|th)\s*Season |
    \s*-?\s*S\d+\b                |
    \s+Part\s*\d+                 |
    \s+(?:II|III|IV|V|VI)(?:\s|$) |
    \s+\d+$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Noise characters that often differ between AniList and TVMaze titles.
_NOISE_RE = re.compile(r"[:\-\[\]()\u300c\u300d]")  # includes 「」


@dataclass
class _Candidate:
    """Tracks the best score for a TVMaze show across all query attempts."""

    best_score: int
    show: dict[str, Any]
    query_hits: int = 1
    matched_queries: list[str] = field(default_factory=list)

    @property
    def effective_score(self) -> int:
        bonus = min(_MAX_XREF_BONUS, _XREF_BONUS_PER_HIT * (self.query_hits - 1))
        return min(100, self.best_score + bonus)


def _clean(text: str) -> str:
    """Lowercase and strip noise characters for comparison."""
    return _NOISE_RE.sub(" ", text.lower()).strip()


def _best_fuzzy_score(query: str, result_name: str) -> int:
    """Compute the best fuzzy match score between *query* and *result_name*.

    Uses multiple rapidfuzz algorithms to handle different mismatch patterns:
    - token_sort_ratio: word-order differences
    - token_set_ratio: subset matches (e.g. "Ajin" vs "AJIN: Demi-Human")
    - partial_ratio (discounted): substring containment
    """
    q_raw = query.lower()
    r_raw = result_name.lower()
    q = _clean(query)
    r = _clean(result_name)

    scores = [
        fuzz.token_sort_ratio(q_raw, r_raw),
        fuzz.token_set_ratio(q_raw, r_raw),
        fuzz.token_sort_ratio(q, r),
        fuzz.token_set_ratio(q, r),
    ]

    # partial_ratio is excellent for subtitle matches ("Ajin" fully contained
    # in "AJIN: Demi-Human") but can cause false positives on short strings,
    # so we discount it.
    partial = max(fuzz.partial_ratio(q, r), fuzz.partial_ratio(r, q))
    scores.append(int(partial * 0.85))

    return max(scores)


def _strip_season_part(title: str) -> str:
    """Strip season / part indicators to produce a base series title."""
    base = _SEASON_PART_RE.sub("", title).strip()
    # Also strip trailing " -" left over after removal (e.g. "Re:ZERO -")
    base = re.sub(r"\s*-\s*$", "", base).strip()
    return base


def _dedupe_titles(titles: list[str]) -> list[str]:
    """Return unique, non-empty titles preserving insertion order."""
    seen: set[str] = set()
    result: list[str] = []
    for t in titles:
        t = t.strip()
        key = t.lower()
        if t and key not in seen:
            seen.add(key)
            result.append(t)
    return result


class TVMazeClient:
    """Client for TVMaze show search with multi-title matching."""

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

    # ------------------------------------------------------------------
    # Internal: raw API call
    # ------------------------------------------------------------------

    async def _api_search(self, query: str) -> list[dict[str, Any]]:
        """Hit the TVMaze search endpoint and return the raw result list."""
        try:
            resp = await self._http.get(_TVMAZE_SEARCH, params={"q": query.strip()})
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.debug("TVMaze search failed for '%s': %s", query, exc)
            return []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_show(self, title: str) -> dict[str, str | None] | None:
        """Search TVMaze for a single *title* (legacy convenience wrapper).

        Delegates to :meth:`search_show_multi` with a single-element list.
        """
        return await self.search_show_multi([title])

    async def search_show_multi(
        self, titles: list[str]
    ) -> dict[str, str | None] | None:
        """Search TVMaze trying multiple title variants.

        For each title variant the top ``_RESULTS_PER_QUERY`` TVMaze results
        are scored using several rapidfuzz algorithms.  If the same TVMaze
        show appears across multiple queries its confidence receives a
        cross-reference bonus.

        Base titles (with season/part stripped) are automatically generated
        and appended to the search list.

        Returns a dict with ``imdb_id``, ``tvdb_id``, ``tvmaze_id`` keys
        (any may be ``None``), or ``None`` if no match exceeded the
        confidence threshold.
        """
        if not titles:
            return None

        # Build the full candidate title list: originals + base variants.
        all_titles: list[str] = list(titles)
        for t in titles:
            base = _strip_season_part(t)
            if base and base.lower() != t.lower():
                all_titles.append(base)

        unique = _dedupe_titles(all_titles)
        if not unique:
            return None

        candidates: dict[int, _Candidate] = {}

        for title in unique:
            results = await self._api_search(title)
            if not results:
                logger.debug("TVMaze: no results for '%s'", title)
                continue

            for entry in results[:_RESULTS_PER_QUERY]:
                show = entry.get("show") or {}
                show_name: str = show.get("name") or ""
                show_id = show.get("id")
                if not show_id or not show_name:
                    continue

                score = _best_fuzzy_score(title, show_name)

                if show_id in candidates:
                    cand = candidates[show_id]
                    if score > cand.best_score:
                        cand.best_score = score
                    cand.query_hits += 1
                    cand.matched_queries.append(title)
                else:
                    candidates[show_id] = _Candidate(
                        best_score=score,
                        show=show,
                        query_hits=1,
                        matched_queries=[title],
                    )

            # Early exit if we already have a very high confidence match.
            if candidates:
                best_so_far = max(c.effective_score for c in candidates.values())
                if best_so_far >= _EARLY_EXIT_SCORE:
                    logger.debug(
                        "TVMaze: early exit at score %d for '%s'",
                        best_so_far,
                        title,
                    )
                    break

        if not candidates:
            logger.debug(
                "TVMaze: no candidates found for titles %s",
                [t[:60] for t in unique],
            )
            return None

        best = max(candidates.values(), key=lambda c: c.effective_score)
        best_name = best.show.get("name", "?")
        eff = best.effective_score

        if eff < _MIN_CONFIDENCE:
            logger.debug(
                "TVMaze: best candidate '%s' (effective_score=%d, raw=%d, "
                "hits=%d) below threshold %d for titles %s",
                best_name,
                eff,
                best.best_score,
                best.query_hits,
                _MIN_CONFIDENCE,
                [t[:60] for t in unique],
            )
            return None

        externals: dict[str, Any] = best.show.get("externals") or {}
        imdb_id: str | None = externals.get("imdb") or None
        tvdb_raw = externals.get("thetvdb")
        tvdb_id: str | None = str(tvdb_raw) if tvdb_raw is not None else None
        tvmaze_raw = best.show.get("id")
        tvmaze_id: str | None = str(tvmaze_raw) if tvmaze_raw is not None else None

        logger.info(
            "TVMaze matched [%s] → '%s' (effective=%d, raw=%d, hits=%d) "
            "imdb=%s tvdb=%s tvmaze=%s",
            " | ".join(best.matched_queries[:3]),
            best_name,
            eff,
            best.best_score,
            best.query_hits,
            imdb_id,
            tvdb_id,
            tvmaze_id,
        )
        return {"imdb_id": imdb_id, "tvdb_id": tvdb_id, "tvmaze_id": tvmaze_id}
