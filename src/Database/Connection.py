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
    ) -> int:
        cursor = await self.execute(
            """INSERT INTO media_mappings
                   (source, source_id, source_title, anilist_id, anilist_title,
                    match_confidence, match_method, media_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, source_id) DO UPDATE SET
                   anilist_id=excluded.anilist_id,
                   anilist_title=excluded.anilist_title,
                   match_confidence=excluded.match_confidence,
                   match_method=excluded.match_method,
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
    ) -> None:
        await self.execute(
            """INSERT INTO anilist_cache
                   (anilist_id, title_romaji, title_english, title_native,
                    episodes, cover_image, description, genres, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(anilist_id) DO UPDATE SET
                   title_romaji=excluded.title_romaji,
                   title_english=excluded.title_english,
                   title_native=excluded.title_native,
                   episodes=excluded.episodes,
                   cover_image=excluded.cover_image,
                   description=excluded.description,
                   genres=excluded.genres,
                   status=excluded.status,
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
