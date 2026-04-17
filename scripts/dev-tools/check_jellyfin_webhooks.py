#!/usr/bin/env python3
"""Diagnostic: dump the Jellyfin Webhook plugin configuration.

Shows all configured webhook destinations, which events are enabled,
and what URLs they point to.

Usage:
    python scripts/dev-tools/check_jellyfin_webhooks.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

# Webhook plugin GUID (stable across versions)
WEBHOOK_PLUGIN_ID = "71552A5A-5C5C-4350-A2AE-EBE451A30173"

# NotificationType enum values from the plugin source
NOTIFICATION_TYPES = {
    0: "None",
    1: "ItemAdded",
    2: "Generic",
    3: "PlaybackStart",
    4: "PlaybackProgress",
    5: "PlaybackStop",
    6: "SubtitleDownloadFailure",
    7: "AuthenticationFailure",
    8: "AuthenticationSuccess",
    9: "SessionStart",
    10: "PendingRestart",
    11: "TaskCompleted",
    12: "PluginInstallationCancelled",
    13: "PluginInstallationFailed",
    14: "PluginInstalled",
    15: "PluginInstalling",
    16: "PluginUninstalled",
    17: "PluginUpdated",
    18: "UserCreated",
    19: "UserDeleted",
    20: "UserLockedOut",
    21: "UserPasswordChanged",
    22: "UserUpdated",
    23: "UserDataSaved",
    24: "ItemDeleted",
}


async def _load_config_from_db() -> tuple[str, str]:
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
                    "SELECT key, value FROM app_settings "
                    "WHERE key IN ('jellyfin.url', 'jellyfin.api_key')"
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


async def main() -> None:
    url = os.environ.get("JELLYFIN_URL", "")
    api_key = os.environ.get("JELLYFIN_API_KEY", "")
    if not (url and api_key):
        url, api_key = await _load_config_from_db()
    if not (url and api_key):
        print("ERROR: Could not find Jellyfin URL/API key.")
        sys.exit(1)

    print(f"Jellyfin URL: {url}")
    print(f"API Key: {api_key[:6]}...\n")

    async with httpx.AsyncClient(
        base_url=url.rstrip("/"),
        headers={
            "Authorization": f'MediaBrowser Client="AnilistLink", Token="{api_key}"',
        },
        timeout=10.0,
    ) as client:
        # Check if plugin is installed
        resp = await client.get("/Plugins")
        resp.raise_for_status()
        plugins = resp.json()
        webhook_plugin = None
        normalized_target = WEBHOOK_PLUGIN_ID.replace("-", "").lower()
        for p in plugins:
            normalized_id = p.get("Id", "").replace("-", "").lower()
            if normalized_id == normalized_target:
                webhook_plugin = p
                break

        if not webhook_plugin:
            print("Webhook plugin is NOT installed.")
            print("Installed plugins:")
            for p in plugins:
                print(f"  - {p.get('Name')} ({p.get('Id')})")
            return

        print(f"Webhook plugin: {webhook_plugin.get('Name')} v{webhook_plugin.get('Version')}")
        print(f"Status: {webhook_plugin.get('Status')}\n")

        # Get plugin configuration — use the ID as reported by the server
        plugin_id = webhook_plugin.get("Id", WEBHOOK_PLUGIN_ID)
        resp = await client.get(f"/Plugins/{plugin_id}/Configuration")
        if resp.status_code == 404:
            print("Plugin configuration not found (no destinations configured).")
            return
        resp.raise_for_status()
        config = resp.json()

        # Dump each destination type
        destination_types = [
            ("GenericOptions", "Generic (HTTP)"),
            ("DiscordOptions", "Discord"),
            ("GotifyOptions", "Gotify"),
            ("PushoverOptions", "Pushover"),
            ("PushbulletOptions", "Pushbullet"),
            ("SlackOptions", "Slack"),
            ("SmtpOptions", "SMTP"),
            ("MqttOptions", "MQTT"),
            ("GenericFormOptions", "Generic Form"),
        ]

        total_destinations = 0
        for key, label in destination_types:
            options = config.get(key, [])
            if not options:
                continue

            for i, opt in enumerate(options):
                total_destinations += 1
                enabled = opt.get("EnableWebhook", False)
                name = opt.get("WebhookName") or f"(unnamed #{i+1})"
                uri = opt.get("WebhookUri", "(no URL)")
                send_all = opt.get("SendAllProperties", False)

                # Decode notification types (may be ints or strings)
                raw_types = opt.get("NotificationTypes", [])
                type_names = []
                for t in raw_types:
                    if isinstance(t, int):
                        type_names.append(NOTIFICATION_TYPES.get(t, f"Unknown({t})"))
                    else:
                        type_names.append(str(t))

                status = "ENABLED" if enabled else "DISABLED"
                print(f"[{label}] {name} — {status}")
                print(f"  URL: {uri}")
                print(f"  Send All Properties: {send_all}")
                print(f"  Events ({len(type_names)}):")
                for tn in sorted(type_names):
                    print(f"    - {tn}")

                # Item type filters
                item_filters = []
                for fkey in ("EnableMovies", "EnableEpisodes", "EnableSeries",
                             "EnableSeasons", "EnableAlbums", "EnableSongs"):
                    if opt.get(fkey, False):
                        item_filters.append(fkey.replace("Enable", ""))
                if item_filters:
                    print(f"  Item types: {', '.join(item_filters)}")

                print()

        if total_destinations == 0:
            print("No webhook destinations configured.")
        else:
            print(f"Total: {total_destinations} destination(s)")


if __name__ == "__main__":
    asyncio.run(main())
