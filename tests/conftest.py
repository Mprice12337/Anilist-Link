"""Shared pytest fixtures for the Anilist-Link test suite."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio

from src.Database.Connection import DatabaseManager
from src.Database.Migrations import run_migrations
from src.Utils.Config import (
    AniListConfig,
    AppConfig,
    CrunchyrollConfig,
    DatabaseConfig,
    JellyfinConfig,
    PlexConfig,
    RadarrConfig,
    SchedulerConfig,
    SonarrConfig,
)


@pytest.fixture
def config() -> AppConfig:
    """Return a minimal AppConfig with sensible defaults for testing."""
    return AppConfig(
        debug=True,
        timezone="UTC",
        host="127.0.0.1",
        port=9876,
        base_url="http://localhost:9876",
        log_path=None,
        anilist=AniListConfig(client_id="test-id", client_secret="test-secret"),
        crunchyroll=CrunchyrollConfig(),
        plex=PlexConfig(url="http://plex:32400", token="test-plex-token"),
        jellyfin=JellyfinConfig(url="http://jellyfin:8096", api_key="test-jf-key"),
        sonarr=SonarrConfig(url="http://sonarr:8989", api_key="test-sonarr-key"),
        radarr=RadarrConfig(url="http://radarr:7878", api_key="test-radarr-key"),
        database=DatabaseConfig(path=Path(":memory:")),
        scheduler=SchedulerConfig(),
    )


@pytest_asyncio.fixture
async def db() -> DatabaseManager:
    """Provide an in-memory DatabaseManager with all migrations applied."""
    manager = DatabaseManager(db_path=Path(":memory:"))
    # Bypass the normal initialize() which calls mkdir on the parent path.
    # Instead, open an in-memory connection directly and run migrations.
    manager._db = await aiosqlite.connect(":memory:")
    manager._db.row_factory = aiosqlite.Row
    await manager._db.execute("PRAGMA foreign_keys=ON")
    await run_migrations(manager._db)

    yield manager

    await manager.close()


@pytest.fixture
def mock_anilist_client() -> AsyncMock:
    """Return an AsyncMock of AniListClient."""
    mock = AsyncMock()
    mock.search_anime.return_value = []
    mock.get_anime.return_value = None
    mock.get_user_list.return_value = []
    mock.update_entry.return_value = True
    mock.close.return_value = None
    return mock


@pytest.fixture
def mock_plex_client() -> AsyncMock:
    """Return an AsyncMock of PlexClient."""
    mock = AsyncMock()
    mock.get_libraries.return_value = []
    mock.get_shows.return_value = []
    mock.get_seasons.return_value = []
    mock.get_episodes.return_value = []
    mock.close.return_value = None
    return mock


@pytest.fixture
def mock_jellyfin_client() -> AsyncMock:
    """Return an AsyncMock of JellyfinClient."""
    mock = AsyncMock()
    mock.get_libraries.return_value = []
    mock.get_shows.return_value = []
    mock.get_seasons.return_value = []
    mock.get_episodes.return_value = []
    mock.close.return_value = None
    return mock
