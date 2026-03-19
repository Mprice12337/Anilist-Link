"""Schema migration utilities."""

from __future__ import annotations

import logging

import aiosqlite

from src.Database.Models import INDEXES, TABLES

logger = logging.getLogger(__name__)

LATEST_VERSION = 17


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Check current schema version and apply pending migrations."""
    current = await _get_current_version(db)
    logger.info("Database schema version: %d (latest: %d)", current, LATEST_VERSION)

    if current < 1:
        await _apply_v1(db)

    if current < 2:
        await _apply_v2(db)

    if current < 3:
        await _apply_v3(db)

    if current < 4:
        await _apply_v4(db)

    if current < 5:
        await _apply_v5(db)

    if current < 6:
        await _apply_v6(db)

    if current < 7:
        await _apply_v7(db)

    if current < 8:
        await _apply_v8(db)

    if current < 9:
        await _apply_v9(db)

    if current < 10:
        await _apply_v10(db)

    if current < 11:
        await _apply_v11(db)

    if current < 12:
        await _apply_v12(db)

    if current < 13:
        await _apply_v13(db)

    if current < 14:
        await _apply_v14(db)

    if current < 15:
        await _apply_v15(db)

    if current < 16:
        await _apply_v16(db)

    if current < 17:
        await _apply_v17(db)


async def _get_current_version(db: aiosqlite.Connection) -> int:
    """Return the current schema version, or 0 if the table doesn't exist."""
    try:
        cursor = await db.execute("SELECT MAX(version) FROM schema_version")
        row = await cursor.fetchone()
        if row and row[0] is not None:
            return int(row[0])
    except aiosqlite.OperationalError:
        # Table doesn't exist yet
        pass
    return 0


async def _apply_v1(db: aiosqlite.Connection) -> None:
    """Create all initial tables and indexes."""
    logger.info("Applying migration v1: creating initial schema")

    for table_name, ddl in TABLES.items():
        await db.execute(ddl)
        logger.debug("Created table: %s", table_name)

    for index_ddl in INDEXES:
        await db.execute(index_ddl)

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (1,))
    await db.commit()
    logger.info("Migration v1 applied successfully")


async def _apply_v2(db: aiosqlite.Connection) -> None:
    """Add cr_session_cache table for Crunchyroll auth persistence."""
    logger.info("Applying migration v2: adding cr_session_cache table")

    await db.execute(TABLES["cr_session_cache"])
    logger.debug("Created table: cr_session_cache")

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (2,))
    await db.commit()
    logger.info("Migration v2 applied successfully")


async def _apply_v3(db: aiosqlite.Connection) -> None:
    """Add app_settings table for GUI-managed configuration."""
    logger.info("Applying migration v3: adding app_settings table")

    await db.execute(TABLES["app_settings"])
    logger.debug("Created table: app_settings")

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (3,))
    await db.commit()
    logger.info("Migration v3 applied successfully")


async def _apply_v4(db: aiosqlite.Connection) -> None:
    """Add plex_media table for persistent library browsing."""
    logger.info("Applying migration v4: adding plex_media table")

    await db.execute(TABLES["plex_media"])
    logger.debug("Created table: plex_media")

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plex_media_library"
        " ON plex_media(library_key)"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (4,))
    await db.commit()
    logger.info("Migration v4 applied successfully")


async def _apply_v5(db: aiosqlite.Connection) -> None:
    """Add series_groups, series_group_entries tables and extend media_mappings."""
    logger.info("Applying migration v5: adding series group tables")

    await db.execute(TABLES["series_groups"])
    logger.debug("Created table: series_groups")

    await db.execute(TABLES["series_group_entries"])
    logger.debug("Created table: series_group_entries")

    # Add new columns to media_mappings
    await db.execute("ALTER TABLE media_mappings ADD COLUMN series_group_id INTEGER")
    await db.execute("ALTER TABLE media_mappings ADD COLUMN season_number INTEGER")

    # Create indexes
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sge_anilist_id"
        " ON series_group_entries(anilist_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_series_groups_root"
        " ON series_groups(root_anilist_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_mappings_group"
        " ON media_mappings(series_group_id)"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (5,))
    await db.commit()
    logger.info("Migration v5 applied successfully")


async def _apply_v6(db: aiosqlite.Connection) -> None:
    """Add restructure_log table for tracking file move operations."""
    logger.info("Applying migration v6: adding restructure_log table")

    await db.execute(TABLES["restructure_log"])
    logger.debug("Created table: restructure_log")

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (6,))
    await db.commit()
    logger.info("Migration v6 applied successfully")


async def _apply_v7(db: aiosqlite.Connection) -> None:
    """Add year column to anilist_cache (no-op if column already exists in schema)."""
    logger.info("Applying migration v7: adding year column to anilist_cache")

    try:
        await db.execute(
            "ALTER TABLE anilist_cache ADD COLUMN year INTEGER NOT NULL DEFAULT 0"
        )
    except aiosqlite.OperationalError as exc:
        if "duplicate column name" in str(exc):
            logger.debug("v7: year column already present, skipping ALTER")
        else:
            raise

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (7,))
    await db.commit()
    logger.info("Migration v7 applied successfully")


async def _apply_v8(db: aiosqlite.Connection) -> None:
    """Add libraries and library_items tables for Library Manager."""
    logger.info("Applying migration v8: adding libraries and library_items tables")

    await db.execute(TABLES["libraries"])
    logger.debug("Created table: libraries")

    await db.execute(TABLES["library_items"])
    logger.debug("Created table: library_items")

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_library_items_library"
        " ON library_items(library_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_library_items_anilist"
        " ON library_items(anilist_id)"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (8,))
    await db.commit()
    logger.info("Migration v8 applied successfully")


async def _apply_v9(db: aiosqlite.Connection) -> None:
    """Add jellyfin_media table for persistent Jellyfin library browsing."""
    logger.info("Applying migration v9: adding jellyfin_media table")

    await db.execute(TABLES["jellyfin_media"])
    logger.debug("Created table: jellyfin_media")

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_jellyfin_media_library"
        " ON jellyfin_media(library_id)"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (9,))
    await db.commit()
    logger.info("Migration v9 applied successfully")


async def _apply_v10(db: aiosqlite.Connection) -> None:
    """Add plex_users, jellyfin_users, cr_sync_preview, cr_sync_log tables."""
    logger.info("Applying migration v10: adding Phase A tables")

    await db.execute(TABLES["plex_users"])
    logger.debug("Created table: plex_users")

    await db.execute(TABLES["jellyfin_users"])
    logger.debug("Created table: jellyfin_users")

    await db.execute(TABLES["cr_sync_preview"])
    logger.debug("Created table: cr_sync_preview")

    await db.execute(TABLES["cr_sync_log"])
    logger.debug("Created table: cr_sync_log")

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_cr_sync_preview_run"
        " ON cr_sync_preview(run_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_cr_sync_log_anilist"
        " ON cr_sync_log(anilist_id)"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (10,))
    await db.commit()
    logger.info("Migration v10 applied successfully")


async def _apply_v11(db: aiosqlite.Connection) -> None:
    """Add download_requests table for P4 Sonarr/Radarr integration."""
    logger.info("Applying migration v11: adding download_requests table")

    await db.execute(TABLES["download_requests"])
    logger.debug("Created table: download_requests")

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_download_requests_anilist"
        " ON download_requests(anilist_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_download_requests_status"
        " ON download_requests(status)"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (11,))
    await db.commit()
    logger.info("Migration v11 applied successfully")


async def _apply_v12(db: aiosqlite.Connection) -> None:
    """Add episodes_json column to cr_sync_preview for episode breakdown detail."""
    logger.info("Applying migration v12: adding episodes_json to cr_sync_preview")

    await db.execute(
        "ALTER TABLE cr_sync_preview"
        " ADD COLUMN episodes_json TEXT NOT NULL DEFAULT '[]'"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (12,))
    await db.commit()
    logger.info("Migration v12 applied successfully")


async def _apply_v13(db: aiosqlite.Connection) -> None:
    """Add Sonarr/Radarr mapping and cache tables for P4 download management."""
    logger.info("Applying migration v13: adding Sonarr/Radarr mapping and cache tables")

    for table in (
        "anilist_sonarr_mapping",
        "anilist_radarr_mapping",
        "sonarr_series_cache",
        "radarr_movie_cache",
    ):
        await db.execute(TABLES[table])
        logger.debug("Created table: %s", table)

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sonarr_mapping_tvdb"
        " ON anilist_sonarr_mapping(tvdb_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sonarr_mapping_group"
        " ON anilist_sonarr_mapping(series_group_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_radarr_mapping_tmdb"
        " ON anilist_radarr_mapping(tmdb_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sonarr_cache_sonarr_id"
        " ON sonarr_series_cache(sonarr_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_radarr_cache_radarr_id"
        " ON radarr_movie_cache(radarr_id)"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (13,))
    await db.commit()
    logger.info("Migration v13 applied successfully")


async def _apply_v14(db: aiosqlite.Connection) -> None:
    """Add user_watchlist table for Phase 1 library view."""
    logger.info("Applying migration v14: adding user_watchlist table")

    await db.execute(TABLES["user_watchlist"])
    logger.debug("Created table: user_watchlist")

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_watchlist_user_status"
        " ON user_watchlist(user_id, list_status)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_watchlist_anilist"
        " ON user_watchlist(anilist_id)"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (14,))
    await db.commit()
    logger.info("Migration v14 applied successfully")


async def _apply_v15(db: aiosqlite.Connection) -> None:
    """Add anilist_sonarr_season_mapping for per-season AniList title resolution."""
    logger.info("Applying migration v15: adding anilist_sonarr_season_mapping table")

    await db.execute("""CREATE TABLE IF NOT EXISTS anilist_sonarr_season_mapping (
            sonarr_id     INTEGER NOT NULL,
            season_number INTEGER NOT NULL,
            anilist_id    INTEGER NOT NULL,
            created_at    TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (sonarr_id, season_number)
        )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_assm_sonarr"
        " ON anilist_sonarr_season_mapping (sonarr_id)"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (15,))
    await db.commit()
    logger.info("Migration v15 applied successfully")


async def _apply_v16(db: aiosqlite.Connection) -> None:
    """Add anilist_arr_skip for caching auto-sync TVDB/TMDB resolution failures."""
    logger.info("Applying migration v16: adding anilist_arr_skip table")

    await db.execute("""CREATE TABLE IF NOT EXISTS anilist_arr_skip (
            anilist_id   INTEGER PRIMARY KEY,
            reason       TEXT NOT NULL,
            skipped_at   TEXT DEFAULT (datetime('now'))
        )""")

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (16,))
    await db.commit()
    logger.info("Migration v16 applied successfully")


async def _apply_v17(db: aiosqlite.Connection) -> None:
    """Add monitor_type column to Sonarr/Radarr mapping tables."""
    logger.info("Applying migration v17: adding monitor_type to mapping tables")

    for table in ("anilist_sonarr_mapping", "anilist_radarr_mapping"):
        try:
            await db.execute(
                f"ALTER TABLE {table}"
                " ADD COLUMN monitor_type TEXT NOT NULL DEFAULT 'future'"
            )
        except aiosqlite.OperationalError as exc:
            if "duplicate column name" in str(exc):
                logger.debug("v17: monitor_type already present in %s, skipping", table)
            else:
                raise

    # Back-fill: entries with monitored=0 should be 'none'
    await db.execute(
        "UPDATE anilist_sonarr_mapping SET monitor_type='none'"
        " WHERE sonarr_monitored=0"
    )
    await db.execute(
        "UPDATE anilist_radarr_mapping SET monitor_type='none'"
        " WHERE radarr_monitored=0"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (17,))
    await db.commit()
    logger.info("Migration v17 applied successfully")
