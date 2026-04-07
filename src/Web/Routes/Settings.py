"""Settings page — manage credentials and options via the web dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.Clients.AnilistClient import AniListClient
from src.Clients.PlexClient import PlexClient
from src.Clients.RadarrClient import RadarrClient
from src.Clients.SonarrClient import SonarrClient
from src.Utils.Config import (
    SECRET_KEYS,
    SETTINGS_MAP,
    get_env_overrides,
    load_config_from_db_settings,
)
from src.Utils.NamingTemplate import NAMING_PRESETS
from src.Web.App import spawn_background_task

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])

# Grouped field definitions for the template.
# Each entry: (db_key, label, input_type)
FIELD_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "Library",
        [
            ("library.name", "Library Name", "text"),
            ("library.paths", "Anime Library Paths", "textarea"),
        ],
    ),
    (
        "Crunchyroll",
        [
            ("crunchyroll.email", "Email", "email"),
            ("crunchyroll.password", "Password", "password"),
            ("crunchyroll.flaresolverr_url", "FlareSolverr URL", "url"),
            ("crunchyroll.headless", "Headless mode", "checkbox"),
            ("crunchyroll.max_pages", "Max pages", "number"),
            ("crunchyroll.auto_sync_enabled", "Auto sync enabled", "checkbox"),
            ("crunchyroll.auto_approve", "Auto-approve changes", "checkbox"),
        ],
    ),
    (
        "AniList",
        [
            ("anilist.client_id", "Client ID", "text"),
            ("anilist.client_secret", "Client Secret", "password"),
        ],
    ),
    (
        "Plex",
        [
            ("plex.url", "Server URL", "url"),
            ("plex.token", "Token", "password"),
            ("plex.anime_library_keys", "Anime Libraries", "plex_library_select"),
        ],
    ),
    (
        "Jellyfin",
        [
            ("jellyfin.url", "Server URL", "url"),
            ("jellyfin.api_key", "API Key", "password"),
            (
                "jellyfin.anime_library_ids",
                "Anime Libraries",
                "jellyfin_library_select",
            ),
        ],
    ),
    (
        "Sonarr",
        [
            ("sonarr.url", "Server URL", "url"),
            ("sonarr.api_key", "API Key", "password"),
            ("sonarr.anime_root_folder", "Anime root folder path", "text"),
            (
                "sonarr.path_prefix",
                "Remote path — as seen by Sonarr (leave blank if same host)",
                "text",
            ),
            (
                "sonarr.local_path_prefix",
                "Local path — same directory as seen by Anilist-Link",
                "text",
            ),
        ],
    ),
    (
        "Radarr",
        [
            ("radarr.url", "Server URL", "url"),
            ("radarr.api_key", "API Key", "password"),
            ("radarr.anime_root_folder", "Anime root folder path", "text"),
            (
                "radarr.path_prefix",
                "Remote path — as seen by Radarr (leave blank if same host)",
                "text",
            ),
            (
                "radarr.local_path_prefix",
                "Local path — same directory as seen by Anilist-Link",
                "text",
            ),
        ],
    ),
    (
        "Options",
        [
            (
                "app.base_url",
                "App URL (used for webhooks — must be reachable by Sonarr/Radarr)",
                "url",
            ),
            (
                "scheduler.cr_sync_time",
                "Crunchyroll daily sync time (HH:MM, 24h)",
                "text",
            ),
            (
                "scheduler.sync_interval_minutes",
                "Crunchyroll sync interval (minutes, used if no time set)",
                "number",
            ),
            ("scheduler.scan_interval_hours", "Scan interval (hours)", "number"),
            (
                "scheduler.library_reindex_interval_hours",
                "Library re-index interval (hours)",
                "number",
            ),
            ("app.debug", "Debug logging", "checkbox"),
            ("app.title_display", "Title display", "select"),
        ],
    ),
    (
        "Library Restructuring",
        [
            ("restructure.plex_path_prefix", "Plex path prefix", "text"),
            ("restructure.local_path_prefix", "Local path prefix", "text"),
        ],
    ),
    (
        "Naming Templates",
        [
            ("naming.file_template", "Episode file naming", "text"),
            ("naming.movie_file_template", "Movie file naming", "text"),
            ("naming.folder_template", "Show folder naming", "text"),
            ("naming.season_folder_template", "Season folder naming", "text"),
            ("naming.illegal_char_replacement", "Illegal character handling", "select"),
        ],
    ),
    (
        "Downloads",
        [
            (
                "downloads.arr_enabled",
                "Enable Sonarr/Radarr integration (add, grab, post-process)",
                "checkbox",
            ),
            (
                "downloads.auto_statuses",
                "Auto-add list statuses (comma-separated: "
                "CURRENT,PLANNING — leave blank to disable)",
                "text",
            ),
            (
                "downloads.monitor_mode",
                "Sonarr monitor mode (future/all/firstSeason/latestSeason)",
                "text",
            ),
            ("downloads.auto_search", "Search immediately on add", "checkbox"),
            (
                "downloads.sync_interval_minutes",
                "Auto-sync interval (minutes)",
                "number",
            ),
        ],
    ),
]


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, saved: int = 0) -> HTMLResponse:
    """Render the settings form."""
    db = request.app.state.db
    templates = request.app.state.templates

    db_settings = await db.get_all_settings()
    env_overrides = get_env_overrides()

    # Build display values: for secrets, never expose the real value
    display: dict[str, str] = {}
    for key, (env_var, code_default) in SETTINGS_MAP.items():
        db_entry = db_settings.get(key)
        if key in env_overrides:
            # Show env value for non-secrets, empty for secrets
            if key in SECRET_KEYS:
                display[key] = ""
            else:
                display[key] = os.environ.get(env_var, code_default)
        elif db_entry and db_entry["value"]:
            if key in SECRET_KEYS:
                display[key] = ""
            else:
                display[key] = str(db_entry["value"])
        else:
            display[key] = code_default

    # Populate library fields from the libraries table (not app_settings)
    libraries = await db.get_all_libraries()
    if libraries:
        lib = libraries[0]
        display["library.name"] = lib["name"]
        path_list = json.loads(lib["paths"]) if lib["paths"] else []
        display["library.paths"] = "\n".join(path_list)
    else:
        display["library.name"] = ""
        display["library.paths"] = ""

    # Auto-detect base_url from the incoming request if not explicitly set.
    # This gives the user a sensible default (the URL they're accessing from)
    # instead of localhost:9876 which won't work for webhooks.
    if (
        not display.get("app.base_url")
        or display["app.base_url"] == "http://localhost:9876"
    ):
        detected = f"{request.url.scheme}://{request.url.netloc}"
        display["app.base_url"] = detected

    # Build the AniList callback URL from the current request so the user
    # knows exactly what to register on AniList's developer page.
    anilist_callback_url = str(request.url_for("anilist_callback"))

    # Fetch Plex show libraries for the anime-library selector
    plex_libraries: list[dict[str, str]] = []
    selected_library_keys: list[str] = []
    config = request.app.state.config
    if config.plex.url and config.plex.token:
        plex_client = PlexClient(url=config.plex.url, token=config.plex.token)
        try:
            libs = await asyncio.wait_for(plex_client.get_libraries(), timeout=5.0)
            plex_libraries = [
                {"key": lib.key, "title": lib.title}
                for lib in libs
                if lib.type in ("show", "movie")
            ]
        except Exception:
            logger.warning("Could not fetch Plex libraries for settings page")
        finally:
            await plex_client.close()
        selected_library_keys = list(config.plex.anime_library_keys)

    # Fetch Jellyfin libraries for the anime-library selector
    jellyfin_libraries: list[dict[str, str]] = []
    selected_jellyfin_ids: list[str] = []
    if config.jellyfin.url and config.jellyfin.api_key:
        from src.Clients.JellyfinClient import JellyfinClient

        jf_client = JellyfinClient(
            url=config.jellyfin.url, api_key=config.jellyfin.api_key
        )
        try:
            jf_libs = await asyncio.wait_for(jf_client.get_libraries(), timeout=5.0)
            jellyfin_libraries = [{"id": lib.id, "name": lib.name} for lib in jf_libs]
        except Exception:
            logger.warning("Could not fetch Jellyfin libraries for settings page")
        finally:
            await jf_client.close()
        # Read saved selection from DB (stored as JSON list)
        jf_sel_raw = display.get("jellyfin.anime_library_ids", "[]")
        try:
            parsed = json.loads(jf_sel_raw)
            selected_jellyfin_ids = (
                [str(v) for v in parsed] if isinstance(parsed, list) else []
            )
        except (json.JSONDecodeError, TypeError):
            selected_jellyfin_ids = []

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "field_groups": FIELD_GROUPS,
            "display": display,
            "env_overrides": env_overrides,
            "secret_keys": SECRET_KEYS,
            "has_secret_value": {
                k for k, v in db_settings.items() if k in SECRET_KEYS and v["value"]
            },
            "saved": bool(saved),
            "anilist_callback_url": anilist_callback_url,
            "plex_libraries": plex_libraries,
            "selected_library_keys": selected_library_keys,
            "jellyfin_libraries": jellyfin_libraries,
            "selected_jellyfin_ids": selected_jellyfin_ids,
            "naming_presets": NAMING_PRESETS,
            "setup": request.query_params.get("setup", ""),
        },
    )


@router.post("/settings")
async def settings_save(request: Request) -> RedirectResponse:
    """Save settings to DB, rebuild config, and redirect back."""
    try:
        return await _settings_save_impl(request)
    except Exception as exc:
        logger.error("Settings save failed: %s", exc, exc_info=True)
        raise


async def _settings_save_impl(request: Request) -> RedirectResponse:
    db = request.app.state.db
    form = await request.form()

    old_config = request.app.state.config

    # Handle library fields (stored in libraries table, not app_settings)
    lib_name = str(form.get("library.name", "")).strip()
    lib_paths_raw = str(form.get("library.paths", "")).strip()
    lib_path_list = [p.strip() for p in lib_paths_raw.splitlines() if p.strip()]
    if lib_name and lib_path_list:
        libraries = await db.get_all_libraries()
        if libraries:
            await db.update_library(
                libraries[0]["id"], lib_name, json.dumps(lib_path_list)
            )
        else:
            await db.create_library(lib_name, json.dumps(lib_path_list))

    # Process each known setting key
    for key in SETTINGS_MAP:
        is_secret = key in SECRET_KEYS
        input_type = _get_input_type(key)

        if input_type in ("plex_library_select", "jellyfin_library_select"):
            # Multi-valued checkboxes: serialize selected keys as JSON list
            selected = form.getlist(key)
            value = json.dumps([str(v) for v in selected])
        elif input_type == "checkbox":
            # Checkbox: present in form = true, absent = false
            value = "true" if form.get(key) else "false"
        else:
            raw = form.get(key) or ""
            value = raw.strip() if isinstance(raw, str) else ""
            # For secret fields, empty submission means "don't change"
            if is_secret and not value:
                continue

        await db.set_setting(key, value, is_secret=is_secret)

    # Rebuild config from updated DB
    db_settings = await db.get_all_settings()
    new_config = load_config_from_db_settings(db_settings)
    request.app.state.config = new_config

    # Rebuild AniList client if credentials changed
    if (
        new_config.anilist.client_id != old_config.anilist.client_id
        or new_config.anilist.client_secret != old_config.anilist.client_secret
    ):
        old_client = request.app.state.anilist_client
        await old_client.close()
        request.app.state.anilist_client = AniListClient(
            client_id=new_config.anilist.client_id,
            client_secret=new_config.anilist.client_secret,
            redirect_uri=new_config.anilist.redirect_uri,
        )
        logger.info("Rebuilt AniList client with new credentials")

    # Reschedule jobs if intervals changed
    if new_config.scheduler != old_config.scheduler:
        scheduler = request.app.state.scheduler
        scheduler.update_intervals(new_config.scheduler)

    # Auto-register webhooks in Sonarr/Radarr if configured
    try:
        base_url = new_config.base_url.rstrip("/")

        if new_config.sonarr.url and new_config.sonarr.api_key:

            async def _register_sonarr_webhook() -> None:
                client = SonarrClient(
                    url=new_config.sonarr.url, api_key=new_config.sonarr.api_key
                )
                try:
                    await client.register_webhook(
                        "Anilist-Link", f"{base_url}/api/webhook/sonarr"
                    )
                    logger.info("Auto-registered Sonarr webhook")
                except Exception as exc:
                    logger.warning("Failed to auto-register Sonarr webhook: %s", exc)
                finally:
                    await client.close()

            spawn_background_task(request.app.state, _register_sonarr_webhook())

        if new_config.radarr.url and new_config.radarr.api_key:

            async def _register_radarr_webhook() -> None:
                client = RadarrClient(
                    url=new_config.radarr.url, api_key=new_config.radarr.api_key
                )
                try:
                    await client.register_webhook(
                        "Anilist-Link", f"{base_url}/api/webhook/radarr"
                    )
                    logger.info("Auto-registered Radarr webhook")
                except Exception as exc:
                    logger.warning("Failed to auto-register Radarr webhook: %s", exc)
                finally:
                    await client.close()

            spawn_background_task(request.app.state, _register_radarr_webhook())
    except Exception as exc:
        logger.error("Webhook auto-registration failed: %s", exc, exc_info=True)

    logger.info("Settings saved via dashboard")
    return RedirectResponse(url="/settings?saved=1", status_code=303)


def _get_input_type(key: str) -> str:
    """Look up the input type for a setting key."""
    for _group, fields in FIELD_GROUPS:
        for field_key, _label, input_type in fields:
            if field_key == key:
                return input_type
    return "text"
