"""Tests for SeriesGroupBuilder with mocked AniListClient and DatabaseManager."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio

from src.Database.Connection import DatabaseManager
from src.Database.Migrations import LATEST_VERSION
from src.Database.Models import INDEXES, TABLES
from src.Scanner.SeriesGroupBuilder import (
    SeriesGroupBuilder,
    _format_start_date,
    _start_date_sort_key,
)

# ---------------------------------------------------------------------------
# Helpers to build mock AniList relation data
# ---------------------------------------------------------------------------


def _make_entry(
    anilist_id: int,
    title: str,
    format: str = "TV",
    type: str = "ANIME",
    episodes: int = 12,
    year: int = 2020,
    month: int = 1,
    day: int = 1,
) -> dict[str, Any]:
    """Build a root_data dict as returned by AniListClient.get_anime_relations."""
    return {
        "id": anilist_id,
        "title": {"romaji": title},
        "format": format,
        "type": type,
        "episodes": episodes,
        "startDate": {"year": year, "month": month, "day": day},
    }


def _make_edge(
    anilist_id: int,
    title: str,
    relation_type: str,
    type: str = "ANIME",
    format: str = "TV",
    episodes: int = 12,
    year: int = 2021,
    month: int = 1,
    day: int = 1,
) -> dict[str, Any]:
    """Build a relation edge as it appears in the 'edges' list."""
    return {
        "relationType": relation_type,
        "node": {
            "id": anilist_id,
            "type": type,
            "format": format,
            "title": {"romaji": title},
            "episodes": episodes,
            "startDate": {"year": year, "month": month, "day": day},
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def real_db():
    """Provide a real in-memory DatabaseManager for integration-style tests."""
    manager = DatabaseManager(Path(":memory:"))
    # Bypass run_migrations to avoid ALTER TABLE conflicts on in-memory DB
    manager._db = await aiosqlite.connect(":memory:")
    manager._db.row_factory = aiosqlite.Row
    await manager._db.execute("PRAGMA foreign_keys=ON")
    for ddl in TABLES.values():
        await manager._db.execute(ddl)
    for idx in INDEXES:
        await manager._db.execute(idx)
    # Columns added by migrations via ALTER TABLE (not in base TABLES DDL)
    for stmt in [
        "ALTER TABLE media_mappings ADD COLUMN series_group_id INTEGER",
        "ALTER TABLE media_mappings ADD COLUMN season_number INTEGER",
    ]:
        try:
            await manager._db.execute(stmt)
        except Exception:
            pass
    await manager._db.execute(
        "INSERT INTO schema_version (version) VALUES (?)", (LATEST_VERSION,)
    )
    await manager._db.commit()
    yield manager
    await manager.close()


def _make_builder(
    db: DatabaseManager,
    relations_map: dict[int, tuple[dict, list[dict]]],
    max_age_hours: int = 168,
) -> SeriesGroupBuilder:
    """Create a SeriesGroupBuilder with a mocked AniListClient.

    *relations_map* maps anilist_id -> (root_data, edges) as returned by
    get_anime_relations.
    """
    mock_client = AsyncMock()

    async def _get_relations(anime_id: int):
        if anime_id in relations_map:
            return relations_map[anime_id]
        return None, []

    mock_client.get_anime_relations = AsyncMock(side_effect=_get_relations)
    return SeriesGroupBuilder(db, mock_client, max_age_hours=max_age_hours)


# ---------------------------------------------------------------------------
# Unit-level tests for helpers
# ---------------------------------------------------------------------------


class TestStartDateSortKey:
    def test_normal_date(self):
        entry = {"startDate": {"year": 2020, "month": 4, "day": 15}}
        assert _start_date_sort_key(entry) == (2020, 4, 15)

    def test_null_date_sorts_last(self):
        entry = {"startDate": None}
        assert _start_date_sort_key(entry) == (9999, 99, 99)

    def test_partial_date(self):
        entry = {"startDate": {"year": 2020, "month": None, "day": None}}
        assert _start_date_sort_key(entry) == (2020, 99, 99)

    def test_missing_start_date_key(self):
        entry = {}
        assert _start_date_sort_key(entry) == (9999, 99, 99)


class TestFormatStartDate:
    def test_normal(self):
        assert _format_start_date({"year": 2020, "month": 4, "day": 7}) == "2020-04-07"

    def test_null_returns_empty(self):
        assert _format_start_date(None) == ""

    def test_no_year_returns_empty(self):
        assert _format_start_date({"year": None, "month": 1, "day": 1}) == ""

    def test_missing_month_day_defaults(self):
        assert _format_start_date({"year": 2020}) == "2020-01-01"


# ---------------------------------------------------------------------------
# Integration tests with real DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_entry_no_relations(real_db: DatabaseManager):
    """A single entry with no relations should produce a group of 1."""
    entry = _make_entry(100, "Solo Anime")
    builder = _make_builder(real_db, {100: (entry, [])})

    group_id, entries = await builder.get_or_build_group(100)
    assert group_id > 0
    assert len(entries) == 1
    assert entries[0]["anilist_id"] == 100
    assert entries[0]["display_title"] == "Solo Anime"


@pytest.mark.asyncio
async def test_linear_chain_sequel(real_db: DatabaseManager):
    """A -> SEQUEL -> B -> SEQUEL -> C should produce a group of 3 sorted by date."""
    entry_a = _make_entry(1, "Anime S1", year=2019)
    entry_b = _make_entry(2, "Anime S2", year=2020)
    entry_c = _make_entry(3, "Anime S3", year=2021)

    relations = {
        1: (
            entry_a,
            [_make_edge(2, "Anime S2", "SEQUEL", year=2020)],
        ),
        2: (
            entry_b,
            [
                _make_edge(1, "Anime S1", "PREQUEL", year=2019),
                _make_edge(3, "Anime S3", "SEQUEL", year=2021),
            ],
        ),
        3: (
            entry_c,
            [_make_edge(2, "Anime S2", "PREQUEL", year=2020)],
        ),
    }
    builder = _make_builder(real_db, relations)
    group_id, entries = await builder.get_or_build_group(1)

    assert len(entries) == 3
    assert entries[0]["anilist_id"] == 1
    assert entries[1]["anilist_id"] == 2
    assert entries[2]["anilist_id"] == 3


@pytest.mark.asyncio
async def test_cached_group_returns_without_traversal(real_db: DatabaseManager):
    """If a group is cached and fresh, get_or_build_group should not re-traverse."""
    entry = _make_entry(100, "Cached Anime")
    relations = {100: (entry, [])}
    builder = _make_builder(real_db, relations, max_age_hours=168)

    # First call: builds group
    gid1, entries1 = await builder.get_or_build_group(100)
    assert len(entries1) == 1

    # Reset the mock call count
    builder._anilist.get_anime_relations.reset_mock()

    # Second call: should use cache, no API calls
    gid2, entries2 = await builder.get_or_build_group(100)
    assert gid1 == gid2
    assert len(entries2) == 1
    builder._anilist.get_anime_relations.assert_not_called()


@pytest.mark.asyncio
async def test_stale_cache_retriggers_traversal(real_db: DatabaseManager):
    """If max_age_hours=0, the group is always stale and should re-traverse."""
    entry = _make_entry(100, "Stale Anime")
    relations = {100: (entry, [])}
    builder = _make_builder(real_db, relations, max_age_hours=168)

    # Build once
    await builder.get_or_build_group(100)

    # Create a new builder with max_age_hours=0 (always stale)
    stale_builder = _make_builder(real_db, relations, max_age_hours=0)
    _, entries = await stale_builder.get_or_build_group(100)
    assert len(entries) == 1
    # The stale builder should have called get_anime_relations
    stale_builder._anilist.get_anime_relations.assert_called()


@pytest.mark.asyncio
async def test_chronological_sorting(real_db: DatabaseManager):
    """Entries should be sorted chronologically by start date."""
    # Deliberately start from the middle entry (id=2)
    entry_a = _make_entry(1, "First", year=2018, month=4)
    entry_b = _make_entry(2, "Second", year=2020, month=1)
    entry_c = _make_entry(3, "Third", year=2019, month=7)

    relations = {
        2: (
            entry_b,
            [
                _make_edge(1, "First", "PREQUEL", year=2018, month=4),
                _make_edge(3, "Third", "PREQUEL", year=2019, month=7),
            ],
        ),
        1: (entry_a, [_make_edge(2, "Second", "SEQUEL", year=2020, month=1)]),
        3: (entry_c, [_make_edge(2, "Second", "SEQUEL", year=2020, month=1)]),
    }
    builder = _make_builder(real_db, relations)
    _, entries = await builder.get_or_build_group(2)

    assert len(entries) == 3
    # Should be sorted: 2018, 2019, 2020
    assert entries[0]["anilist_id"] == 1  # 2018
    assert entries[1]["anilist_id"] == 3  # 2019
    assert entries[2]["anilist_id"] == 2  # 2020


@pytest.mark.asyncio
async def test_mixed_formats_included(real_db: DatabaseManager):
    """TV, OVA, and MOVIE entries should all be included (they're all ANIME type)."""
    tv = _make_entry(1, "Main Series", format="TV", year=2020)
    ova = _make_entry(2, "OVA Episode", format="OVA", year=2021)
    movie = _make_entry(3, "The Movie", format="MOVIE", year=2022)

    relations = {
        1: (
            tv,
            [
                _make_edge(2, "OVA Episode", "SEQUEL", format="OVA", year=2021),
                _make_edge(3, "The Movie", "SEQUEL", format="MOVIE", year=2022),
            ],
        ),
        2: (ova, [_make_edge(1, "Main Series", "PREQUEL", year=2020)]),
        3: (movie, [_make_edge(1, "Main Series", "PREQUEL", year=2020)]),
    }
    builder = _make_builder(real_db, relations)
    _, entries = await builder.get_or_build_group(1)

    assert len(entries) == 3
    formats = {e["format"] for e in entries}
    assert "TV" in formats
    assert "OVA" in formats
    assert "MOVIE" in formats


@pytest.mark.asyncio
async def test_non_anime_filtered_out(real_db: DatabaseManager):
    """MANGA relations should not be followed during traversal."""
    anime_entry = _make_entry(1, "Anime Series", year=2020)
    manga_entry = _make_entry(2, "Manga Series", type="MANGA", year=2019)

    relations = {
        1: (
            anime_entry,
            [
                # Manga relation should be ignored (type != ANIME)
                {
                    "relationType": "SEQUEL",
                    "node": {
                        "id": 2,
                        "type": "MANGA",
                        "format": "MANGA",
                        "title": {"romaji": "Manga Series"},
                        "episodes": None,
                        "startDate": {"year": 2019, "month": 1, "day": 1},
                    },
                },
            ],
        ),
        # Should never be fetched
        2: (manga_entry, []),
    }
    builder = _make_builder(real_db, relations)
    _, entries = await builder.get_or_build_group(1)

    # Only the anime entry should be in the group
    assert len(entries) == 1
    assert entries[0]["anilist_id"] == 1


@pytest.mark.asyncio
async def test_side_story_relation_not_followed(real_db: DatabaseManager):
    """Only SEQUEL/PREQUEL relations should be followed, not SIDE_STORY etc."""
    main = _make_entry(1, "Main Show", year=2020)
    side = _make_entry(2, "Side Story", year=2020)

    relations = {
        1: (
            main,
            [
                {
                    "relationType": "SIDE_STORY",
                    "node": {
                        "id": 2,
                        "type": "ANIME",
                        "format": "TV",
                        "title": {"romaji": "Side Story"},
                        "episodes": 6,
                        "startDate": {"year": 2020, "month": 6, "day": 1},
                    },
                },
            ],
        ),
        2: (side, []),
    }
    builder = _make_builder(real_db, relations)
    _, entries = await builder.get_or_build_group(1)

    assert len(entries) == 1
    assert entries[0]["anilist_id"] == 1


@pytest.mark.asyncio
async def test_traversal_starting_from_middle(real_db: DatabaseManager):
    """Starting from a middle entry should still discover the full chain."""
    entry_a = _make_entry(1, "S1", year=2018)
    entry_b = _make_entry(2, "S2", year=2019)
    entry_c = _make_entry(3, "S3", year=2020)

    relations = {
        1: (entry_a, [_make_edge(2, "S2", "SEQUEL", year=2019)]),
        2: (
            entry_b,
            [
                _make_edge(1, "S1", "PREQUEL", year=2018),
                _make_edge(3, "S3", "SEQUEL", year=2020),
            ],
        ),
        3: (entry_c, [_make_edge(2, "S2", "PREQUEL", year=2019)]),
    }
    builder = _make_builder(real_db, relations)

    # Start from the middle
    _, entries = await builder.get_or_build_group(2)
    assert len(entries) == 3
    # Root should be the chronologically earliest
    assert entries[0]["anilist_id"] == 1


@pytest.mark.asyncio
async def test_empty_traversal_returns_empty(real_db: DatabaseManager):
    """If the API returns None for the starting entry, return (0, [])."""
    # No entries in the relations map => get_anime_relations returns (None, [])
    builder = _make_builder(real_db, {})
    group_id, entries = await builder.get_or_build_group(9999)
    assert group_id == 0
    assert entries == []


@pytest.mark.asyncio
async def test_group_entries_have_season_order(real_db: DatabaseManager):
    """Each entry should have a sequential season_order starting at 1."""
    entry_a = _make_entry(1, "S1", year=2020)
    entry_b = _make_entry(2, "S2", year=2021)

    relations = {
        1: (entry_a, [_make_edge(2, "S2", "SEQUEL", year=2021)]),
        2: (entry_b, [_make_edge(1, "S1", "PREQUEL", year=2020)]),
    }
    builder = _make_builder(real_db, relations)
    _, entries = await builder.get_or_build_group(1)

    assert entries[0]["season_order"] == 1
    assert entries[1]["season_order"] == 2


@pytest.mark.asyncio
async def test_group_display_title_uses_root_entry(real_db: DatabaseManager):
    """display_title should come from the chronologically first entry."""
    entry_a = _make_entry(1, "Original Series", year=2018)
    entry_b = _make_entry(2, "The Sequel", year=2020)

    relations = {
        1: (entry_a, [_make_edge(2, "The Sequel", "SEQUEL", year=2020)]),
        2: (entry_b, [_make_edge(1, "Original Series", "PREQUEL", year=2018)]),
    }
    builder = _make_builder(real_db, relations)
    group_id, _ = await builder.get_or_build_group(2)

    group = await real_db.get_series_group_by_root(1)
    assert group is not None
    assert group["display_title"] == "Original Series"
