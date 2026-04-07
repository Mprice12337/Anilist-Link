"""Schema migration utilities."""

from __future__ import annotations

import logging

import aiosqlite

from src.Database.Models import INDEXES, TABLES

logger = logging.getLogger(__name__)

LATEST_VERSION = 1


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Check current schema version and apply pending migrations."""
    current = await _get_current_version(db)
    logger.info("Database schema version: %d (latest: %d)", current, LATEST_VERSION)

    if current < 1:
        await _apply_v1(db)
    else:
        # DBs that were migrated incrementally (old v1–v13 system) may be
        # missing tables or columns that the consolidated v1 schema adds.
        # CREATE TABLE IF NOT EXISTS / ADD COLUMN guards are idempotent and
        # safe to run against any existing database.
        await _ensure_tables_and_indexes(db)
        await _apply_column_guards(db)


async def _get_current_version(db: aiosqlite.Connection) -> int:
    """Return the current schema version, or 0 if the table doesn't exist."""
    try:
        cursor = await db.execute("SELECT MAX(version) FROM schema_version")
        row = await cursor.fetchone()
        if row and row[0] is not None:
            return int(row[0])
    except aiosqlite.OperationalError:
        pass
    return 0


async def _apply_v1(db: aiosqlite.Connection) -> None:
    """Create all tables and indexes for the initial 1.0 schema."""
    logger.info("Applying migration v1: creating initial schema")

    for table_name, ddl in TABLES.items():
        await db.execute(ddl)
        logger.debug("Created table: %s", table_name)

    for index_ddl in INDEXES:
        await db.execute(index_ddl)

    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (1,))
    await db.commit()
    logger.info("Migration v1 applied successfully")


async def _ensure_tables_and_indexes(db: aiosqlite.Connection) -> None:
    """Create any tables or indexes that are missing (idempotent).

    Safe to run on any database — CREATE TABLE/INDEX IF NOT EXISTS is a
    no-op when the object already exists.
    """
    for table_name, ddl in TABLES.items():
        await db.execute(ddl)
        logger.debug("Ensured table: %s", table_name)

    for index_ddl in INDEXES:
        await db.execute(index_ddl)

    await db.commit()
    logger.debug("Table/index ensure pass complete")


async def _apply_column_guards(db: aiosqlite.Connection) -> None:
    """Add columns that may be absent in DBs from older incremental migrations.

    Each guard uses try/except because SQLite raises OperationalError when
    a column already exists (there is no ADD COLUMN IF NOT EXISTS syntax).
    Missing columns are logged at INFO; already-present columns are silently
    skipped.
    """
    guards: list[tuple[str, str, str]] = [
        # (table, column, column_definition)
        ("plex_media", "folder_name", "TEXT NOT NULL DEFAULT ''"),
        ("plex_media", "library_title", "TEXT NOT NULL DEFAULT ''"),
        ("plex_media", "summary", "TEXT NOT NULL DEFAULT ''"),
        ("jellyfin_media", "folder_name", "TEXT NOT NULL DEFAULT ''"),
        (
            "anilist_cache",
            "expires_at",
            "TEXT NOT NULL DEFAULT (datetime('now', '+7 days'))",
        ),
        ("anilist_cache", "year", "INTEGER NOT NULL DEFAULT 0"),
        ("media_mappings", "series_group_id", "INTEGER"),
        ("media_mappings", "season_number", "INTEGER"),
        ("library_items", "series_group_id", "INTEGER"),
        ("library_items", "cover_image", "TEXT NOT NULL DEFAULT ''"),
        ("library_items", "anilist_format", "TEXT NOT NULL DEFAULT ''"),
        ("library_items", "anilist_episodes", "INTEGER"),
        ("library_items", "year", "INTEGER NOT NULL DEFAULT 0"),
    ]

    added: list[str] = []
    for table, column, col_def in guards:
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            added.append(f"{table}.{column}")
        except aiosqlite.OperationalError:
            pass  # Column already exists — expected for up-to-date DBs

    if added:
        await db.commit()
        logger.info("Added missing columns: %s", ", ".join(added))
