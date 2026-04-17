#!/usr/bin/env python3
"""Diagnostic: connect to Jellyfin WebSocket and log ALL events.

Usage:
    python scripts/test_jellyfin_websocket.py [--no-scan] [--verbose]

Reads JELLYFIN_URL and JELLYFIN_API_KEY from app_settings DB or environment.
Logs every WebSocket message type received, to discover what Jellyfin sends
during library scans, per-item metadata refreshes, playback, etc.

Press Ctrl+C to stop.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

async def _load_config_from_db() -> tuple[str, str]:
    """Try to read Jellyfin URL/key from the app_settings database."""
    import aiosqlite

    db_paths = [
        "/config/anilist_link.db",
        os.path.join("data", "anilist_link.db"),
        os.path.join("config", "anilist_link.db"),
    ]
    for path in db_paths:
        if not os.path.exists(path):
            continue
        try:
            async with aiosqlite.connect(path) as db:
                url = api_key = ""
                async with db.execute(
                    "SELECT key, value FROM app_settings WHERE key IN ('jellyfin.url', 'jellyfin.api_key')"
                ) as cursor:
                    async for row in cursor:
                        if row[0] == "jellyfin.url":
                            url = row[1]
                        elif row[0] == "jellyfin.api_key":
                            api_key = row[1]
                if url and api_key:
                    return url, api_key
        except Exception:
            continue
    return "", ""


async def get_jellyfin_config() -> tuple[str, str]:
    url = os.environ.get("JELLYFIN_URL", "")
    api_key = os.environ.get("JELLYFIN_API_KEY", "")
    if url and api_key:
        return url, api_key
    db_url, db_key = await _load_config_from_db()
    return db_url or url, db_key or api_key


# ---------------------------------------------------------------------------
# WebSocket listener
# ---------------------------------------------------------------------------

# Message types we expect but are low-value when idle
_QUIET_TYPES = {"KeepAlive"}

# Track message type counts for summary
_msg_counts: dict[str, int] = {}


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def _summarize_data(data, max_len: int = 300) -> str:
    """Produce a readable summary of message data."""
    if data is None:
        return "(no data)"
    s = json.dumps(data, default=str)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


async def listen(url: str, api_key: str, auto_scan: bool, verbose: bool) -> None:
    """Connect to Jellyfin WebSocket and log all incoming messages."""
    ws_base = url.rstrip("/")
    if ws_base.startswith("https://"):
        ws_base = "wss://" + ws_base[8:]
    elif ws_base.startswith("http://"):
        ws_base = "ws://" + ws_base[7:]

    ws_url = f"{ws_base}/socket?ApiKey={api_key}"
    display_url = ws_url.replace(api_key, api_key[:6] + "...")

    print(f"[WS] Connecting to {display_url}")

    async with websockets.connect(ws_url, ping_interval=None) as ws:
        print("[WS] Connected! Listening for ALL events...\n")

        # Subscribe to task info for scan tracking
        await ws.send(json.dumps({
            "MessageType": "ScheduledTasksInfoStart",
            "Data": "0,2000",
        }))
        print("[WS] Subscribed to ScheduledTasksInfo (every 2s)")

        # Also subscribe to activity log
        await ws.send(json.dumps({
            "MessageType": "ActivityLogEntryStart",
            "Data": "0,5000",
        }))
        print("[WS] Subscribed to ActivityLogEntry (every 5s)\n")

        if auto_scan:
            import httpx
            print("[SCAN] Triggering a library scan in 3 seconds...")
            await asyncio.sleep(3)
            async with httpx.AsyncClient(
                base_url=url.rstrip("/"),
                headers={"Authorization": f'MediaBrowser Client="AnilistLink", Token="{api_key}"'},
                timeout=10.0,
            ) as client:
                resp = await client.post("/Library/Refresh")
                print(f"[SCAN] {'OK' if resp.status_code < 300 else f'FAILED {resp.status_code}'}\n")

        scan_running = False
        last_task_log = 0.0  # throttle idle task info

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"[{_ts()}] Non-JSON: {raw[:200]}")
                    continue

                msg_type = msg.get("MessageType", "Unknown")
                data = msg.get("Data")

                # Count every message type
                _msg_counts[msg_type] = _msg_counts.get(msg_type, 0) + 1

                # --- ForceKeepAlive: respond and show briefly ---
                if msg_type == "ForceKeepAlive":
                    await ws.send(json.dumps({"MessageType": "KeepAlive"}))
                    if verbose:
                        print(f"[{_ts()}]   KeepAlive ping/pong")
                    continue

                # --- KeepAlive echo: skip unless verbose ---
                if msg_type == "KeepAlive":
                    continue

                # --- ScheduledTasksInfo: track scan state ---
                if msg_type == "ScheduledTasksInfo":
                    if isinstance(data, list):
                        # Show ALL running tasks (not just RefreshLibrary)
                        all_running = [
                            t for t in data
                            if t.get("State") == "Running"
                        ]
                        running = [
                            t for t in all_running
                            if t.get("Key") == "RefreshLibrary"
                        ]
                        was = scan_running
                        scan_running = bool(running)
                        if scan_running and not was:
                            print(f"[{_ts()}] ** SCAN STARTED")
                        elif not scan_running and was:
                            print(f"[{_ts()}] ** SCAN ENDED")
                        if running:
                            pct = running[0].get("CurrentProgressPercentage", 0) or 0
                            print(f"[{_ts()}]   Scan progress: {pct:.1f}%")

                        # Show any non-RefreshLibrary tasks that are running
                        other_running = [t for t in all_running if t.get("Key") != "RefreshLibrary"]
                        if other_running:
                            for t in other_running:
                                print(f"[{_ts()}]   Task running: {t.get('Name', '?')} (Key={t.get('Key', '?')})")

                        elif verbose or (time.time() - last_task_log > 30):
                            # Show idle task summary every 30s
                            task_names = [t.get("Name", "?") for t in data if t.get("State") == "Running"]
                            if task_names:
                                print(f"[{_ts()}]   Tasks running: {', '.join(task_names)}")
                            last_task_log = time.time()
                    continue

                # --- Everything else: ALWAYS print ---
                print(f"[{_ts()}] << {msg_type}")

                if msg_type == "LibraryChanged":
                    added = len(data.get("ItemsAdded", [])) if data else 0
                    updated = len(data.get("ItemsUpdated", [])) if data else 0
                    removed = len(data.get("ItemsRemoved", [])) if data else 0
                    folders_added = len(data.get("FoldersAddedTo", [])) if data else 0
                    folders_removed = len(data.get("FoldersRemovedFrom", [])) if data else 0
                    print(f"     Added={added} Updated={updated} Removed={removed}")
                    print(f"     FoldersAddedTo={folders_added} FoldersRemovedFrom={folders_removed}")
                    if verbose and data:
                        for key in ("ItemsAdded", "ItemsUpdated", "ItemsRemoved"):
                            items = data.get(key, [])
                            if items:
                                print(f"     {key}: {items[:5]}{'...' if len(items) > 5 else ''}")

                elif msg_type == "RefreshProgress":
                    print(f"     {_summarize_data(data)}")

                elif msg_type == "ScheduledTaskEnded":
                    if isinstance(data, dict):
                        print(f"     Task={data.get('Name')!r} Key={data.get('Key')!r} Status={data.get('Status')!r}")
                    else:
                        print(f"     {_summarize_data(data)}")

                elif msg_type == "UserDataChanged":
                    if isinstance(data, dict):
                        entries = data.get("UserDataList", [])
                        user = data.get("UserId", "?")
                        print(f"     User={user} Items={len(entries)}")
                        if verbose:
                            for e in entries[:3]:
                                print(f"       ItemId={e.get('ItemId', '?')} Played={e.get('Played', '?')}")
                    else:
                        print(f"     {_summarize_data(data)}")

                elif msg_type == "ActivityLogEntry":
                    if isinstance(data, list):
                        for entry in data[:5]:
                            print(f"     [{entry.get('Severity', '?')}] {entry.get('Name', '?')}: {entry.get('Overview', '')[:100]}")
                    else:
                        print(f"     {_summarize_data(data)}")

                else:
                    # Unknown/new message type — always show full data
                    print(f"     {_summarize_data(data)}")

                print()

        except websockets.ConnectionClosed as e:
            print(f"\n[WS] Connection closed: {e}")
        except KeyboardInterrupt:
            pass

    print(f"\n[WS] Message type summary:")
    for mt, count in sorted(_msg_counts.items(), key=lambda x: -x[1]):
        print(f"  {mt}: {count}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    url, api_key = await get_jellyfin_config()
    if not url or not api_key:
        print("ERROR: Could not find Jellyfin URL/API key.")
        print("Set JELLYFIN_URL and JELLYFIN_API_KEY env vars,")
        print("or ensure app_settings DB has them configured.")
        sys.exit(1)

    print(f"Jellyfin URL: {url}")
    print(f"API Key: {api_key[:6]}...\n")

    auto_scan = "--no-scan" in sys.argv
    # Invert: --no-scan means don't scan
    auto_scan = "--no-scan" not in sys.argv
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    if not auto_scan:
        print("(--no-scan: just listening, no auto-trigger)\n")
    if verbose:
        print("(--verbose: showing all details)\n")

    await listen(url, api_key, auto_scan=auto_scan, verbose=verbose)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDone.")
