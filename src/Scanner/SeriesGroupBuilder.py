"""Build series groups by traversing AniList SEQUEL/PREQUEL relations."""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from src.Clients.AnilistClient import AniListClient
from src.Database.Connection import DatabaseManager

logger = logging.getLogger(__name__)

# Only follow these relation types when building a series group
_FOLLOW_RELATIONS = {"SEQUEL", "PREQUEL"}


def _start_date_sort_key(entry: dict[str, Any]) -> tuple[int, int, int]:
    """Return a (year, month, day) tuple for sorting. Nulls sort last."""
    sd = entry.get("startDate") or {}
    return (
        sd.get("year") or 9999,
        sd.get("month") or 99,
        sd.get("day") or 99,
    )


def _format_start_date(sd: dict[str, Any] | None) -> str:
    """Format a {year, month, day} dict as 'YYYY-MM-DD', or '' if null."""
    if not sd or not sd.get("year"):
        return ""
    y = sd["year"]
    m = sd.get("month") or 1
    d = sd.get("day") or 1
    return f"{y:04d}-{m:02d}-{d:02d}"


class SeriesGroupBuilder:
    """Builds and caches series groups from AniList relation graphs.

    A *series group* is the set of all AniList entries reachable from a
    starting entry via SEQUEL / PREQUEL edges (type=ANIME only).
    Entries are sorted chronologically by ``startDate``.
    """

    def __init__(
        self,
        db: DatabaseManager,
        anilist_client: AniListClient,
        max_age_hours: int = 168,
    ) -> None:
        self._db = db
        self._anilist = anilist_client
        self._max_age_hours = max_age_hours
        # In-memory session cache: anilist_id → (group_id, entries)
        # Prevents repeated DB + API calls for IDs we already resolved
        # during this builder instance's lifetime.
        self._session_cache: dict[int, tuple[int, list[dict[str, Any]]]] = {}

    async def get_or_build_group(
        self, anilist_id: int
    ) -> tuple[int, list[dict[str, Any]]]:
        """Return ``(group_id, entries_list)`` for the group containing *anilist_id*.

        If the group is already cached and fresh, returns from the database.
        Otherwise traverses the relation graph, persists, and returns.
        """
        # Fast path: in-memory session cache (avoids DB + API entirely)
        if anilist_id in self._session_cache:
            return self._session_cache[anilist_id]

        # Check if this AniList ID already belongs to a fresh cached group
        existing = await self._db.get_series_group_by_anilist_id(anilist_id)
        if existing:
            root_id = existing["root_anilist_id"]
            if await self._db.is_series_group_fresh(root_id, self._max_age_hours):
                entries = await self._db.get_series_group_entries(existing["id"])
                logger.debug(
                    "Using cached series group '%s' (%d entries)",
                    existing["display_title"],
                    len(entries),
                )
                result = (existing["id"], entries)
                # Cache all IDs in this group so siblings hit the fast path
                for e in entries:
                    self._session_cache[e["anilist_id"]] = result
                return result

        # Traverse the relation graph
        collected = await self._traverse_relations(anilist_id)

        if not collected:
            # Shouldn't happen, but guard against empty results
            logger.warning(
                "Relation traversal returned nothing for AniList %d", anilist_id
            )
            return 0, []

        # Sort chronologically
        collected.sort(key=_start_date_sort_key)

        # The root entry is the first (chronologically earliest)
        root_entry = collected[0]
        root_anilist_id = root_entry["id"]
        title_obj = root_entry.get("title") or {}
        display_title = title_obj.get("english") or title_obj.get("romaji") or ""

        # Persist group
        group_id = await self._db.upsert_series_group(
            root_anilist_id=root_anilist_id,
            display_title=display_title,
            entry_count=len(collected),
        )

        # Clear old entries and re-populate
        await self._db.clear_series_group_entries(group_id)
        for order, entry in enumerate(collected, start=1):
            entry_title_obj = entry.get("title") or {}
            entry_display = (
                entry_title_obj.get("english") or entry_title_obj.get("romaji") or ""
            )
            await self._db.upsert_series_group_entry(
                group_id=group_id,
                anilist_id=entry["id"],
                season_order=order,
                display_title=entry_display,
                format=entry.get("format") or "",
                episodes=entry.get("episodes"),
                start_date=_format_start_date(entry.get("startDate")),
            )

        entries = await self._db.get_series_group_entries(group_id)
        logger.info(
            "Built series group '%s' with %d entries (root AniList %d)",
            display_title,
            len(entries),
            root_anilist_id,
        )
        result = (group_id, entries)
        # Cache all IDs so sibling entries hit the fast path
        for e in entries:
            self._session_cache[e["anilist_id"]] = result
        return result

    async def _cache_entry_metadata(self, entry: dict[str, Any]) -> None:
        """Persist an entry's metadata in anilist_cache (cover, desc, etc.)."""
        title = entry.get("title") or {}
        cover = (entry.get("coverImage") or {}).get("large", "")
        year = entry.get("seasonYear") or (
            (entry.get("startDate") or {}).get("year") or 0
        )
        genres = entry.get("genres") or []
        import json

        await self._db.set_cached_metadata(
            anilist_id=entry["id"],
            title_romaji=title.get("romaji") or "",
            title_english=title.get("english") or "",
            title_native=title.get("native") or "",
            episodes=entry.get("episodes"),
            cover_image=cover,
            description=entry.get("description") or "",
            genres=json.dumps(genres),
            status=entry.get("status") or "",
            year=year,
        )

    async def _traverse_relations(self, start_id: int) -> list[dict[str, Any]]:
        """BFS walk of SEQUEL/PREQUEL edges, collecting all ANIME entries."""
        visited: set[int] = set()
        queue: deque[int] = deque([start_id])
        collected: list[dict[str, Any]] = []

        while queue:
            current_id = queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)

            root_data, edges = await self._anilist.get_anime_relations(current_id)
            if root_data is None:
                logger.warning("Could not fetch relations for AniList %d", current_id)
                continue

            collected.append(root_data)
            # Cache metadata so cover images etc. are available later
            await self._cache_entry_metadata(root_data)

            for edge in edges:
                rel_type = edge.get("relationType", "")
                node = edge.get("node", {})
                node_id = node.get("id")
                node_type = node.get("type", "")

                if (
                    rel_type in _FOLLOW_RELATIONS
                    and node_type == "ANIME"
                    and node_id
                    and node_id not in visited
                ):
                    queue.append(node_id)

        return collected
