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
        current = 1
    else:
        # DBs that were migrated incrementally (old v1–v13 system) may be
        # missing tables or columns that the consolidated v1 schema adds.
        # CREATE TABLE IF NOT EXISTS / ADD COLUMN guards are idempotent and
        # safe to run against any existing database.
        await _ensure_tables_and_indexes(db)
        await _apply_column_guards(db)

    if current < 2:
        await _apply_v2(db)
        current = 2

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


async def _apply_v2(db: aiosqlite.Connection) -> None:
    """Add UNIQUE(user_id, media_mapping_id) to sync_state.

    SQLite does not support ALTER TABLE ADD CONSTRAINT, so we recreate the
    table.  INSERT OR IGNORE preserves existing rows and silently drops any
    accidental duplicates.
    """
    logger.info("Applying migration v2: adding UNIQUE constraint to sync_state")
    await db.execute("ALTER TABLE sync_state RENAME TO sync_state_old")
    await db.execute("""
        CREATE TABLE sync_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            media_mapping_id INTEGER NOT NULL,
            last_episode INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT '',
            synced_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, media_mapping_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (media_mapping_id)
                REFERENCES media_mappings(id) ON DELETE CASCADE
        )
        """)
    await db.execute("""
        INSERT OR IGNORE INTO sync_state
            (id, user_id, media_mapping_id, last_episode, status, synced_at)
        SELECT id, user_id, media_mapping_id, last_episode, status, synced_at
        FROM sync_state_old
        """)
    await db.execute("DROP TABLE sync_state_old")
    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (2,))
    await db.commit()
    logger.info("Migration v2 applied: sync_state UNIQUE constraint added")


async def _apply_v3(db: aiosqlite.Connection) -> None:
    """Add restructure_plans table and plan_id column to restructure_log.

    Allows saving plans at analyze-time (even when not executed) and
    linking executed file moves back to their originating plan.
    """
    logger.info("Applying migration v3: adding restructure_plans table")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS restructure_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_dirs TEXT NOT NULL DEFAULT '[]',
            output_dir TEXT NOT NULL DEFAULT '',
            level TEXT NOT NULL DEFAULT 'full_restructure',
            file_template TEXT NOT NULL DEFAULT '',
            folder_template TEXT NOT NULL DEFAULT '',
            season_folder_template TEXT NOT NULL DEFAULT '',
            movie_file_template TEXT NOT NULL DEFAULT '',
            title_pref TEXT NOT NULL DEFAULT 'romaji',
            illegal_char_replacement TEXT NOT NULL DEFAULT '',
            group_count INTEGER NOT NULL DEFAULT 0,
            file_count INTEGER NOT NULL DEFAULT 0,
            plan_summary TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'planned',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            applied_at TEXT
        )
    """)
    # Add plan_id to restructure_log — idempotent via try/except
    try:
        await db.execute("ALTER TABLE restructure_log ADD COLUMN plan_id INTEGER")
    except aiosqlite.OperationalError:
        pass  # Column already exists
    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (3,))
    await db.commit()
    logger.info("Migration v3 applied: restructure_plans table added")
