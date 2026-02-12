"""OAuth2 account linking endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/anilist")
async def anilist_login(request: Request) -> RedirectResponse:
    """Redirect user to AniList OAuth2 authorization page."""
    config = request.app.state.config
    if not config.anilist.client_id:
        logger.warning("AniList OAuth attempted without client credentials")
        return RedirectResponse(url="/?error=no_credentials", status_code=303)

    client = request.app.state.anilist_client
    callback_url = str(request.url_for("anilist_callback"))
    url = client.get_authorize_url(redirect_uri=callback_url)
    logger.info("Redirecting to AniList OAuth: %s", url)
    return RedirectResponse(url=url)


@router.get("/anilist/callback")
async def anilist_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Exchange authorization code for token and link AniList account."""
    if error or not code:
        logger.error("AniList OAuth error: %s", error or "no code returned")
        return RedirectResponse(url="/?error=oauth_failed", status_code=303)

    client = request.app.state.anilist_client
    db = request.app.state.db
    callback_url = str(request.url_for("anilist_callback"))

    try:
        token_data = await client.exchange_code_for_token(
            code, redirect_uri=callback_url
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
    except Exception:
        logger.exception("Failed to link AniList account")
        return RedirectResponse(url="/?error=oauth_failed", status_code=303)

    return RedirectResponse(url="/", status_code=303)


@router.post("/anilist/unlink/{user_id}")
async def anilist_unlink(request: Request, user_id: str) -> RedirectResponse:
    """Remove a linked AniList account."""
    db = request.app.state.db
    await db.delete_user(user_id)
    logger.info("Unlinked user: %s", user_id)
    return RedirectResponse(url="/", status_code=303)
