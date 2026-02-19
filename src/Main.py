"""Anilist-Link — Application entry point."""

from __future__ import annotations

import asyncio

import uvicorn
from dotenv import load_dotenv

from src.Clients.AnilistClient import AniListClient
from src.Clients.CrunchyrollClient import CrunchyrollClient
from src.Clients.PlexClient import PlexClient
from src.Database.Connection import DatabaseManager
from src.Matching.TitleMatcher import TitleMatcher
from src.Scanner.MetadataScanner import MetadataScanner
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Scheduler.Jobs import JobScheduler
from src.Sync.WatchSyncer import WatchSyncer
from src.Utils.Config import AppConfig, load_config, load_config_from_db_settings
from src.Utils.Logging import get_logger, setup_logging
from src.Web.App import create_app

logger = get_logger(__name__)


async def crunchyroll_sync_task(
    config: AppConfig,
    db: DatabaseManager,
    anilist_client: AniListClient,
    dry_run: bool = False,
) -> None:
    """Run a single Crunchyroll → AniList sync cycle."""
    if not config.crunchyroll.email:
        logger.debug("Crunchyroll sync skipped — no credentials configured")
        return

    logger.info("Starting Crunchyroll sync%s", " (DRY RUN)" if dry_run else "")
    cr_client = CrunchyrollClient(
        email=config.crunchyroll.email,
        password=config.crunchyroll.password,
        headless=config.crunchyroll.headless,
        flaresolverr_url=config.crunchyroll.flaresolverr_url,
        max_pages=config.crunchyroll.max_pages,
        db=db,
    )

    title_matcher = TitleMatcher(similarity_threshold=0.75)
    syncer = WatchSyncer(
        db, anilist_client, title_matcher, cr_client, config, dry_run=dry_run
    )

    try:
        authenticated = await cr_client.authenticate()
        if not authenticated:
            logger.error("Crunchyroll authentication failed")
            return

        await syncer.run_sync()
    except Exception:
        logger.exception("Crunchyroll sync error")
    finally:
        await cr_client.cleanup()


async def plex_metadata_scan_task(
    config: AppConfig,
    db: DatabaseManager,
    anilist_client: AniListClient,
    dry_run: bool = False,
    library_keys: list[str] | None = None,
) -> None:
    """Run a single Plex metadata scan cycle."""
    if not config.plex.url or not config.plex.token:
        logger.debug("Plex scan skipped — no URL or token configured")
        return

    logger.info("Starting Plex metadata scan%s", " (DRY RUN)" if dry_run else "")
    plex_client = PlexClient(url=config.plex.url, token=config.plex.token)
    title_matcher = TitleMatcher(similarity_threshold=0.75)
    group_builder = SeriesGroupBuilder(db, anilist_client)
    scanner = MetadataScanner(
        db,
        anilist_client,
        title_matcher,
        plex_client,
        config,
        group_builder=group_builder,
    )

    # Use explicit library_keys if provided, else fall back to config
    if library_keys is None and config.plex.anime_library_keys:
        library_keys = list(config.plex.anime_library_keys)

    try:
        await scanner.run_scan(dry_run=dry_run, library_keys=library_keys)
    except Exception:
        logger.exception("Plex metadata scan error")
    finally:
        await plex_client.close()


async def main() -> None:
    """Application startup sequence."""
    load_dotenv()

    # Phase 1: boot with env-only config (needed for DB path and logging)
    config = load_config()
    setup_logging(config.debug, config.log_path)

    logger.info("Anilist-Link v0.1.0 starting")
    logger.info("Debug mode: %s", config.debug)

    # Phase 2: initialize database and run migrations
    db = DatabaseManager(config.database.path)
    await db.initialize()

    # Phase 3: rebuild config with DB settings merged in
    db_settings = await db.get_all_settings()
    config = load_config_from_db_settings(db_settings)

    # Create AniList client
    anilist_client = AniListClient(
        client_id=config.anilist.client_id,
        client_secret=config.anilist.client_secret,
        redirect_uri=config.anilist.redirect_uri,
    )

    # Create scheduler
    scheduler = JobScheduler(config.scheduler)

    # Build FastAPI app (stores references on app.state)
    app = create_app(config, db, anilist_client, scheduler)

    # Register the Crunchyroll sync job — reads config/client from app.state
    # so settings page changes take effect without restart
    async def _cr_sync() -> None:
        await crunchyroll_sync_task(app.state.config, db, app.state.anilist_client)

    async def _plex_scan() -> None:
        await plex_metadata_scan_task(app.state.config, db, app.state.anilist_client)

    scheduler.register_jobs(
        crunchyroll_sync_func=_cr_sync,
        plex_scan_func=_plex_scan,
    )

    # Expose callables for ad-hoc triggering (used by dry-run endpoints)
    app.state.cr_sync_task = lambda dry_run=False: crunchyroll_sync_task(
        app.state.config, db, app.state.anilist_client, dry_run=dry_run
    )
    app.state.plex_scan_task = (
        lambda dry_run=False, library_keys=None: plex_metadata_scan_task(
            app.state.config,
            db,
            app.state.anilist_client,
            dry_run=dry_run,
            library_keys=library_keys,
        )
    )

    # Start uvicorn
    uv_config = uvicorn.Config(
        app=app,
        host=config.host,
        port=config.port,
        log_level="debug" if config.debug else "info",
    )
    server = uvicorn.Server(uv_config)

    logger.info("Serving on http://%s:%d", config.host, config.port)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
