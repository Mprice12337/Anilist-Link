#!/bin/sh
#
# reset-arr-data.sh — Clear Sonarr/Radarr mapping and cache data.
# Preserves: credentials (sonarr.url, sonarr.api_key, etc.) and app_settings.
#
# Usage (inside Docker container):
#   /config/dev-tools/reset-arr-data.sh [--yes]
#
# Pass --yes to skip the confirmation prompt.

set -e

DB="/config/anilist_link.db"

if [ ! -f "$DB" ]; then
    echo "ERROR: Database not found at $DB"
    exit 1
fi

# Tables to clear (mapping/cache data only, no credentials)
TABLES="
download_requests
anilist_sonarr_mapping
anilist_radarr_mapping
anilist_sonarr_season_mapping
anilist_arr_skip
sonarr_series_cache
radarr_movie_cache
series_group_entries
series_groups
user_watchlist
"

echo "=== Reset Sonarr/Radarr Data ==="
echo "Database: $DB"
echo ""
echo "Tables to clear:"
for t in $TABLES; do
    count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM $t;" 2>/dev/null || echo "0")
    printf "  %-35s %s rows\n" "$t" "$count"
done

echo ""
echo "Preserved: app_settings (credentials, URLs, path prefixes), users, all other data"
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
echo "Done. Arr mapping/cache data cleared, credentials preserved."
