"""Settings page — manage credentials and options via the web dashboard."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.Clients.AnilistClient import AniListClient
from src.Utils.Config import (
    SECRET_KEYS,
    SETTINGS_MAP,
    get_env_overrides,
    load_config_from_db_settings,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])

# Grouped field definitions for the template.
# Each entry: (db_key, label, input_type)
FIELD_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "Crunchyroll",
        [
            ("crunchyroll.email", "Email", "email"),
            ("crunchyroll.password", "Password", "password"),
            ("crunchyroll.flaresolverr_url", "FlareSolverr URL", "url"),
            ("crunchyroll.headless", "Headless mode", "checkbox"),
            ("crunchyroll.max_pages", "Max pages", "number"),
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
        ],
    ),
    (
        "Jellyfin",
        [
            ("jellyfin.url", "Server URL", "url"),
            ("jellyfin.api_key", "API Key", "password"),
        ],
    ),
    (
        "Options",
        [
            ("scheduler.sync_interval_minutes", "Sync interval (minutes)", "number"),
            ("scheduler.scan_interval_hours", "Scan interval (hours)", "number"),
            ("app.debug", "Debug logging", "checkbox"),
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

    # Build the AniList callback URL from the current request so the user
    # knows exactly what to register on AniList's developer page.
    anilist_callback_url = str(request.url_for("anilist_callback"))

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
        },
    )


@router.post("/settings")
async def settings_save(request: Request) -> RedirectResponse:
    """Save settings to DB, rebuild config, and redirect back."""
    db = request.app.state.db
    form = await request.form()

    old_config = request.app.state.config

    # Process each known setting key
    for key in SETTINGS_MAP:
        is_secret = key in SECRET_KEYS
        input_type = _get_input_type(key)

        if input_type == "checkbox":
            # Checkbox: present in form = true, absent = false
            value = "true" if form.get(key) else "false"
        else:
            value = (form.get(key) or "").strip()
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

    logger.info("Settings saved via dashboard")
    return RedirectResponse(url="/settings?saved=1", status_code=303)


def _get_input_type(key: str) -> str:
    """Look up the input type for a setting key."""
    for _group, fields in FIELD_GROUPS:
        for field_key, _label, input_type in fields:
            if field_key == key:
                return input_type
    return "text"
