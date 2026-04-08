"""Anilist-Link — Application entry point."""

from __future__ import annotations

import asyncio
import json

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
from src.Sync.CrunchyrollPreviewRunner import (
    CrunchyrollPreviewProgress,
    CrunchyrollPreviewRunner,
)
from src.Sync.DownloadSyncer import DownloadSyncer
from src.Sync.JellyfinWatchSyncer import JellyfinWatchSyncer
from src.Sync.PlexWatchSyncer import PlexWatchSyncer
from src.Sync.WatchlistRefresh import watchlist_refresh_task
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
        if not dry_run:
            await watchlist_refresh_task(db, anilist_client)
    except Exception:
        logger.exception("Crunchyroll sync error")
    finally:
        await cr_client.cleanup()


async def crunchyroll_preview_task(
    config: AppConfig,
    db: DatabaseManager,
    anilist_client: AniListClient,
) -> None:
    """Run a Crunchyroll preview scan (no AniList mutations).

    Used by the scheduled job when auto_approve is disabled so changes
    appear in the /crunchyroll review page rather than being applied directly.
    """
    if not config.crunchyroll.email or not config.crunchyroll.password:
        logger.debug("Crunchyroll preview skipped — no credentials configured")
        return

    logger.info("Starting scheduled Crunchyroll preview scan (auto-approve off)")
    cr_client = CrunchyrollClient(
        email=config.crunchyroll.email,
        password=config.crunchyroll.password,
        headless=config.crunchyroll.headless,
        flaresolverr_url=config.crunchyroll.flaresolverr_url,
        max_pages=config.crunchyroll.max_pages,
        db=db,
    )

    users = await db.get_users_by_service("anilist")
    if not users:
        logger.warning("Crunchyroll preview skipped — no AniList accounts linked")
        return

    title_matcher = TitleMatcher(similarity_threshold=0.75)
    runner = CrunchyrollPreviewRunner(
        db,
        anilist_client,
        title_matcher,
        cr_client,
        config,
        CrunchyrollPreviewProgress(),
    )

    try:
        authenticated = await cr_client.authenticate()
        if not authenticated:
            logger.error("Crunchyroll authentication failed")
            return
        await runner.run_preview(users[0])
    except Exception:
        logger.exception("Crunchyroll preview scan error")
    finally:
        await cr_client.cleanup()


async def download_sync_task(
    config: AppConfig,
    db: DatabaseManager,
    anilist_client: AniListClient,
) -> None:
    """Run a single download auto-sync cycle."""
    logger.info("Starting download auto-sync")
    syncer = DownloadSyncer(db, anilist_client, config)
    try:
        result = await syncer.run_sync()
        logger.info(
            "Download sync complete: +%d sonarr, +%d radarr, %d skipped, %d errors",
            result.added_to_sonarr,
            result.added_to_radarr,
            result.skipped,
            result.errors,
        )
    except Exception:
        logger.exception("Download sync error")


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


async def library_reindex_task(
    db: DatabaseManager,
    anilist_client: AniListClient,
) -> None:
    """Re-index all local libraries so the app stays in sync with on-disk changes."""
    from src.Scanner.LibraryRestructurer import LibraryRestructurer, RestructureProgress
    from src.Scanner.LocalDirectoryScanner import LocalDirectoryScanner

    logger.info("Starting scheduled library re-index")
    title_matcher = TitleMatcher(similarity_threshold=0.75)
    group_builder = SeriesGroupBuilder(db, anilist_client)
    restructurer = LibraryRestructurer(db=db, group_builder=group_builder)
    dir_scanner = LocalDirectoryScanner(
        db=db, anilist_client=anilist_client, title_matcher=title_matcher
    )

    libraries = await db.get_all_libraries()
    if not libraries:
        logger.debug("Library re-index skipped — no libraries configured")
        return

    total_seeded = 0
    try:
        for library in libraries:
            raw = library.get("paths") or "[]"
            try:
                library_paths: list[str] = json.loads(raw)
            except Exception:
                continue
            if not library_paths:
                continue

            all_shows = []
            scan_progress = RestructureProgress(status="running")
            for path in library_paths:
                shows = await dir_scanner.scan_directory(path, scan_progress)
                all_shows.extend(shows)

            if not all_shows:
                continue

            progress = RestructureProgress(status="running")
            plan = await restructurer.analyze(
                all_shows, progress, level="full_restructure"
            )

            await db.execute(
                "DELETE FROM library_items WHERE library_id = ?", (library["id"],)
            )
            seeded = await restructurer.seed_library_items(
                plan, library["id"], from_source=True
            )
            total_seeded += seeded

        logger.info("Scheduled library re-index complete — %d items", total_seeded)
    except Exception:
        logger.exception("Scheduled library re-index failed")


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
        cfg = app.state.config
        if not cfg.crunchyroll.auto_sync_enabled:
            logger.debug("Crunchyroll auto-sync is disabled — skipping scheduled run")
            return
        if cfg.crunchyroll.auto_approve:
            await crunchyroll_sync_task(cfg, db, app.state.anilist_client)
        else:
            await crunchyroll_preview_task(cfg, db, app.state.anilist_client)

    async def _plex_scan() -> None:
        await plex_metadata_scan_task(app.state.config, db, app.state.anilist_client)

    async def _download_sync() -> None:
        await download_sync_task(app.state.config, db, app.state.anilist_client)

    async def _library_reindex() -> None:
        await library_reindex_task(db, app.state.anilist_client)

    async def _watchlist_refresh() -> None:
        await watchlist_refresh_task(db, app.state.anilist_client)

    async def _jellyfin_watch_sync() -> None:
        cfg = app.state.config
        if not cfg.jellyfin.url or not cfg.jellyfin.api_key:
            return
        from src.Clients.JellyfinClient import JellyfinClient as _JF

        jf = _JF(url=cfg.jellyfin.url, api_key=cfg.jellyfin.api_key)
        try:
            syncer = JellyfinWatchSyncer(
                db=db, anilist_client=app.state.anilist_client, jellyfin_client=jf
            )
            await syncer.sync_to_anilist()
        finally:
            await jf.close()

    async def _plex_watch_sync() -> None:
        cfg = app.state.config
        if not cfg.plex.url or not cfg.plex.token:
            return
        from src.Clients.PlexClient import PlexClient as _Plex

        plex = _Plex(url=cfg.plex.url, token=cfg.plex.token)
        try:
            syncer = PlexWatchSyncer(
                db=db, anilist_client=app.state.anilist_client, plex_client=plex
            )
            await syncer.sync_to_anilist()
        finally:
            await plex.close()

    scheduler.register_jobs(
        crunchyroll_sync_func=_cr_sync,
        plex_scan_func=_plex_scan,
        download_sync_func=_download_sync,
        download_sync_interval_minutes=config.download_sync.sync_interval_minutes,
        library_reindex_func=_library_reindex,
        watchlist_refresh_func=_watchlist_refresh,
        jellyfin_watch_sync_func=_jellyfin_watch_sync,
        plex_watch_sync_func=_plex_watch_sync,
    )

    # Expose callables for ad-hoc triggering (used by manual-run endpoints)
    app.state.cr_sync_task = lambda dry_run=False: crunchyroll_sync_task(
        app.state.config, db, app.state.anilist_client, dry_run=dry_run
    )
    app.state.cr_preview_task = lambda: crunchyroll_preview_task(
        app.state.config, db, app.state.anilist_client
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
    app.state.download_sync_task = lambda: download_sync_task(
        app.state.config, db, app.state.anilist_client
    )
    app.state.watchlist_refresh_task = lambda: watchlist_refresh_task(
        db, app.state.anilist_client
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
