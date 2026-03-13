"""SQLite/aiosqlite connection management."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

from src.Database.Migrations import run_migrations

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Async SQLite database manager with repository methods."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open connection, enable WAL mode, and run migrations."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        logger.info("Database opened: %s", self._db_path)
        await run_migrations(self._db)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("Database closed")

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._db

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        cursor = await self.db.execute(sql, params)
        await self.db.commit()
        return cursor

    async def fetch_one(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> dict[str, Any] | None:
        cursor = await self.db.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetch_all(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        cursor = await self.db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Media Mappings
    # ------------------------------------------------------------------

    async def upsert_media_mapping(
        self,
        source: str,
        source_id: str,
        source_title: str,
        anilist_id: int,
        anilist_title: str = "",
        match_confidence: float = 0.0,
        match_method: str = "",
        media_type: str = "ANIME",
        series_group_id: int | None = None,
        season_number: int | None = None,
    ) -> int:
        cursor = await self.execute(
            """INSERT INTO media_mappings
                   (source, source_id, source_title, anilist_id, anilist_title,
                    match_confidence, match_method, media_type,
                    series_group_id, season_number)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, source_id) DO UPDATE SET
                   anilist_id=excluded.anilist_id,
                   anilist_title=excluded.anilist_title,
                   match_confidence=excluded.match_confidence,
                   match_method=excluded.match_method,
                   series_group_id=excluded.series_group_id,
                   season_number=excluded.season_number,
                   updated_at=datetime('now')
            """,
            (
                source,
                source_id,
                source_title,
                anilist_id,
                anilist_title,
                match_confidence,
                match_method,
                media_type,
                series_group_id,
                season_number,
            ),
        )
        return cursor.lastrowid or 0

    async def get_mapping_by_source(
        self, source: str, source_id: str
    ) -> dict[str, Any] | None:
        return await self.fetch_one(
            "SELECT * FROM media_mappings WHERE source=? AND source_id=?",
            (source, source_id),
        )

    async def get_mapping_by_anilist_id(self, anilist_id: int) -> list[dict[str, Any]]:
        return await self.fetch_all(
            "SELECT * FROM media_mappings WHERE anilist_id=?",
            (anilist_id,),
        )

    async def get_all_mappings(self) -> list[dict[str, Any]]:
        return await self.fetch_all(
            "SELECT * FROM media_mappings" " ORDER BY updated_at DESC"
        )

    async def get_mapping_count(self) -> int:
        row = await self.fetch_one("SELECT COUNT(*) as cnt FROM media_mappings")
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    async def upsert_user(
        self,
        user_id: str,
        service: str,
        username: str,
        access_token: str,
        token_type: str = "Bearer",
        anilist_id: int = 0,
    ) -> None:
        await self.execute(
            """INSERT INTO users
                   (user_id, service, username,
                    access_token, token_type, anilist_id)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   username=excluded.username,
                   access_token=excluded.access_token,
                   token_type=excluded.token_type,
                   anilist_id=excluded.anilist_id,
                   updated_at=datetime('now')
            """,
            (user_id, service, username, access_token, token_type, anilist_id),
        )

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        return await self.fetch_one("SELECT * FROM users WHERE user_id=?", (user_id,))

    async def get_users_by_service(self, service: str) -> list[dict[str, Any]]:
        return await self.fetch_all("SELECT * FROM users WHERE service=?", (service,))

    async def get_all_users(self) -> list[dict[str, Any]]:
        return await self.fetch_all("SELECT * FROM users ORDER BY created_at DESC")

    async def get_user_count(self) -> int:
        row = await self.fetch_one("SELECT COUNT(*) as cnt FROM users")
        return row["cnt"] if row else 0

    async def delete_user(self, user_id: str) -> None:
        await self.execute("DELETE FROM users WHERE user_id=?", (user_id,))

    # ------------------------------------------------------------------
    # Sync State
    # ------------------------------------------------------------------

    async def upsert_sync_state(
        self,
        user_id: str,
        media_mapping_id: int,
        last_episode: int,
        status: str,
    ) -> None:
        await self.execute(
            """INSERT INTO sync_state (user_id, media_mapping_id, last_episode, status)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, media_mapping_id) DO UPDATE SET
                   last_episode=excluded.last_episode,
                   status=excluded.status,
                   synced_at=datetime('now')
            """,
            (user_id, media_mapping_id, last_episode, status),
        )

    async def get_sync_state(
        self, user_id: str, media_mapping_id: int
    ) -> dict[str, Any] | None:
        return await self.fetch_one(
            "SELECT * FROM sync_state WHERE user_id=? AND media_mapping_id=?",
            (user_id, media_mapping_id),
        )

    # ------------------------------------------------------------------
    # AniList Cache
    # ------------------------------------------------------------------

    async def get_cached_metadata(self, anilist_id: int) -> dict[str, Any] | None:
        return await self.fetch_one(
            "SELECT * FROM anilist_cache"
            " WHERE anilist_id=?"
            " AND expires_at > datetime('now')",
            (anilist_id,),
        )

    async def set_cached_metadata(
        self,
        anilist_id: int,
        title_romaji: str = "",
        title_english: str = "",
        title_native: str = "",
        episodes: int | None = None,
        cover_image: str = "",
        description: str = "",
        genres: str = "[]",
        status: str = "",
        year: int = 0,
    ) -> None:
        await self.execute(
            """INSERT INTO anilist_cache
                   (anilist_id, title_romaji, title_english, title_native,
                    episodes, cover_image, description, genres, status, year)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(anilist_id) DO UPDATE SET
                   title_romaji=excluded.title_romaji,
                   title_english=excluded.title_english,
                   title_native=excluded.title_native,
                   episodes=excluded.episodes,
                   cover_image=excluded.cover_image,
                   description=excluded.description,
                   genres=excluded.genres,
                   status=excluded.status,
                   year=excluded.year,
                   cached_at=datetime('now'),
                   expires_at=datetime('now', '+7 days')
            """,
            (
                anilist_id,
                title_romaji,
                title_english,
                title_native,
                episodes,
                cover_image,
                description,
                genres,
                status,
                year,
            ),
        )

    async def cleanup_expired_cache(self) -> int:
        cursor = await self.execute(
            "DELETE FROM anilist_cache WHERE expires_at <= datetime('now')"
        )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Manual Overrides
    # ------------------------------------------------------------------

    async def add_override(
        self,
        source: str,
        source_id: str,
        source_title: str,
        anilist_id: int,
        created_by: str = "",
    ) -> int:
        cursor = await self.execute(
            """INSERT INTO manual_overrides
                   (source, source_id, source_title,
                    anilist_id, created_by)
               VALUES (?, ?, ?, ?, ?)
            """,
            (source, source_id, source_title, anilist_id, created_by),
        )
        return cursor.lastrowid or 0

    async def get_override(self, source: str, source_id: str) -> dict[str, Any] | None:
        return await self.fetch_one(
            "SELECT * FROM manual_overrides WHERE source=? AND source_id=?",
            (source, source_id),
        )

    async def get_all_overrides(self) -> list[dict[str, Any]]:
        return await self.fetch_all(
            "SELECT * FROM manual_overrides" " ORDER BY created_at DESC"
        )

    async def delete_override(self, override_id: int) -> None:
        await self.execute("DELETE FROM manual_overrides WHERE id=?", (override_id,))

    # ------------------------------------------------------------------
    # Crunchyroll Session Cache
    # ------------------------------------------------------------------

    async def save_cr_session(
        self,
        cookies_json: str,
        access_token: str,
        account_id: str,
        device_id: str,
    ) -> None:
        """Persist Crunchyroll auth session (replaces any existing row)."""
        await self.execute("DELETE FROM cr_session_cache")
        await self.execute(
            """INSERT INTO cr_session_cache
                   (id, cookies_json, access_token, account_id, device_id)
               VALUES (1, ?, ?, ?, ?)
            """,
            (cookies_json, access_token, account_id, device_id),
        )

    async def load_cr_session(self) -> dict[str, Any] | None:
        """Load cached CR session if not expired."""
        return await self.fetch_one(
            "SELECT * FROM cr_session_cache"
            " WHERE id=1 AND expires_at > datetime('now')"
        )

    async def clear_cr_session(self) -> None:
        """Delete all cached CR session data."""
        await self.execute("DELETE FROM cr_session_cache")

    # ------------------------------------------------------------------
    # App Settings
    # ------------------------------------------------------------------

    async def get_setting(self, key: str) -> str | None:
        """Return a single setting value, or None if not set."""
        row = await self.fetch_one("SELECT value FROM app_settings WHERE key=?", (key,))
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str, is_secret: bool = False) -> None:
        """Insert or update a single setting."""
        await self.execute(
            """INSERT INTO app_settings (key, value, is_secret, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value,
                   is_secret=excluded.is_secret,
                   updated_at=datetime('now')
            """,
            (key, value, int(is_secret)),
        )

    async def get_all_settings(self) -> dict[str, dict[str, Any]]:
        """Return all settings as {key: {value, is_secret}}."""
        rows = await self.fetch_all("SELECT key, value, is_secret FROM app_settings")
        return {
            r["key"]: {"value": r["value"], "is_secret": bool(r["is_secret"])}
            for r in rows
        }

    # ------------------------------------------------------------------
    # Series Groups
    # ------------------------------------------------------------------

    async def upsert_series_group(
        self,
        root_anilist_id: int,
        display_title: str,
        entry_count: int,
    ) -> int:
        """Insert or update a series group. Returns the group id."""
        cursor = await self.execute(
            """INSERT INTO series_groups
                   (root_anilist_id, display_title, entry_count)
               VALUES (?, ?, ?)
               ON CONFLICT(root_anilist_id) DO UPDATE SET
                   display_title=excluded.display_title,
                   entry_count=excluded.entry_count,
                   updated_at=datetime('now')
            """,
            (root_anilist_id, display_title, entry_count),
        )
        # ON CONFLICT UPDATE does not set lastrowid; fetch the id explicitly
        row = await self.fetch_one(
            "SELECT id FROM series_groups WHERE root_anilist_id=?",
            (root_anilist_id,),
        )
        return row["id"] if row else (cursor.lastrowid or 0)

    async def get_series_group_by_root(
        self, root_anilist_id: int
    ) -> dict[str, Any] | None:
        """Return a series group by its root AniList ID."""
        return await self.fetch_one(
            "SELECT * FROM series_groups WHERE root_anilist_id=?",
            (root_anilist_id,),
        )

    async def get_series_group_by_anilist_id(
        self, anilist_id: int
    ) -> dict[str, Any] | None:
        """Return the series group that contains a given AniList ID."""
        return await self.fetch_one(
            """SELECT sg.* FROM series_groups sg
               JOIN series_group_entries sge ON sge.group_id = sg.id
               WHERE sge.anilist_id = ?""",
            (anilist_id,),
        )

    async def is_series_group_fresh(
        self, root_anilist_id: int, max_age_hours: int = 168
    ) -> bool:
        """Check if a series group was updated within max_age_hours."""
        row = await self.fetch_one(
            """SELECT updated_at FROM series_groups
               WHERE root_anilist_id = ?
               AND updated_at > datetime('now', ? || ' hours')""",
            (root_anilist_id, f"-{max_age_hours}"),
        )
        return row is not None

    async def upsert_series_group_entry(
        self,
        group_id: int,
        anilist_id: int,
        season_order: int,
        display_title: str = "",
        format: str = "",
        episodes: int | None = None,
        start_date: str = "",
    ) -> None:
        """Insert or update a single entry in a series group."""
        await self.execute(
            """INSERT INTO series_group_entries
                   (group_id, anilist_id, season_order, display_title,
                    format, episodes, start_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(group_id, anilist_id) DO UPDATE SET
                   season_order=excluded.season_order,
                   display_title=excluded.display_title,
                   format=excluded.format,
                   episodes=excluded.episodes,
                   start_date=excluded.start_date
            """,
            (
                group_id,
                anilist_id,
                season_order,
                display_title,
                format,
                episodes,
                start_date,
            ),
        )

    async def get_series_group_entries(self, group_id: int) -> list[dict[str, Any]]:
        """Return all entries in a series group, ordered by season_order."""
        return await self.fetch_all(
            "SELECT * FROM series_group_entries WHERE group_id=? ORDER BY season_order",
            (group_id,),
        )

    async def clear_series_group_entries(self, group_id: int) -> None:
        """Delete all entries for a series group (before re-populating)."""
        await self.execute(
            "DELETE FROM series_group_entries WHERE group_id=?",
            (group_id,),
        )

    # ------------------------------------------------------------------
    # Plex Media
    # ------------------------------------------------------------------

    async def upsert_plex_media(
        self,
        rating_key: str,
        title: str,
        year: int | None,
        thumb: str,
        summary: str,
        library_key: str,
        library_title: str,
        folder_name: str,
    ) -> None:
        """Insert or update a Plex media entry."""
        await self.execute(
            """INSERT INTO plex_media
                   (rating_key, title, year, thumb, summary,
                    library_key, library_title, folder_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(rating_key) DO UPDATE SET
                   title=excluded.title,
                   year=excluded.year,
                   thumb=excluded.thumb,
                   summary=excluded.summary,
                   library_key=excluded.library_key,
                   library_title=excluded.library_title,
                   folder_name=excluded.folder_name,
                   updated_at=datetime('now')
            """,
            (
                rating_key,
                title,
                year,
                thumb,
                summary,
                library_key,
                library_title,
                folder_name,
            ),
        )

    async def get_plex_media_with_mappings(
        self, library_key: str | None = None
    ) -> list[dict[str, Any]]:
        """Return plex_media LEFT JOINed with media_mappings and anilist_cache."""
        sql = """
            SELECT
                pm.rating_key, pm.title AS plex_title, pm.year AS plex_year,
                pm.thumb, pm.summary, pm.library_key, pm.library_title,
                pm.folder_name,
                mm.anilist_id, mm.match_confidence, mm.match_method,
                ac.title_romaji, ac.title_english, ac.cover_image
            FROM plex_media pm
            LEFT JOIN media_mappings mm
                ON mm.source = 'plex' AND mm.source_id = pm.rating_key
            LEFT JOIN anilist_cache ac
                ON ac.anilist_id = mm.anilist_id
                AND ac.expires_at > datetime('now')
        """
        params: tuple[Any, ...] = ()
        if library_key:
            sql += " WHERE pm.library_key = ?"
            params = (library_key,)
        sql += " ORDER BY pm.title COLLATE NOCASE"
        return await self.fetch_all(sql, params)

    async def get_plex_media_count(self) -> int:
        """Return the total number of plex_media rows."""
        row = await self.fetch_one("SELECT COUNT(*) as cnt FROM plex_media")
        return row["cnt"] if row else 0

    async def delete_mapping_by_source(self, source: str, source_id: str) -> None:
        """Delete a media_mappings row by source and source_id."""
        await self.execute(
            "DELETE FROM media_mappings WHERE source=? AND source_id=?",
            (source, source_id),
        )

    # ------------------------------------------------------------------
    # Restructure Log
    # ------------------------------------------------------------------

    async def log_restructure_operation(
        self,
        group_title: str,
        source_path: str,
        destination_path: str,
        operation: str = "move",
        status: str = "success",
        error_message: str = "",
    ) -> None:
        """Log a single file restructure operation."""
        await self.execute(
            """INSERT INTO restructure_log
                   (group_title, source_path, destination_path,
                    operation, status, error_message)
               VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                group_title,
                source_path,
                destination_path,
                operation,
                status,
                error_message,
            ),
        )

    async def get_restructure_log(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return recent restructure log entries."""
        return await self.fetch_all(
            "SELECT * FROM restructure_log ORDER BY executed_at DESC LIMIT ?",
            (limit,),
        )

    async def delete_plex_media_by_rating_key(self, rating_key: str) -> None:
        """Delete a single plex_media row and its media_mappings."""
        await self.db.execute(
            "DELETE FROM media_mappings WHERE source='plex'"
            " AND (source_id=? OR source_id LIKE ?)",
            (rating_key, f"{rating_key}:S%"),
        )
        await self.db.execute(
            "DELETE FROM plex_media WHERE rating_key=?",
            (rating_key,),
        )
        await self.db.commit()

    # ------------------------------------------------------------------
    # User Watchlist
    # ------------------------------------------------------------------

    async def upsert_watchlist_entry(
        self,
        user_id: str,
        anilist_id: int,
        list_status: str = "",
        progress: int = 0,
        score: float = 0.0,
        anilist_title: str = "",
        anilist_format: str = "",
        anilist_episodes: int | None = None,
        cover_image: str = "",
        airing_status: str = "",
        start_year: int | None = None,
    ) -> None:
        """Insert or update a single watchlist entry."""
        await self.execute(
            """INSERT INTO user_watchlist
                   (user_id, anilist_id, list_status, progress, score,
                    anilist_title, anilist_format, anilist_episodes,
                    cover_image, airing_status, start_year, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(user_id, anilist_id) DO UPDATE SET
                   list_status=excluded.list_status,
                   progress=excluded.progress,
                   score=excluded.score,
                   anilist_title=excluded.anilist_title,
                   anilist_format=excluded.anilist_format,
                   anilist_episodes=excluded.anilist_episodes,
                   cover_image=excluded.cover_image,
                   airing_status=excluded.airing_status,
                   start_year=excluded.start_year,
                   last_synced_at=datetime('now')
            """,
            (
                user_id,
                anilist_id,
                list_status,
                progress,
                score,
                anilist_title,
                anilist_format,
                anilist_episodes,
                cover_image,
                airing_status,
                start_year,
            ),
        )

    async def bulk_upsert_watchlist(
        self, user_id: str, entries: list[dict[str, Any]]
    ) -> int:
        """Bulk upsert watchlist entries. Returns number of rows processed."""
        for entry in entries:
            await self.upsert_watchlist_entry(
                user_id=user_id,
                anilist_id=entry["anilist_id"],
                list_status=entry.get("list_status", ""),
                progress=entry.get("progress", 0),
                score=entry.get("score", 0.0),
                anilist_title=entry.get("title", ""),
                anilist_format=entry.get("format", ""),
                anilist_episodes=entry.get("episodes"),
                cover_image=entry.get("cover_image", ""),
                airing_status=entry.get("airing_status", ""),
                start_year=entry.get("start_year"),
            )
        return len(entries)

    async def get_watchlist(
        self,
        user_id: str,
        list_statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return watchlist entries for a user, optionally filtered by status."""
        if list_statuses:
            placeholders = ",".join("?" for _ in list_statuses)
            return await self.fetch_all(
                f"SELECT * FROM user_watchlist WHERE user_id=?"
                f" AND list_status IN ({placeholders})"
                f" ORDER BY anilist_title COLLATE NOCASE",
                tuple([user_id] + list(list_statuses)),
            )
        return await self.fetch_all(
            "SELECT * FROM user_watchlist WHERE user_id=?"
            " ORDER BY anilist_title COLLATE NOCASE",
            (user_id,),
        )

    async def get_watchlist_entry(
        self, user_id: str, anilist_id: int
    ) -> dict[str, Any] | None:
        """Return a single watchlist entry."""
        return await self.fetch_one(
            "SELECT * FROM user_watchlist WHERE user_id=? AND anilist_id=?",
            (user_id, anilist_id),
        )

    async def clear_watchlist(self, user_id: str) -> None:
        """Delete all watchlist entries for a user."""
        await self.execute("DELETE FROM user_watchlist WHERE user_id=?", (user_id,))

    async def delete_plex_library_data(self, library_key: str) -> int:
        """Delete all plex_media and associated media_mappings for a library.

        Returns the number of plex_media rows deleted.
        sync_state rows cascade-delete via FK on media_mappings.
        """
        # Get all rating_keys for this library
        rows = await self.fetch_all(
            "SELECT rating_key FROM plex_media WHERE library_key=?",
            (library_key,),
        )
        rating_keys = [r["rating_key"] for r in rows]
        if not rating_keys:
            return 0

        # Delete media_mappings for these shows (including Structure B
        # season-level mappings like "12345:S2")
        for rk in rating_keys:
            await self.db.execute(
                "DELETE FROM media_mappings WHERE source='plex'"
                " AND (source_id=? OR source_id LIKE ?)",
                (rk, f"{rk}:S%"),
            )

        # Delete plex_media rows
        await self.db.execute(
            "DELETE FROM plex_media WHERE library_key=?",
            (library_key,),
        )

        await self.db.commit()
        return len(rating_keys)
