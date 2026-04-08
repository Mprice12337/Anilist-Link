"""APScheduler job definitions for periodic scans and syncs."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.Utils.Config import SchedulerConfig

logger = logging.getLogger(__name__)

JOB_CRUNCHYROLL_SYNC = "crunchyroll_sync"
JOB_METADATA_SCAN = "metadata_scan"
JOB_PLEX_METADATA_SCAN = "plex_metadata_scan"
JOB_WATCH_SYNC = "watch_sync"
JOB_DOWNLOAD_SYNC = "download_sync"
JOB_LIBRARY_REINDEX = "library_reindex"
JOB_WATCHLIST_REFRESH = "watchlist_refresh"


def _cr_trigger(config: SchedulerConfig) -> CronTrigger | IntervalTrigger:
    """Return the appropriate trigger for the Crunchyroll sync job.

    If ``config.cr_sync_time`` is a valid ``HH:MM`` string, returns a
    :class:`CronTrigger` that fires once daily at that local time.
    Otherwise falls back to an :class:`IntervalTrigger` using
    ``sync_interval_minutes``.
    """
    time_str = (config.cr_sync_time or "").strip()
    if time_str:
        parts = time_str.split(":")
        if len(parts) == 2:
            try:
                hour, minute = int(parts[0]), int(parts[1])
                logger.info(
                    "Crunchyroll sync scheduled daily at %02d:%02d", hour, minute
                )
                return CronTrigger(hour=hour, minute=minute)
            except ValueError:
                logger.warning(
                    "Invalid cr_sync_time '%s' — falling back to interval", time_str
                )
    return IntervalTrigger(minutes=config.sync_interval_minutes)


class JobScheduler:
    """Wrapper around APScheduler for managing periodic jobs."""

    def __init__(self, config: SchedulerConfig) -> None:
        self._config = config
        self._scheduler = AsyncIOScheduler()

    def register_jobs(
        self,
        crunchyroll_sync_func: Callable[[], Awaitable[None]] | None = None,
        metadata_scan_func: Callable[[], Awaitable[None]] | None = None,
        watch_sync_func: Callable[[], Awaitable[None]] | None = None,
        plex_scan_func: Callable[[], Awaitable[None]] | None = None,
        download_sync_func: Callable[[], Awaitable[None]] | None = None,
        download_sync_interval_minutes: int = 60,
        library_reindex_func: Callable[[], Awaitable[None]] | None = None,
        watchlist_refresh_func: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Register job callables with configured intervals."""
        if crunchyroll_sync_func:
            self._scheduler.add_job(
                crunchyroll_sync_func,
                trigger=_cr_trigger(self._config),
                id=JOB_CRUNCHYROLL_SYNC,
                name="Crunchyroll Watch History Sync",
                replace_existing=True,
            )

        if metadata_scan_func:
            self._scheduler.add_job(
                metadata_scan_func,
                trigger=IntervalTrigger(hours=self._config.scan_interval_hours),
                id=JOB_METADATA_SCAN,
                name="Metadata Scan",
                replace_existing=True,
            )
            logger.info(
                "Registered %s job (every %d hr)",
                JOB_METADATA_SCAN,
                self._config.scan_interval_hours,
            )

        if watch_sync_func:
            self._scheduler.add_job(
                watch_sync_func,
                trigger=IntervalTrigger(minutes=self._config.sync_interval_minutes),
                id=JOB_WATCH_SYNC,
                name="Watch Status Sync",
                replace_existing=True,
            )
            logger.info(
                "Registered %s job (every %d min)",
                JOB_WATCH_SYNC,
                self._config.sync_interval_minutes,
            )

        if plex_scan_func:
            self._scheduler.add_job(
                plex_scan_func,
                trigger=IntervalTrigger(hours=self._config.scan_interval_hours),
                id=JOB_PLEX_METADATA_SCAN,
                name="Plex Metadata Scan",
                replace_existing=True,
            )
            logger.info(
                "Registered %s job (every %d hr)",
                JOB_PLEX_METADATA_SCAN,
                self._config.scan_interval_hours,
            )

        if download_sync_func:
            self._scheduler.add_job(
                download_sync_func,
                trigger=IntervalTrigger(minutes=download_sync_interval_minutes),
                id=JOB_DOWNLOAD_SYNC,
                name="Download Auto-Sync",
                replace_existing=True,
            )
            logger.info(
                "Registered %s job (every %d min)",
                JOB_DOWNLOAD_SYNC,
                download_sync_interval_minutes,
            )

        if library_reindex_func:
            self._scheduler.add_job(
                library_reindex_func,
                trigger=IntervalTrigger(
                    hours=self._config.library_reindex_interval_hours
                ),
                id=JOB_LIBRARY_REINDEX,
                name="Library Re-Index",
                replace_existing=True,
            )
            logger.info(
                "Registered %s job (every %d hr)",
                JOB_LIBRARY_REINDEX,
                self._config.library_reindex_interval_hours,
            )

        if watchlist_refresh_func:
            self._scheduler.add_job(
                watchlist_refresh_func,
                trigger=IntervalTrigger(
                    minutes=self._config.watchlist_refresh_interval_minutes
                ),
                id=JOB_WATCHLIST_REFRESH,
                name="AniList Watchlist Refresh",
                replace_existing=True,
            )
            logger.info(
                "Registered %s job (every %d min)",
                JOB_WATCHLIST_REFRESH,
                self._config.watchlist_refresh_interval_minutes,
            )

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Scheduler started")

    def shutdown(self, wait: bool = False) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
            logger.info("Scheduler shut down")

    def update_intervals(self, new_config: SchedulerConfig) -> None:
        """Reschedule existing jobs if intervals changed."""
        if new_config == self._config:
            return

        old = self._config
        self._config = new_config

        # Reschedule CR sync if its trigger inputs changed
        cr_changed = (
            new_config.sync_interval_minutes != old.sync_interval_minutes
            or new_config.cr_sync_time != old.cr_sync_time
        )
        if cr_changed:
            job = self._scheduler.get_job(JOB_CRUNCHYROLL_SYNC)
            if job:
                trigger = _cr_trigger(new_config)
                job.reschedule(trigger=trigger)
                logger.info("Rescheduled %s with updated trigger", JOB_CRUNCHYROLL_SYNC)

        if new_config.sync_interval_minutes != old.sync_interval_minutes:
            job = self._scheduler.get_job(JOB_WATCH_SYNC)
            if job:
                job.reschedule(
                    trigger=IntervalTrigger(minutes=new_config.sync_interval_minutes)
                )
                logger.info(
                    "Rescheduled %s to every %d min",
                    JOB_WATCH_SYNC,
                    new_config.sync_interval_minutes,
                )

        if new_config.scan_interval_hours != old.scan_interval_hours:
            for job_id in (JOB_METADATA_SCAN, JOB_PLEX_METADATA_SCAN):
                job = self._scheduler.get_job(job_id)
                if job:
                    job.reschedule(
                        trigger=IntervalTrigger(hours=new_config.scan_interval_hours)
                    )
                    logger.info(
                        "Rescheduled %s to every %d hr",
                        job_id,
                        new_config.scan_interval_hours,
                    )

        if (
            new_config.library_reindex_interval_hours
            != old.library_reindex_interval_hours
        ):
            job = self._scheduler.get_job(JOB_LIBRARY_REINDEX)
            if job:
                job.reschedule(
                    trigger=IntervalTrigger(
                        hours=new_config.library_reindex_interval_hours
                    )
                )
                logger.info(
                    "Rescheduled %s to every %d hr",
                    JOB_LIBRARY_REINDEX,
                    new_config.library_reindex_interval_hours,
                )

        if (
            new_config.watchlist_refresh_interval_minutes
            != old.watchlist_refresh_interval_minutes
        ):
            job = self._scheduler.get_job(JOB_WATCHLIST_REFRESH)
            if job:
                job.reschedule(
                    trigger=IntervalTrigger(
                        minutes=new_config.watchlist_refresh_interval_minutes
                    )
                )
                logger.info(
                    "Rescheduled %s to every %d min",
                    JOB_WATCHLIST_REFRESH,
                    new_config.watchlist_refresh_interval_minutes,
                )

    def trigger_job(self, job_id: str) -> bool:
        """Manually trigger a job by ID. Returns True if the job exists."""
        job = self._scheduler.get_job(job_id)
        if job is None:
            logger.warning("Job %s not found", job_id)
            return False
        job.modify(next_run_time=datetime.now())
        logger.info("Manually triggered job: %s", job_id)
        return True

    def get_job_status(self) -> list[dict[str, Any]]:
        """Return status of all registered jobs."""
        jobs = self._scheduler.get_jobs()
        result: list[dict[str, Any]] = []
        for job in jobs:
            result.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run_time": (
                        str(job.next_run_time) if job.next_run_time else None
                    ),
                    "pending": job.pending,
                }
            )
        return result
