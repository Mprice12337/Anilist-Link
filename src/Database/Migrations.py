"""Schema migration utilities."""

from __future__ import annotations

import logging

import aiosqlite

from src.Database.Models import INDEXES, TABLES

logger = logging.getLogger(__name__)

LATEST_VERSION = 3


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
