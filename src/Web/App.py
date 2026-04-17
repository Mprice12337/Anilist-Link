"""FastAPI application factory for the web dashboard."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.Clients.AnilistClient import AniListClient
from src.Database.Connection import DatabaseManager
from src.Scheduler.Jobs import JobScheduler
from src.Sync.WatchlistRefresh import watchlist_refresh_task
from src.Utils.Config import AppConfig

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "Templates"
STATIC_DIR = WEB_DIR / "Static"


async def _register_arr_webhooks(config: AppConfig) -> None:
    """Register Sonarr/Radarr webhooks if the services are configured."""
    from src.Clients.RadarrClient import RadarrClient
    from src.Clients.SonarrClient import SonarrClient

    base_url = config.base_url.rstrip("/")

    if config.sonarr.url and config.sonarr.api_key:
        try:
            client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
            await client.register_webhook(
                "Anilist-Link", f"{base_url}/api/webhook/sonarr"
            )
            logger.info("Auto-registered Sonarr webhook at startup")
            await client.close()
        except Exception as exc:
            logger.warning("Failed to register Sonarr webhook at startup: %s", exc)

    if config.radarr.url and config.radarr.api_key:
        try:
            radarr = RadarrClient(url=config.radarr.url, api_key=config.radarr.api_key)
            await radarr.register_webhook(
                "Anilist-Link", f"{base_url}/api/webhook/radarr"
            )
            logger.info("Auto-registered Radarr webhook at startup")
            await radarr.close()
        except Exception as exc:
            logger.warning("Failed to register Radarr webhook at startup: %s", exc)


def create_app(
    config: AppConfig,
    db: DatabaseManager,
    anilist_client: AniListClient,
    scheduler: JobScheduler,
) -> FastAPI:
    """Build the FastAPI application with all routes and middleware."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup
        logger.info("Starting scheduler")
        scheduler.start()

        # Auto-register arr webhooks (fire-and-forget, non-blocking)
        asyncio.create_task(_register_arr_webhooks(config))

        # Refresh AniList watchlist cache on startup (fire-and-forget)
        asyncio.create_task(watchlist_refresh_task(db, anilist_client))

        yield
        # Shutdown
        jf_listener = getattr(app.state, "jellyfin_listener", None)
        if jf_listener:
            logger.info("Stopping Jellyfin WebSocket listener")
            await jf_listener.stop()
        logger.info("Shutting down scheduler")
        scheduler.shutdown(wait=False)
        logger.info("Closing AniList client")
        await anilist_client.close()
        logger.info("Closing database")
        await db.close()

    app = FastAPI(
        title="Anilist-Link",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Store dependencies on app.state for access in route handlers
    app.state.config = config
    app.state.db = db
    app.state.anilist_client = anilist_client
    app.state.scheduler = scheduler
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def _utc_to_local(utc_str: str) -> str:
        """Convert a UTC datetime string from SQLite to the container's local time."""
        if not utc_str:
            return utc_str
        try:
            dt = datetime.strptime(utc_str[:19], "%Y-%m-%d %H:%M:%S")
            dt_local = dt.replace(tzinfo=timezone.utc).astimezone()
            return dt_local.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return utc_str

    templates.env.filters["localtime"] = _utc_to_local
    app.state.templates = templates
    app.state.background_tasks = set()  # prevent GC of fire-and-forget tasks
    # Map of task_key -> asyncio.Task for cancellable long-running ops.
    # See spawn_background_task(task_key=...) and cancel_task() below.
    app.state.cancellable_tasks = {}

    # Static files
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Register routers
    from src.Web.Routes.ArrWebhook import router as arr_webhook_router
    from src.Web.Routes.Auth import router as auth_router
    from src.Web.Routes.ConnectionTest import router as connection_test_router
    from src.Web.Routes.CrunchyrollSync import router as cr_sync_router
    from src.Web.Routes.Dashboard import router as dashboard_router
    from src.Web.Routes.Downloads import router as downloads_router
    from src.Web.Routes.JellyfinLibrary import router as jellyfin_library_router
    from src.Web.Routes.JellyfinScan import router as jellyfin_scan_router
    from src.Web.Routes.JellyfinWebhook import router as jellyfin_webhook_router
    from src.Web.Routes.Library import router as library_router
    from src.Web.Routes.ManualGrab import router as manual_grab_router
    from src.Web.Routes.Mappings import router as mappings_router
    from src.Web.Routes.Onboarding import router as onboarding_router
    from src.Web.Routes.PlexLibrary import router as plex_library_router
    from src.Web.Routes.PlexScan import router as plex_scan_router
    from src.Web.Routes.Restructure import router as restructure_router
    from src.Web.Routes.Settings import router as settings_router
    from src.Web.Routes.SonarrSync import router as sonarr_sync_router
    from src.Web.Routes.Tools import router as tools_router
    from src.Web.Routes.UnifiedLibrary import router as unified_library_router
    from src.Web.Routes.WatchlistLibrary import router as watchlist_library_router
    from src.Web.Routes.WatchSync import router as watch_sync_router

    app.include_router(arr_webhook_router)
    app.include_router(downloads_router)
    app.include_router(auth_router)
    app.include_router(tools_router)
    app.include_router(unified_library_router)
    app.include_router(connection_test_router)
    app.include_router(cr_sync_router)
    app.include_router(dashboard_router)
    app.include_router(jellyfin_library_router)
    app.include_router(jellyfin_scan_router)
    app.include_router(jellyfin_webhook_router)
    app.include_router(library_router)
    app.include_router(manual_grab_router)
    app.include_router(mappings_router)
    app.include_router(onboarding_router)
    app.include_router(plex_library_router)
    app.include_router(plex_scan_router)
    app.include_router(restructure_router)
    app.include_router(settings_router)
    app.include_router(sonarr_sync_router)
    app.include_router(watch_sync_router)
    app.include_router(watchlist_library_router)

    return app


def spawn_background_task(
    app_state: object,
    coro,  # type: ignore[no-untyped-def]
    task_key: str | None = None,
) -> asyncio.Task:  # type: ignore[type-arg]
    """Create a background task that is prevented from being garbage-collected.

    The task automatically removes itself from the tracking set on completion.
    Use this instead of bare ``asyncio.create_task()`` in route handlers.

    If ``task_key`` is provided, the task is registered in
    ``app_state.cancellable_tasks`` so it can be cancelled via
    :func:`cancel_task` / ``POST /api/tasks/{task_key}/cancel``.  Any existing
    task registered under the same key will NOT be cancelled — callers should
    check for an in-flight task first and refuse to start a duplicate.
    """
    task = asyncio.create_task(coro)
    tasks: set = app_state.background_tasks  # type: ignore[attr-defined]
    tasks.add(task)
    task.add_done_callback(tasks.discard)

    if task_key:
        registry: dict = getattr(app_state, "cancellable_tasks", None)  # type: ignore[attr-defined]
        if registry is None:
            registry = {}
            app_state.cancellable_tasks = registry  # type: ignore[attr-defined]
        registry[task_key] = task

        def _unregister(t: asyncio.Task) -> None:  # type: ignore[type-arg]
            # Only drop if the stored task is still us — a subsequent spawn
            # under the same key would have replaced it.
            if registry.get(task_key) is t:
                registry.pop(task_key, None)

        task.add_done_callback(_unregister)

    return task


def cancel_task(app_state: object, task_key: str) -> bool:
    """Request cancellation of a background task registered under ``task_key``.

    Returns True if a live task was found and cancellation was requested;
    False if no task was registered or the task already finished.

    Cancellation raises :class:`asyncio.CancelledError` inside the coroutine at
    its next ``await`` point.  Background coroutines that care about clean
    shutdown should catch it, update their progress record to ``status =
    "cancelled"``, and then either re-raise or return normally.
    """
    registry: dict = getattr(app_state, "cancellable_tasks", {}) or {}  # type: ignore[attr-defined]
    task: asyncio.Task | None = registry.get(task_key)  # type: ignore[type-arg]
    if task is None or task.done():
        return False
    task.cancel()
    return True
