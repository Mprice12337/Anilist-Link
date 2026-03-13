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
    series_group_id: int | None = None
    season_number: int | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class SeriesGroup:
    id: int = 0
    root_anilist_id: int = 0
    display_title: str = ""
    entry_count: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class SeriesGroupEntry:
    id: int = 0
    group_id: int = 0
    anilist_id: int = 0
    season_order: int = 0
    display_title: str = ""
    format: str = ""
    episodes: int | None = None
    start_date: str = ""
    created_at: str = ""


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
    year: int = 0
    cached_at: str = ""
    expires_at: str = ""


@dataclass
class PlexMedia:
    rating_key: str = ""
    title: str = ""
    year: int | None = None
    thumb: str = ""
    summary: str = ""
    library_key: str = ""
    library_title: str = ""
    folder_name: str = ""
    added_at: str = ""
    updated_at: str = ""


@dataclass
class RestructureLogEntry:
    id: int = 0
    group_title: str = ""
    source_path: str = ""
    destination_path: str = ""
    operation: str = "move"
    status: str = "success"
    error_message: str = ""
    executed_at: str = ""


@dataclass
class ManualOverride:
    id: int = 0
    source: str = ""
    source_id: str = ""
    source_title: str = ""
    anilist_id: int = 0
    created_by: str = ""
    created_at: str = ""


@dataclass
class AnilistSonarrMapping:
    id: int = 0
    anilist_id: int = 0
    tvdb_id: int = 0
    sonarr_id: int = 0
    title: str = ""
    in_sonarr: bool = False
    sonarr_monitored: bool = False
    created_at: str = ""
    updated_at: str = ""


@dataclass
class AnilistRadarrMapping:
    id: int = 0
    anilist_id: int = 0
    tmdb_id: int = 0
    radarr_id: int = 0
    title: str = ""
    in_radarr: bool = False
    radarr_monitored: bool = False
    created_at: str = ""
    updated_at: str = ""


@dataclass
class SonarrSeriesCache:
    id: int = 0
    tvdb_id: int = 0
    sonarr_id: int = 0
    title: str = ""
    status: str = ""
    monitored: bool = False
    path: str = ""
    quality_profile_id: int = 0
    root_folder: str = ""
    cached_at: str = ""


@dataclass
class RadarrMovieCache:
    id: int = 0
    tmdb_id: int = 0
    radarr_id: int = 0
    title: str = ""
    status: str = ""
    monitored: bool = False
    path: str = ""
    quality_profile_id: int = 0
    root_folder: str = ""
    cached_at: str = ""


@dataclass
class UserWatchlist:
    id: int = 0
    user_id: str = ""
    anilist_id: int = 0
    list_status: str = ""
    progress: int = 0
    score: float = 0.0
    anilist_title: str = ""
    anilist_format: str = ""
    anilist_episodes: int | None = None
    cover_image: str = ""
    airing_status: str = ""
    start_year: int | None = None
    last_synced_at: str = ""


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
            year INTEGER NOT NULL DEFAULT 0,
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
    "plex_media": """
        CREATE TABLE IF NOT EXISTS plex_media (
            rating_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL DEFAULT '',
            year INTEGER,
            thumb TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            library_key TEXT NOT NULL DEFAULT '',
            library_title TEXT NOT NULL DEFAULT '',
            folder_name TEXT NOT NULL DEFAULT '',
            added_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "series_groups": """
        CREATE TABLE IF NOT EXISTS series_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            root_anilist_id INTEGER NOT NULL UNIQUE,
            display_title TEXT NOT NULL DEFAULT '',
            entry_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "restructure_log": """
        CREATE TABLE IF NOT EXISTS restructure_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_title TEXT NOT NULL DEFAULT '',
            source_path TEXT NOT NULL DEFAULT '',
            destination_path TEXT NOT NULL DEFAULT '',
            operation TEXT NOT NULL DEFAULT 'move',
            status TEXT NOT NULL DEFAULT 'success',
            error_message TEXT NOT NULL DEFAULT '',
            executed_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "series_group_entries": """
        CREATE TABLE IF NOT EXISTS series_group_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            anilist_id INTEGER NOT NULL,
            season_order INTEGER NOT NULL DEFAULT 0,
            display_title TEXT NOT NULL DEFAULT '',
            format TEXT NOT NULL DEFAULT '',
            episodes INTEGER,
            start_date TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (group_id) REFERENCES series_groups(id) ON DELETE CASCADE,
            UNIQUE(group_id, anilist_id)
        )
    """,
    "anilist_sonarr_mapping": """
        CREATE TABLE IF NOT EXISTS anilist_sonarr_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anilist_id INTEGER NOT NULL UNIQUE,
            tvdb_id INTEGER NOT NULL DEFAULT 0,
            sonarr_id INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '',
            in_sonarr INTEGER NOT NULL DEFAULT 0,
            sonarr_monitored INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "anilist_radarr_mapping": """
        CREATE TABLE IF NOT EXISTS anilist_radarr_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anilist_id INTEGER NOT NULL UNIQUE,
            tmdb_id INTEGER NOT NULL DEFAULT 0,
            radarr_id INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '',
            in_radarr INTEGER NOT NULL DEFAULT 0,
            radarr_monitored INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "sonarr_series_cache": """
        CREATE TABLE IF NOT EXISTS sonarr_series_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tvdb_id INTEGER NOT NULL UNIQUE,
            sonarr_id INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            monitored INTEGER NOT NULL DEFAULT 0,
            path TEXT NOT NULL DEFAULT '',
            quality_profile_id INTEGER NOT NULL DEFAULT 0,
            root_folder TEXT NOT NULL DEFAULT '',
            cached_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "radarr_movie_cache": """
        CREATE TABLE IF NOT EXISTS radarr_movie_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmdb_id INTEGER NOT NULL UNIQUE,
            radarr_id INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            monitored INTEGER NOT NULL DEFAULT 0,
            path TEXT NOT NULL DEFAULT '',
            quality_profile_id INTEGER NOT NULL DEFAULT 0,
            root_folder TEXT NOT NULL DEFAULT '',
            cached_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "user_watchlist": """
        CREATE TABLE IF NOT EXISTS user_watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            anilist_id INTEGER NOT NULL,
            list_status TEXT NOT NULL DEFAULT '',
            progress INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            anilist_title TEXT NOT NULL DEFAULT '',
            anilist_format TEXT NOT NULL DEFAULT '',
            anilist_episodes INTEGER,
            cover_image TEXT NOT NULL DEFAULT '',
            airing_status TEXT NOT NULL DEFAULT '',
            start_year INTEGER,
            last_synced_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, anilist_id)
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
    "CREATE INDEX IF NOT EXISTS idx_plex_media_library" " ON plex_media(library_key)",
]
