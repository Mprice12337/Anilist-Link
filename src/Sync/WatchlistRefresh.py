"""Refresh the local AniList watchlist cache for all linked users."""

from __future__ import annotations

import asyncio
import logging

from src.Clients.AnilistClient import AniListClient
from src.Database.Connection import DatabaseManager

logger = logging.getLogger(__name__)


async def _refresh_user(
    anilist_client: AniListClient,
    db: DatabaseManager,
    user: dict,
) -> None:
    entries = await anilist_client.get_user_watchlist(
        user["anilist_id"], user["access_token"]
    )
    count = await db.bulk_upsert_watchlist(user["user_id"], entries)
    logger.info(
        "Watchlist refresh complete for user %s — %d entries",
        user["user_id"],
        count,
    )


async def watchlist_refresh_task(
    db: DatabaseManager,
    anilist_client: AniListClient,
) -> None:
    """Refresh the local AniList watchlist cache for all linked users."""
    users = await db.get_users_by_service("anilist")
    if not users:
        logger.debug("Watchlist refresh skipped — no AniList accounts linked")
        return

    logger.info("Refreshing AniList watchlist for %d user(s)", len(users))

    async def _safe_refresh(user: dict) -> None:
        try:
            await _refresh_user(anilist_client, db, user)
        except Exception:
            logger.exception(
                "Watchlist refresh failed for user %s", user.get("user_id")
            )

    await asyncio.gather(*(_safe_refresh(u) for u in users))
