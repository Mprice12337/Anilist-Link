"""Schema migration utilities."""

from __future__ import annotations

import logging

import aiosqlite

from src.Database.Models import INDEXES, TABLES

logger = logging.getLogger(__name__)

LATEST_VERSION = 14


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
    """Add year column to anilist_cache."""
    logger.info("Applying migration v7: adding year column to anilist_cache")

    await db.execute(
        "ALTER TABLE anilist_cache ADD COLUMN year INTEGER NOT NULL DEFAULT 0"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (7,))
    await db.commit()
    logger.info("Migration v7 applied successfully")


async def _apply_v8(db: aiosqlite.Connection) -> None:
    """Add anilist_sonarr_mapping table."""
    logger.info("Applying migration v8: adding anilist_sonarr_mapping table")

    await db.execute(TABLES["anilist_sonarr_mapping"])
    logger.debug("Created table: anilist_sonarr_mapping")

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sonarr_mapping_tvdb"
        " ON anilist_sonarr_mapping(tvdb_id)"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (8,))
    await db.commit()
    logger.info("Migration v8 applied successfully")


async def _apply_v9(db: aiosqlite.Connection) -> None:
    """Add anilist_radarr_mapping table."""
    logger.info("Applying migration v9: adding anilist_radarr_mapping table")

    await db.execute(TABLES["anilist_radarr_mapping"])
    logger.debug("Created table: anilist_radarr_mapping")

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_radarr_mapping_tmdb"
        " ON anilist_radarr_mapping(tmdb_id)"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (9,))
    await db.commit()
    logger.info("Migration v9 applied successfully")


async def _apply_v10(db: aiosqlite.Connection) -> None:
    """Add sonarr_series_cache table."""
    logger.info("Applying migration v10: adding sonarr_series_cache table")

    await db.execute(TABLES["sonarr_series_cache"])
    logger.debug("Created table: sonarr_series_cache")

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (10,))
    await db.commit()
    logger.info("Migration v10 applied successfully")


async def _apply_v11(db: aiosqlite.Connection) -> None:
    """Add radarr_movie_cache table."""
    logger.info("Applying migration v11: adding radarr_movie_cache table")

    await db.execute(TABLES["radarr_movie_cache"])
    logger.debug("Created table: radarr_movie_cache")

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (11,))
    await db.commit()
    logger.info("Migration v11 applied successfully")


async def _apply_v12(db: aiosqlite.Connection) -> None:
    """Add sonarr_id, radarr_id lookup indexes."""
    logger.info("Applying migration v12: adding sonarr/radarr arr_id indexes")

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sonarr_mapping_sonarr_id"
        " ON anilist_sonarr_mapping(sonarr_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_radarr_mapping_radarr_id"
        " ON anilist_radarr_mapping(radarr_id)"
    )

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (12,))
    await db.commit()
    logger.info("Migration v12 applied successfully")


async def _apply_v13(db: aiosqlite.Connection) -> None:
    """Add anilist_id index to sonarr/radarr mappings."""
    logger.info("Applying migration v13: adding anilist_id indexes on *arr mappings")

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sonarr_mapping_anilist"
        " ON anilist_sonarr_mapping(anilist_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_radarr_mapping_anilist"
        " ON anilist_radarr_mapping(anilist_id)"
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
