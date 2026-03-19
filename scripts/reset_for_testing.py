"""Reset database for onboarding re-test.

Clears all operational data (settings, mappings, library items, logs, etc.)
while keeping AniList cache, series groups, and series group entries intact
so the API doesn't need to be re-queried.

Usage:
    python scripts/reset_for_testing.py [--db-path PATH] [--confirm]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Tables to wipe completely
TABLES_TO_CLEAR = [
    "media_mappings",
    "users",
    "sync_state",
    "manual_overrides",
    "cr_session_cache",
    "app_settings",
    "plex_media",
    "jellyfin_media",
    "restructure_log",
    "libraries",
    "library_items",
    "plex_users",
    "jellyfin_users",
    "cr_sync_preview",
    "cr_sync_log",
    "download_requests",
]

# Tables to preserve (not touched)
TABLES_TO_KEEP = [
    "schema_version",
    "anilist_cache",
    "series_groups",
    "series_group_entries",
]


def find_db() -> Path:
    """Locate the database using the same logic as Config.py."""
    config_dir = Path("/config")
    if config_dir.exists():
        return config_dir / "anilist_link.db"
    local = Path("./data")
    return local / "anilist_link.db"


def reset(db_path: Path, confirm: bool) -> None:
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    print(f"Database: {db_path}")
    print()
    print("Will CLEAR:")
    for t in TABLES_TO_CLEAR:
        print(f"  - {t}")
    print()
    print("Will KEEP:")
    for t in TABLES_TO_KEEP:
        print(f"  ✓ {t}")
    print()

    if not confirm:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(0)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Get actual tables present in the DB so we don't fail on missing ones
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing = {row[0] for row in cur.fetchall()}

    cleared = []
    skipped = []
    for table in TABLES_TO_CLEAR:
        if table in existing:
            cur.execute(f"DELETE FROM {table}")  # noqa: S608 (trusted constant list)
            count = cur.rowcount
            cleared.append((table, count))
        else:
            skipped.append(table)

    con.commit()

    # Report preserved row counts
    print("Results:")
    for table, count in cleared:
        print(f"  cleared {table:30s}  ({count} rows removed)")
    for table in skipped:
        print(f"  skipped {table:30s}  (table not found)")
    print()

    preserved = []
    for table in TABLES_TO_KEEP:
        if table in existing:
            cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
            n = cur.fetchone()[0]
            preserved.append((table, n))

    print("Preserved:")
    for table, n in preserved:
        print(f"  kept    {table:30s}  ({n} rows)")

    con.close()
    print()
    print("Done. Restart the app to begin onboarding fresh.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, help="Path to the SQLite database file")
    parser.add_argument(
        "--confirm", action="store_true", help="Skip the confirmation prompt"
    )
    args = parser.parse_args()

    db_path = args.db_path or find_db()
    reset(db_path, args.confirm)


if __name__ == "__main__":
    main()
