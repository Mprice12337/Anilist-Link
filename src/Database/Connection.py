"""SQLite/aiosqlite connection management."""

from __future__ import annotations

import json
import logging
import time
import uuid
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

    async def get_recent_activity(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent activity across all log tables, ordered by timestamp."""
        parts: list[str] = []
        # Guard for each table — only include if it exists
        try:
            await self.fetch_one("SELECT 1 FROM cr_sync_log LIMIT 1")
            parts.append(
                "SELECT 'cr_sync' AS type, show_title AS label, applied_at AS ts"
                " FROM cr_sync_log WHERE undone_at IS NULL"
            )
        except Exception:
            pass
        try:
            await self.fetch_one("SELECT 1 FROM restructure_log LIMIT 1")
            parts.append(
                "SELECT 'restructure' AS type, group_title AS label, executed_at AS ts"
                " FROM restructure_log"
            )
        except Exception:
            pass
        try:
            await self.fetch_one("SELECT 1 FROM download_requests LIMIT 1")
            parts.append(
                "SELECT 'download' AS type, anilist_title AS label, created_at AS ts"
                " FROM download_requests"
            )
        except Exception:
            pass
        if not parts:
            return []
        sql = (
            "SELECT type, label, ts FROM ("
            + " UNION ALL ".join(parts)
            + ") ORDER BY ts DESC LIMIT ?"
        )
        return await self.fetch_all(sql, (limit,))

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
                title_romaji or "",
                title_english or "",
                title_native or "",
                episodes,
                cover_image or "",
                description or "",
                genres or "[]",
                status or "",
                year or 0,
            ),
        )

    async def cleanup_expired_cache(self) -> int:
        cursor = await self.execute(
            "DELETE FROM anilist_cache WHERE expires_at <= datetime('now')"
        )
        return cursor.rowcount

    async def delete_cached_metadata(self, anilist_id: int) -> None:
        """Remove a single AniList cache entry so it is re-fetched on next use."""
        await self.execute(
            "DELETE FROM anilist_cache WHERE anilist_id=?", (anilist_id,)
        )

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
    # Notifications (persisted across page loads)
    # ------------------------------------------------------------------

    async def get_notifications(self) -> list[dict[str, Any]]:
        """Return all active (non-dismissed) notifications."""
        raw = await self.get_setting("notifications")
        if not raw:
            return []
        try:
            items = json.loads(raw)
            return [n for n in items if not n.get("dismissed")]
        except (json.JSONDecodeError, TypeError):
            return []

    async def add_notification(
        self,
        *,
        notification_type: str,
        message: str,
        action_url: str = "",
        action_label: str = "",
    ) -> str:
        """Add a persistent notification. Returns its id."""
        items = []
        raw = await self.get_setting("notifications")
        if raw:
            try:
                items = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                items = []

        nid = uuid.uuid4().hex[:12]
        items.append(
            {
                "id": nid,
                "type": notification_type,
                "message": message,
                "action_url": action_url,
                "action_label": action_label,
                "created_at": time.time(),
                "dismissed": False,
            }
        )
        await self.set_setting("notifications", json.dumps(items))
        return nid

    async def dismiss_notification(self, notification_id: str) -> bool:
        """Mark a notification as dismissed. Returns True if found."""
        raw = await self.get_setting("notifications")
        if not raw:
            return False
        try:
            items = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return False

        found = False
        for n in items:
            if n.get("id") == notification_id:
                n["dismissed"] = True
                found = True
        if found:
            await self.set_setting("notifications", json.dumps(items))
        return found

    async def dismiss_notifications_by_url(self, action_url: str) -> int:
        """Dismiss all notifications whose action_url matches. Returns count."""
        raw = await self.get_setting("notifications")
        if not raw:
            return 0
        try:
            items = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return 0

        count = 0
        for n in items:
            if not n.get("dismissed") and n.get("action_url") == action_url:
                n["dismissed"] = True
                count += 1
        if count:
            await self.set_setting("notifications", json.dumps(items))
        return count

    async def clear_dismissed_notifications(self) -> None:
        """Remove all dismissed notifications from storage."""
        raw = await self.get_setting("notifications")
        if not raw:
            return
        try:
            items = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        active = [n for n in items if not n.get("dismissed")]
        await self.set_setting("notifications", json.dumps(active))

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
        title_romaji: str = "",
        title_english: str = "",
        format: str = "",
        episodes: int | None = None,
        start_date: str = "",
    ) -> None:
        """Insert or update a single entry in a series group."""
        await self.execute(
            """INSERT INTO series_group_entries
                   (group_id, anilist_id, season_order, display_title,
                    title_romaji, title_english, format, episodes, start_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(group_id, anilist_id) DO UPDATE SET
                   season_order=excluded.season_order,
                   display_title=excluded.display_title,
                   title_romaji=excluded.title_romaji,
                   title_english=excluded.title_english,
                   format=excluded.format,
                   episodes=excluded.episodes,
                   start_date=excluded.start_date
            """,
            (
                group_id,
                anilist_id,
                season_order,
                display_title,
                title_romaji,
                title_english,
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

    async def get_series_group_entries_with_titles(
        self, group_id: int
    ) -> list[dict[str, Any]]:
        """Return series group entries enriched with title_romaji/title_english
        from anilist_cache.  Used by seed_library_items so that
        _match_subdir_to_entry has full titles for fuzzy matching, not just
        the abbreviated display_title stored in series_group_entries.
        """
        return await self.fetch_all(
            """SELECT sge.*,
                      ac.title_romaji, ac.title_english
               FROM series_group_entries sge
               LEFT JOIN anilist_cache ac
                   ON ac.anilist_id = sge.anilist_id
                  AND ac.expires_at > datetime('now')
               WHERE sge.group_id = ?
               ORDER BY sge.season_order""",
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
                ac.title_romaji, ac.title_english, ac.cover_image,
                ac.episodes, ac.status AS anilist_status, ac.year AS anilist_year
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
        plan_id: int | None = None,
    ) -> None:
        """Log a single file restructure operation."""
        await self.execute(
            """INSERT INTO restructure_log
                   (group_title, source_path, destination_path,
                    operation, status, error_message, plan_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_title,
                source_path,
                destination_path,
                operation,
                status,
                error_message,
                plan_id,
            ),
        )

    async def get_restructure_log(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return recent restructure log entries."""
        return await self.fetch_all(
            "SELECT * FROM restructure_log ORDER BY executed_at DESC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Restructure Plans
    # ------------------------------------------------------------------

    async def save_restructure_plan(
        self,
        source_dirs: str,
        output_dir: str,
        level: str,
        file_template: str,
        folder_template: str,
        season_folder_template: str,
        movie_file_template: str,
        title_pref: str,
        illegal_char_replacement: str,
        group_count: int,
        file_count: int,
        plan_summary: str,
        status: str = "planned",
    ) -> int:
        """Persist a restructure plan (before or after execution). Returns plan id."""
        cursor = await self.execute(
            """INSERT INTO restructure_plans
                   (source_dirs, output_dir, level,
                    file_template, folder_template, season_folder_template,
                    movie_file_template, title_pref, illegal_char_replacement,
                    group_count, file_count, plan_summary, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_dirs,
                output_dir,
                level,
                file_template,
                folder_template,
                season_folder_template,
                movie_file_template,
                title_pref,
                illegal_char_replacement,
                group_count,
                file_count,
                plan_summary,
                status,
            ),
        )
        return cursor.lastrowid or 0

    async def update_restructure_plan_status(
        self,
        plan_id: int,
        status: str,
        applied_at: str | None = None,
    ) -> None:
        """Update the status of a restructure plan (e.g. to 'applied')."""
        if applied_at:
            await self.execute(
                "UPDATE restructure_plans SET status=?, applied_at=? WHERE id=?",
                (status, applied_at, plan_id),
            )
        else:
            await self.execute(
                "UPDATE restructure_plans SET status=? WHERE id=?",
                (status, plan_id),
            )

    async def get_restructure_plans(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent restructure plans, newest first."""
        return await self.fetch_all(
            "SELECT * FROM restructure_plans ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def get_restructure_plan(self, plan_id: int) -> dict[str, Any] | None:
        """Return a single restructure plan by id."""
        return await self.fetch_one(
            "SELECT * FROM restructure_plans WHERE id=?",
            (plan_id,),
        )

    async def get_restructure_log_for_plan(
        self, plan_id: int, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Return restructure_log entries linked to a specific plan."""
        return await self.fetch_all(
            "SELECT * FROM restructure_log"
            " WHERE plan_id=? ORDER BY executed_at DESC LIMIT ?",
            (plan_id, limit),
        )

    # ------------------------------------------------------------------
    # Libraries
    # ------------------------------------------------------------------

    async def create_library(self, name: str, paths: str) -> int:
        """Create a new library. *paths* is a JSON array string. Returns id."""
        cursor = await self.execute(
            "INSERT INTO libraries (name, paths) VALUES (?, ?)",
            (name, paths),
        )
        return cursor.lastrowid or 0

    async def update_library(self, library_id: int, name: str, paths: str) -> None:
        """Update a library's name and paths."""
        await self.execute(
            "UPDATE libraries SET name=?, paths=?,"
            " updated_at=datetime('now') WHERE id=?",
            (name, paths, library_id),
        )

    async def delete_library(self, library_id: int) -> None:
        """Delete a library and cascade-delete its items."""
        await self.execute("DELETE FROM libraries WHERE id=?", (library_id,))

    async def get_library(self, library_id: int) -> dict[str, Any] | None:
        return await self.fetch_one("SELECT * FROM libraries WHERE id=?", (library_id,))

    async def get_all_libraries(self) -> list[dict[str, Any]]:
        return await self.fetch_all(
            "SELECT * FROM libraries ORDER BY name COLLATE NOCASE"
        )

    # ------------------------------------------------------------------
    # Library Items
    # ------------------------------------------------------------------

    async def upsert_library_item(
        self,
        library_id: int,
        folder_path: str,
        folder_name: str,
        anilist_id: int | None = None,
        anilist_title: str = "",
        match_confidence: float = 0.0,
        match_method: str = "",
        anilist_format: str = "",
        anilist_episodes: int | None = None,
        year: int = 0,
        cover_image: str = "",
        series_group_id: int | None = None,
    ) -> int:
        """Insert or update a library item. Returns the row id."""
        cursor = await self.execute(
            """INSERT INTO library_items
                   (library_id, folder_path, folder_name, anilist_id,
                    anilist_title, match_confidence, match_method,
                    anilist_format, anilist_episodes, year, cover_image,
                    series_group_id, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(library_id, folder_path) DO UPDATE SET
                   folder_name=excluded.folder_name,
                   anilist_id=excluded.anilist_id,
                   anilist_title=excluded.anilist_title,
                   match_confidence=excluded.match_confidence,
                   match_method=excluded.match_method,
                   anilist_format=excluded.anilist_format,
                   anilist_episodes=excluded.anilist_episodes,
                   year=excluded.year,
                   cover_image=excluded.cover_image,
                   series_group_id=excluded.series_group_id,
                   scanned_at=datetime('now')
            """,
            (
                library_id,
                folder_path,
                folder_name,
                anilist_id,
                anilist_title,
                match_confidence,
                match_method,
                anilist_format,
                anilist_episodes,
                year,
                cover_image,
                series_group_id,
            ),
        )
        return cursor.lastrowid or 0

    async def get_library_items_with_cache(
        self, library_id: int
    ) -> list[dict[str, Any]]:
        """Return library items LEFT JOINed with anilist_cache for rich display."""
        return await self.fetch_all(
            """SELECT
                   li.*,
                   COALESCE(ac.cover_image, li.cover_image) AS display_cover,
                   ac.title_romaji, ac.title_english, ac.title_native,
                   ac.description, ac.genres, ac.status AS anilist_status,
                   ac.episodes, ac.year AS anilist_year
               FROM library_items li
               LEFT JOIN anilist_cache ac
                   ON ac.anilist_id = li.anilist_id
                   AND ac.expires_at > datetime('now')
               WHERE li.library_id = ?
               ORDER BY li.folder_name COLLATE NOCASE
            """,
            (library_id,),
        )

    async def get_library_item_folder_paths(self, library_id: int) -> set[str]:
        """Return all folder_path values for a library (for change detection)."""
        rows = await self.fetch_all(
            "SELECT folder_path FROM library_items WHERE library_id=?",
            (library_id,),
        )
        return {r["folder_path"] for r in rows}

    async def delete_library_items_not_in(
        self, library_id: int, folder_paths: set[str]
    ) -> int:
        """Delete items whose folder_path is not in the given set. Returns count."""
        if not folder_paths:
            cursor = await self.execute(
                "DELETE FROM library_items WHERE library_id=?",
                (library_id,),
            )
            return cursor.rowcount
        all_rows = await self.fetch_all(
            "SELECT id, folder_path FROM library_items WHERE library_id=?",
            (library_id,),
        )
        to_delete = [r["id"] for r in all_rows if r["folder_path"] not in folder_paths]
        if not to_delete:
            return 0
        placeholders = ",".join("?" for _ in to_delete)
        cursor = await self.execute(
            f"DELETE FROM library_items WHERE id IN ({placeholders})",
            tuple(to_delete),
        )
        return cursor.rowcount

    async def update_library_item_match(
        self,
        item_id: int,
        anilist_id: int,
        anilist_title: str = "",
        match_confidence: float = 1.0,
        match_method: str = "manual",
        cover_image: str = "",
        anilist_format: str = "",
        anilist_episodes: int | None = None,
        year: int = 0,
    ) -> None:
        """Set or update the AniList match for a library item."""
        await self.execute(
            """UPDATE library_items SET
                   anilist_id=?, anilist_title=?, match_confidence=?,
                   match_method=?, cover_image=?, anilist_format=?,
                   anilist_episodes=?, year=?, scanned_at=datetime('now')
               WHERE id=?
            """,
            (
                anilist_id,
                anilist_title,
                match_confidence,
                match_method,
                cover_image,
                anilist_format,
                anilist_episodes,
                year,
                item_id,
            ),
        )

    async def clear_library_item_match(self, item_id: int) -> None:
        """Remove the AniList match from a library item."""
        await self.execute(
            """UPDATE library_items SET
                   anilist_id=NULL, anilist_title='', match_confidence=0.0,
                   match_method='', cover_image='', anilist_format='',
                   anilist_episodes=NULL, year=0, series_group_id=NULL
               WHERE id=?
            """,
            (item_id,),
        )

    async def get_library_item_counts(self, library_id: int) -> dict[str, int]:
        """Return total, matched, and unmatched counts for a library."""
        row = await self.fetch_one(
            """SELECT
                   COUNT(*) AS total,
                   SUM(CASE WHEN anilist_id IS NOT NULL THEN 1 ELSE 0 END) AS matched
               FROM library_items WHERE library_id=?
            """,
            (library_id,),
        )
        if not row:
            return {"total": 0, "matched": 0, "unmatched": 0}
        total = row["total"] or 0
        matched = row["matched"] or 0
        return {"total": total, "matched": matched, "unmatched": total - matched}

    async def get_library_item(self, item_id: int) -> dict[str, Any] | None:
        """Return a single library item by id."""
        return await self.fetch_one(
            "SELECT * FROM library_items WHERE id=?", (item_id,)
        )

    async def find_anilist_match_by_folder(
        self, folder_name: str, exclude_source: str = ""
    ) -> dict[str, Any] | None:
        """Find an existing AniList match for a folder name across all sources.

        Checks (in order):
        1. library_items — populated by restructure/local scans
        2. plex_media JOIN media_mappings (skipped when exclude_source='plex')
        3. jellyfin_media JOIN media_mappings (skipped when exclude_source='jellyfin')

        Returns the first match with anilist_id, anilist_title, match_confidence,
        match_method, and series_group_id — or None.
        """
        if not folder_name:
            return None

        # 1. library_items (local library, populated by restructure seeding)
        row = await self.fetch_one(
            """SELECT anilist_id, anilist_title, match_confidence,
                      match_method, series_group_id
               FROM library_items
               WHERE folder_name = ? AND anilist_id IS NOT NULL AND anilist_id > 0
               ORDER BY match_confidence DESC
               LIMIT 1""",
            (folder_name,),
        )
        if row:
            return dict(row)

        # 2. Plex cross-reference
        if exclude_source != "plex":
            row = await self.fetch_one(
                """SELECT mm.anilist_id, mm.anilist_title, mm.match_confidence,
                          mm.match_method, mm.series_group_id
                   FROM plex_media pm
                   JOIN media_mappings mm
                       ON mm.source = 'plex' AND mm.source_id = pm.rating_key
                   WHERE pm.folder_name = ? AND mm.anilist_id > 0
                   ORDER BY mm.match_confidence DESC
                   LIMIT 1""",
                (folder_name,),
            )
            if row:
                return dict(row)

        # 3. Jellyfin cross-reference
        if exclude_source != "jellyfin":
            row = await self.fetch_one(
                """SELECT mm.anilist_id, mm.anilist_title, mm.match_confidence,
                          mm.match_method, mm.series_group_id
                   FROM jellyfin_media jm
                   JOIN media_mappings mm
                       ON mm.source = 'jellyfin' AND mm.source_id = jm.item_id
                   WHERE jm.folder_name = ? AND mm.anilist_id > 0
                   ORDER BY mm.match_confidence DESC
                   LIMIT 1""",
                (folder_name,),
            )
            if row:
                return dict(row)

        return None

    # ------------------------------------------------------------------
    # Plex Media (continued)
    # ------------------------------------------------------------------

    async def get_plex_matches_for_folder_names(
        self, folder_names: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Return Plex media info keyed by folder_name.

        Queries plex_media LEFT JOIN media_mappings. Each value dict
        contains rating_key, plex_title, and existing mapping data.
        """
        if not folder_names:
            return {}
        placeholders = ",".join("?" for _ in folder_names)
        rows = await self.fetch_all(
            f"""SELECT
                    pm.folder_name, pm.rating_key, pm.title AS plex_title,
                    mm.anilist_id AS mapping_anilist_id,
                    mm.match_confidence AS mapping_confidence,
                    mm.match_method AS mapping_method
                FROM plex_media pm
                LEFT JOIN media_mappings mm
                    ON mm.source = 'plex' AND mm.source_id = pm.rating_key
                WHERE pm.folder_name IN ({placeholders})
            """,
            tuple(folder_names),
        )
        result: dict[str, dict[str, Any]] = {}
        for r in rows:
            result[r["folder_name"]] = dict(r)
        return result

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
    # Jellyfin Media
    # ------------------------------------------------------------------

    async def upsert_jellyfin_media(
        self,
        item_id: str,
        title: str,
        year: int | None,
        path: str,
        library_id: str,
        library_name: str,
        folder_name: str,
    ) -> None:
        """Insert or update a Jellyfin media entry."""
        await self.execute(
            """INSERT INTO jellyfin_media
                   (item_id, title, year, path, library_id, library_name, folder_name)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(item_id) DO UPDATE SET
                   title=excluded.title,
                   year=excluded.year,
                   path=excluded.path,
                   library_id=excluded.library_id,
                   library_name=excluded.library_name,
                   folder_name=excluded.folder_name,
                   updated_at=datetime('now')
            """,
            (item_id, title, year, path, library_id, library_name, folder_name),
        )

    async def get_jellyfin_media_with_mappings(
        self, library_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return jellyfin_media LEFT JOINed with media_mappings and anilist_cache."""
        sql = """
            SELECT
                jm.item_id, jm.title AS jellyfin_title, jm.year AS jellyfin_year,
                jm.path, jm.library_id, jm.library_name, jm.folder_name,
                mm.anilist_id, mm.match_confidence, mm.match_method,
                ac.title_romaji, ac.title_english, ac.cover_image,
                ac.episodes, ac.status AS anilist_status, ac.year AS anilist_year
            FROM jellyfin_media jm
            LEFT JOIN media_mappings mm
                ON mm.source = 'jellyfin' AND mm.source_id = jm.item_id
            LEFT JOIN anilist_cache ac
                ON ac.anilist_id = mm.anilist_id
                AND ac.expires_at > datetime('now')
        """
        params: tuple[Any, ...] = ()
        if library_id:
            sql += " WHERE jm.library_id = ?"
            params = (library_id,)
        sql += " ORDER BY jm.title COLLATE NOCASE"
        return await self.fetch_all(sql, params)

    async def get_jellyfin_media_count(self) -> int:
        """Return the total number of jellyfin_media rows."""
        row = await self.fetch_one("SELECT COUNT(*) as cnt FROM jellyfin_media")
        return row["cnt"] if row else 0

    async def get_jellyfin_matches_for_folder_names(
        self, folder_names: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Return Jellyfin media info keyed by folder_name."""
        if not folder_names:
            return {}
        placeholders = ",".join("?" for _ in folder_names)
        rows = await self.fetch_all(
            f"""SELECT
                    jm.folder_name, jm.item_id, jm.title AS jellyfin_title,
                    mm.anilist_id AS mapping_anilist_id,
                    mm.match_confidence AS mapping_confidence,
                    mm.match_method AS mapping_method
                FROM jellyfin_media jm
                LEFT JOIN media_mappings mm
                    ON mm.source = 'jellyfin' AND mm.source_id = jm.item_id
                WHERE jm.folder_name IN ({placeholders})
            """,
            tuple(folder_names),
        )
        result: dict[str, dict[str, Any]] = {}
        for r in rows:
            result[r["folder_name"]] = dict(r)
        return result

    async def delete_jellyfin_library_data(self, library_id: str) -> int:
        """Delete all jellyfin_media and associated media_mappings for a library."""
        rows = await self.fetch_all(
            "SELECT item_id FROM jellyfin_media WHERE library_id=?",
            (library_id,),
        )
        item_ids = [r["item_id"] for r in rows]
        if not item_ids:
            return 0

        for iid in item_ids:
            await self.db.execute(
                "DELETE FROM media_mappings WHERE source='jellyfin' AND source_id=?",
                (iid,),
            )

        await self.db.execute(
            "DELETE FROM jellyfin_media WHERE library_id=?",
            (library_id,),
        )
        await self.db.commit()
        return len(item_ids)

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
        title_romaji: str = "",
        title_english: str = "",
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
                    anilist_title, title_romaji, title_english,
                    anilist_format, anilist_episodes,
                    cover_image, airing_status, start_year, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(user_id, anilist_id) DO UPDATE SET
                   list_status=excluded.list_status,
                   progress=excluded.progress,
                   score=excluded.score,
                   anilist_title=excluded.anilist_title,
                   title_romaji=excluded.title_romaji,
                   title_english=excluded.title_english,
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
                title_romaji,
                title_english,
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
                title_romaji=entry.get("title_romaji", ""),
                title_english=entry.get("title_english", ""),
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

    # ------------------------------------------------------------------
    # Crunchyroll Preview
    # ------------------------------------------------------------------

    async def insert_cr_preview_rows(self, rows: list[dict[str, Any]]) -> None:
        """Bulk insert cr_sync_preview rows."""
        for row in rows:
            await self.execute(
                """INSERT INTO cr_sync_preview
                       (user_id, run_id, cr_title, anilist_id, anilist_title,
                        confidence, proposed_status, proposed_progress,
                        current_status, current_progress, action, episodes_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["user_id"],
                    row["run_id"],
                    row["cr_title"],
                    row["anilist_id"],
                    row["anilist_title"],
                    row["confidence"],
                    row["proposed_status"],
                    row["proposed_progress"],
                    row["current_status"],
                    row["current_progress"],
                    row["action"],
                    row.get("episodes_json", "[]"),
                ),
            )

    async def get_cr_preview_run(self, run_id: str) -> list[dict[str, Any]]:
        """Return all cr_sync_preview rows for a run_id."""
        return await self.fetch_all(
            "SELECT * FROM cr_sync_preview WHERE run_id=? ORDER BY cr_title",
            (run_id,),
        )

    async def get_cr_preview_runs(self, user_id: str) -> list[dict[str, Any]]:
        """Return distinct runs for a user, most recent first."""
        return await self.fetch_all(
            """SELECT run_id, MIN(created_at) AS created_at,
                      COUNT(*) AS entry_count,
                      SUM(approved) AS approved_count
               FROM cr_sync_preview WHERE user_id=?
               GROUP BY run_id ORDER BY created_at DESC""",
            (user_id,),
        )

    async def get_latest_cr_preview_run_id(self, user_id: str) -> str | None:
        """Return the most recent run_id for a user."""
        row = await self.fetch_one(
            """SELECT run_id FROM cr_sync_preview WHERE user_id=?
               ORDER BY created_at DESC LIMIT 1""",
            (user_id,),
        )
        return row["run_id"] if row else None

    async def update_cr_preview_entry(
        self,
        entry_id: int,
        anilist_id: int,
        anilist_title: str,
        confidence: float,
        proposed_status: str,
        proposed_progress: int,
        action: str,
    ) -> None:
        """Update a preview entry after manual rematch."""
        await self.execute(
            """UPDATE cr_sync_preview
               SET anilist_id=?, anilist_title=?, confidence=?,
                   proposed_status=?, proposed_progress=?, action=?
               WHERE id=?
            """,
            (
                anilist_id,
                anilist_title,
                confidence,
                proposed_status,
                proposed_progress,
                action,
                entry_id,
            ),
        )

    async def set_cr_preview_approved(
        self, entry_ids: list[int], approved: bool
    ) -> None:
        """Bulk approve/unapprove preview entries."""
        if not entry_ids:
            return
        placeholders = ",".join("?" for _ in entry_ids)
        await self.execute(
            f"UPDATE cr_sync_preview SET approved=? WHERE id IN ({placeholders})",
            (int(approved), *entry_ids),
        )

    # ------------------------------------------------------------------
    # Crunchyroll Sync Log
    # ------------------------------------------------------------------

    async def insert_cr_sync_log_entry(
        self,
        user_id: str,
        anilist_id: int,
        show_title: str,
        before_status: str,
        before_progress: int,
        after_status: str,
        after_progress: int,
        sync_run_id: str,
        cr_sync_preview_id: int | None = None,
    ) -> int:
        """Insert one cr_sync_log row. Returns the new row id."""
        cursor = await self.execute(
            """INSERT INTO cr_sync_log
                   (user_id, anilist_id, show_title, before_status,
                    before_progress, after_status, after_progress,
                    sync_run_id, cr_sync_preview_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                anilist_id,
                show_title,
                before_status,
                before_progress,
                after_status,
                after_progress,
                sync_run_id,
                cr_sync_preview_id,
            ),
        )
        return cursor.lastrowid or 0

    async def get_cr_sync_log(
        self, user_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Return recent cr_sync_log entries, newest first."""
        if user_id:
            return await self.fetch_all(
                "SELECT * FROM cr_sync_log WHERE user_id=?"
                " ORDER BY applied_at DESC LIMIT ?",
                (user_id, limit),
            )
        return await self.fetch_all(
            "SELECT * FROM cr_sync_log ORDER BY applied_at DESC LIMIT ?",
            (limit,),
        )

    async def get_cr_sync_log_entry(self, log_id: int) -> dict[str, Any] | None:
        """Return a single cr_sync_log row."""
        return await self.fetch_one("SELECT * FROM cr_sync_log WHERE id=?", (log_id,))

    async def mark_cr_sync_log_undone(self, log_id: int) -> None:
        """Set undone_at timestamp on a log entry."""
        await self.execute(
            "UPDATE cr_sync_log SET undone_at=datetime('now') WHERE id=?",
            (log_id,),
        )

    # ------------------------------------------------------------------
    # Watch Sync Log (Plex / Jellyfin)
    # ------------------------------------------------------------------

    async def insert_watch_sync_log_entry(
        self,
        source: str,
        user_id: str,
        anilist_id: int,
        show_title: str,
        before_status: str,
        before_progress: int,
        after_status: str,
        after_progress: int,
    ) -> int:
        """Insert one watch_sync_log row. Returns the new row id."""
        cursor = await self.execute(
            """INSERT INTO watch_sync_log
                   (source, user_id, anilist_id, show_title, before_status,
                    before_progress, after_status, after_progress)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source,
                user_id,
                anilist_id,
                show_title,
                before_status,
                before_progress,
                after_status,
                after_progress,
            ),
        )
        await self.db.commit()
        return cursor.lastrowid or 0

    async def get_watch_sync_log(
        self,
        source: str | None = None,
        user_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return recent watch_sync_log entries, newest first."""
        if source and user_id:
            return await self.fetch_all(
                "SELECT * FROM watch_sync_log WHERE source=? AND user_id=?"
                " ORDER BY applied_at DESC LIMIT ?",
                (source, user_id, limit),
            )
        if source:
            return await self.fetch_all(
                "SELECT * FROM watch_sync_log WHERE source=?"
                " ORDER BY applied_at DESC LIMIT ?",
                (source, limit),
            )
        if user_id:
            return await self.fetch_all(
                "SELECT * FROM watch_sync_log WHERE user_id=?"
                " ORDER BY applied_at DESC LIMIT ?",
                (user_id, limit),
            )
        return await self.fetch_all(
            "SELECT * FROM watch_sync_log ORDER BY applied_at DESC LIMIT ?",
            (limit,),
        )

    async def get_watch_sync_log_entry(self, log_id: int) -> dict[str, Any] | None:
        """Return a single watch_sync_log row."""
        return await self.fetch_one(
            "SELECT * FROM watch_sync_log WHERE id=?", (log_id,)
        )

    async def mark_watch_sync_log_undone(self, log_id: int) -> None:
        """Set undone_at timestamp on a watch_sync_log entry."""
        await self.execute(
            "UPDATE watch_sync_log SET undone_at=datetime('now') WHERE id=?",
            (log_id,),
        )
        await self.db.commit()

    # ------------------------------------------------------------------
    # Download Requests (P4)
    # ------------------------------------------------------------------

    async def create_download_request(
        self,
        anilist_id: int,
        anilist_title: str,
        service: str,
        external_id: int | None,
        tvdb_id: int | None,
        tmdb_id: int | None,
        status: str,
        error_message: str,
        quality_profile_id: int | None,
        root_folder: str,
        requested_by: str,
        executed_at: str | None,
    ) -> int:
        """Insert a download request record and return its row ID."""
        cursor = await self.execute(
            """
            INSERT INTO download_requests
                (anilist_id, anilist_title, service, external_id, tvdb_id, tmdb_id,
                 status, error_message, quality_profile_id, root_folder,
                 requested_by, executed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                anilist_id,
                anilist_title,
                service,
                external_id,
                tvdb_id,
                tmdb_id,
                status,
                error_message,
                quality_profile_id,
                root_folder,
                requested_by,
                executed_at,
            ),
        )
        return cursor.lastrowid or 0

    async def get_download_requests(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent download requests, newest first."""
        return await self.fetch_all(
            "SELECT * FROM download_requests ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def get_download_request(self, req_id: int) -> dict[str, Any] | None:
        """Return a single download request by ID."""
        return await self.fetch_one(
            "SELECT * FROM download_requests WHERE id=?", (req_id,)
        )

    # ------------------------------------------------------------------
    # Sonarr / Radarr mapping methods
    # ------------------------------------------------------------------

    async def upsert_sonarr_mapping(
        self,
        anilist_id: int,
        **kwargs: Any,
    ) -> None:
        """Insert or replace an anilist_sonarr_mapping row."""
        fields = [
            "series_group_id",
            "tvdb_id",
            "sonarr_id",
            "sonarr_title",
            "sonarr_season",
            "episode_offset",
            "is_absolute_numbering",
            "in_sonarr",
            "sonarr_monitored",
            "sonarr_root_folder",
            "confidence",
            "confirmed",
            "last_verified_at",
        ]
        now = (
            kwargs.pop("updated_at", None)
            or __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat()
        )
        set_clause = ", ".join(f"{f} = ?" for f in fields if f in kwargs)
        set_clause += ", updated_at = ?"
        values = [kwargs[f] for f in fields if f in kwargs] + [now]

        existing = await self.fetch_one(
            "SELECT id FROM anilist_sonarr_mapping WHERE anilist_id = ?", (anilist_id,)
        )
        if existing:
            await self.execute(
                f"UPDATE anilist_sonarr_mapping SET {set_clause} WHERE anilist_id = ?",
                (*values, anilist_id),
            )
        else:
            col_names = ", ".join(f for f in fields if f in kwargs)
            placeholders = ", ".join("?" for f in fields if f in kwargs)
            await self.execute(
                f"INSERT INTO anilist_sonarr_mapping"
                f" (anilist_id, {col_names}, created_at, updated_at)"
                f" VALUES (?, {placeholders}, ?, ?)",
                (anilist_id, *[kwargs[f] for f in fields if f in kwargs], now, now),
            )
        await self.db.commit()

    async def get_sonarr_mapping(self, anilist_id: int) -> dict[str, Any] | None:
        """Return the sonarr mapping for an AniList entry, or None."""
        return await self.fetch_one(
            "SELECT * FROM anilist_sonarr_mapping WHERE anilist_id = ?",
            (anilist_id,),
        )

    async def get_all_sonarr_mappings(
        self, confirmed_only: bool = False
    ) -> list[dict[str, Any]]:
        """Return all sonarr mappings, optionally only confirmed ones."""
        if confirmed_only:
            return await self.fetch_all(
                "SELECT * FROM anilist_sonarr_mapping WHERE confirmed = 1"
                " ORDER BY anilist_id"
            )
        return await self.fetch_all(
            "SELECT * FROM anilist_sonarr_mapping ORDER BY anilist_id"
        )

    async def get_unconfirmed_sonarr_mappings(self) -> list[dict[str, Any]]:
        """Return mappings that need user confirmation."""
        return await self.fetch_all(
            "SELECT * FROM anilist_sonarr_mapping"
            " WHERE confirmed = 0 AND in_sonarr = 1"
            " ORDER BY confidence DESC, anilist_id"
        )

    async def confirm_sonarr_mapping(self, anilist_id: int) -> None:
        """Mark a sonarr mapping as confirmed."""
        await self.execute(
            "UPDATE anilist_sonarr_mapping SET confirmed = 1,"
            " updated_at = datetime('now') WHERE anilist_id = ?",
            (anilist_id,),
        )
        await self.db.commit()

    async def upsert_radarr_mapping(
        self,
        anilist_id: int,
        **kwargs: Any,
    ) -> None:
        """Insert or replace an anilist_radarr_mapping row."""
        fields = [
            "tmdb_id",
            "radarr_id",
            "radarr_title",
            "in_radarr",
            "radarr_monitored",
            "radarr_root_folder",
            "confidence",
            "confirmed",
            "last_verified_at",
        ]
        now = (
            __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat()
        )
        set_clause = ", ".join(f"{f} = ?" for f in fields if f in kwargs)
        set_clause += ", updated_at = ?"
        values = [kwargs[f] for f in fields if f in kwargs] + [now]

        existing = await self.fetch_one(
            "SELECT id FROM anilist_radarr_mapping WHERE anilist_id = ?", (anilist_id,)
        )
        if existing:
            await self.execute(
                f"UPDATE anilist_radarr_mapping SET {set_clause} WHERE anilist_id = ?",
                (*values, anilist_id),
            )
        else:
            col_names = ", ".join(f for f in fields if f in kwargs)
            placeholders = ", ".join("?" for f in fields if f in kwargs)
            await self.execute(
                f"INSERT INTO anilist_radarr_mapping"
                f" (anilist_id, {col_names}, created_at, updated_at)"
                f" VALUES (?, {placeholders}, ?, ?)",
                (anilist_id, *[kwargs[f] for f in fields if f in kwargs], now, now),
            )
        await self.db.commit()

    async def get_radarr_mapping(self, anilist_id: int) -> dict[str, Any] | None:
        """Return the radarr mapping for an AniList entry, or None."""
        return await self.fetch_one(
            "SELECT * FROM anilist_radarr_mapping WHERE anilist_id = ?",
            (anilist_id,),
        )

    # ------------------------------------------------------------------
    # Sonarr / Radarr cache methods
    # ------------------------------------------------------------------

    async def upsert_sonarr_cache_entry(
        self,
        tvdb_id: int,
        sonarr_id: int,
        title: str,
        sort_title: str = "",
        status: str = "",
        monitored: bool = False,
        root_folder: str = "",
        quality_profile_id: int | None = None,
        alternate_titles: str = "[]",
        seasons_json: str = "[]",
    ) -> None:
        """Insert or replace a sonarr_series_cache entry."""
        await self.execute(
            """
            INSERT INTO sonarr_series_cache
                (tvdb_id, sonarr_id, title, sort_title, status, monitored,
                 root_folder, quality_profile_id, alternate_titles,
                 seasons_json, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(tvdb_id) DO UPDATE SET
                sonarr_id = excluded.sonarr_id,
                title = excluded.title,
                sort_title = excluded.sort_title,
                status = excluded.status,
                monitored = excluded.monitored,
                root_folder = excluded.root_folder,
                quality_profile_id = excluded.quality_profile_id,
                alternate_titles = excluded.alternate_titles,
                seasons_json = excluded.seasons_json,
                last_updated = datetime('now')
            """,
            (
                tvdb_id,
                sonarr_id,
                title,
                sort_title,
                status,
                int(monitored),
                root_folder,
                quality_profile_id,
                alternate_titles,
                seasons_json,
            ),
        )
        await self.db.commit()

    async def bulk_upsert_sonarr_cache(self, series_list: list[dict[str, Any]]) -> int:
        """Bulk-upsert sonarr_series_cache from a Sonarr /api/v3/series response.

        Returns the number of entries upserted.
        """
        import json

        count = 0
        for item in series_list:
            tvdb_id = item.get("tvdbId")
            sonarr_id = item.get("id")
            if not tvdb_id or not sonarr_id:
                continue
            alt_titles = json.dumps(
                [
                    t.get("title", "")
                    for t in item.get("alternateTitles", [])
                    if t.get("title")
                ]
            )
            seasons = json.dumps(
                [
                    {
                        "seasonNumber": s.get("seasonNumber", 0),
                        "monitored": s.get("monitored", False),
                        "episodeCount": (s.get("statistics") or {}).get(
                            "episodeCount", 0
                        ),
                        "totalEpisodeCount": (s.get("statistics") or {}).get(
                            "totalEpisodeCount", 0
                        ),
                    }
                    for s in item.get("seasons", [])
                ]
            )
            await self.upsert_sonarr_cache_entry(
                tvdb_id=tvdb_id,
                sonarr_id=sonarr_id,
                title=item.get("title", ""),
                sort_title=item.get("sortTitle", ""),
                status=item.get("status", ""),
                monitored=item.get("monitored", False),
                root_folder=item.get("rootFolderPath", ""),
                quality_profile_id=item.get("qualityProfileId"),
                alternate_titles=alt_titles,
                seasons_json=seasons,
            )
            count += 1
        return count

    async def get_sonarr_cache_entry(self, tvdb_id: int) -> dict[str, Any] | None:
        """Return a sonarr_series_cache entry by TVDB ID."""
        return await self.fetch_one(
            "SELECT * FROM sonarr_series_cache WHERE tvdb_id = ?", (tvdb_id,)
        )

    async def get_sonarr_cache_by_sonarr_id(
        self, sonarr_id: int
    ) -> dict[str, Any] | None:
        """Return a sonarr_series_cache entry by Sonarr ID."""
        return await self.fetch_one(
            "SELECT * FROM sonarr_series_cache WHERE sonarr_id = ?", (sonarr_id,)
        )

    async def get_all_sonarr_cache(self) -> list[dict[str, Any]]:
        """Return all cached Sonarr series."""
        return await self.fetch_all("SELECT * FROM sonarr_series_cache ORDER BY title")

    async def clear_sonarr_cache(self) -> None:
        """Remove all sonarr_series_cache entries."""
        await self.execute("DELETE FROM sonarr_series_cache")
        await self.db.commit()

    async def upsert_radarr_cache_entry(
        self,
        tmdb_id: int,
        radarr_id: int,
        title: str,
        sort_title: str = "",
        year: int = 0,
        status: str = "",
        monitored: bool = False,
        root_folder: str = "",
        quality_profile_id: int | None = None,
    ) -> None:
        """Insert or replace a radarr_movie_cache entry."""
        await self.execute(
            """
            INSERT INTO radarr_movie_cache
                (tmdb_id, radarr_id, title, sort_title, year, status,
                 monitored, root_folder, quality_profile_id, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(tmdb_id) DO UPDATE SET
                radarr_id = excluded.radarr_id,
                title = excluded.title,
                sort_title = excluded.sort_title,
                year = excluded.year,
                status = excluded.status,
                monitored = excluded.monitored,
                root_folder = excluded.root_folder,
                quality_profile_id = excluded.quality_profile_id,
                last_updated = datetime('now')
            """,
            (
                tmdb_id,
                radarr_id,
                title,
                sort_title,
                year,
                status,
                int(monitored),
                root_folder,
                quality_profile_id,
            ),
        )
        await self.db.commit()

    async def bulk_upsert_radarr_cache(self, movies_list: list[dict[str, Any]]) -> int:
        """Bulk-upsert radarr_movie_cache from a Radarr /api/v3/movie response."""
        count = 0
        for item in movies_list:
            tmdb_id = item.get("tmdbId")
            radarr_id = item.get("id")
            if not tmdb_id or not radarr_id:
                continue
            await self.upsert_radarr_cache_entry(
                tmdb_id=tmdb_id,
                radarr_id=radarr_id,
                title=item.get("title", ""),
                sort_title=item.get("sortTitle", ""),
                year=item.get("year", 0),
                status=item.get("status", ""),
                monitored=item.get("monitored", False),
                root_folder=item.get("rootFolderPath", ""),
                quality_profile_id=item.get("qualityProfileId"),
            )
            count += 1
        return count

    async def get_radarr_cache_entry(self, tmdb_id: int) -> dict[str, Any] | None:
        """Return a radarr_movie_cache entry by TMDB ID."""
        return await self.fetch_one(
            "SELECT * FROM radarr_movie_cache WHERE tmdb_id = ?", (tmdb_id,)
        )

    async def get_all_radarr_cache(self) -> list[dict[str, Any]]:
        """Return all cached Radarr movies."""
        return await self.fetch_all("SELECT * FROM radarr_movie_cache ORDER BY title")

    async def clear_radarr_cache(self) -> None:
        """Remove all radarr_movie_cache entries."""
        await self.execute("DELETE FROM radarr_movie_cache")

    # ------------------------------------------------------------------
    # Plex Users (watch sync account linking)
    # ------------------------------------------------------------------

    async def upsert_plex_user(
        self,
        plex_username: str,
        plex_uuid: str,
        anilist_user_id: str = "",
        plex_token: str = "",
        is_admin: bool = False,
    ) -> None:
        """Insert or update a linked Plex user (replace by plex_uuid)."""
        await self.execute("DELETE FROM plex_users WHERE plex_uuid=?", (plex_uuid,))
        await self.execute(
            """INSERT INTO plex_users
                   (anilist_user_id, plex_username, plex_uuid, plex_token, is_admin)
               VALUES (?, ?, ?, ?, ?)
            """,
            (anilist_user_id, plex_username, plex_uuid, plex_token, int(is_admin)),
        )

    async def get_plex_user(self) -> dict[str, Any] | None:
        """Return the single linked Plex user (first row)."""
        return await self.fetch_one("SELECT * FROM plex_users ORDER BY id LIMIT 1")

    async def get_all_plex_users(self) -> list[dict[str, Any]]:
        """Return all linked Plex users."""
        return await self.fetch_all("SELECT * FROM plex_users ORDER BY created_at")

    async def delete_plex_user(self, plex_uuid: str) -> None:
        """Unlink a Plex user by their UUID."""
        await self.execute("DELETE FROM plex_users WHERE plex_uuid=?", (plex_uuid,))

    async def clear_plex_users(self) -> None:
        """Remove all linked Plex users."""
        await self.execute("DELETE FROM plex_users")

    # ------------------------------------------------------------------
    # Jellyfin Users (watch sync account linking)
    # ------------------------------------------------------------------

    async def upsert_jellyfin_user(
        self,
        jf_user_id: str,
        jf_username: str,
        anilist_user_id: str = "",
        jf_token: str = "",
    ) -> None:
        """Insert or update a linked Jellyfin user (replace by jf_user_id)."""
        await self.execute(
            "DELETE FROM jellyfin_users WHERE jf_user_id=?", (jf_user_id,)
        )
        await self.execute(
            """INSERT INTO jellyfin_users
                   (anilist_user_id, jf_username, jf_user_id, jf_token)
               VALUES (?, ?, ?, ?)
            """,
            (anilist_user_id, jf_username, jf_user_id, jf_token),
        )

    async def get_jellyfin_user(self) -> dict[str, Any] | None:
        """Return the single linked Jellyfin user (first row)."""
        return await self.fetch_one("SELECT * FROM jellyfin_users ORDER BY id LIMIT 1")

    async def get_all_jellyfin_users(self) -> list[dict[str, Any]]:
        """Return all linked Jellyfin users."""
        return await self.fetch_all("SELECT * FROM jellyfin_users ORDER BY created_at")

    async def delete_jellyfin_user(self, jf_user_id: str) -> None:
        """Unlink a Jellyfin user by their user ID."""
        await self.execute(
            "DELETE FROM jellyfin_users WHERE jf_user_id=?", (jf_user_id,)
        )

    async def clear_jellyfin_users(self) -> None:
        """Remove all linked Jellyfin users."""
        await self.execute("DELETE FROM jellyfin_users")
        await self.db.commit()
