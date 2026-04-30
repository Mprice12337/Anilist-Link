"""Jellyfin ↔ AniList watch status synchronization.

Supports two sync directions:
  - Jellyfin → AniList  (``sync_to_anilist``): reads watched episodes from
    Jellyfin and updates the linked AniList account.
  - AniList → Jellyfin  (``sync_to_jellyfin``): reads AniList watch progress
    and marks the appropriate episodes as played in Jellyfin.
"""

from __future__ import annotations

import logging
from typing import Any

from src.Clients.AnilistClient import AniListClient
from src.Clients.JellyfinClient import JellyfinClient
from src.Database.Connection import DatabaseManager
from src.Sync.WatchlistRefresh import watchlist_refresh_task
from src.Sync.WatchSyncBase import WatchSyncBase

logger = logging.getLogger(__name__)


class JellyfinWatchSyncer(WatchSyncBase):
    """Orchestrates Jellyfin ↔ AniList watch synchronization."""

    _sync_source = "jellyfin"

    def __init__(
        self,
        db: DatabaseManager,
        anilist_client: AniListClient,
        jellyfin_client: JellyfinClient,
    ) -> None:
        self._db = db
        self._anilist = anilist_client
        self._jellyfin = jellyfin_client

    # ==================================================================
    # Jellyfin → AniList
    # ==================================================================

    async def sync_to_anilist(self, live_check: bool = False) -> dict[str, int]:
        """Read Jellyfin watch state and update AniList.

        Args:
            live_check: When True (scheduled/auto runs), refresh the AniList
                watchlist cache before processing so status checks use fresh
                data rather than potentially stale cached values.

        Returns a summary dict with keys ``checked``, ``updated``,
        ``skipped``, ``errors``.
        """
        results: dict[str, int] = {
            "checked": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
        }

        jf_user = await self._db.get_jellyfin_user()
        if not jf_user:
            logger.warning("No Jellyfin user linked — skipping sync to AniList")
            return results

        jf_user_id: str = jf_user["jf_user_id"]
        jf_username: str = jf_user["jf_username"]
        logger.info(
            "Starting Jellyfin → AniList sync for Jellyfin user '%s'%s",
            jf_username,
            " (live check)" if live_check else "",
        )

        if live_check:
            await watchlist_refresh_task(self._db, self._anilist)

        anilist_user = (
            await self._db.get_user(jf_user["anilist_user_id"])
            if jf_user.get("anilist_user_id")
            else None
        )
        if not anilist_user:
            # Fall back to the first linked AniList user
            users = await self._db.get_users_by_service("anilist")
            anilist_user = users[0] if users else None
        if not anilist_user:
            logger.warning("No AniList account linked — skipping sync")
            return results

        anilist_user_id: str = anilist_user["user_id"]
        access_token: str = anilist_user["access_token"]

        # Get all Jellyfin media that has an AniList mapping
        mappings = await self._get_jellyfin_mappings()
        if not mappings:
            logger.info("No Jellyfin media mappings found — nothing to sync")
            return results

        for mapping in mappings:
            try:
                await self._process_mapping_to_anilist(
                    mapping, jf_user_id, anilist_user_id, access_token, results
                )
            except Exception:
                logger.exception(
                    "Error processing mapping for '%s'",
                    mapping.get("source_title", "?"),
                )
                results["errors"] += 1

        logger.info(
            "Jellyfin → AniList sync complete: checked=%d updated=%d "
            "skipped=%d errors=%d",
            results["checked"],
            results["updated"],
            results["skipped"],
            results["errors"],
        )
        return results

    async def _process_mapping_to_anilist(
        self,
        mapping: dict[str, Any],
        jf_user_id: str,
        anilist_user_id: str,
        access_token: str,
        results: dict[str, int],
    ) -> None:
        """Process one media_mappings row — update AniList if progress changed.

        A mapping with ``series_group_id`` set may be either:
        - **Structure B**: one Jellyfin item is a multi-season container → split
          episode counts per season and attribute to each series-group entry.
        - **Structure A**: separate Jellyfin items per AniList entry, but the
          scanner still tagged them with ``series_group_id`` for metadata
          purposes.  Detected by the item having ≤ 1 real season in Jellyfin.

        We resolve the ambiguity by checking how many seasons Jellyfin reports.
        """
        series_id: str = mapping["source_id"]
        anilist_id: int = mapping["anilist_id"]
        mapping_id: int = mapping["id"]
        series_group_id: int | None = mapping.get("series_group_id")

        # Fast-path: Movie-type items in Jellyfin don't have episode sub-items.
        # Many anime libraries store shows as Movie items rather than Series.
        # Treat Movie.Played=True as fully watched (all episodes = COMPLETED).
        jf_item = await self._jellyfin.get_item(series_id)
        if jf_item and jf_item.get("Type") == "Movie":
            item_with_data = await self._jellyfin.get_item_with_userdata(
                series_id, jf_user_id
            )
            is_played = (item_with_data or {}).get("UserData", {}).get("Played", False)
            if not is_played:
                results["skipped"] += 1
                return
            # Played=True → COMPLETED with all known episodes
            total_eps: int = (
                jf_item.get("ChildCount") or mapping.get("anilist_episodes") or 1
            )
            results["checked"] += 1
            await self._maybe_update_anilist(
                anilist_id,
                anilist_user_id,
                access_token,
                mapping_id,
                watched_count=total_eps,
                total_episodes=total_eps,
                results=results,
                show_title=(
                    mapping.get("anilist_title") or mapping.get("source_title", "")
                ),
            )
            return

        if series_group_id:
            season_id_map = await self._build_season_id_map(series_id)
            if len(season_id_map) > 1:
                # True Structure B: multiple real seasons in one Jellyfin item
                await self._process_structure_b(
                    series_id,
                    series_group_id,
                    jf_user_id,
                    anilist_user_id,
                    access_token,
                    mapping_id,
                    results,
                    season_id_map=season_id_map,
                )
                return
            # else: Structure A tagged with group — fall through to single-item handling

        # Structure A: this item IS one AniList entry's full episode list
        episodes = await self._jellyfin.get_series_episodes_with_userdata(
            series_id, jf_user_id
        )
        if not episodes:
            logger.warning(
                "No episodes found for Jellyfin series %s (AniList #%d) — "
                "check that source_id is a valid Jellyfin series UUID",
                series_id,
                anilist_id,
            )
            results["skipped"] += 1
            return

        watched_count = sum(
            1 for ep in episodes if ep.get("UserData", {}).get("Played", False)
        )
        results["checked"] += 1
        await self._maybe_update_anilist(
            anilist_id,
            anilist_user_id,
            access_token,
            mapping_id,
            watched_count,
            total_episodes=len(episodes),
            results=results,
            show_title=mapping.get("anilist_title") or mapping.get("source_title", ""),
        )

    async def _process_structure_b(
        self,
        series_id: str,
        series_group_id: int,
        jf_user_id: str,
        anilist_user_id: str,
        access_token: str,
        base_mapping_id: int,
        results: dict[str, int],
        season_id_map: dict[int, str] | None = None,
    ) -> None:
        """Handle Structure B: per-season mapping to series group entries."""
        group_entries = await self._db.fetch_all(
            """SELECT sge.anilist_id, sge.season_order, sge.display_title,
                      ac.episodes AS total_episodes
               FROM series_group_entries sge
               LEFT JOIN anilist_cache ac ON ac.anilist_id = sge.anilist_id
               WHERE sge.group_id = ?
               ORDER BY sge.season_order ASC""",
            (series_group_id,),
        )
        if not group_entries:
            results["skipped"] += 1
            return

        # Use pre-built map if provided, otherwise fetch it
        if season_id_map is None:
            season_id_map = await self._build_season_id_map(series_id)

        for i, entry in enumerate(group_entries):
            season_number = i + 1  # Jellyfin seasons start at 1
            season_item_id = season_id_map.get(season_number)
            if season_item_id is None:
                logger.debug(
                    "Season %d not found in Jellyfin series %s — skipping entry",
                    season_number,
                    series_id,
                )
                results["skipped"] += 1
                continue

            episodes = await self._jellyfin.get_series_episodes_with_userdata(
                series_id, jf_user_id, season_item_id=season_item_id
            )
            if not episodes:
                results["skipped"] += 1
                continue

            watched_count = sum(
                1 for ep in episodes if ep.get("UserData", {}).get("Played", False)
            )
            total = entry.get("total_episodes") or len(episodes)
            results["checked"] += 1

            synth_mapping_id = await self._get_or_create_mapping_id(
                series_id, entry["anilist_id"], season_number
            )
            await self._maybe_update_anilist(
                entry["anilist_id"],
                anilist_user_id,
                access_token,
                synth_mapping_id,
                watched_count,
                total_episodes=total,
                results=results,
                show_title=entry.get("display_title", ""),
            )

    async def _get_or_create_mapping_id(
        self, series_id: str, anilist_id: int, season_number: int
    ) -> int:
        """Return a real media_mappings id for a season-level jellyfin entry.

        For season 1 (or shows without a series group), returns the existing
        mapping for the series.  For season N > 1, creates a dedicated
        mapping row with source_id ``{series_id}::season::{N}`` so that
        sync_state FK constraints are satisfied.
        """
        if season_number <= 1:
            row = await self._db.fetch_one(
                "SELECT id FROM media_mappings"
                " WHERE source='jellyfin' AND source_id=?",
                (series_id,),
            )
            if row:
                return row["id"]

        # Season-qualified source_id for sequel seasons
        season_source_id = f"{series_id}::season::{season_number}"
        row = await self._db.fetch_one(
            "SELECT id FROM media_mappings" " WHERE source='jellyfin' AND source_id=?",
            (season_source_id,),
        )
        if row:
            return row["id"]

        # Look up the parent mapping to inherit its series_group_id
        parent = await self._db.fetch_one(
            "SELECT series_group_id, anilist_title FROM media_mappings"
            " WHERE source='jellyfin' AND source_id=?",
            (series_id,),
        )
        group_id = parent["series_group_id"] if parent else None

        cursor = await self._db.execute(
            """INSERT INTO media_mappings
                   (source, source_id, anilist_id, anilist_title,
                    match_method, series_group_id, season_number)
               VALUES ('jellyfin', ?, ?, ?, 'sync_state', ?, ?)""",
            (
                season_source_id,
                anilist_id,
                parent.get("anilist_title", "") if parent else "",
                group_id,
                season_number,
            ),
        )
        return cursor.lastrowid

    # ==================================================================
    # AniList → Jellyfin
    # ==================================================================

    async def sync_to_jellyfin(self) -> dict[str, int]:
        """Read AniList watch progress and mark episodes played in Jellyfin.

        Only pushes progress *forward* — never unmarks episodes that are
        already played in Jellyfin.
        """
        results: dict[str, int] = {
            "checked": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
        }

        jf_user = await self._db.get_jellyfin_user()
        if not jf_user:
            logger.warning("No Jellyfin user linked — skipping sync to Jellyfin")
            return results

        jf_user_id: str = jf_user["jf_user_id"]

        users = await self._db.get_users_by_service("anilist")
        if not users:
            logger.warning("No AniList account linked — skipping sync")
            return results

        anilist_user = users[0]
        anilist_user_id: str = anilist_user["user_id"]

        logger.info("Starting AniList → Jellyfin sync")

        # Use the cached user_watchlist table — avoids a live API call and
        # is guaranteed to be fresh (refreshed every 30 min + on CR sync).
        # Only process entries that have actual progress (skip PLANNING/0).
        watchlist = await self._db.fetch_all(
            """SELECT anilist_id, list_status, progress, anilist_episodes
               FROM user_watchlist
               WHERE user_id=? AND progress > 0
               ORDER BY anilist_id""",
            (anilist_user_id,),
        )
        if not watchlist:
            logger.info(
                "No watchlist entries with progress > 0 — nothing to push to Jellyfin"
            )
            return results

        for entry in watchlist:
            try:
                await self._push_anilist_entry_to_jellyfin(
                    entry, jf_user_id, anilist_user_id, results
                )
            except Exception:
                logger.exception(
                    "Error pushing AniList entry #%s to Jellyfin",
                    entry.get("anilist_id", "?"),
                )
                results["errors"] += 1

        logger.info(
            "AniList → Jellyfin sync complete: checked=%d updated=%d "
            "skipped=%d errors=%d",
            results["checked"],
            results["updated"],
            results["skipped"],
            results["errors"],
        )
        return results

    async def _push_anilist_entry_to_jellyfin(
        self,
        entry: dict[str, Any],
        jf_user_id: str,
        anilist_user_id: str,
        results: dict[str, int],
    ) -> None:
        """Push one AniList entry's progress into Jellyfin."""
        anilist_id: int = entry.get("anilist_id", 0)
        progress: int = entry.get("progress", 0)

        if not anilist_id or progress == 0:
            results["skipped"] += 1
            return

        # Find the Jellyfin mapping — either a direct mapping or via series group.
        # Sequels don't have their own media_mappings row; their parent series
        # does, linked through series_group_entries.
        series_id, season_number, mapping_title, mapping_id = (
            await self._resolve_jellyfin_target(anilist_id)
        )
        if series_id is None:
            logger.debug("AniList #%d has no Jellyfin mapping — skipping", anilist_id)
            results["skipped"] += 1
            return

        results["checked"] += 1

        # Fast-path for Movie-type items: no episode sub-items exist.
        # Mark the movie itself as played when AniList shows any progress.
        jf_item = await self._jellyfin.get_item(series_id)
        if jf_item and jf_item.get("Type") == "Movie":
            item_with_data = await self._jellyfin.get_item_with_userdata(
                series_id, jf_user_id
            )
            already_played = (
                (item_with_data or {}).get("UserData", {}).get("Played", False)
            )
            if already_played:
                logger.debug(
                    "AniList #%d: Movie '%s' already played in Jellyfin",
                    anilist_id,
                    jf_item.get("Name", series_id),
                )
                results["skipped"] += 1
                return
            await self._jellyfin.mark_episode_played(series_id, jf_user_id)
            movie_name = jf_item.get("Name", series_id)
            logger.info(
                "Marked Movie '%s' as played in Jellyfin for AniList #%d",
                movie_name,
                anilist_id,
            )
            if mapping_id:
                await self._db.upsert_sync_state(
                    user_id=anilist_user_id,
                    media_mapping_id=mapping_id,
                    last_episode=progress,
                    status=entry.get("list_status", "COMPLETED"),
                )
            await self._db.insert_watch_sync_log_entry(
                source="jellyfin",
                user_id=anilist_user_id,
                anilist_id=anilist_id,
                show_title=mapping_title or movie_name,
                before_status="",
                before_progress=0,
                after_status="played",
                after_progress=progress,
                direction="to_media",
            )
            results["updated"] += 1
            return

        # Resolve season number → Jellyfin season UUID
        season_item_id: str | None = None
        episode_offset: int = 0
        if season_number is not None:
            season_id_map = await self._build_season_id_map(series_id)
            season_item_id = season_id_map.get(season_number)
            if season_item_id is None:
                # Structure C fallback: Jellyfin has fewer seasons than AniList
                # entries (e.g. one season with 23 eps covering two AniList
                # entries).  Fall back to the first season (or whole series)
                # and use an episode offset based on prior group entries.
                episode_offset = await self._calc_episode_offset(
                    anilist_id, season_number
                )
                if episode_offset < 0:
                    logger.debug(
                        "AniList #%d: season %d not in Jellyfin and no offset "
                        "could be calculated — skipping",
                        anilist_id,
                        season_number,
                    )
                    results["skipped"] += 1
                    return
                # Use Season 1 if it exists, otherwise fetch all episodes
                season_item_id = season_id_map.get(1)
                logger.debug(
                    "AniList #%d: season %d not in Jellyfin — Structure C "
                    "fallback with offset=%d",
                    anilist_id,
                    season_number,
                    episode_offset,
                )

        logger.debug(
            "AniList #%d JF series=%s season=%s uuid=%s "
            "user=%s progress=%d offset=%d",
            anilist_id,
            series_id,
            season_number,
            season_item_id,
            jf_user_id,
            progress,
            episode_offset,
        )
        episodes = await self._jellyfin.get_series_episodes_with_userdata(
            series_id, jf_user_id, season_item_id=season_item_id
        )
        if not episodes:
            logger.warning(
                "No episodes returned for AniList #%d → series %s season=%s user=%s",
                anilist_id,
                series_id,
                season_number,
                jf_user_id,
            )
            results["skipped"] += 1
            return

        episodes_to_mark = episodes[episode_offset : episode_offset + progress]
        marked = 0
        for ep in episodes_to_mark:
            if not ep.get("UserData", {}).get("Played", False):
                await self._jellyfin.mark_episode_played(ep["Id"], jf_user_id)
                marked += 1

        # Always record the baseline in sync_state — even if all episodes were
        # already played (marked=0).  This prevents the forward sync from
        # seeing those Jellyfin-played episodes as "new" and writing them back
        # to AniList unnecessarily.
        if mapping_id:
            await self._db.upsert_sync_state(
                user_id=anilist_user_id,
                media_mapping_id=mapping_id,
                last_episode=progress,
                status=entry.get("list_status", ""),
            )

        if marked:
            logger.info(
                "Marked %d episodes played in Jellyfin for AniList #%d (%s)",
                marked,
                anilist_id,
                mapping_title,
            )
            await self._db.insert_watch_sync_log_entry(
                source="jellyfin",
                user_id=anilist_user_id,
                anilist_id=anilist_id,
                show_title=mapping_title,
                before_status="",
                before_progress=0,
                after_status="played",
                after_progress=progress,
                direction="to_media",
            )
            results["updated"] += 1
        else:
            logger.debug(
                "AniList #%d: first %d episodes already played in Jellyfin",
                anilist_id,
                progress,
            )
            results["skipped"] += 1

    # ==================================================================
    # Helpers
    # ==================================================================

    async def _resolve_jellyfin_target(
        self, anilist_id: int
    ) -> tuple[str | None, int | None, str, int]:
        """Find the Jellyfin series ID and season number for an AniList entry.

        Returns ``(series_id, season_number, title, mapping_id)`` where
        season_number is None for single-season shows (no series group) and
        mapping_id matches the key used by sync_state so the backfill and
        forward sync share the same baseline record.

        Handles two cases:
        1. Direct mapping: the AniList entry has its own media_mappings row.
        2. Sequel via series group: the entry is a sequel whose parent series
           is in media_mappings; we find its season via series_group_entries.
        """
        # Case 1: direct mapping — the Jellyfin item IS this specific show,
        # so we never need to filter by season.  Season filtering (Case 2) only
        # applies when one Jellyfin item is a multi-season container covering
        # several AniList entries (Structure B sequels that have no own mapping).
        mappings = await self._db.get_mapping_by_anilist_id(anilist_id)
        jf_mappings = [m for m in mappings if m["source"] == "jellyfin"]
        # Try each mapping in order — skip stale entries (IDs that no longer
        # exist in Jellyfin, e.g. after a library rescan regenerated item IDs).
        for m in jf_mappings:
            candidate_id: str = m["source_id"]
            item = await self._jellyfin.get_item(candidate_id)
            if item is not None:
                return candidate_id, None, m.get("anilist_title", ""), m["id"]
            logger.debug(
                "Mapping source_id=%s for AniList #%d is stale — skipping",
                candidate_id,
                anilist_id,
            )

        # Case 2: sequel — find which series group contains this anilist_id
        sge_row = await self._db.fetch_one(
            "SELECT group_id, season_order FROM series_group_entries"
            " WHERE anilist_id=?",
            (anilist_id,),
        )
        if not sge_row:
            return None, None, "", 0

        group_id: int = sge_row["group_id"]
        season_number_sq: int = sge_row["season_order"]

        parent_mapping = await self._db.fetch_one(
            """SELECT id, source_id, anilist_title FROM media_mappings
               WHERE source='jellyfin' AND series_group_id=?
               LIMIT 1""",
            (group_id,),
        )
        if not parent_mapping:
            return None, None, "", 0

        parent_id: str = parent_mapping["source_id"]
        parent_item = await self._jellyfin.get_item(parent_id)
        if parent_item is None:
            logger.debug(
                "Series group parent source_id=%s is stale for AniList #%d",
                parent_id,
                anilist_id,
            )
            return None, None, "", 0

        # Use _get_or_create_mapping_id so forward sync and backfill share
        # the same real mapping row (and FK constraints are satisfied).
        real_mapping_id = await self._get_or_create_mapping_id(
            parent_id, anilist_id, season_number_sq
        )
        return (
            parent_id,
            season_number_sq,
            parent_mapping.get("anilist_title", ""),
            real_mapping_id,
        )

    async def _build_season_id_map(self, series_id: str) -> dict[int, str]:
        """Return a mapping of season_number → Jellyfin season item UUID.

        Fetches seasons from Jellyfin and maps ``IndexNumber`` → ``Id``.
        Only includes real seasons (IndexNumber >= 1).
        """
        seasons = await self._jellyfin.get_show_seasons(series_id)
        return {s.index: s.item_id for s in seasons if s.index >= 1}

    async def _calc_episode_offset(self, anilist_id: int, season_number: int) -> int:
        """Calculate the episode offset for a Structure C fallback.

        When Jellyfin has one season but AniList splits the show into multiple
        entries (e.g. "86 Part 1" = 11 eps, "86 Part 2" = 12 eps, all under
        one Jellyfin Season 1 with 23 episodes), the offset for Part 2 is
        the sum of episode counts of all prior entries in the series group.

        Returns -1 if the offset cannot be determined.
        """
        sge_row = await self._db.fetch_one(
            "SELECT group_id, season_order FROM series_group_entries"
            " WHERE anilist_id=?",
            (anilist_id,),
        )
        if not sge_row:
            return -1

        group_id: int = sge_row["group_id"]
        prior_entries = await self._db.fetch_all(
            """SELECT episodes FROM series_group_entries
               WHERE group_id=? AND season_order < ?
               ORDER BY season_order ASC""",
            (group_id, season_number),
        )
        if not prior_entries:
            return -1

        offset = 0
        for entry in prior_entries:
            ep_count = entry.get("episodes")
            if ep_count is None:
                # Cannot reliably calculate offset without episode counts
                return -1
            offset += ep_count
        return offset

    async def _get_jellyfin_mappings(self) -> list[dict[str, Any]]:
        """Return all media_mappings rows for source='jellyfin'."""
        return await self._db.fetch_all(
            "SELECT * FROM media_mappings WHERE source='jellyfin' ORDER BY id"
        )
