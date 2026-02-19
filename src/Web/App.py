"""FastAPI application factory for the web dashboard."""

from __future__ import annotations

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

    # Static files
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Register routers
    from src.Web.Routes.Auth import router as auth_router
    from src.Web.Routes.Dashboard import router as dashboard_router
    from src.Web.Routes.PlexLibrary import router as plex_library_router
    from src.Web.Routes.PlexScan import router as plex_scan_router
    from src.Web.Routes.Restructure import router as restructure_router
    from src.Web.Routes.Settings import router as settings_router

    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(plex_library_router)
    app.include_router(plex_scan_router)
    app.include_router(restructure_router)
    app.include_router(settings_router)

    return app
