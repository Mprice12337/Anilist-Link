"""Database table definitions and data models."""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MediaMapping:
    id: int = 0
    source: str = ""  # "plex", "jellyfin"
    source_id: str = ""
    source_title: str = ""
    anilist_id: int = 0
    anilist_title: str = ""
    match_confidence: float = 0.0
    match_method: str = ""  # "fuzzy", "manual", "exact"
    media_type: str = "ANIME"
    created_at: str = ""
    updated_at: str = ""


@dataclass
class User:
    user_id: str = ""
    service: str = ""  # "anilist"
    username: str = ""
    access_token: str = ""
    token_type: str = "Bearer"
    anilist_id: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class SyncState:
    id: int = 0
    user_id: str = ""
    media_mapping_id: int = 0
    last_episode: int = 0
    status: str = ""  # "CURRENT", "COMPLETED", "PLANNING", etc.
    synced_at: str = ""


@dataclass
class AniListCache:
    anilist_id: int = 0
    title_romaji: str = ""
    title_english: str = ""
    title_native: str = ""
    episodes: int | None = None
    cover_image: str = ""
    description: str = ""
    genres: str = ""  # JSON array stored as string
    status: str = ""
    cached_at: str = ""
    expires_at: str = ""


@dataclass
class ManualOverride:
    id: int = 0
    source: str = ""
    source_id: str = ""
    source_title: str = ""
    anilist_id: int = 0
    created_by: str = ""
    created_at: str = ""


# ---------------------------------------------------------------------------
# SQL Schema
# ---------------------------------------------------------------------------

TABLES: dict[str, str] = {
    "schema_version": """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "media_mappings": """
        CREATE TABLE IF NOT EXISTS media_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_title TEXT NOT NULL DEFAULT '',
            anilist_id INTEGER NOT NULL,
            anilist_title TEXT NOT NULL DEFAULT '',
            match_confidence REAL NOT NULL DEFAULT 0.0,
            match_method TEXT NOT NULL DEFAULT '',
            media_type TEXT NOT NULL DEFAULT 'ANIME',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source, source_id)
        )
    """,
    "users": """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            service TEXT NOT NULL DEFAULT 'anilist',
            username TEXT NOT NULL DEFAULT '',
            access_token TEXT NOT NULL DEFAULT '',
            token_type TEXT NOT NULL DEFAULT 'Bearer',
            anilist_id INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "sync_state": """
        CREATE TABLE IF NOT EXISTS sync_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            media_mapping_id INTEGER NOT NULL,
            last_episode INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT '',
            synced_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (media_mapping_id)
                REFERENCES media_mappings(id) ON DELETE CASCADE
        )
    """,
    "anilist_cache": """
        CREATE TABLE IF NOT EXISTS anilist_cache (
            anilist_id INTEGER PRIMARY KEY,
            title_romaji TEXT NOT NULL DEFAULT '',
            title_english TEXT NOT NULL DEFAULT '',
            title_native TEXT NOT NULL DEFAULT '',
            episodes INTEGER,
            cover_image TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            genres TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT '',
            cached_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL DEFAULT (datetime('now', '+7 days'))
        )
    """,
    "manual_overrides": """
        CREATE TABLE IF NOT EXISTS manual_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_title TEXT NOT NULL DEFAULT '',
            anilist_id INTEGER NOT NULL,
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "cr_session_cache": """
        CREATE TABLE IF NOT EXISTS cr_session_cache (
            id INTEGER PRIMARY KEY,
            cookies_json TEXT NOT NULL DEFAULT '[]',
            access_token TEXT NOT NULL DEFAULT '',
            account_id TEXT NOT NULL DEFAULT '',
            device_id TEXT NOT NULL DEFAULT '',
            cached_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL DEFAULT (datetime('now', '+30 days'))
        )
    """,
    "app_settings": """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            is_secret INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
}

INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_sync_state_user_media"
    " ON sync_state(user_id, media_mapping_id)",
    "CREATE INDEX IF NOT EXISTS idx_anilist_cache_expires"
    " ON anilist_cache(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_media_mappings_anilist"
    " ON media_mappings(anilist_id)",
    "CREATE INDEX IF NOT EXISTS idx_users_service ON users(service)",
]
