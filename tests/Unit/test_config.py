"""Tests for Config dataclasses and load_config()."""

import dataclasses
from pathlib import Path

import pytest

from src.Utils.Config import (
    AniListConfig,
    AppConfig,
    CrunchyrollConfig,
    DatabaseConfig,
    DownloadSyncConfig,
    JellyfinConfig,
    PlexConfig,
    RadarrConfig,
    SchedulerConfig,
    SonarrConfig,
    load_config,
    _env_bool,
    _env_int,
    _parse_json_list,
    _redirect_host,
)


# ---------------------------------------------------------------------------
# Dataclass construction
# ---------------------------------------------------------------------------


class TestDataclassConstruction:
    def test_anilist_config_defaults(self) -> None:
        cfg = AniListConfig(client_id="id", client_secret="secret")
        assert cfg.client_id == "id"
        assert cfg.client_secret == "secret"
        assert cfg.redirect_uri == ""

    def test_plex_config_defaults(self) -> None:
        cfg = PlexConfig()
        assert cfg.url == ""
        assert cfg.token == ""
        assert cfg.anime_library_keys == ()

    def test_jellyfin_config_defaults(self) -> None:
        cfg = JellyfinConfig()
        assert cfg.url == ""
        assert cfg.api_key == ""

    def test_sonarr_config_defaults(self) -> None:
        cfg = SonarrConfig()
        assert cfg.url == ""
        assert cfg.api_key == ""
        assert cfg.anime_root_folder == ""

    def test_radarr_config_defaults(self) -> None:
        cfg = RadarrConfig()
        assert cfg.url == ""
        assert cfg.api_key == ""

    def test_crunchyroll_config_defaults(self) -> None:
        cfg = CrunchyrollConfig()
        assert cfg.email == ""
        assert cfg.headless is True
        assert cfg.max_pages == 10
        assert cfg.auto_sync_enabled is True
        assert cfg.auto_approve is False

    def test_scheduler_config_defaults(self) -> None:
        cfg = SchedulerConfig()
        assert cfg.scan_interval_hours == 24
        assert cfg.sync_interval_minutes == 15
        assert cfg.cr_sync_time == "02:00"
        assert cfg.watchlist_refresh_interval_minutes == 30

    def test_database_config_default_path(self) -> None:
        cfg = DatabaseConfig()
        assert cfg.path == Path("./data/anilist_link.db")

    def test_download_sync_config_defaults(self) -> None:
        cfg = DownloadSyncConfig()
        assert cfg.auto_statuses == ("CURRENT",)
        assert cfg.monitor_mode == "future"
        assert cfg.auto_search is False
        assert cfg.sync_interval_minutes == 60
        assert cfg.arr_enabled is True

    def test_app_config_full_construction(self) -> None:
        cfg = AppConfig(
            debug=True,
            timezone="America/New_York",
            host="127.0.0.1",
            port=8080,
            base_url="http://localhost:8080",
            anilist=AniListConfig(client_id="cid", client_secret="csec"),
            plex=PlexConfig(url="http://plex:32400", token="tok"),
        )
        assert cfg.debug is True
        assert cfg.timezone == "America/New_York"
        assert cfg.port == 8080
        assert cfg.anilist.client_id == "cid"
        assert cfg.plex.url == "http://plex:32400"


# ---------------------------------------------------------------------------
# Frozen dataclass immutability
# ---------------------------------------------------------------------------


class TestFrozenImmutability:
    def test_anilist_config_immutable(self) -> None:
        cfg = AniListConfig(client_id="id", client_secret="secret")
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.client_id = "other"  # type: ignore[misc]

    def test_app_config_immutable(self) -> None:
        cfg = AppConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.debug = True  # type: ignore[misc]

    def test_plex_config_immutable(self) -> None:
        cfg = PlexConfig(url="http://plex:32400")
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.url = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_env_bool_true_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("true", "1", "yes", "True", "YES"):
            monkeypatch.setenv("TEST_BOOL", val)
            assert _env_bool("TEST_BOOL") is True

    def test_env_bool_false_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("false", "0", "no", "anything"):
            monkeypatch.setenv("TEST_BOOL", val)
            assert _env_bool("TEST_BOOL") is False

    def test_env_bool_missing_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_BOOL", raising=False)
        assert _env_bool("TEST_BOOL") is False
        assert _env_bool("TEST_BOOL", True) is True

    def test_env_int_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT", "42")
        assert _env_int("TEST_INT", 10) == 42

    def test_env_int_invalid_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT", "notanumber")
        assert _env_int("TEST_INT", 10) == 10

    def test_env_int_missing_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_INT", raising=False)
        assert _env_int("TEST_INT", 99) == 99

    def test_redirect_host_converts_wildcard(self) -> None:
        assert _redirect_host("0.0.0.0") == "localhost"
        assert _redirect_host("::") == "localhost"
        assert _redirect_host("") == "localhost"

    def test_redirect_host_keeps_specific(self) -> None:
        assert _redirect_host("192.168.1.100") == "192.168.1.100"
        assert _redirect_host("myhost") == "myhost"

    def test_parse_json_list_valid(self) -> None:
        assert _parse_json_list('["1", "2", "3"]') == ("1", "2", "3")

    def test_parse_json_list_empty(self) -> None:
        assert _parse_json_list("[]") == ()

    def test_parse_json_list_invalid_json(self) -> None:
        assert _parse_json_list("not json") == ()

    def test_parse_json_list_non_list(self) -> None:
        assert _parse_json_list('{"key": "val"}') == ()


# ---------------------------------------------------------------------------
# load_config with env vars
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_load_config_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config returns sensible defaults when no env vars are set."""
        # Clear relevant env vars to ensure defaults
        for var in (
            "DEBUG", "PORT", "HOST", "TZ",
            "ANILIST_CLIENT_ID", "ANILIST_CLIENT_SECRET",
            "PLEX_URL", "PLEX_TOKEN",
            "JELLYFIN_URL", "JELLYFIN_API_KEY",
            "SONARR_URL", "SONARR_API_KEY",
            "RADARR_URL", "RADARR_API_KEY",
            "CRUNCHYROLL_EMAIL", "CRUNCHYROLL_PASSWORD",
            "FLARESOLVERR_URL", "HEADLESS_MODE",
            "MAX_PAGES", "CR_AUTO_SYNC_ENABLED", "CR_AUTO_APPROVE",
            "SCAN_INTERVAL", "SYNC_INTERVAL", "CR_SYNC_TIME",
            "PLEX_ANIME_LIBRARIES",
            "DOWNLOAD_AUTO_STATUSES", "DOWNLOAD_MONITOR_MODE",
            "DOWNLOAD_AUTO_SEARCH", "DOWNLOAD_SYNC_INTERVAL",
        ):
            monkeypatch.delenv(var, raising=False)

        cfg = load_config()
        assert cfg.debug is False
        assert cfg.timezone == "UTC"
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 9876
        assert cfg.anilist.client_id == ""
        assert cfg.anilist.client_secret == ""
        assert cfg.plex.url == ""
        assert cfg.crunchyroll.headless is True
        assert cfg.crunchyroll.max_pages == 10
        assert cfg.scheduler.scan_interval_hours == 24
        assert cfg.scheduler.sync_interval_minutes == 15

    def test_load_config_with_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config picks up env vars correctly."""
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("PORT", "8080")
        monkeypatch.setenv("HOST", "127.0.0.1")
        monkeypatch.setenv("TZ", "America/Chicago")
        monkeypatch.setenv("ANILIST_CLIENT_ID", "my-client-id")
        monkeypatch.setenv("ANILIST_CLIENT_SECRET", "my-secret")
        monkeypatch.setenv("PLEX_URL", "http://plex:32400")
        monkeypatch.setenv("PLEX_TOKEN", "plex-tok")
        monkeypatch.setenv("JELLYFIN_URL", "http://jf:8096")
        monkeypatch.setenv("JELLYFIN_API_KEY", "jf-key")
        monkeypatch.setenv("SONARR_URL", "http://sonarr:8989")
        monkeypatch.setenv("SONARR_API_KEY", "sonarr-key")
        monkeypatch.setenv("RADARR_URL", "http://radarr:7878")
        monkeypatch.setenv("RADARR_API_KEY", "radarr-key")
        monkeypatch.setenv("MAX_PAGES", "20")
        monkeypatch.setenv("SCAN_INTERVAL", "12")
        monkeypatch.setenv("SYNC_INTERVAL", "30")

        cfg = load_config()
        assert cfg.debug is True
        assert cfg.port == 8080
        assert cfg.host == "127.0.0.1"
        assert cfg.timezone == "America/Chicago"
        assert cfg.anilist.client_id == "my-client-id"
        assert cfg.anilist.client_secret == "my-secret"
        assert cfg.anilist.redirect_uri == "http://127.0.0.1:8080/auth/anilist/callback"
        assert cfg.plex.url == "http://plex:32400"
        assert cfg.plex.token == "plex-tok"
        assert cfg.jellyfin.url == "http://jf:8096"
        assert cfg.jellyfin.api_key == "jf-key"
        assert cfg.sonarr.url == "http://sonarr:8989"
        assert cfg.radarr.url == "http://radarr:7878"
        assert cfg.crunchyroll.max_pages == 20
        assert cfg.scheduler.scan_interval_hours == 12
        assert cfg.scheduler.sync_interval_minutes == 30

    def test_load_config_redirect_uri_uses_localhost_for_wildcard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When HOST is 0.0.0.0, redirect_uri uses localhost."""
        monkeypatch.setenv("HOST", "0.0.0.0")
        monkeypatch.setenv("PORT", "9876")
        monkeypatch.setenv("ANILIST_CLIENT_ID", "")
        monkeypatch.setenv("ANILIST_CLIENT_SECRET", "")
        cfg = load_config()
        assert "localhost" in cfg.anilist.redirect_uri
        assert "0.0.0.0" not in cfg.anilist.redirect_uri

    def test_load_config_plex_anime_libraries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PLEX_ANIME_LIBRARIES", '["1", "3", "5"]')
        cfg = load_config()
        assert cfg.plex.anime_library_keys == ("1", "3", "5")

    def test_load_config_download_sync(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DOWNLOAD_AUTO_STATUSES", "CURRENT,PLANNING")
        monkeypatch.setenv("DOWNLOAD_MONITOR_MODE", "all")
        monkeypatch.setenv("DOWNLOAD_AUTO_SEARCH", "true")
        monkeypatch.setenv("DOWNLOAD_SYNC_INTERVAL", "30")
        cfg = load_config()
        assert cfg.download_sync.auto_statuses == ("CURRENT", "PLANNING")
        assert cfg.download_sync.monitor_mode == "all"
        assert cfg.download_sync.auto_search is True
        assert cfg.download_sync.sync_interval_minutes == 30
