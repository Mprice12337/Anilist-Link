#!/bin/sh
# Delete or inspect virtual Jellyfin items (seasons/episodes with no filesystem path).
#
# Usage (run inside the container):
#   /config/dev-tools/delete-virtual-items.sh <item_id>              # inspect only
#   /config/dev-tools/delete-virtual-items.sh <item_id> --delete     # delete it
#   /config/dev-tools/delete-virtual-items.sh <series_id> --list     # list seasons
#
# Reads Jellyfin URL and API key from the app_settings DB table.

set -e

DB="/config/anilist_link.db"
if [ ! -f "$DB" ]; then
    echo "Database not found at $DB"
    exit 1
fi

ITEM_ID="${1:?Usage: $0 <item_id> [--delete|--list]}"
ACTION="${2:-inspect}"

JF_URL=$(sqlite3 "$DB" "SELECT value FROM app_settings WHERE key='jellyfin.url'" 2>/dev/null | sed 's:/*$::')
JF_KEY=$(sqlite3 "$DB" "SELECT value FROM app_settings WHERE key='jellyfin.api_key'" 2>/dev/null)

if [ -z "$JF_URL" ] || [ -z "$JF_KEY" ]; then
    echo "Could not read jellyfin.url or jellyfin.api_key from app_settings."
    exit 1
fi

AUTH="MediaBrowser Client=\"AnilistLink\", Token=\"${JF_KEY}\""

python3 -u - "$JF_URL" "$JF_KEY" "$ITEM_ID" "$ACTION" << 'PYEOF'
import sys, json, urllib.request, urllib.error, ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

base_url, api_key, item_id, action = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
auth = f'MediaBrowser Client="AnilistLink", Token="{api_key}"'
headers = {"Authorization": auth, "Accept": "application/json"}


def api_get(path, params=None):
    from urllib.parse import urlencode
    url = f"{base_url}{path}"
    p = dict(params or {})
    p["api_key"] = api_key
    url += "?" + urlencode(p)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"GET {path} failed: HTTP {e.code}")
        print(f"URL: {url}")
        print(e.read().decode()[:300])
        raise


def api_delete(path):
    from urllib.parse import urlencode
    url = f"{base_url}{path}?{urlencode({'api_key': api_key})}"
    req = urllib.request.Request(url, headers=headers, method="DELETE")
    with urllib.request.urlopen(req, context=ctx) as resp:
        return resp.status


def inspect_item(iid):
    data = api_get("/Items", {
        "Ids": iid,
        "Fields": "Path,Overview,LocationType,ParentId,IsFolder,ProviderIds,IndexNumber",
    })
    items = data.get("Items", [])
    if not items:
        print(f"Item {iid} not found.")
        return None
    item = items[0]
    print("=" * 60)
    print(f"  Name:          {item.get('Name')}")
    print(f"  Type:          {item.get('Type')}")
    print(f"  LocationType:  {item.get('LocationType')}")
    print(f"  Path:          {item.get('Path') or '(none)'}")
    print(f"  IndexNumber:   {item.get('IndexNumber')}")
    print(f"  IsFolder:      {item.get('IsFolder')}")
    print(f"  ParentId:      {item.get('ParentId')}")
    print(f"  ProviderIds:   {json.dumps(item.get('ProviderIds', {}))}")
    print("=" * 60)
    loc = item.get("LocationType", "")
    if loc == "Virtual":
        print(">>> VIRTUAL item — safe to delete.")
    elif not item.get("Path"):
        print(f">>> No Path (LocationType={loc!r}) — likely virtual.")
    else:
        print(f">>> LocationType={loc!r}, Path={item.get('Path')!r}")
        print("    May NOT be virtual — delete with caution.")
    return item


def list_seasons(series_id):
    data = api_get("/Items", {
        "ParentId": series_id,
        "IncludeItemTypes": "Season",
        "Fields": "Path,LocationType,IndexNumber",
        "SortBy": "IndexNumber",
        "SortOrder": "Ascending",
    })
    seasons = data.get("Items", [])
    if not seasons:
        print(f"No seasons found under {series_id}.")
        return
    print(f"\nSeasons under series {series_id}:")
    print(f"{'Idx':<5} {'Location':<14} {'Id':<34} {'Name'}")
    print("-" * 90)
    for s in seasons:
        idx = str(s.get("IndexNumber", "?"))
        loc = s.get("LocationType", "?")
        sid = s.get("Id", "?")
        name = s.get("Name", "?")
        tag = " <<< VIRTUAL" if loc == "Virtual" or not s.get("Path") else ""
        print(f"{idx:<5} {loc:<14} {sid:<34} {name}{tag}")


if action == "--list":
    list_seasons(item_id)
elif action == "--delete":
    item = inspect_item(item_id)
    if item:
        print()
        try:
            status = api_delete(f"/Items/{item_id}")
            print(f"DELETE returned HTTP {status} — item removed.")
        except urllib.error.HTTPError as e:
            print(f"DELETE failed: HTTP {e.code}")
            print(e.read().decode()[:500])
else:
    item = inspect_item(item_id)
    if item:
        print("\nRe-run with --delete to remove this item.")
PYEOF
