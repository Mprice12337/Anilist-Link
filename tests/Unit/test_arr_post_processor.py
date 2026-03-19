"""Unit tests for ArrPostProcessor — dry-run and naming template behaviour."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.Download.ArrPostProcessor import ArrPostProcessor
from src.Utils.Config import AppConfig, SonarrConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    sonarr_path_prefix: str = "/media/tv",
    sonarr_local_prefix: str = "/mnt/media/tv",
) -> AppConfig:
    """Return a minimal AppConfig with Sonarr path prefixes set."""
    return AppConfig(
        sonarr=SonarrConfig(
            url="http://sonarr:8989",
            api_key="testkey",
            path_prefix=sonarr_path_prefix,
            local_path_prefix=sonarr_local_prefix,
        )
    )


def _make_db(folder_template: str = "{title}", illegal_char_repl: str = "") -> MagicMock:
    """Return a mock DatabaseManager that returns the given naming settings."""
    db = MagicMock()

    async def get_setting(key: str) -> str | None:
        if key == "naming.folder_template":
            return folder_template
        if key == "naming.illegal_char_replacement":
            return illegal_char_repl
        return None

    db.get_setting = get_setting

    async def fetch_one(query: str, params: tuple = ()) -> dict[str, Any] | None:
        # Return a fake AniList mapping for any sonarr_id/season combo
        return {"anilist_id": 21234}

    db.fetch_one = fetch_one

    async def get_users_by_service(service: str) -> list:
        return []

    db.get_users_by_service = get_users_by_service

    async def get_watchlist_entry(*args: Any) -> None:
        return None

    db.get_watchlist_entry = get_watchlist_entry

    async def get_cached_metadata(anilist_id: int) -> dict[str, Any] | None:
        return {"title_romaji": "Re:Zero kara Hajimeru Isekai Seikatsu", "title_english": "Re:ZERO"}

    db.get_cached_metadata = get_cached_metadata

    return db


def _make_sonarr_client(series_path: str, episode_files: list[dict]) -> MagicMock:
    """Return a mock SonarrClient."""
    client = MagicMock()

    async def get_series_by_id(series_id: int) -> dict[str, Any]:
        return {"id": series_id, "title": "Re:ZERO", "path": series_path}

    async def get_episodes(series_id: int) -> list[dict[str, Any]]:
        return [
            {"episodeFileId": ef["id"], "seasonNumber": ef.get("_season", 1)}
            for ef in episode_files
        ]

    async def get_episode_files(series_id: int) -> list[dict[str, Any]]:
        return episode_files

    async def close() -> None:
        pass

    client.get_series_by_id = get_series_by_id
    client.get_episodes = get_episodes
    client.get_episode_files = get_episode_files
    client.close = close
    return client


# ---------------------------------------------------------------------------
# Tests — _get_folder_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_folder_name_default_template() -> None:
    """Default {title} template returns sanitized AniList title."""
    db = _make_db(folder_template="{title}")
    processor = ArrPostProcessor(db=db, config=_make_config())
    name = await processor._get_folder_name("Re:Zero kara Hajimeru Isekai Seikatsu")
    assert name == "ReZero kara Hajimeru Isekai Seikatsu"


@pytest.mark.asyncio
async def test_get_folder_name_with_year_template() -> None:
    """{title} [{year}] template includes year when provided."""
    db = _make_db(folder_template="{title} [{year}]")
    processor = ArrPostProcessor(db=db, config=_make_config())
    name = await processor._get_folder_name("Attack on Titan", year=2013)
    assert name == "Attack on Titan [2013]"


@pytest.mark.asyncio
async def test_get_folder_name_year_omitted_when_zero() -> None:
    """{title} [{year}] collapses to just title when year is 0."""
    db = _make_db(folder_template="{title} [{year}]")
    processor = ArrPostProcessor(db=db, config=_make_config())
    name = await processor._get_folder_name("Attack on Titan", year=0)
    # Empty year → "Attack on Titan []" → NamingTemplate cleanup removes "[]"
    assert name == "Attack on Titan"


@pytest.mark.asyncio
async def test_get_folder_name_illegal_char_hyphen() -> None:
    """Illegal char replacement 'hyphen' replaces : with -."""
    db = _make_db(folder_template="{title}", illegal_char_repl="-")
    processor = ArrPostProcessor(db=db, config=_make_config())
    name = await processor._get_folder_name("Re:Zero")
    assert name == "Re-Zero"


# ---------------------------------------------------------------------------
# Tests — reprocess_sonarr_series (dry_run)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reprocess_requires_path_prefix() -> None:
    """Returns a clear error when path prefix is not configured."""
    config = AppConfig(sonarr=SonarrConfig(url="http://sonarr", api_key="k"))
    db = _make_db()
    processor = ArrPostProcessor(db=db, config=config)
    result = await processor.reprocess_sonarr_series(123, dry_run=True)
    assert result["ok"] is False
    assert "path prefix" in result["error"].lower() or "local filesystem" in result["error"].lower()


@pytest.mark.asyncio
async def test_reprocess_dry_run_path_translation() -> None:
    """Dry run translates arr paths to local and back correctly."""
    series_path = "/media/tv/Re Zero"
    episode_files = [
        {"id": 1, "path": "/media/tv/Re Zero/Season 1/ReZero.S01E01.mkv", "_season": 1},
        {"id": 2, "path": "/media/tv/Re Zero/Season 1/ReZero.S01E02.mkv", "_season": 1},
    ]

    db = _make_db(folder_template="{title}")
    config = _make_config(
        sonarr_path_prefix="/media/tv",
        sonarr_local_prefix="/mnt/media/tv",
    )
    processor = ArrPostProcessor(db=db, config=config)

    mock_client = _make_sonarr_client(series_path, episode_files)
    with patch("src.Download.ArrPostProcessor.SonarrClient", return_value=mock_client):
        result = await processor.reprocess_sonarr_series(42, dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert len(result["files"]) == 2

    f = result["files"][0]
    # arr paths use /media/tv
    assert f["arr_from"].startswith("/media/tv/")
    assert f["arr_to"].startswith("/media/tv/")
    # local paths use /mnt/media/tv
    assert f["local_from"].startswith("/mnt/media/tv/")
    assert f["local_to"].startswith("/mnt/media/tv/")
    # action is move (not already at target)
    assert f["action"] == "move"


@pytest.mark.asyncio
async def test_reprocess_dry_run_uses_folder_template() -> None:
    """Dry run uses naming.folder_template for the subfolder name."""
    series_path = "/media/tv/Re Zero"
    episode_files = [
        {"id": 1, "path": "/media/tv/Re Zero/Season 1/ReZero.S01E01.mkv", "_season": 1},
    ]

    db = _make_db(folder_template="{title} [{year}]")

    # Override get_cached_metadata to include year (INTEGER column)
    async def get_cached_metadata(anilist_id: int) -> dict[str, Any]:
        return {
            "title_romaji": "Re:Zero kara Hajimeru Isekai Seikatsu",
            "title_english": "Re:ZERO",
            "year": 2016,
        }
    db.get_cached_metadata = get_cached_metadata

    config = _make_config()
    processor = ArrPostProcessor(db=db, config=config)

    mock_client = _make_sonarr_client(series_path, episode_files)
    with patch("src.Download.ArrPostProcessor.SonarrClient", return_value=mock_client):
        result = await processor.reprocess_sonarr_series(42, dry_run=True)

    assert result["ok"] is True
    f = result["files"][0]
    # folder_name should include year from the {title} [{year}] template
    assert "2016" in f["folder_name"]
    assert "Re" in f["folder_name"] or "Zero" in f["folder_name"]


@pytest.mark.asyncio
async def test_reprocess_dry_run_skips_already_correct_paths() -> None:
    """Files already at target path are marked action=skip."""
    folder_name = "ReZero kara Hajimeru Isekai Seikatsu"
    series_path = "/media/tv/Re Zero"
    # File already in the correct subfolder
    episode_files = [
        {
            "id": 1,
            "path": f"/media/tv/Re Zero/{folder_name}/ReZero.S01E01.mkv",
            "_season": 1,
        },
    ]

    db = _make_db(folder_template="{title}")
    # Return only romaji (no english title) so sanitized name matches folder_name
    async def get_cached_metadata_romaji_only(anilist_id: int) -> dict[str, Any]:
        return {"title_romaji": "Re:Zero kara Hajimeru Isekai Seikatsu"}
    db.get_cached_metadata = get_cached_metadata_romaji_only

    config = _make_config()
    processor = ArrPostProcessor(db=db, config=config)

    mock_client = _make_sonarr_client(series_path, episode_files)
    with patch("src.Download.ArrPostProcessor.SonarrClient", return_value=mock_client):
        result = await processor.reprocess_sonarr_series(42, dry_run=True)

    assert result["ok"] is True
    assert result["files"][0]["action"] == "skip"
