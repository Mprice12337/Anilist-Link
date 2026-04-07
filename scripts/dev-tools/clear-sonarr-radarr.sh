#!/bin/sh
#
# clear-sonarr-radarr.sh — Clear Sonarr and Radarr credentials and config.
# Removes connection settings from app_settings.
#
# Usage (inside Docker container):
#   /config/dev-tools/clear-sonarr-radarr.sh [--yes]

set -e

DB="/config/anilist_link.db"

if [ ! -f "$DB" ]; then
    echo "ERROR: Database not found at $DB"
    exit 1
fi

SONARR_KEYS="sonarr.url sonarr.api_key sonarr.anime_root_folder sonarr.path_prefix sonarr.local_path_prefix sonarr.connected"
RADARR_KEYS="radarr.url radarr.api_key radarr.anime_root_folder radarr.path_prefix radarr.local_path_prefix radarr.connected"

echo "=== Clear Sonarr & Radarr Credentials ==="
echo "Database: $DB"
echo ""

echo "Sonarr settings to remove:"
for k in $SONARR_KEYS; do
    val=$(sqlite3 "$DB" "SELECT value FROM app_settings WHERE key='$k';" 2>/dev/null || echo "(not set)")
    printf "  %-35s %s\n" "$k" "$val"
done

echo ""
echo "Radarr settings to remove:"
for k in $RADARR_KEYS; do
    val=$(sqlite3 "$DB" "SELECT value FROM app_settings WHERE key='$k';" 2>/dev/null || echo "(not set)")
    printf "  %-35s %s\n" "$k" "$val"
done
echo ""

if [ "$1" != "--yes" ]; then
    printf "Proceed? [y/N] "
    read -r confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Aborted."
        exit 0
    fi
fi

for k in $SONARR_KEYS $RADARR_KEYS; do
    sqlite3 "$DB" "DELETE FROM app_settings WHERE key='$k';"
done
echo "  Cleared Sonarr & Radarr settings"

echo ""
echo "Done. Sonarr and Radarr credentials cleared."
