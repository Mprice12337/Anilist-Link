#!/bin/sh
#
# reset-for-testing.sh — Clear all media/operational data for a clean test run.
#
# Clears:
#   - All media mappings, scans, and library snapshots
#   - Sync state, watch logs, CR preview/history
#   - Series groups, AniList cache, watchlist cache
#   - Download requests and Sonarr/Radarr mappings
#   - Restructure plans and logs
#   - Onboarding status (so the wizard runs again)
#   - Notification banners
#
# Preserves (untouched):
#   - app_settings credentials (URLs, API keys, tokens, passwords)
#   - users            — AniList OAuth tokens
#   - plex_users       — linked Plex account
#   - jellyfin_users   — linked Jellyfin account
#   - cr_session_cache — Crunchyroll browser session/cookies
#   - schema_version
#
# This lets you go through onboarding fresh without having to re-enter or
# re-authenticate any credentials.
#
# Usage (inside Docker container):
#   /config/dev-tools/reset-for-testing.sh [--yes]
#
# Pass --yes to skip the confirmation prompt.

set -e

DB="/config/anilist_link.db"

if [ ! -f "$DB" ]; then
    echo "ERROR: Database not found at $DB"
    exit 1
fi

TABLES="
media_mappings
sync_state
manual_overrides
plex_media
jellyfin_media
series_groups
series_group_entries
anilist_cache
user_watchlist
restructure_log
restructure_plans
libraries
library_items
cr_sync_preview
cr_sync_log
watch_sync_log
download_requests
anilist_sonarr_mapping
anilist_radarr_mapping
anilist_sonarr_season_mapping
anilist_arr_skip
sonarr_series_cache
radarr_movie_cache
"

echo "=== Reset for Testing ==="
echo "Database: $DB"
echo ""
echo "Will CLEAR (full tables):"
for t in $TABLES; do
    exists=$(sqlite3 "$DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='$t';" 2>/dev/null || echo "0")
    if [ "$exists" = "1" ]; then
        count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM $t;" 2>/dev/null || echo "?")
        printf "  - %-35s (%s rows)\n" "$t" "$count"
    else
        printf "  · %-35s (not found)\n" "$t"
    fi
done

echo ""
echo "Will CLEAR (app_settings rows):"
onb_count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM app_settings WHERE key LIKE 'onboarding.%' OR key = 'notifications';" 2>/dev/null || echo "0")
printf "  - %-35s (%s rows)\n" "app_settings (onboarding/notifications)" "$onb_count"

echo ""
echo "Will PRESERVE:"
for t in schema_version users plex_users jellyfin_users cr_session_cache; do
    exists=$(sqlite3 "$DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='$t';" 2>/dev/null || echo "0")
    if [ "$exists" = "1" ]; then
        count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM $t;" 2>/dev/null || echo "?")
        printf "  ✓ %-35s (%s rows)\n" "$t" "$count"
    fi
done
cred_count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM app_settings WHERE key NOT LIKE 'onboarding.%' AND key != 'notifications';" 2>/dev/null || echo "0")
printf "  ✓ %-35s (%s rows)\n" "app_settings (credentials)" "$cred_count"

echo ""

if [ "$1" != "--yes" ]; then
    printf "Proceed? [y/N] "
    read -r confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Aborted."
        exit 0
    fi
fi

sqlite3 "$DB" "PRAGMA foreign_keys = OFF;"

for t in $TABLES; do
    exists=$(sqlite3 "$DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='$t';" 2>/dev/null || echo "0")
    if [ "$exists" = "1" ]; then
        sqlite3 "$DB" "DELETE FROM $t;"
        printf "  cleared  %-35s\n" "$t"
    else
        printf "  skipped  %-35s (table not found)\n" "$t"
    fi
done

sqlite3 "$DB" "DELETE FROM app_settings WHERE key LIKE 'onboarding.%' OR key = 'notifications';"
echo "  cleared  app_settings (onboarding/notifications)"

sqlite3 "$DB" "PRAGMA foreign_keys = ON;"
sqlite3 "$DB" "VACUUM;"

echo ""
echo "Done. Restart the app to go through onboarding with existing credentials."
