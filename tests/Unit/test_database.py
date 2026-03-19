"""Comprehensive tests for DatabaseManager using in-memory SQLite."""

from __future__ import annotations

import pytest
import pytest_asyncio
from pathlib import Path

import aiosqlite

from src.Database.Connection import DatabaseManager
from src.Database.Models import INDEXES, TABLES
from src.Database.Migrations import LATEST_VERSION


async def _init_in_memory_db(manager: DatabaseManager) -> None:
    """Manually create all tables from the TABLES dict on an in-memory DB.

    This bypasses run_migrations() because migrations use ALTER TABLE to add
    columns that already exist in the latest TABLES DDL (e.g. v12 adds
    episodes_json which is already in the cr_sync_preview CREATE TABLE).
    """
    manager._db = await aiosqlite.connect(":memory:")
    manager._db.row_factory = aiosqlite.Row
    await manager._db.execute("PRAGMA journal_mode=WAL")
    await manager._db.execute("PRAGMA foreign_keys=ON")

    for ddl in TABLES.values():
        await manager._db.execute(ddl)
    for idx in INDEXES:
        await manager._db.execute(idx)

    # Columns added by migrations via ALTER TABLE (not in base TABLES DDL)
    alter_statements = [
        "ALTER TABLE media_mappings ADD COLUMN series_group_id INTEGER",
        "ALTER TABLE media_mappings ADD COLUMN season_number INTEGER",
    ]
    for stmt in alter_statements:
        try:
            await manager._db.execute(stmt)
        except Exception:
            pass  # Column may already exist if TABLES DDL was updated

    # Mark schema at latest version so other code doesn't think it needs migration
    await manager._db.execute(
        "INSERT INTO schema_version (version) VALUES (?)", (LATEST_VERSION,)
    )
    await manager._db.commit()


@pytest_asyncio.fixture
async def db():
    """Create an in-memory DatabaseManager with all tables, tear down after test."""
    manager = DatabaseManager(Path(":memory:"))
    await _init_in_memory_db(manager)
    yield manager
    await manager.close()


# ------------------------------------------------------------------
# 1. Initialization
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_tables_created(db: DatabaseManager):
    """All expected tables should exist after initialization."""
    tables_to_check = [
        "schema_version",
        "media_mappings",
        "users",
        "sync_state",
        "anilist_cache",
        "manual_overrides",
        "app_settings",
        "series_groups",
        "series_group_entries",
        "plex_media",
        "jellyfin_media",
        "download_requests",
    ]
    for table in tables_to_check:
        row = await db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        assert row is not None, f"Table '{table}' should exist"


@pytest.mark.asyncio
async def test_schema_version_is_latest(db: DatabaseManager):
    """Schema version should be set to LATEST_VERSION."""
    row = await db.fetch_one("SELECT MAX(version) as v FROM schema_version")
    assert row is not None
    assert row["v"] == LATEST_VERSION


@pytest.mark.asyncio
async def test_init_idempotent():
    """Initializing twice should not raise errors."""
    manager = DatabaseManager(Path(":memory:"))
    await _init_in_memory_db(manager)
    # Re-creating tables with IF NOT EXISTS should be safe
    await _init_in_memory_db(manager)
    await manager.close()


# ------------------------------------------------------------------
# 2. User CRUD
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_and_get_user(db: DatabaseManager):
    """upsert_user then get_user should return the saved user."""
    await db.upsert_user(
        user_id="user1",
        service="anilist",
        username="TestUser",
        access_token="token123",
        anilist_id=42,
    )
    user = await db.get_user("user1")
    assert user is not None
    assert user["username"] == "TestUser"
    assert user["service"] == "anilist"
    assert user["anilist_id"] == 42


@pytest.mark.asyncio
async def test_upsert_user_updates_existing(db: DatabaseManager):
    """Upserting a user with the same user_id should update fields."""
    await db.upsert_user("u1", "anilist", "Old", "tok1", anilist_id=1)
    await db.upsert_user("u1", "anilist", "New", "tok2", anilist_id=2)
    user = await db.get_user("u1")
    assert user is not None
    assert user["username"] == "New"
    assert user["access_token"] == "tok2"
    assert user["anilist_id"] == 2


@pytest.mark.asyncio
async def test_get_users_by_service(db: DatabaseManager):
    """get_users_by_service should filter by service."""
    await db.upsert_user("u1", "anilist", "A", "t1")
    await db.upsert_user("u2", "plex", "B", "t2")
    await db.upsert_user("u3", "anilist", "C", "t3")
    anilist_users = await db.get_users_by_service("anilist")
    assert len(anilist_users) == 2
    plex_users = await db.get_users_by_service("plex")
    assert len(plex_users) == 1


@pytest.mark.asyncio
async def test_delete_user(db: DatabaseManager):
    """delete_user should remove the user."""
    await db.upsert_user("u1", "anilist", "A", "t1")
    await db.delete_user("u1")
    user = await db.get_user("u1")
    assert user is None


@pytest.mark.asyncio
async def test_get_user_count(db: DatabaseManager):
    """get_user_count should reflect the number of users."""
    assert await db.get_user_count() == 0
    await db.upsert_user("u1", "anilist", "A", "t1")
    await db.upsert_user("u2", "anilist", "B", "t2")
    assert await db.get_user_count() == 2


@pytest.mark.asyncio
async def test_get_nonexistent_user(db: DatabaseManager):
    """get_user for a missing user_id should return None."""
    user = await db.get_user("does_not_exist")
    assert user is None


# ------------------------------------------------------------------
# 3. Media Mappings
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_and_get_mapping(db: DatabaseManager):
    """upsert_media_mapping then get_mapping_by_source should return it."""
    mapping_id = await db.upsert_media_mapping(
        source="plex",
        source_id="123",
        source_title="My Anime",
        anilist_id=456,
        anilist_title="AniList Title",
        match_confidence=0.95,
        match_method="fuzzy",
    )
    assert mapping_id > 0
    mapping = await db.get_mapping_by_source("plex", "123")
    assert mapping is not None
    assert mapping["anilist_id"] == 456
    assert mapping["match_confidence"] == 0.95


@pytest.mark.asyncio
async def test_upsert_mapping_updates_on_conflict(db: DatabaseManager):
    """Upserting same source+source_id should update, not duplicate."""
    await db.upsert_media_mapping("plex", "1", "Title A", 100)
    await db.upsert_media_mapping("plex", "1", "Title A", 200)
    mapping = await db.get_mapping_by_source("plex", "1")
    assert mapping is not None
    assert mapping["anilist_id"] == 200  # anilist_id updated
    assert mapping["source_title"] == "Title A"  # source_title not updated on conflict
    count = await db.get_mapping_count()
    assert count == 1  # no duplicate row


@pytest.mark.asyncio
async def test_delete_mapping_by_source(db: DatabaseManager):
    """delete_mapping_by_source should remove the mapping."""
    await db.upsert_media_mapping("plex", "99", "Show", 111)
    await db.delete_mapping_by_source("plex", "99")
    mapping = await db.get_mapping_by_source("plex", "99")
    assert mapping is None


@pytest.mark.asyncio
async def test_get_all_mappings(db: DatabaseManager):
    """get_all_mappings should return all mappings."""
    await db.upsert_media_mapping("plex", "1", "A", 10)
    await db.upsert_media_mapping("jellyfin", "2", "B", 20)
    all_mappings = await db.get_all_mappings()
    assert len(all_mappings) == 2


@pytest.mark.asyncio
async def test_get_mapping_by_anilist_id(db: DatabaseManager):
    """get_mapping_by_anilist_id should return mappings for that anilist_id."""
    await db.upsert_media_mapping("plex", "1", "A", 100)
    await db.upsert_media_mapping("jellyfin", "2", "A", 100)
    await db.upsert_media_mapping("plex", "3", "B", 200)
    results = await db.get_mapping_by_anilist_id(100)
    assert len(results) == 2


# ------------------------------------------------------------------
# 4. Manual Overrides
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_and_get_override(db: DatabaseManager):
    """add_override then get_override should return it."""
    override_id = await db.add_override(
        source="plex",
        source_id="rk100",
        source_title="Wrong Title",
        anilist_id=999,
        created_by="admin",
    )
    assert override_id > 0
    override = await db.get_override("plex", "rk100")
    assert override is not None
    assert override["anilist_id"] == 999
    assert override["created_by"] == "admin"


@pytest.mark.asyncio
async def test_get_all_overrides(db: DatabaseManager):
    """get_all_overrides should return all overrides."""
    await db.add_override("plex", "1", "A", 10)
    await db.add_override("plex", "2", "B", 20)
    overrides = await db.get_all_overrides()
    assert len(overrides) == 2


@pytest.mark.asyncio
async def test_delete_override(db: DatabaseManager):
    """delete_override should remove the override by id."""
    oid = await db.add_override("plex", "1", "A", 10)
    await db.delete_override(oid)
    override = await db.get_override("plex", "1")
    assert override is None


# ------------------------------------------------------------------
# 5. Settings
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_and_get_setting(db: DatabaseManager):
    """set_setting then get_setting should return the value."""
    await db.set_setting("theme", "dark")
    val = await db.get_setting("theme")
    assert val == "dark"


@pytest.mark.asyncio
async def test_set_setting_overwrites(db: DatabaseManager):
    """Setting the same key twice should update the value."""
    await db.set_setting("theme", "dark")
    await db.set_setting("theme", "light")
    val = await db.get_setting("theme")
    assert val == "light"


@pytest.mark.asyncio
async def test_get_setting_nonexistent(db: DatabaseManager):
    """get_setting for a missing key should return None."""
    val = await db.get_setting("nonexistent_key")
    assert val is None


@pytest.mark.asyncio
async def test_get_all_settings(db: DatabaseManager):
    """get_all_settings should return all keys with value and is_secret."""
    await db.set_setting("plex.url", "http://plex:32400")
    await db.set_setting("plex.token", "secret_tok", is_secret=True)
    settings = await db.get_all_settings()
    assert "plex.url" in settings
    assert settings["plex.url"]["value"] == "http://plex:32400"
    assert settings["plex.url"]["is_secret"] is False
    assert "plex.token" in settings
    assert settings["plex.token"]["is_secret"] is True


# ------------------------------------------------------------------
# 6. AniList Cache
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_and_get_cached_metadata(db: DatabaseManager):
    """set_cached_metadata then get_cached_metadata should return it."""
    await db.set_cached_metadata(
        anilist_id=12345,
        title_romaji="Shingeki no Kyojin",
        title_english="Attack on Titan",
        episodes=25,
        status="FINISHED",
        year=2013,
    )
    cached = await db.get_cached_metadata(12345)
    assert cached is not None
    assert cached["title_romaji"] == "Shingeki no Kyojin"
    assert cached["title_english"] == "Attack on Titan"
    assert cached["episodes"] == 25
    assert cached["year"] == 2013


@pytest.mark.asyncio
async def test_get_cached_metadata_nonexistent(db: DatabaseManager):
    """get_cached_metadata for an uncached ID should return None."""
    cached = await db.get_cached_metadata(99999)
    assert cached is None


@pytest.mark.asyncio
async def test_cleanup_expired_cache(db: DatabaseManager):
    """cleanup_expired_cache should remove entries with past expires_at."""
    # Insert a fresh entry (default expires_at = now + 7 days)
    await db.set_cached_metadata(anilist_id=1, title_romaji="Fresh")
    # Manually insert an expired entry
    await db.execute(
        """INSERT INTO anilist_cache
               (anilist_id, title_romaji, expires_at)
           VALUES (?, ?, datetime('now', '-1 day'))""",
        (2, "Expired"),
    )
    deleted = await db.cleanup_expired_cache()
    assert deleted == 1
    # Fresh entry still exists
    assert await db.get_cached_metadata(1) is not None
    # Expired entry is gone
    row = await db.fetch_one(
        "SELECT * FROM anilist_cache WHERE anilist_id=?", (2,)
    )
    assert row is None


@pytest.mark.asyncio
async def test_set_cached_metadata_upserts(db: DatabaseManager):
    """Setting cache for the same anilist_id should update, not duplicate."""
    await db.set_cached_metadata(anilist_id=10, title_romaji="Old")
    await db.set_cached_metadata(anilist_id=10, title_romaji="New")
    cached = await db.get_cached_metadata(10)
    assert cached is not None
    assert cached["title_romaji"] == "New"
    rows = await db.fetch_all(
        "SELECT * FROM anilist_cache WHERE anilist_id=?", (10,)
    )
    assert len(rows) == 1


# ------------------------------------------------------------------
# 7. Series Groups
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_series_group(db: DatabaseManager):
    """upsert_series_group should create a group and return its id."""
    group_id = await db.upsert_series_group(
        root_anilist_id=100,
        display_title="Attack on Titan",
        entry_count=4,
    )
    assert group_id > 0


@pytest.mark.asyncio
async def test_upsert_series_group_updates_existing(db: DatabaseManager):
    """Upserting with same root_anilist_id should update, returning same id."""
    gid1 = await db.upsert_series_group(100, "Title v1", 2)
    gid2 = await db.upsert_series_group(100, "Title v2", 4)
    assert gid1 == gid2
    group = await db.get_series_group_by_root(100)
    assert group is not None
    assert group["display_title"] == "Title v2"
    assert group["entry_count"] == 4


@pytest.mark.asyncio
async def test_get_series_group_by_root(db: DatabaseManager):
    """get_series_group_by_root should find group by root_anilist_id."""
    await db.upsert_series_group(200, "Naruto", 5)
    group = await db.get_series_group_by_root(200)
    assert group is not None
    assert group["root_anilist_id"] == 200
    missing = await db.get_series_group_by_root(999)
    assert missing is None


@pytest.mark.asyncio
async def test_upsert_and_get_series_group_entries(db: DatabaseManager):
    """upsert_series_group_entry then get_series_group_entries should return entries."""
    group_id = await db.upsert_series_group(100, "AoT", 2)
    await db.upsert_series_group_entry(
        group_id=group_id,
        anilist_id=101,
        season_order=1,
        display_title="AoT Season 1",
        format="TV",
        episodes=25,
        start_date="2013-04-07",
    )
    await db.upsert_series_group_entry(
        group_id=group_id,
        anilist_id=102,
        season_order=2,
        display_title="AoT Season 2",
        format="TV",
        episodes=12,
        start_date="2017-04-01",
    )
    entries = await db.get_series_group_entries(group_id)
    assert len(entries) == 2
    assert entries[0]["anilist_id"] == 101
    assert entries[0]["season_order"] == 1
    assert entries[1]["anilist_id"] == 102
    assert entries[1]["season_order"] == 2


@pytest.mark.asyncio
async def test_clear_series_group_entries(db: DatabaseManager):
    """clear_series_group_entries should remove all entries for a group."""
    gid = await db.upsert_series_group(100, "Test", 2)
    await db.upsert_series_group_entry(gid, 1, 1, "Entry 1")
    await db.upsert_series_group_entry(gid, 2, 2, "Entry 2")
    await db.clear_series_group_entries(gid)
    entries = await db.get_series_group_entries(gid)
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_get_series_group_by_anilist_id(db: DatabaseManager):
    """get_series_group_by_anilist_id should find group via entry membership."""
    gid = await db.upsert_series_group(100, "Series", 2)
    await db.upsert_series_group_entry(gid, 101, 1, "S1")
    await db.upsert_series_group_entry(gid, 102, 2, "S2")
    group = await db.get_series_group_by_anilist_id(102)
    assert group is not None
    assert group["id"] == gid
    assert group["root_anilist_id"] == 100


@pytest.mark.asyncio
async def test_is_series_group_fresh(db: DatabaseManager):
    """A newly upserted group should be fresh; max_age_hours=0 means stale."""
    await db.upsert_series_group(100, "Fresh Group", 1)
    assert await db.is_series_group_fresh(100, max_age_hours=168) is True
    assert await db.is_series_group_fresh(100, max_age_hours=0) is False


# ------------------------------------------------------------------
# 8. Low-level helpers
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_property_raises_if_not_initialized():
    """Accessing db property before initialize() should raise RuntimeError."""
    manager = DatabaseManager(Path(":memory:"))
    with pytest.raises(RuntimeError, match="not initialized"):
        _ = manager.db
