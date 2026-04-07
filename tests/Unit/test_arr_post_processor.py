"""Unit tests for ArrPostProcessor — dry-run and naming template behaviour."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.Download.ArrPostProcessor import ArrPostProcessor
from src.Utils.Config import AppConfig, RadarrConfig, SonarrConfig

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


def _make_db(
    folder_template: str = "{title}",
    illegal_char_repl: str = "",
    library_path: str = "/anime",
) -> MagicMock:
    """Return a mock DatabaseManager that returns the given naming settings."""
    db = MagicMock()

    async def get_setting(key: str) -> str | None:
        if key == "naming.folder_template":
            return folder_template
        if key == "naming.illegal_char_replacement":
            return illegal_char_repl
        if key == "app.title_display":
            return "romaji"
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
        return {
            "title_romaji": "Re:Zero kara Hajimeru Isekai Seikatsu",
            "title_english": "Re:ZERO",
        }

    db.get_cached_metadata = get_cached_metadata

    async def get_all_libraries() -> list[dict[str, Any]]:
        if library_path:
            import json

            return [{"id": 1, "name": "Anime", "paths": json.dumps([library_path])}]
        return []

    db.get_all_libraries = get_all_libraries

    async def get_series_group_by_anilist_id(anilist_id: int) -> None:
        return None  # No series group by default

    db.get_series_group_by_anilist_id = get_series_group_by_anilist_id

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
# Helpers — title_info dicts
# ---------------------------------------------------------------------------


def _title_info(
    title: str = "Test", romaji: str = "", english: str = "", year: int = 0
) -> dict:
    return {
        "title": title,
        "title_romaji": romaji or title,
        "title_english": english or title,
        "year": year,
    }


# ---------------------------------------------------------------------------
# Tests — _get_folder_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_folder_name_default_template() -> None:
    """Default {title} template returns sanitized AniList title."""
    db = _make_db(folder_template="{title}")
    processor = ArrPostProcessor(db=db, config=_make_config())
    name = await processor._get_folder_name(
        _title_info("Re:Zero kara Hajimeru Isekai Seikatsu")
    )
    assert name == "ReZero kara Hajimeru Isekai Seikatsu"


@pytest.mark.asyncio
async def test_get_folder_name_with_year_template() -> None:
    """{title} [{year}] template includes year when provided."""
    db = _make_db(folder_template="{title} [{year}]")
    processor = ArrPostProcessor(db=db, config=_make_config())
    name = await processor._get_folder_name(_title_info("Attack on Titan", year=2013))
    assert name == "Attack on Titan [2013]"


@pytest.mark.asyncio
async def test_get_folder_name_year_omitted_when_zero() -> None:
    """{title} [{year}] collapses to just title when year is 0."""
    db = _make_db(folder_template="{title} [{year}]")
    processor = ArrPostProcessor(db=db, config=_make_config())
    name = await processor._get_folder_name(_title_info("Attack on Titan", year=0))
    # Empty year → "Attack on Titan []" → NamingTemplate cleanup removes "[]"
    assert name == "Attack on Titan"


@pytest.mark.asyncio
async def test_get_folder_name_illegal_char_hyphen() -> None:
    """Illegal char replacement 'hyphen' replaces : with -."""
    db = _make_db(folder_template="{title}", illegal_char_repl="-")
    processor = ArrPostProcessor(db=db, config=_make_config())
    name = await processor._get_folder_name(_title_info("Re:Zero"))
    assert name == "Re-Zero"


@pytest.mark.asyncio
async def test_get_folder_name_romaji_vs_english() -> None:
    """{title.romaji} uses romaji, {title.english} uses english."""
    db = _make_db(folder_template="{title.romaji}")
    processor = ArrPostProcessor(db=db, config=_make_config())
    info = _title_info(
        title="Shingeki no Kyojin",
        romaji="Shingeki no Kyojin",
        english="Attack on Titan",
    )
    name = await processor._get_folder_name(info)
    assert name == "Shingeki no Kyojin"


# ---------------------------------------------------------------------------
# Tests — reprocess_sonarr_series (dry_run)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reprocess_works_without_path_prefix() -> None:
    """Reprocess works when path prefixes are empty (same-host setup)."""
    series_path = "/anime/Re Zero"
    episode_files = [
        {"id": 1, "path": "/anime/Re Zero/Season 1/ep01.mkv", "_season": 1},
    ]

    db = _make_db(folder_template="{title}")
    config = AppConfig(sonarr=SonarrConfig(url="http://sonarr", api_key="k"))
    processor = ArrPostProcessor(db=db, config=config)

    mock_client = _make_sonarr_client(series_path, episode_files)
    with patch("src.Download.ArrPostProcessor.SonarrClient", return_value=mock_client):
        result = await processor.reprocess_sonarr_series(42, dry_run=True)

    assert result["ok"] is True
    f = result["files"][0]
    # With no prefix, arr and local paths should be identical
    assert f["arr_from"] == f["local_from"]
    assert f["arr_to"] == f["local_to"]


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
    # Source arr paths use /media/tv
    assert f["arr_from"].startswith("/media/tv/")
    # Source local paths use /mnt/media/tv
    assert f["local_from"].startswith("/mnt/media/tv/")
    # Target should be in the library path (/anime)
    assert f["local_to"].startswith("/anime/")
    assert f["arr_to"].startswith("/anime/")
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
    # File already in the correct library subfolder (with season folder)
    episode_files = [
        {
            "id": 1,
            "path": f"/anime/{folder_name}/Season 1/ReZero.S01E01.mkv",
            "_season": 1,
        },
    ]

    db = _make_db(folder_template="{title}", library_path="/anime")

    # Return only romaji (no english title) so sanitized name matches folder_name
    async def get_cached_metadata_romaji_only(anilist_id: int) -> dict[str, Any]:
        return {"title_romaji": "Re:Zero kara Hajimeru Isekai Seikatsu"}

    db.get_cached_metadata = get_cached_metadata_romaji_only

    config = _make_config(sonarr_path_prefix="", sonarr_local_prefix="")
    processor = ArrPostProcessor(db=db, config=config)

    mock_client = _make_sonarr_client(series_path, episode_files)
    with patch("src.Download.ArrPostProcessor.SonarrClient", return_value=mock_client):
        result = await processor.reprocess_sonarr_series(42, dry_run=True)

    assert result["ok"] is True
    assert result["files"][0]["action"] == "skip"


# ---------------------------------------------------------------------------
# Tests — process_sonarr_download (webhook handler)
# ---------------------------------------------------------------------------


def _sonarr_payload(
    series_id: int = 42,
    file_id: int = 1,
    file_path: str = "/media/tv/Re Zero/Season 1/ReZero.S01E01.mkv",
    series_path: str = "/media/tv/Re Zero",
    season_number: int = 1,
) -> dict[str, Any]:
    return {
        "eventType": "Download",
        "series": {"id": series_id, "path": series_path},
        "episodeFile": {"id": file_id, "path": file_path},
        "episodes": [{"seasonNumber": season_number}],
    }


@pytest.mark.asyncio
async def test_webhook_sonarr_uses_folder_template() -> None:
    """Webhook handler uses naming.folder_template, not bare sanitize."""
    db = _make_db(folder_template="{title} [{year}]")

    async def get_cached_metadata(anilist_id: int) -> dict[str, Any]:
        return {
            "title_romaji": "Attack on Titan",
            "title_english": "Attack on Titan",
            "year": 2013,
        }

    db.get_cached_metadata = get_cached_metadata

    config = _make_config(sonarr_path_prefix="", sonarr_local_prefix="")
    processor = ArrPostProcessor(db=db, config=config)

    moved_paths: list[tuple[str, str]] = []

    def fake_move(src: str, dst: str) -> bool:
        moved_paths.append((src, dst))
        return True

    mock_sonarr = MagicMock()
    mock_sonarr.update_episode_file = AsyncMock()
    mock_sonarr.close = AsyncMock()

    with (
        patch.object(ArrPostProcessor, "_move_file", side_effect=fake_move),
        patch("src.Download.ArrPostProcessor.SonarrClient", return_value=mock_sonarr),
    ):
        await processor.process_sonarr_download(
            _sonarr_payload(
                file_path="/media/tv/Re Zero/Season 1/ep01.mkv",
                series_path="/media/tv/Re Zero",
            )
        )

    assert len(moved_paths) == 1
    _, dst = moved_paths[0]
    # Should use template "{title} [{year}]" → "Attack on Titan [2013]"
    assert "Attack on Titan [2013]" in dst
    # Should target the library path, not the Sonarr series path
    assert dst.startswith("/anime/")


@pytest.mark.asyncio
async def test_webhook_sonarr_path_prefix_translation() -> None:
    """Webhook handler translates arr paths to local and back."""
    db = _make_db(folder_template="{title}")

    async def get_cached_metadata(anilist_id: int) -> dict[str, Any]:
        return {"title_romaji": "Naruto", "title_english": "Naruto"}

    db.get_cached_metadata = get_cached_metadata

    config = _make_config(
        sonarr_path_prefix="/media/tv",
        sonarr_local_prefix="/mnt/media/tv",
    )
    processor = ArrPostProcessor(db=db, config=config)

    moved_paths: list[tuple[str, str]] = []

    def fake_move(src: str, dst: str) -> bool:
        moved_paths.append((src, dst))
        return True

    mock_sonarr = MagicMock()
    mock_sonarr.update_episode_file = AsyncMock()
    mock_sonarr.close = AsyncMock()

    with (
        patch.object(ArrPostProcessor, "_move_file", side_effect=fake_move),
        patch("src.Download.ArrPostProcessor.SonarrClient", return_value=mock_sonarr),
    ):
        await processor.process_sonarr_download(
            _sonarr_payload(
                file_path="/media/tv/Naruto/Season 1/ep01.mkv",
                series_path="/media/tv/Naruto",
            )
        )

    assert len(moved_paths) == 1
    src, dst = moved_paths[0]
    # Source should be translated to local prefix
    assert src.startswith("/mnt/media/tv/")
    # Target should be in the library path (no prefix translation needed for /anime)
    assert dst.startswith("/anime/")


@pytest.mark.asyncio
async def test_webhook_radarr_uses_folder_template() -> None:
    """Radarr webhook handler uses naming.folder_template."""
    db = _make_db(folder_template="{title} [{year}]")

    async def get_cached_metadata(anilist_id: int) -> dict[str, Any]:
        return {
            "title_romaji": "Kimi no Na wa",
            "title_english": "Your Name",
            "year": 2016,
        }

    db.get_cached_metadata = get_cached_metadata

    # Radarr mapping lookup
    async def fetch_one(query: str, params: tuple = ()) -> dict[str, Any] | None:
        if "anilist_radarr_mapping" in query:
            return {"anilist_id": 21519}
        return {"anilist_id": 21519}

    db.fetch_one = fetch_one

    config = AppConfig(
        radarr=RadarrConfig(
            url="http://radarr:7878",
            api_key="testkey",
            path_prefix="",
            local_path_prefix="",
        )
    )
    processor = ArrPostProcessor(db=db, config=config)

    moved_paths: list[tuple[str, str]] = []

    def fake_move(src: str, dst: str) -> bool:
        moved_paths.append((src, dst))
        return True

    mock_radarr = MagicMock()
    mock_radarr.update_movie_file = AsyncMock()
    mock_radarr.close = AsyncMock()

    with (
        patch.object(ArrPostProcessor, "_move_file", side_effect=fake_move),
        patch("src.Download.ArrPostProcessor.RadarrClient", return_value=mock_radarr),
    ):
        await processor.process_radarr_download(
            {
                "eventType": "Download",
                "movie": {
                    "id": 10,
                    "folderPath": "/movies/Your Name",
                },
                "movieFile": {
                    "id": 5,
                    "path": "/movies/Your Name/Your.Name.2016.mkv",
                },
            }
        )

    assert len(moved_paths) == 1
    _, dst = moved_paths[0]
    # Title pref is romaji → "Kimi no Na wa [2016]" (not english "Your Name")
    assert "Kimi no Na wa [2016]" in dst
