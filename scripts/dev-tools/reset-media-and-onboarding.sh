#!/bin/sh
#
# reset-media-and-onboarding.sh — Clear all media data AND reset onboarding status.
# Same as reset-media.sh but also resets onboarding to "not_started".
# Preserves: credentials, other app_settings, users, schema_version.
#
# Usage (inside Docker container):
#   /config/dev-tools/reset-media-and-onboarding.sh [--yes]

set -e

DB="/config/anilist_link.db"

if [ ! -f "$DB" ]; then
    echo "ERROR: Database not found at $DB"
    exit 1
fi

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

echo "=== Reset Media Data + Onboarding ==="
echo "Database: $DB"
echo ""
echo "Tables to clear:"
for t in $TABLES; do
    count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM $t;" 2>/dev/null || echo "0")
    printf "  %-35s %s rows\n" "$t" "$count"
done

echo ""
echo "Onboarding keys to reset:"
onb_status=$(sqlite3 "$DB" "SELECT value FROM app_settings WHERE key='onboarding.status';" 2>/dev/null || echo "(not set)")
onb_step=$(sqlite3 "$DB" "SELECT value FROM app_settings WHERE key='onboarding.step';" 2>/dev/null || echo "(not set)")
onb_skip=$(sqlite3 "$DB" "SELECT value FROM app_settings WHERE key='onboarding.skip_scan_ready';" 2>/dev/null || echo "(not set)")
echo "  onboarding.status:          $onb_status -> not_started"
echo "  onboarding.step:            $onb_step -> 1"
echo "  onboarding.skip_scan_ready: $onb_skip -> (deleted)"
echo ""
echo "Preserved: credentials, other app_settings, users, schema_version"
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

# Reset onboarding state
sqlite3 "$DB" "INSERT OR REPLACE INTO app_settings (key, value, is_secret) VALUES ('onboarding.status', 'not_started', 0);"
sqlite3 "$DB" "INSERT OR REPLACE INTO app_settings (key, value, is_secret) VALUES ('onboarding.step', '1', 0);"
sqlite3 "$DB" "DELETE FROM app_settings WHERE key='onboarding.skip_scan_ready';"
echo "  Reset onboarding status"

sqlite3 "$DB" "VACUUM;"

echo ""
echo "Done. Media data cleared and onboarding reset to not_started."
