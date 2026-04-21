"""Shared base for Plex/Jellyfin watch syncers.

Extracts the common AniList update logic (sync_state comparison, COMPLETED
guard, status determination, API update, log entry) used identically by
PlexWatchSyncer and JellyfinWatchSyncer.
"""

from __future__ import annotations

import logging

from src.Clients.AnilistClient import AniListClient
from src.Database.Connection import DatabaseManager

logger = logging.getLogger(__name__)


class WatchSyncBase:
    """Mixin providing the shared _maybe_update_anilist logic."""

    _db: DatabaseManager
    _anilist: AniListClient
    _sync_source: str = ""  # Subclasses set to "plex" or "jellyfin"

    async def _maybe_update_anilist(
        self,
        anilist_id: int,
        anilist_user_id: str,
        access_token: str,
        mapping_id: int,
        watched_count: int,
        total_episodes: int,
        results: dict[str, int],
        show_title: str = "",
    ) -> None:
        """Compare with sync_state and update AniList if progress changed."""
        sync_state = await self._db.get_sync_state(anilist_user_id, mapping_id)
        last_episode = sync_state["last_episode"] if sync_state else 0
        last_status = sync_state["status"] if sync_state else ""

        if watched_count <= last_episode:
            results["skipped"] += 1
            return

        # Don't downgrade a show the user has already marked as completed on AniList.
        # Any other status (CURRENT, DROPPED, PAUSED, PLANNING) is fine to overwrite
        # — if they're watching more episodes, that's the ground truth.
        cached = await self._db.get_watchlist_entry(anilist_user_id, anilist_id)
        if cached and cached.get("list_status") == "COMPLETED":
            logger.debug(
                "Skipping AniList #%d — already COMPLETED on AniList", anilist_id
            )
            results["skipped"] += 1
            return

        # Determine AniList status
        if watched_count == 0:
            status = "PLANNING"
        elif total_episodes > 0 and watched_count >= total_episodes:
            status = "COMPLETED"
        else:
            status = "CURRENT"

        logger.info(
            "Updating AniList #%d: progress %d→%d (%s)",
            anilist_id,
            last_episode,
            watched_count,
            status,
        )

        try:
            await self._anilist.update_anime_progress(
                anime_id=anilist_id,
                access_token=access_token,
                progress=watched_count,
                status=status,
            )
            await self._db.upsert_sync_state(
                user_id=anilist_user_id,
                media_mapping_id=mapping_id,
                last_episode=watched_count,
                status=status,
            )
            await self._db.insert_watch_sync_log_entry(
                source=self._sync_source,
                user_id=anilist_user_id,
                anilist_id=anilist_id,
                show_title=show_title,
                before_status=last_status,
                before_progress=last_episode,
                after_status=status,
                after_progress=watched_count,
            )
            results["updated"] += 1
        except Exception:
            logger.exception(
                "Failed to update AniList #%d to progress %d",
                anilist_id,
                watched_count,
            )
            results["errors"] += 1
