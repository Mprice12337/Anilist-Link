#!/bin/sh
#
# clear-plex-jellyfin.sh — Clear Plex and Jellyfin credentials and config.
# Removes connection settings from app_settings and clears per-user auth tables.
#
# Usage (inside Docker container):
#   /config/dev-tools/clear-plex-jellyfin.sh [--yes]

set -e

DB="/config/anilist_link.db"

if [ ! -f "$DB" ]; then
    echo "ERROR: Database not found at $DB"
    exit 1
fi

# app_settings keys to delete
PLEX_KEYS="plex.url plex.token plex.anime_library_keys plex.connected plex.has_plexpass plex.poll_interval plex.sync_libraries plex.libraries_json"
JELLYFIN_KEYS="jellyfin.url jellyfin.api_key jellyfin.anime_library_ids jellyfin.connected"

echo "=== Clear Plex & Jellyfin Credentials ==="
echo "Database: $DB"
echo ""

echo "Plex settings to remove:"
for k in $PLEX_KEYS; do
    val=$(sqlite3 "$DB" "SELECT value FROM app_settings WHERE key='$k';" 2>/dev/null || echo "(not set)")
    printf "  %-35s %s\n" "$k" "$val"
done

echo ""
echo "Jellyfin settings to remove:"
for k in $JELLYFIN_KEYS; do
    val=$(sqlite3 "$DB" "SELECT value FROM app_settings WHERE key='$k';" 2>/dev/null || echo "(not set)")
    printf "  %-35s %s\n" "$k" "$val"
done

plex_users_count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM plex_users;" 2>/dev/null || echo "0")
jf_users_count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM jellyfin_users;" 2>/dev/null || echo "0")
echo ""
echo "User tables to clear:"
echo "  plex_users:     $plex_users_count rows"
echo "  jellyfin_users: $jf_users_count rows"
echo ""

if [ "$1" != "--yes" ]; then
    printf "Proceed? [y/N] "
    read -r confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Aborted."
        exit 0
    fi
fi

for k in $PLEX_KEYS $JELLYFIN_KEYS; do
    sqlite3 "$DB" "DELETE FROM app_settings WHERE key='$k';"
done
echo "  Cleared Plex & Jellyfin settings"

sqlite3 "$DB" "DELETE FROM plex_users;"
sqlite3 "$DB" "DELETE FROM jellyfin_users;"
echo "  Cleared plex_users and jellyfin_users tables"

echo ""
echo "Done. Plex and Jellyfin credentials cleared."
