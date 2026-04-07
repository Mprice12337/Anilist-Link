"""FastAPI application factory for the web dashboard."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.Clients.AnilistClient import AniListClient
from src.Database.Connection import DatabaseManager
from src.Scheduler.Jobs import JobScheduler
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

        yield
        # Shutdown
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
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.background_tasks = set()  # prevent GC of fire-and-forget tasks

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
    app.include_router(library_router)
    app.include_router(manual_grab_router)
    app.include_router(mappings_router)
    app.include_router(onboarding_router)
    app.include_router(plex_library_router)
    app.include_router(plex_scan_router)
    app.include_router(restructure_router)
    app.include_router(settings_router)
    app.include_router(sonarr_sync_router)
    app.include_router(watchlist_library_router)

    return app


def spawn_background_task(app_state: object, coro) -> asyncio.Task:  # type: ignore[type-arg]
    """Create a background task that is prevented from being garbage-collected.

    The task automatically removes itself from the tracking set on completion.
    Use this instead of bare ``asyncio.create_task()`` in route handlers.
    """
    task = asyncio.create_task(coro)
    tasks: set = app_state.background_tasks  # type: ignore[attr-defined]
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task
