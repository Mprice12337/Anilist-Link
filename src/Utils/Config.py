"""Configuration management from environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AniListConfig:
    client_id: str
    client_secret: str
    redirect_uri: str = ""


@dataclass(frozen=True)
class CrunchyrollConfig:
    email: str = ""
    password: str = ""
    flaresolverr_url: str = ""
    headless: bool = True
    max_pages: int = 10
    auto_sync_enabled: bool = True
    auto_approve: bool = False


@dataclass(frozen=True)
class PlexConfig:
    url: str = ""
    token: str = ""
    anime_library_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class JellyfinConfig:
    url: str = ""
    api_key: str = ""
    anime_library_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SonarrConfig:
    url: str = ""
    api_key: str = ""
    anime_root_folder: str = ""
    path_prefix: str = ""
    local_path_prefix: str = ""


@dataclass(frozen=True)
class RadarrConfig:
    url: str = ""
    api_key: str = ""
    anime_root_folder: str = ""
    path_prefix: str = ""
    local_path_prefix: str = ""


@dataclass(frozen=True)
class DatabaseConfig:
    path: Path = Path("./data/anilist_link.db")


@dataclass(frozen=True)
class SchedulerConfig:
    scan_interval_hours: int = 24
    sync_interval_minutes: int = 15
    cr_sync_time: str = (
        "02:00"  # "HH:MM" for daily at a fixed time; empty = use interval
    )
    library_reindex_interval_hours: int = 6
    watchlist_refresh_interval_minutes: int = 30


@dataclass(frozen=True)
class DownloadSyncConfig:
    auto_statuses: tuple[str, ...] = ("CURRENT",)
    monitor_mode: str = "future"
    auto_search: bool = False
    sync_interval_minutes: int = 60
    arr_enabled: bool = True


@dataclass(frozen=True)
class AppConfig:
    debug: bool = False
    timezone: str = "UTC"
    host: str = "0.0.0.0"
    port: int = 9876
    base_url: str = "http://localhost:9876"
    log_path: Path | None = None
    anilist: AniListConfig = AniListConfig(client_id="", client_secret="")
    crunchyroll: CrunchyrollConfig = CrunchyrollConfig()
    plex: PlexConfig = PlexConfig()
    jellyfin: JellyfinConfig = JellyfinConfig()
    sonarr: SonarrConfig = SonarrConfig()
    radarr: RadarrConfig = RadarrConfig()
    database: DatabaseConfig = DatabaseConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    download_sync: DownloadSyncConfig = DownloadSyncConfig()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("true", "1", "yes")


def _env_int(key: str, default: int) -> int:
    val = _env(key, "")
    if val:
        try:
            return int(val)
        except ValueError:
            pass
    return default


def _project_root() -> Path:
    """Return the project root directory (parent of src/)."""
    return Path(__file__).resolve().parent.parent.parent


def _resolve_db_path() -> Path:
    config_dir = Path("/config")
    if config_dir.exists() and config_dir.is_dir():
        return config_dir / "anilist_link.db"
    local = _project_root() / "data"
    local.mkdir(parents=True, exist_ok=True)
    return local / "anilist_link.db"


def _resolve_log_path() -> Path | None:
    config_dir = Path("/config")
    if config_dir.exists() and config_dir.is_dir():
        return config_dir / "logs" / "anilist_link.log"
    log_dir = _project_root() / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "anilist_link.log"


def _redirect_host(bind_host: str) -> str:
    """Convert a bind address to a usable redirect host.

    ``0.0.0.0`` / ``::`` are valid *listen* addresses but not valid browser
    URLs, so we swap them for ``localhost``.
    """
    if bind_host in ("0.0.0.0", "::", ""):
        return "localhost"
    return bind_host


def _parse_json_list(raw: str) -> tuple[str, ...]:
    """Parse a JSON list string into a tuple of strings."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return tuple(str(v) for v in parsed)
    except (json.JSONDecodeError, TypeError):
        pass
    return ()


def load_config() -> AppConfig:
    """Parse environment variables into an immutable AppConfig."""
    debug = _env_bool("DEBUG")
    port = _env_int("PORT", 9876)
    host = _env("HOST", "0.0.0.0")

    return AppConfig(
        debug=debug,
        timezone=_env("TZ", "UTC"),
        host=host,
        port=port,
        log_path=_resolve_log_path(),
        anilist=AniListConfig(
            client_id=_env("ANILIST_CLIENT_ID"),
            client_secret=_env("ANILIST_CLIENT_SECRET"),
            redirect_uri=f"http://{_redirect_host(host)}:{port}/auth/anilist/callback",
        ),
        crunchyroll=CrunchyrollConfig(
            email=_env("CRUNCHYROLL_EMAIL"),
            password=_env("CRUNCHYROLL_PASSWORD"),
            flaresolverr_url=_env("FLARESOLVERR_URL"),
            headless=_env_bool("HEADLESS_MODE", True),
            max_pages=_env_int("MAX_PAGES", 10),
            auto_sync_enabled=_env_bool("CR_AUTO_SYNC_ENABLED", True),
            auto_approve=_env_bool("CR_AUTO_APPROVE", True),
        ),
        plex=PlexConfig(
            url=_env("PLEX_URL"),
            token=_env("PLEX_TOKEN"),
            anime_library_keys=_parse_json_list(_env("PLEX_ANIME_LIBRARIES", "[]")),
        ),
        jellyfin=JellyfinConfig(
            url=_env("JELLYFIN_URL"),
            api_key=_env("JELLYFIN_API_KEY"),
            anime_library_ids=_parse_json_list(
                _env("JELLYFIN_ANIME_LIBRARY_IDS", "[]")
            ),
        ),
        sonarr=SonarrConfig(
            url=_env("SONARR_URL"),
            api_key=_env("SONARR_API_KEY"),
        ),
        radarr=RadarrConfig(
            url=_env("RADARR_URL"),
            api_key=_env("RADARR_API_KEY"),
        ),
        database=DatabaseConfig(path=_resolve_db_path()),
        scheduler=SchedulerConfig(
            scan_interval_hours=_env_int("SCAN_INTERVAL", 24),
            sync_interval_minutes=_env_int("SYNC_INTERVAL", 15),
            cr_sync_time=_env("CR_SYNC_TIME", ""),
            library_reindex_interval_hours=_env_int("LIBRARY_REINDEX_INTERVAL", 6),
            watchlist_refresh_interval_minutes=_env_int(
                "WATCHLIST_REFRESH_INTERVAL", 30
            ),
        ),
        download_sync=DownloadSyncConfig(
            auto_statuses=tuple(
                s.strip()
                for s in _env("DOWNLOAD_AUTO_STATUSES", "CURRENT").split(",")
                if s.strip()
            ),
            monitor_mode=_env("DOWNLOAD_MONITOR_MODE", "future"),
            auto_search=_env_bool("DOWNLOAD_AUTO_SEARCH", False),
            sync_interval_minutes=_env_int("DOWNLOAD_SYNC_INTERVAL", 60),
        ),
    )


# ---------------------------------------------------------------------------
# Mapping between app_settings DB keys and (env var, config field, default)
# ---------------------------------------------------------------------------

# Each entry: (db_key, env_var_name, code_default)
SETTINGS_MAP: dict[str, tuple[str, str]] = {
    "crunchyroll.email": ("CRUNCHYROLL_EMAIL", ""),
    "crunchyroll.password": ("CRUNCHYROLL_PASSWORD", ""),
    "crunchyroll.flaresolverr_url": ("FLARESOLVERR_URL", ""),
    "crunchyroll.headless": ("HEADLESS_MODE", "true"),
    "crunchyroll.max_pages": ("MAX_PAGES", "10"),
    "crunchyroll.auto_sync_enabled": ("CR_AUTO_SYNC_ENABLED", "true"),
    "crunchyroll.auto_approve": ("CR_AUTO_APPROVE", "true"),
    "anilist.client_id": ("ANILIST_CLIENT_ID", ""),
    "anilist.client_secret": ("ANILIST_CLIENT_SECRET", ""),
    "plex.url": ("PLEX_URL", ""),
    "plex.token": ("PLEX_TOKEN", ""),
    "plex.anime_library_keys": ("PLEX_ANIME_LIBRARIES", "[]"),
    "jellyfin.url": ("JELLYFIN_URL", ""),
    "jellyfin.api_key": ("JELLYFIN_API_KEY", ""),
    "jellyfin.anime_library_ids": ("JELLYFIN_ANIME_LIBRARY_IDS", "[]"),
    "sonarr.url": ("SONARR_URL", ""),
    "sonarr.api_key": ("SONARR_API_KEY", ""),
    "sonarr.anime_root_folder": ("SONARR_ANIME_ROOT_FOLDER", ""),
    "sonarr.path_prefix": ("SONARR_PATH_PREFIX", ""),
    "sonarr.local_path_prefix": ("SONARR_LOCAL_PATH_PREFIX", ""),
    "radarr.url": ("RADARR_URL", ""),
    "radarr.api_key": ("RADARR_API_KEY", ""),
    "radarr.anime_root_folder": ("RADARR_ANIME_ROOT_FOLDER", ""),
    "radarr.path_prefix": ("RADARR_PATH_PREFIX", ""),
    "radarr.local_path_prefix": ("RADARR_LOCAL_PATH_PREFIX", ""),
    "app.base_url": ("APP_BASE_URL", "http://localhost:9876"),
    "scheduler.sync_interval_minutes": ("SYNC_INTERVAL", "15"),
    "scheduler.scan_interval_hours": ("SCAN_INTERVAL", "24"),
    "scheduler.cr_sync_time": ("CR_SYNC_TIME", "02:00"),
    "scheduler.library_reindex_interval_hours": ("LIBRARY_REINDEX_INTERVAL", "6"),
    "scheduler.watchlist_refresh_interval_minutes": (
        "WATCHLIST_REFRESH_INTERVAL",
        "30",
    ),
    "app.debug": ("DEBUG", "false"),
    "app.title_display": ("TITLE_DISPLAY", "romaji"),
    "restructure.plex_path_prefix": ("RESTRUCTURE_PLEX_PREFIX", ""),
    "restructure.local_path_prefix": ("RESTRUCTURE_LOCAL_PREFIX", ""),
    "naming.file_template": ("NAMING_FILE_TEMPLATE", "{title} - S{season}E{episode}"),
    "naming.folder_template": ("NAMING_FOLDER_TEMPLATE", "{title}"),
    "naming.season_folder_template": (
        "NAMING_SEASON_FOLDER_TEMPLATE",
        "Season {season}",
    ),
    "naming.movie_file_template": (
        "NAMING_MOVIE_FILE_TEMPLATE",
        "{title} [{year}]",
    ),
    "naming.illegal_char_replacement": (
        "NAMING_ILLEGAL_CHAR_REPLACEMENT",
        "",
    ),
    "downloads.auto_statuses": ("DOWNLOAD_AUTO_STATUSES", ""),
    "downloads.monitor_mode": ("DOWNLOAD_MONITOR_MODE", "future"),
    "downloads.auto_search": ("DOWNLOAD_AUTO_SEARCH", "false"),
    "downloads.sync_interval_minutes": ("DOWNLOAD_SYNC_INTERVAL", "60"),
    "downloads.arr_enabled": ("DOWNLOADS_ARR_ENABLED", "true"),
}

# Keys that represent secret values (passwords, tokens, api keys)
SECRET_KEYS: set[str] = {
    "crunchyroll.password",
    "anilist.client_secret",
    "plex.token",
    "jellyfin.api_key",
    "sonarr.api_key",
    "radarr.api_key",
}


def _resolve(
    key: str,
    db_settings: dict[str, dict[str, object]],
) -> str:
    """Resolve a setting value: env var > DB value > code default."""
    env_var, code_default = SETTINGS_MAP[key]
    env_val = os.environ.get(env_var, "")
    if env_val:
        return env_val
    db_entry = db_settings.get(key)
    if db_entry and db_entry["value"]:
        return str(db_entry["value"])
    return code_default


def get_env_overrides() -> set[str]:
    """Return the set of setting keys currently overridden by env vars."""
    overrides: set[str] = set()
    for key, (env_var, _default) in SETTINGS_MAP.items():
        if os.environ.get(env_var, ""):
            overrides.add(key)
    return overrides


def load_config_from_db_settings(
    db_settings: dict[str, dict[str, object]],
) -> AppConfig:
    """Build AppConfig by merging env vars, DB settings, and code defaults.

    Resolution per field: env var > DB value > code default.
    """

    def r(key: str) -> str:
        return _resolve(key, db_settings)

    host = _env("HOST", "0.0.0.0")
    port = _env_int("PORT", 9876)
    debug = r("app.debug").lower() in ("true", "1", "yes")

    return AppConfig(
        debug=debug,
        timezone=_env("TZ", "UTC"),
        host=host,
        port=port,
        base_url=r("app.base_url") or f"http://localhost:{port}",
        log_path=_resolve_log_path(),
        anilist=AniListConfig(
            client_id=r("anilist.client_id"),
            client_secret=r("anilist.client_secret"),
            redirect_uri=f"http://{_redirect_host(host)}:{port}/auth/anilist/callback",
        ),
        crunchyroll=CrunchyrollConfig(
            email=r("crunchyroll.email"),
            password=r("crunchyroll.password"),
            flaresolverr_url=r("crunchyroll.flaresolverr_url"),
            headless=r("crunchyroll.headless").lower() in ("true", "1", "yes"),
            max_pages=int(r("crunchyroll.max_pages") or "10"),
            auto_sync_enabled=r("crunchyroll.auto_sync_enabled").lower()
            in ("true", "1", "yes"),
            auto_approve=r("crunchyroll.auto_approve").lower() in ("true", "1", "yes"),
        ),
        plex=PlexConfig(
            url=r("plex.url"),
            token=r("plex.token"),
            anime_library_keys=_parse_json_list(r("plex.anime_library_keys")),
        ),
        jellyfin=JellyfinConfig(
            url=r("jellyfin.url"),
            api_key=r("jellyfin.api_key"),
            anime_library_ids=_parse_json_list(r("jellyfin.anime_library_ids")),
        ),
        sonarr=SonarrConfig(
            url=r("sonarr.url"),
            api_key=r("sonarr.api_key"),
            anime_root_folder=r("sonarr.anime_root_folder"),
            path_prefix=r("sonarr.path_prefix"),
            local_path_prefix=r("sonarr.local_path_prefix"),
        ),
        radarr=RadarrConfig(
            url=r("radarr.url"),
            api_key=r("radarr.api_key"),
            anime_root_folder=r("radarr.anime_root_folder"),
            path_prefix=r("radarr.path_prefix"),
            local_path_prefix=r("radarr.local_path_prefix"),
        ),
        database=DatabaseConfig(path=_resolve_db_path()),
        scheduler=SchedulerConfig(
            scan_interval_hours=int(r("scheduler.scan_interval_hours") or "24"),
            sync_interval_minutes=int(r("scheduler.sync_interval_minutes") or "15"),
            cr_sync_time=r("scheduler.cr_sync_time"),
            library_reindex_interval_hours=int(
                r("scheduler.library_reindex_interval_hours") or "6"
            ),
            watchlist_refresh_interval_minutes=int(
                r("scheduler.watchlist_refresh_interval_minutes") or "30"
            ),
        ),
        download_sync=DownloadSyncConfig(
            auto_statuses=tuple(
                s.strip()
                for s in (r("downloads.auto_statuses") or "").split(",")
                if s.strip()
            ),
            monitor_mode=r("downloads.monitor_mode") or "future",
            auto_search=(r("downloads.auto_search") or "false").lower()
            in ("true", "1", "yes"),
            sync_interval_minutes=int(r("downloads.sync_interval_minutes") or "60"),
            arr_enabled=(r("downloads.arr_enabled") or "true").lower()
            not in ("false", "0", "no"),
        ),
    )
