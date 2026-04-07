#!/bin/sh
#
# reset-media.sh — Clear all scanned media, mappings, and library data.
# Preserves: credentials, app_settings, users, onboarding status, schema_version.
#
# Usage (inside Docker container):
#   /config/dev-tools/reset-media.sh [--yes]
#
# Pass --yes to skip the confirmation prompt.

set -e

DB="/config/anilist_link.db"

if [ ! -f "$DB" ]; then
    echo "ERROR: Database not found at $DB"
    exit 1
fi

# Tables to clear (children before parents for FK safety)
TABLES="
sync_state
media_mappings
library_items
libraries
series_group_entries
series_groups
plex_media
jellyfin_media
anilist_cache
manual_overrides
restructure_log
cr_sync_preview
cr_sync_log
download_requests
anilist_sonarr_mapping
anilist_radarr_mapping
anilist_sonarr_season_mapping
anilist_arr_skip
sonarr_series_cache
radarr_movie_cache
user_watchlist
"

echo "=== Reset Media Data ==="
echo "Database: $DB"
echo ""
echo "Tables to clear:"
for t in $TABLES; do
    count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM $t;" 2>/dev/null || echo "0")
    printf "  %-35s %s rows\n" "$t" "$count"
done

echo ""
echo "Preserved: users, app_settings, cr_session_cache, schema_version, plex_users, jellyfin_users"
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
    sqlite3 "$DB" "DELETE FROM $t;"
    echo "  Cleared $t"
done
sqlite3 "$DB" "PRAGMA foreign_keys = ON;"
sqlite3 "$DB" "VACUUM;"

echo ""
echo "Done. Media data cleared, credentials and settings preserved."
