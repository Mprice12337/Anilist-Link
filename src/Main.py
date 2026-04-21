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
from src.Scanner.MetadataScanner import MetadataScanner
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
from src.Web.Routes.Helpers import create_group_builder, create_title_matcher

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

    title_matcher = create_title_matcher()
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

    title_matcher = create_title_matcher()
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
    title_matcher = create_title_matcher()
    group_builder = create_group_builder(db, anilist_client)
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
    title_matcher = create_title_matcher()
    group_builder = create_group_builder(db, anilist_client)
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
        if not cfg.jellyfin.watch_sync_enabled:
            logger.debug("Jellyfin watch sync is disabled — skipping scheduled run")
            return
        from src.Clients.JellyfinClient import JellyfinClient as _JF

        jf = _JF(url=cfg.jellyfin.url, api_key=cfg.jellyfin.api_key)
        try:
            syncer = JellyfinWatchSyncer(
                db=db, anilist_client=app.state.anilist_client, jellyfin_client=jf
            )
            await syncer.sync_to_anilist(live_check=True)
        finally:
            await jf.close()

    async def _plex_watch_sync() -> None:
        cfg = app.state.config
        if not cfg.plex.url or not cfg.plex.token:
            return
        if not cfg.plex.watch_sync_enabled:
            logger.debug("Plex watch sync is disabled — skipping scheduled run")
            return
        from src.Clients.PlexClient import PlexClient as _Plex

        plex = _Plex(url=cfg.plex.url, token=cfg.plex.token)
        try:
            syncer = PlexWatchSyncer(
                db=db, anilist_client=app.state.anilist_client, plex_client=plex
            )
            await syncer.sync_to_anilist(live_check=True)
        finally:
            await plex.close()

    # Jellyfin WebSocket listener — replaces the 60s polling approach with
    # a persistent connection that gets instant scan completion events.
    app.state.jellyfin_listener = None

    # Lock prevents concurrent auto-scans (e.g. rapid RefreshLibrary +
    # WebhookItemAdded transitions firing back-to-back).
    _jellyfin_auto_scan_lock = asyncio.Lock()

    async def _on_jellyfin_scan_complete() -> None:
        """Callback fired by the WebSocket listener when a library scan ends.

        Runs the full metadata pipeline so externally-added media gets
        matched, has metadata written, and virtual seasons cleaned up.
        Skips the heavy work when a user-initiated scan is already running.
        """
        cfg = app.state.config
        if not cfg.jellyfin.url or not cfg.jellyfin.api_key:
            return
        lib_ids = (
            list(cfg.jellyfin.anime_library_ids)
            if cfg.jellyfin.anime_library_ids
            else []
        )
        if not lib_ids:
            return

        # If a user-initiated scan/apply is already running, just do the
        # lightweight virtual cleanup — the user scan handles everything.
        jf_scan = getattr(app.state, "jellyfin_scan_progress", None)
        jf_apply = getattr(app.state, "jellyfin_apply_progress", None)
        user_scan_active = (jf_scan and jf_scan.status == "running") or (
            jf_apply and jf_apply.status == "running"
        )

        from src.Clients.JellyfinClient import JellyfinClient as _JF

        if user_scan_active:
            jf = _JF(url=cfg.jellyfin.url, api_key=cfg.jellyfin.api_key)
            try:
                deleted = await jf.delete_virtual_seasons(lib_ids)
                if deleted:
                    logger.info(
                        "WebSocket-triggered cleanup removed %d virtual "
                        "seasons (user scan active — skipping auto-scan)",
                        deleted,
                    )
            except Exception:
                logger.debug("Virtual season cleanup error", exc_info=True)
            finally:
                await jf.close()
            return

        # Acquire lock so concurrent events don't stack up full scans.
        if _jellyfin_auto_scan_lock.locked():
            logger.debug("Auto-scan already in progress — skipping duplicate")
            return

        async with _jellyfin_auto_scan_lock:
            logger.info("Running auto-scan for new/changed Jellyfin items")
            jf = _JF(url=cfg.jellyfin.url, api_key=cfg.jellyfin.api_key)
            try:
                from src.Scanner.JellyfinMetadataScanner import (
                    JellyfinMetadataScanner,
                )

                title_matcher = create_title_matcher()
                group_builder = create_group_builder(db, app.state.anilist_client)
                scanner = JellyfinMetadataScanner(
                    db,
                    app.state.anilist_client,
                    title_matcher,
                    jf,
                    cfg,
                    group_builder,
                )

                # Jellyfin just finished its own scan so items are already
                # indexed — no need for an initial refresh_and_wait.
                # Existing mappings are skipped quickly; only genuinely new
                # items go through AniList search + fuzzy matching.
                results = await scanner.run_scan(preview=False, library_ids=lib_ids)
                logger.info(
                    "Auto-scan complete: %d matched, %d skipped, %d failed",
                    results.matched,
                    results.skipped,
                    results.failed,
                )

                if results.matched > 0:
                    # NFOs were written — trigger a Jellyfin refresh so it
                    # reads them, then do recursive episode metadata refresh.
                    listener = app.state.jellyfin_listener
                    if listener:
                        listener.suppress_callbacks = True
                    try:
                        await jf.refresh_and_wait(app.state, library_ids=lib_ids)

                        for lid in lib_ids:
                            series_ids = await jf.get_series_ids_in_library(lid)
                            for sid in series_ids:
                                await jf.refresh_item_metadata(
                                    sid, recursive=True, replace_all=True
                                )
                    finally:
                        if listener:
                            listener.suppress_callbacks = False

                # Always clean up virtual seasons at the end.
                deleted = await jf.delete_virtual_seasons(lib_ids)
                if deleted:
                    logger.info(
                        "Auto-scan cleanup removed %d virtual seasons",
                        deleted,
                    )
            except Exception:
                logger.exception("Auto-scan pipeline error")
            finally:
                await jf.close()

    if config.jellyfin.url and config.jellyfin.api_key:
        from src.Clients.JellyfinEventListener import JellyfinEventListener

        listener = JellyfinEventListener(
            url=config.jellyfin.url,
            api_key=config.jellyfin.api_key,
            on_scan_complete=_on_jellyfin_scan_complete,
        )
        app.state.jellyfin_listener = listener
        await listener.start()

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
