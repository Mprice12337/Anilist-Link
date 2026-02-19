"""Schema migration utilities."""

from __future__ import annotations

import logging

import aiosqlite

from src.Database.Models import INDEXES, TABLES

logger = logging.getLogger(__name__)

LATEST_VERSION = 7


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
