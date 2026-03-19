"""OAuth2 account linking endpoints."""

from __future__ import annotations

import logging
import urllib.parse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/anilist")
async def anilist_login(request: Request) -> RedirectResponse:
    """Redirect user to AniList OAuth2 authorization page."""
    config = request.app.state.config
    db = request.app.state.db

    # Fall back to DB-stored credentials if env vars weren't set
    client_id = (
        config.anilist.client_id or await db.get_setting("anilist.client_id") or ""
    )
    if not client_id:
        logger.warning("AniList OAuth attempted without client credentials")
        return RedirectResponse(url="/?error=no_credentials", status_code=303)

    client = request.app.state.anilist_client
    callback_url = str(request.url_for("anilist_callback"))
    url = client.get_authorize_url(redirect_uri=callback_url, client_id=client_id)
    logger.info("Redirecting to AniList OAuth: %s", url)
    return RedirectResponse(url=url)


@router.get("/anilist/callback", response_model=None)
async def anilist_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse | HTMLResponse:
    """Exchange authorization code for token and link AniList account."""
    if error or not code:
        logger.error("AniList OAuth error: %s", error or "no code returned")
        return RedirectResponse(url="/?error=oauth_failed", status_code=303)

    config = request.app.state.config
    db = request.app.state.db
    client = request.app.state.anilist_client
    callback_url = str(request.url_for("anilist_callback"))

    # Use DB credentials as fallback for token exchange
    client_id = (
        config.anilist.client_id or await db.get_setting("anilist.client_id") or ""
    )
    client_secret = (
        config.anilist.client_secret
        or await db.get_setting("anilist.client_secret")
        or ""
    )

    try:
        token_data = await client.exchange_code_for_token(
            code,
            redirect_uri=callback_url,
            client_id=client_id or None,
            client_secret=client_secret or None,
        )
        access_token = token_data["access_token"]
        token_type = token_data.get("token_type", "Bearer")

        # Fetch the authenticated user's profile
        viewer = await client.get_viewer(access_token)
        anilist_id = viewer.get("id", 0)
        username = viewer.get("name", "Unknown")

        # Store user in database
        user_id = f"anilist_{anilist_id}"
        await db.upsert_user(
            user_id=user_id,
            service="anilist",
            username=username,
            access_token=access_token,
            token_type=token_type,
            anilist_id=anilist_id,
        )

        logger.info("Linked AniList account: %s (ID: %d)", username, anilist_id)
    except Exception as exc:
        logger.exception("Failed to link AniList account")
        import urllib.parse as _up

        err_msg = _up.quote(str(exc)[:200])
        return RedirectResponse(
            url=f"/auth/anilist/done?error={err_msg}", status_code=303
        )

    # Render a lightweight done page that closes a popup or redirects normally
    username_safe = urllib.parse.quote(username)
    return RedirectResponse(
        url=f"/auth/anilist/done?username={username_safe}&user_id={urllib.parse.quote(user_id)}",
        status_code=303,
    )


@router.get("/anilist/done", response_class=HTMLResponse)
async def anilist_done(
    request: Request,
    username: str = "",
    user_id: str = "",
    error: str = "",
) -> HTMLResponse:
    """Post-OAuth landing page. Closes the popup and notifies the opener, or redirects."""
    if error:
        is_rate_limit = (
            "rate" in error.lower() or "429" in error or "limit" in error.lower()
        )
        detail = (
            "AniList is rate-limited — the scan is using the API. Wait a minute and try again."
            if is_rate_limit
            else error
        )
        return HTMLResponse(content=f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>AniList — Error</title>
<style>
  body {{ font-family: sans-serif; background: #0f1923; color: #e2e8f0;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
  .box {{ text-align: center; padding: 2rem; max-width: 400px; }}
  .icon {{ font-size: 3rem; }}
  .detail {{ font-size: 0.9rem; color: #94a3b8; margin-top: 0.5rem; }}
  button {{ margin-top: 1.5rem; padding: 0.5rem 1.5rem; background: #3b82f6; color: #fff;
            border: none; border-radius: 6px; cursor: pointer; font-size: 0.95rem; }}
</style>
</head>
<body>
<div class="box">
  <div class="icon">✗</div>
  <h2>Authorization failed</h2>
  <p class="detail">{detail}</p>
  <button onclick="window.close()">Close</button>
</div>
<script>
  if (window.opener && !window.opener.closed) {{
    window.opener.postMessage(
      {{ type: 'anilist_auth_error', error: {repr(detail)} }},
      window.location.origin
    );
  }}
</script>
</body>
</html>""")

    return HTMLResponse(content=f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>AniList Linked</title>
<style>
  body {{ font-family: sans-serif; background: #0f1923; color: #e2e8f0;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
  .box {{ text-align: center; padding: 2rem; }}
  .check {{ font-size: 3rem; }}
</style>
</head>
<body>
<div class="box">
  <div class="check">✓</div>
  <h2>Linked as {username}</h2>
  <p>You can close this tab and return to setup.</p>
</div>
<script>
  // Notify onboarding page (if opened as a popup) then close
  if (window.opener && !window.opener.closed) {{
    window.opener.postMessage(
      {{ type: 'anilist_auth_done', username: {repr(username)}, user_id: {repr(user_id)} }},
      window.location.origin
    );
    setTimeout(function() {{ window.close(); }}, 800);
  }}
</script>
</body>
</html>""")


@router.post("/anilist/unlink/{user_id}")
async def anilist_unlink(request: Request, user_id: str) -> RedirectResponse:
    """Remove a linked AniList account."""
    db = request.app.state.db
    await db.delete_user(user_id)
    logger.info("Unlinked user: %s", user_id)
    return RedirectResponse(url="/", status_code=303)
