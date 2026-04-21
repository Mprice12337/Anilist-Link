"""Plex ↔ AniList watch status synchronization.

Supports two sync directions:
  - Plex → AniList  (``sync_to_anilist``): reads watched episodes from
    Plex and updates the linked AniList account.
  - AniList → Plex  (``sync_to_plex``): reads AniList watch progress and
    marks the appropriate episodes as watched in Plex.

Note: Plex watch status reads use the admin token.  ``viewCount`` on
episodes reflects the currently-authenticated user's own watch state when
using their personal token.  On single-user Plex setups the admin token IS
the user's token and viewCount is correct.  For multi-home setups a per-user
token would be required for accurate per-user data.
"""

from __future__ import annotations

import logging
from typing import Any

from src.Clients.AnilistClient import AniListClient
from src.Clients.PlexClient import PlexClient
from src.Database.Connection import DatabaseManager
from src.Sync.WatchlistRefresh import watchlist_refresh_task
from src.Sync.WatchSyncBase import WatchSyncBase

logger = logging.getLogger(__name__)


class PlexWatchSyncer(WatchSyncBase):
    """Orchestrates Plex ↔ AniList watch synchronization."""

    _sync_source = "plex"

    def __init__(
        self,
        db: DatabaseManager,
        anilist_client: AniListClient,
        plex_client: PlexClient,
    ) -> None:
        self._db = db
        self._anilist = anilist_client
        self._plex = plex_client

    # ==================================================================
    # Plex → AniList
    # ==================================================================

    async def sync_to_anilist(self, live_check: bool = False) -> dict[str, int]:
        """Read Plex watch state and update AniList.

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

        plex_user = await self._db.get_plex_user()
        if not plex_user:
            logger.warning("No Plex user linked — skipping sync to AniList")
            return results

        logger.info(
            "Starting Plex → AniList sync for Plex user '%s'%s",
            plex_user["plex_username"],
            " (live check)" if live_check else "",
        )

        if live_check:
            await watchlist_refresh_task(self._db, self._anilist)

        anilist_user = (
            await self._db.get_user(plex_user["anilist_user_id"])
            if plex_user.get("anilist_user_id")
            else None
        )
        if not anilist_user:
            users = await self._db.get_users_by_service("anilist")
            anilist_user = users[0] if users else None
        if not anilist_user:
            logger.warning("No AniList account linked — skipping sync")
            return results

        anilist_user_id: str = anilist_user["user_id"]
        access_token: str = anilist_user["access_token"]

        mappings = await self._get_plex_mappings()
        if not mappings:
            logger.info("No Plex media mappings found — nothing to sync")
            return results

        for mapping in mappings:
            try:
                await self._process_mapping_to_anilist(
                    mapping, anilist_user_id, access_token, results
                )
            except Exception:
                logger.exception(
                    "Error processing mapping for '%s'",
                    mapping.get("source_title", "?"),
                )
                results["errors"] += 1

        logger.info(
            "Plex → AniList sync complete: checked=%d updated=%d "
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
        anilist_user_id: str,
        access_token: str,
        results: dict[str, int],
    ) -> None:
        """Process one media_mappings row — update AniList if progress changed."""
        source_id: str = mapping["source_id"]
        anilist_id: int = mapping["anilist_id"]
        mapping_id: int = mapping["id"]

        # Season-level mapping (e.g. "12345:S2") — extract show rating_key
        if ":S" in source_id:
            rating_key, season_part = source_id.split(":S", 1)
            try:
                season_number = int(season_part)
            except ValueError:
                results["skipped"] += 1
                return
            episodes = await self._plex.get_show_episodes(rating_key)
            # Filter to the target season
            episodes = [ep for ep in episodes if ep.parent_index == season_number]
        else:
            # Show-level mapping — use all episodes
            rating_key = source_id
            episodes = await self._plex.get_show_episodes(rating_key)

        if not episodes:
            results["skipped"] += 1
            return

        watched_count = sum(1 for ep in episodes if ep.view_count > 0)
        total_episodes = len(episodes)
        results["checked"] += 1

        await self._maybe_update_anilist(
            anilist_id,
            anilist_user_id,
            access_token,
            mapping_id,
            watched_count,
            total_episodes,
            results,
            show_title=mapping.get("anilist_title") or mapping.get("source_title", ""),
        )

    # ==================================================================
    # AniList → Plex
    # ==================================================================

    async def sync_to_plex(self) -> dict[str, int]:
        """Read AniList watch progress and mark episodes watched in Plex.

        Only pushes progress forward — never unmarks already-watched episodes.
        """
        results: dict[str, int] = {
            "checked": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
        }

        plex_user = await self._db.get_plex_user()
        if not plex_user:
            logger.warning("No Plex user linked — skipping sync to Plex")
            return results

        users = await self._db.get_users_by_service("anilist")
        if not users:
            logger.warning("No AniList account linked — skipping sync")
            return results

        anilist_user = users[0]
        anilist_user_id: str = anilist_user["user_id"]

        logger.info("Starting AniList → Plex sync")

        watchlist = await self._db.fetch_all(
            """SELECT anilist_id, list_status, progress, anilist_episodes
               FROM user_watchlist
               WHERE user_id=? AND progress > 0
               ORDER BY anilist_id""",
            (anilist_user_id,),
        )
        if not watchlist:
            logger.info(
                "No watchlist entries with progress > 0 — nothing to push to Plex"
            )
            return results

        for entry in watchlist:
            try:
                await self._push_anilist_entry_to_plex(entry, anilist_user_id, results)
            except Exception:
                logger.exception(
                    "Error pushing AniList entry #%s to Plex",
                    entry.get("anilist_id", "?"),
                )
                results["errors"] += 1

        logger.info(
            "AniList → Plex sync complete: checked=%d updated=%d "
            "skipped=%d errors=%d",
            results["checked"],
            results["updated"],
            results["skipped"],
            results["errors"],
        )
        return results

    async def _push_anilist_entry_to_plex(
        self,
        entry: dict[str, Any],
        anilist_user_id: str,
        results: dict[str, int],
    ) -> None:
        """Push one AniList entry's progress into Plex."""
        anilist_id: int = entry.get("anilist_id", 0)
        progress: int = entry.get("progress", 0)

        if not anilist_id or progress == 0:
            results["skipped"] += 1
            return

        mappings = await self._db.get_mapping_by_anilist_id(anilist_id)
        plex_mappings = [m for m in mappings if m["source"] == "plex"]
        if not plex_mappings:
            results["skipped"] += 1
            return

        results["checked"] += 1
        for mapping in plex_mappings:
            mapping_id: int = mapping["id"]
            source_id: str = mapping["source_id"]

            if ":S" in source_id:
                rating_key, season_part = source_id.split(":S", 1)
                try:
                    season_number = int(season_part)
                except ValueError:
                    continue
                episodes = await self._plex.get_show_episodes(rating_key)
                episodes = [ep for ep in episodes if ep.parent_index == season_number]
            else:
                episodes = await self._plex.get_show_episodes(source_id)

            if not episodes:
                continue

            episodes_to_mark = episodes[:progress]
            marked = 0
            for ep in episodes_to_mark:
                if ep.view_count == 0:
                    await self._plex.mark_episode_watched(ep.rating_key)
                    marked += 1

            # Record the baseline in sync_state regardless of whether we marked
            # new episodes.  Without this, the forward sync would see the
            # Plex-played episodes as "new" progress and write them back to
            # AniList unnecessarily.
            await self._db.upsert_sync_state(
                user_id=anilist_user_id,
                media_mapping_id=mapping_id,
                last_episode=progress,
                status=entry.get("list_status", ""),
            )

            if marked:
                logger.info(
                    "Marked %d episodes watched in Plex for AniList #%d",
                    marked,
                    anilist_id,
                )
                results["updated"] += 1
            else:
                results["skipped"] += 1

    # ==================================================================
    # Helpers
    # ==================================================================

    async def _get_plex_mappings(self) -> list[dict[str, Any]]:
        """Return all media_mappings rows for source='plex'."""
        return await self._db.fetch_all(
            "SELECT * FROM media_mappings WHERE source='plex' ORDER BY id"
        )
