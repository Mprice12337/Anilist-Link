"""Reset all media/operational data while preserving credentials and auth.

Clears:
  - All media mappings, scans, and library snapshots
  - Sync state, watch logs, CR preview/history
  - Series groups, AniList cache, watchlist cache
  - Download requests and Sonarr/Radarr mappings
  - Restructure plans and logs
  - Onboarding status (so the wizard runs again)
  - Notification banners

Preserves (untouched):
  - app_settings credentials (URLs, API keys, tokens, passwords)
  - users  — AniList OAuth tokens
  - plex_users  — linked Plex account
  - jellyfin_users  — linked Jellyfin account
  - cr_session_cache  — Crunchyroll browser session/cookies
  - schema_version

This lets you go through onboarding fresh without having to re-enter or
re-authenticate any credentials.

Usage:
    python scripts/reset_for_testing.py [--db-path PATH] [--confirm]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Tables wiped entirely
# ---------------------------------------------------------------------------
TABLES_TO_CLEAR = [
    "media_mappings",
    "sync_state",
    "manual_overrides",
    "plex_media",
    "jellyfin_media",
    "series_groups",
    "series_group_entries",
    "anilist_cache",
    "user_watchlist",
    "restructure_log",
    "restructure_plans",
    "libraries",
    "library_items",
    "cr_sync_preview",
    "cr_sync_log",
    "watch_sync_log",
    "download_requests",
    "anilist_sonarr_mapping",
    "anilist_radarr_mapping",
    "sonarr_series_cache",
    "radarr_movie_cache",
]

# ---------------------------------------------------------------------------
# Tables kept entirely
# ---------------------------------------------------------------------------
TABLES_TO_KEEP = [
    "schema_version",
    "users",            # AniList OAuth tokens
    "plex_users",       # linked Plex account
    "jellyfin_users",   # linked Jellyfin account
    "cr_session_cache", # Crunchyroll browser session
]

# app_settings rows whose key starts with any of these prefixes are deleted.
# Everything else (credentials, service URLs, scheduler config, etc.) is kept.
SETTINGS_PREFIXES_TO_CLEAR = [
    "onboarding.",
    "notifications",    # exact key — notification banners
]


def _should_clear_setting(key: str) -> bool:
    for prefix in SETTINGS_PREFIXES_TO_CLEAR:
        if key == prefix or key.startswith(prefix):
            return True
    return False


def find_db() -> Path:
    """Locate the database using the same logic as Config.py."""
    config_dir = Path("/config")
    if config_dir.exists() and config_dir.is_dir():
        return config_dir / "anilist_link.db"
    return Path("./data/anilist_link.db")


def reset(db_path: Path, confirm: bool) -> None:
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing = {row[0] for row in cur.fetchall()}

    # Identify which app_settings rows will be deleted
    settings_to_delete: list[str] = []
    settings_to_keep: list[str] = []
    if "app_settings" in existing:
        cur.execute("SELECT key FROM app_settings ORDER BY key")
        for row in cur.fetchall():
            key = row[0]
            if _should_clear_setting(key):
                settings_to_delete.append(key)
            else:
                settings_to_keep.append(key)

    # Print plan
    print(f"\nDatabase: {db_path}\n")
    print("Will CLEAR (full tables):")
    for t in TABLES_TO_CLEAR:
        marker = "  -" if t in existing else "  · (not found)"
        print(f"  {'·' if t not in existing else '-'} {t}")

    if settings_to_delete:
        print(f"\n  - app_settings rows ({len(settings_to_delete)}):")
        for k in settings_to_delete:
            print(f"      {k}")

    print("\nWill KEEP:")
    for t in TABLES_TO_KEEP:
        print(f"  ✓ {t}")
    if settings_to_keep:
        print(f"  ✓ app_settings credentials ({len(settings_to_keep)} rows kept)")

    print()

    if not confirm:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            con.close()
            sys.exit(0)

    # ── Execute ─────────────────────────────────────────────────────────
    cleared: list[tuple[str, int]] = []
    skipped: list[str] = []

    for table in TABLES_TO_CLEAR:
        if table in existing:
            cur.execute(f"DELETE FROM {table}")  # noqa: S608
            cleared.append((table, cur.rowcount))
        else:
            skipped.append(table)

    # Selectively clear app_settings
    if settings_to_delete and "app_settings" in existing:
        placeholders = ",".join("?" for _ in settings_to_delete)
        cur.execute(
            f"DELETE FROM app_settings WHERE key IN ({placeholders})",  # noqa: S608
            settings_to_delete,
        )
        cleared.append(("app_settings (onboarding/notifications)", cur.rowcount))

    con.commit()

    # ── Report ───────────────────────────────────────────────────────────
    print("\nResults:")
    for table, count in cleared:
        print(f"  cleared  {table:<42}  ({count} rows removed)")
    for table in skipped:
        print(f"  skipped  {table:<42}  (table not found)")

    print("\nPreserved:")
    for table in TABLES_TO_KEEP:
        if table in existing:
            cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
            n = cur.fetchone()[0]
            print(f"  kept     {table:<42}  ({n} rows)")
    if settings_to_keep:
        print(f"  kept     {'app_settings (credentials)':<42}  ({len(settings_to_keep)} rows)")

    con.close()
    print("\nDone. Restart the app to go through onboarding with existing credentials.\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db-path", type=Path, help="Path to the SQLite database file"
    )
    parser.add_argument(
        "--confirm", action="store_true", help="Skip the confirmation prompt"
    )
    args = parser.parse_args()

    db_path = args.db_path or find_db()
    reset(db_path, args.confirm)


if __name__ == "__main__":
    main()
