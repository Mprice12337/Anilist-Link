"""Refresh the local AniList watchlist cache for all linked users."""

from __future__ import annotations

import asyncio
import logging

import httpx

from src.Clients.AnilistClient import AniListClient
from src.Database.Connection import DatabaseManager
from src.Web.ActivityTracker import ActivityTracker

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
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Watchlist refresh failed for user %s — HTTP %d: %s",
                user.get("user_id"),
                exc.response.status_code,
                exc.response.text[:200] if exc.response.text else "(empty)",
            )
        except Exception:
            logger.exception(
                "Watchlist refresh failed for user %s", user.get("user_id")
            )

    await asyncio.gather(*(_safe_refresh(u) for u in users))


async def watchlist_activity_loop(
    db: DatabaseManager,
    anilist_client: AniListClient,
    tracker: ActivityTracker,
    interval_minutes: int = 15,
) -> None:
    """Refresh the watchlist on a cadence, but only while the UI is active.

    Sleeps *interval_minutes* between checks. After each sleep, refreshes
    only if user activity occurred within the last *interval_minutes* —
    otherwise the dashboard is dormant and there's no one watching for the
    data. The first refresh on startup is unconditional so freshly-linked
    accounts populate immediately.
    """
    interval_s = max(60, interval_minutes * 60)

    # Unconditional first refresh on startup — populates newly-linked accounts.
    try:
        await watchlist_refresh_task(db, anilist_client)
    except Exception:
        logger.exception("Initial watchlist refresh failed")

    while True:
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            return

        if not tracker.is_recently_active(within_seconds=interval_s):
            logger.debug(
                "Watchlist refresh skipped — dashboard dormant for >%d min",
                interval_minutes,
            )
            continue

        try:
            await watchlist_refresh_task(db, anilist_client)
        except Exception:
            logger.exception("Watchlist refresh loop iteration failed")


async def periodic_refresh_during_job(
    db: DatabaseManager,
    anilist_client: AniListClient,
    interval_minutes: int = 15,
) -> None:
    """Refresh the watchlist every *interval_minutes* until cancelled.

    Spawn this as a side-task while a long-running job (e.g. Crunchyroll
    sync) runs, then cancel it when the job finishes. Ensures jobs that
    overrun the normal refresh cadence still see fresh AniList data.
    """
    interval_s = max(60, interval_minutes * 60)
    while True:
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            return
        try:
            await watchlist_refresh_task(db, anilist_client)
        except Exception:
            logger.exception("Mid-job watchlist refresh failed")
