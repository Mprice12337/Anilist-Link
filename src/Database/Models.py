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
    rating: float | None = None
    studio: str = ""
    imdb_id: str = ""
    tvdb_id: str = ""
    tvmaze_id: str = ""
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
class JellyfinMedia:
    item_id: str = ""
    title: str = ""
    year: int | None = None
    path: str = ""
    library_id: str = ""
    library_name: str = ""
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
class Library:
    id: int = 0
    name: str = ""
    paths: str = "[]"  # JSON array of directory paths
    created_at: str = ""
    updated_at: str = ""


@dataclass
class LibraryItem:
    id: int = 0
    library_id: int = 0
    folder_path: str = ""
    folder_name: str = ""
    anilist_id: int | None = None
    anilist_title: str = ""
    match_confidence: float = 0.0
    match_method: str = ""
    anilist_format: str = ""
    anilist_episodes: int | None = None
    year: int = 0
    cover_image: str = ""
    series_group_id: int | None = None
    scanned_at: str = ""


@dataclass
class DownloadRequest:
    id: int = 0
    anilist_id: int = 0
    anilist_title: str = ""
    service: str = ""  # "sonarr" or "radarr"
    external_id: int | None = None  # Sonarr/Radarr series/movie ID after add
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    status: str = "pending"  # "pending", "added", "exists", "error"
    error_message: str = ""
    quality_profile_id: int | None = None
    root_folder: str = ""
    requested_by: str = ""
    created_at: str = ""
    executed_at: str | None = None


@dataclass
class AnilistSonarrMapping:
    id: int = 0
    anilist_id: int = 0
    series_group_id: int | None = None
    tvdb_id: int | None = None
    sonarr_id: int | None = None
    sonarr_title: str = ""
    sonarr_season: int | None = None
    episode_offset: int = 0
    is_absolute_numbering: int = 0
    in_sonarr: int = 0
    sonarr_monitored: int = 0
    sonarr_root_folder: str = ""
    confidence: str = "low"  # "high", "medium", "low"
    confirmed: int = 0
    last_verified_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class AnilistRadarrMapping:
    id: int = 0
    anilist_id: int = 0
    tmdb_id: int | None = None
    radarr_id: int | None = None
    radarr_title: str = ""
    in_radarr: int = 0
    radarr_monitored: int = 0
    radarr_root_folder: str = ""
    confidence: str = "low"
    confirmed: int = 0
    last_verified_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class SonarrSeriesCache:
    tvdb_id: int = 0
    sonarr_id: int = 0
    title: str = ""
    sort_title: str = ""
    status: str = ""  # "continuing", "ended"
    monitored: int = 0
    root_folder: str = ""
    quality_profile_id: int | None = None
    alternate_titles: str = "[]"  # JSON array
    seasons_json: str = "[]"  # JSON [{seasonNumber, monitored, episodeCount}]
    last_updated: str = ""


@dataclass
class RadarrMovieCache:
    tmdb_id: int = 0
    radarr_id: int = 0
    title: str = ""
    sort_title: str = ""
    year: int = 0
    status: str = ""
    monitored: int = 0
    root_folder: str = ""
    quality_profile_id: int | None = None
    last_updated: str = ""


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
            series_group_id INTEGER,
            season_number INTEGER,
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
            UNIQUE(user_id, media_mapping_id),
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
            rating REAL,
            studio TEXT NOT NULL DEFAULT '',
            imdb_id TEXT NOT NULL DEFAULT '',
            tvdb_id TEXT NOT NULL DEFAULT '',
            tvmaze_id TEXT NOT NULL DEFAULT '',
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
    "jellyfin_media": """
        CREATE TABLE IF NOT EXISTS jellyfin_media (
            item_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL DEFAULT '',
            year INTEGER,
            path TEXT NOT NULL DEFAULT '',
            library_id TEXT NOT NULL DEFAULT '',
            library_name TEXT NOT NULL DEFAULT '',
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
            executed_at TEXT NOT NULL DEFAULT (datetime('now')),
            plan_id INTEGER
        )
    """,
    "restructure_plans": """
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
    """,
    "libraries": """
        CREATE TABLE IF NOT EXISTS libraries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            paths TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "library_items": """
        CREATE TABLE IF NOT EXISTS library_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            library_id INTEGER NOT NULL,
            folder_path TEXT NOT NULL DEFAULT '',
            folder_name TEXT NOT NULL DEFAULT '',
            anilist_id INTEGER,
            anilist_title TEXT NOT NULL DEFAULT '',
            match_confidence REAL NOT NULL DEFAULT 0.0,
            match_method TEXT NOT NULL DEFAULT '',
            anilist_format TEXT NOT NULL DEFAULT '',
            anilist_episodes INTEGER,
            year INTEGER NOT NULL DEFAULT 0,
            cover_image TEXT NOT NULL DEFAULT '',
            series_group_id INTEGER,
            scanned_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (library_id) REFERENCES libraries(id) ON DELETE CASCADE,
            UNIQUE(library_id, folder_path)
        )
    """,
    "series_group_entries": """
        CREATE TABLE IF NOT EXISTS series_group_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            anilist_id INTEGER NOT NULL,
            season_order INTEGER NOT NULL DEFAULT 0,
            display_title TEXT NOT NULL DEFAULT '',
            title_romaji TEXT NOT NULL DEFAULT '',
            title_english TEXT NOT NULL DEFAULT '',
            format TEXT NOT NULL DEFAULT '',
            episodes INTEGER,
            start_date TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (group_id) REFERENCES series_groups(id) ON DELETE CASCADE,
            UNIQUE(group_id, anilist_id)
        )
    """,
    "plex_users": """
        CREATE TABLE IF NOT EXISTS plex_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anilist_user_id TEXT NOT NULL DEFAULT '',
            plex_username TEXT NOT NULL DEFAULT '',
            plex_uuid TEXT NOT NULL DEFAULT '',
            plex_token TEXT NOT NULL DEFAULT '',
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "jellyfin_users": """
        CREATE TABLE IF NOT EXISTS jellyfin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anilist_user_id TEXT NOT NULL DEFAULT '',
            jf_username TEXT NOT NULL DEFAULT '',
            jf_user_id TEXT NOT NULL DEFAULT '',
            jf_token TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "cr_sync_preview": """
        CREATE TABLE IF NOT EXISTS cr_sync_preview (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '',
            cr_title TEXT NOT NULL DEFAULT '',
            anilist_id INTEGER NOT NULL DEFAULT 0,
            anilist_title TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0.0,
            proposed_status TEXT NOT NULL DEFAULT '',
            proposed_progress INTEGER NOT NULL DEFAULT 0,
            current_status TEXT NOT NULL DEFAULT '',
            current_progress INTEGER NOT NULL DEFAULT 0,
            action TEXT NOT NULL DEFAULT '',
            approved INTEGER NOT NULL DEFAULT 0,
            episodes_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "cr_sync_log": """
        CREATE TABLE IF NOT EXISTS cr_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT '',
            anilist_id INTEGER NOT NULL DEFAULT 0,
            show_title TEXT NOT NULL DEFAULT '',
            before_status TEXT NOT NULL DEFAULT '',
            before_progress INTEGER NOT NULL DEFAULT 0,
            after_status TEXT NOT NULL DEFAULT '',
            after_progress INTEGER NOT NULL DEFAULT 0,
            applied_at TEXT NOT NULL DEFAULT (datetime('now')),
            sync_run_id TEXT NOT NULL DEFAULT '',
            undone_at TEXT,
            cr_sync_preview_id INTEGER
        )
    """,
    "watch_sync_log": """
        CREATE TABLE IF NOT EXISTS watch_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL DEFAULT '',
            anilist_id INTEGER NOT NULL DEFAULT 0,
            show_title TEXT NOT NULL DEFAULT '',
            before_status TEXT NOT NULL DEFAULT '',
            before_progress INTEGER NOT NULL DEFAULT 0,
            after_status TEXT NOT NULL DEFAULT '',
            after_progress INTEGER NOT NULL DEFAULT 0,
            applied_at TEXT NOT NULL DEFAULT (datetime('now')),
            undone_at TEXT,
            direction TEXT NOT NULL DEFAULT 'to_anilist'
        )
    """,
    "download_requests": """
        CREATE TABLE IF NOT EXISTS download_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anilist_id INTEGER NOT NULL DEFAULT 0,
            anilist_title TEXT NOT NULL DEFAULT '',
            service TEXT NOT NULL DEFAULT '',
            external_id INTEGER,
            tvdb_id INTEGER,
            tmdb_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            error_message TEXT NOT NULL DEFAULT '',
            quality_profile_id INTEGER,
            root_folder TEXT NOT NULL DEFAULT '',
            requested_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            executed_at TEXT
        )
    """,
    "anilist_sonarr_mapping": """
        CREATE TABLE IF NOT EXISTS anilist_sonarr_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anilist_id INTEGER NOT NULL,
            series_group_id INTEGER,
            tvdb_id INTEGER,
            sonarr_id INTEGER,
            sonarr_title TEXT NOT NULL DEFAULT '',
            sonarr_season INTEGER,
            episode_offset INTEGER NOT NULL DEFAULT 0,
            is_absolute_numbering INTEGER NOT NULL DEFAULT 0,
            in_sonarr INTEGER NOT NULL DEFAULT 0,
            sonarr_monitored INTEGER NOT NULL DEFAULT 0,
            monitor_type TEXT NOT NULL DEFAULT 'future',
            sonarr_root_folder TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'low',
            confirmed INTEGER NOT NULL DEFAULT 0,
            last_verified_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(anilist_id)
        )
    """,
    "anilist_radarr_mapping": """
        CREATE TABLE IF NOT EXISTS anilist_radarr_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anilist_id INTEGER NOT NULL,
            tmdb_id INTEGER,
            radarr_id INTEGER,
            radarr_title TEXT NOT NULL DEFAULT '',
            in_radarr INTEGER NOT NULL DEFAULT 0,
            radarr_monitored INTEGER NOT NULL DEFAULT 0,
            monitor_type TEXT NOT NULL DEFAULT 'future',
            radarr_root_folder TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'low',
            confirmed INTEGER NOT NULL DEFAULT 0,
            last_verified_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(anilist_id)
        )
    """,
    "sonarr_series_cache": """
        CREATE TABLE IF NOT EXISTS sonarr_series_cache (
            tvdb_id INTEGER PRIMARY KEY,
            sonarr_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            sort_title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            monitored INTEGER NOT NULL DEFAULT 0,
            root_folder TEXT NOT NULL DEFAULT '',
            quality_profile_id INTEGER,
            alternate_titles TEXT NOT NULL DEFAULT '[]',
            seasons_json TEXT NOT NULL DEFAULT '[]',
            last_updated TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "radarr_movie_cache": """
        CREATE TABLE IF NOT EXISTS radarr_movie_cache (
            tmdb_id INTEGER PRIMARY KEY,
            radarr_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            sort_title TEXT NOT NULL DEFAULT '',
            year INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT '',
            monitored INTEGER NOT NULL DEFAULT 0,
            root_folder TEXT NOT NULL DEFAULT '',
            quality_profile_id INTEGER,
            last_updated TEXT NOT NULL DEFAULT (datetime('now'))
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
            title_romaji TEXT NOT NULL DEFAULT '',
            title_english TEXT NOT NULL DEFAULT '',
            anilist_format TEXT NOT NULL DEFAULT '',
            anilist_episodes INTEGER,
            cover_image TEXT NOT NULL DEFAULT '',
            airing_status TEXT NOT NULL DEFAULT '',
            start_year INTEGER,
            last_synced_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, anilist_id)
        )
    """,
    "anilist_sonarr_season_mapping": """
        CREATE TABLE IF NOT EXISTS anilist_sonarr_season_mapping (
            sonarr_id     INTEGER NOT NULL,
            season_number INTEGER NOT NULL,
            anilist_id    INTEGER NOT NULL,
            created_at    TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (sonarr_id, season_number)
        )
    """,
    "anilist_arr_skip": """
        CREATE TABLE IF NOT EXISTS anilist_arr_skip (
            anilist_id   INTEGER PRIMARY KEY,
            reason       TEXT NOT NULL,
            skipped_at   TEXT DEFAULT (datetime('now'))
        )
    """,
}

INDEXES: list[str] = [
    # sync_state
    "CREATE INDEX IF NOT EXISTS idx_sync_state_user_media"
    " ON sync_state(user_id, media_mapping_id)",
    # anilist_cache
    "CREATE INDEX IF NOT EXISTS idx_anilist_cache_expires"
    " ON anilist_cache(expires_at)",
    # media_mappings
    "CREATE INDEX IF NOT EXISTS idx_media_mappings_anilist"
    " ON media_mappings(anilist_id)",
    "CREATE INDEX IF NOT EXISTS idx_media_mappings_group"
    " ON media_mappings(series_group_id)",
    # users
    "CREATE INDEX IF NOT EXISTS idx_users_service ON users(service)",
    # plex_media
    "CREATE INDEX IF NOT EXISTS idx_plex_media_library ON plex_media(library_key)",
    # jellyfin_media
    "CREATE INDEX IF NOT EXISTS idx_jellyfin_media_library"
    " ON jellyfin_media(library_id)",
    # series_groups / series_group_entries
    "CREATE INDEX IF NOT EXISTS idx_series_groups_root"
    " ON series_groups(root_anilist_id)",
    "CREATE INDEX IF NOT EXISTS idx_sge_anilist_id"
    " ON series_group_entries(anilist_id)",
    # library_items
    "CREATE INDEX IF NOT EXISTS idx_library_items_library"
    " ON library_items(library_id)",
    "CREATE INDEX IF NOT EXISTS idx_library_items_anilist"
    " ON library_items(anilist_id)",
    # cr_sync
    "CREATE INDEX IF NOT EXISTS idx_cr_sync_preview_run" " ON cr_sync_preview(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_cr_sync_log_anilist" " ON cr_sync_log(anilist_id)",
    "CREATE INDEX IF NOT EXISTS idx_watch_sync_log_anilist"
    " ON watch_sync_log(anilist_id)",
    "CREATE INDEX IF NOT EXISTS idx_watch_sync_log_source" " ON watch_sync_log(source)",
    # download_requests
    "CREATE INDEX IF NOT EXISTS idx_download_requests_anilist"
    " ON download_requests(anilist_id)",
    "CREATE INDEX IF NOT EXISTS idx_download_requests_status"
    " ON download_requests(status)",
    # sonarr/radarr mapping + cache
    "CREATE INDEX IF NOT EXISTS idx_sonarr_mapping_tvdb"
    " ON anilist_sonarr_mapping(tvdb_id)",
    "CREATE INDEX IF NOT EXISTS idx_sonarr_mapping_group"
    " ON anilist_sonarr_mapping(series_group_id)",
    "CREATE INDEX IF NOT EXISTS idx_radarr_mapping_tmdb"
    " ON anilist_radarr_mapping(tmdb_id)",
    "CREATE INDEX IF NOT EXISTS idx_sonarr_cache_sonarr_id"
    " ON sonarr_series_cache(sonarr_id)",
    "CREATE INDEX IF NOT EXISTS idx_radarr_cache_radarr_id"
    " ON radarr_movie_cache(radarr_id)",
    # user_watchlist
    "CREATE INDEX IF NOT EXISTS idx_watchlist_user_status"
    " ON user_watchlist(user_id, list_status)",
    "CREATE INDEX IF NOT EXISTS idx_watchlist_anilist" " ON user_watchlist(anilist_id)",
    # anilist_sonarr_season_mapping
    "CREATE INDEX IF NOT EXISTS idx_assm_sonarr"
    " ON anilist_sonarr_season_mapping(sonarr_id)",
]
