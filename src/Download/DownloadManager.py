"""P4 Download Manager — orchestrates AniList → Sonarr/Radarr add requests."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.Clients.AnilistClient import AniListClient
from src.Clients.RadarrClient import MovieAlreadyExistsError, RadarrClient
from src.Clients.SonarrClient import SeriesAlreadyExistsError, SonarrClient
from src.Database.Connection import DatabaseManager

logger = logging.getLogger(__name__)

# AniList formats treated as movies
MOVIE_FORMATS = {"MOVIE", "ONA", "SPECIAL", "MUSIC"}


@dataclass
class AddResult:
    """Result of a download add request."""

    ok: bool
    service: str  # "sonarr" or "radarr"
    anilist_id: int
    anilist_title: str
    external_id: int | None = None
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    status: str = "pending"  # "added", "exists", "error"
    error: str = ""
    download_request_id: int | None = None


class DownloadManager:
    """Orchestrates AniList → Sonarr/Radarr add requests."""

    def __init__(
        self,
        db: DatabaseManager,
        anilist_client: AniListClient,
    ) -> None:
        self._db = db
        self._anilist = anilist_client

    async def add_to_sonarr(
        self,
        anilist_id: int,
        sonarr_client: SonarrClient,
        quality_profile_id: int,
        root_folder_path: str,
        *,
        monitored: bool = True,
        requested_by: str = "",
    ) -> AddResult:
        """Resolve an AniList entry to TVDB and add it to Sonarr."""
        # Fetch AniList metadata + external links
        media = await self._anilist.get_anime_external_links(anilist_id)
        if not media:
            return AddResult(
                ok=False,
                service="sonarr",
                anilist_id=anilist_id,
                anilist_title="",
                status="error",
                error=f"AniList entry {anilist_id} not found",
            )

        title = _pick_title(media)
        links = media.get("externalLinks", [])
        tvdb_id = self._anilist.extract_tvdb_id(links)

        if not tvdb_id:
            # AniList didn't supply a numeric TVDB ID; fall back to Sonarr title search
            logger.info(
                "No TVDB ID in AniList links for '%s'; searching Sonarr by title", title
            )
            try:
                candidates = await sonarr_client.lookup_series(title)
                if candidates:
                    tvdb_id = candidates[0].get("tvdbId")
                    logger.info(
                        "Sonarr title search found tvdbId=%d for '%s'", tvdb_id, title
                    )
            except Exception as exc:
                logger.warning("Sonarr title search failed for '%s': %s", title, exc)

        if not tvdb_id:
            result = AddResult(
                ok=False,
                service="sonarr",
                anilist_id=anilist_id,
                anilist_title=title,
                status="error",
                error=f"No TVDB ID found for '{title}' via AniList or Sonarr search",
            )
            await self._log_request(
                result, quality_profile_id, root_folder_path, requested_by
            )
            return result

        try:
            sonarr_data = await sonarr_client.add_series(
                tvdb_id=tvdb_id,
                title=title,
                quality_profile_id=quality_profile_id,
                root_folder_path=root_folder_path,
                monitored=monitored,
            )
            external_id = sonarr_data.get("id")
            result = AddResult(
                ok=True,
                service="sonarr",
                anilist_id=anilist_id,
                anilist_title=title,
                external_id=external_id,
                tvdb_id=tvdb_id,
                status="added",
            )
            logger.info(
                "Added '%s' (tvdbId=%d) to Sonarr (id=%s)", title, tvdb_id, external_id
            )
        except SeriesAlreadyExistsError as exc:
            result = AddResult(
                ok=True,
                service="sonarr",
                anilist_id=anilist_id,
                anilist_title=title,
                tvdb_id=tvdb_id,
                status="exists",
                error=str(exc),
            )
            logger.info("Series '%s' already in Sonarr", title)
        except Exception as exc:
            result = AddResult(
                ok=False,
                service="sonarr",
                anilist_id=anilist_id,
                anilist_title=title,
                tvdb_id=tvdb_id,
                status="error",
                error=str(exc),
            )
            logger.warning("Failed to add '%s' to Sonarr: %s", title, exc)

        req_id = await self._log_request(
            result, quality_profile_id, root_folder_path, requested_by
        )
        result.download_request_id = req_id
        return result

    async def add_to_radarr(
        self,
        anilist_id: int,
        radarr_client: RadarrClient,
        quality_profile_id: int,
        root_folder_path: str,
        *,
        monitored: bool = True,
        requested_by: str = "",
    ) -> AddResult:
        """Resolve an AniList entry to TMDB and add it to Radarr."""
        media = await self._anilist.get_anime_external_links(anilist_id)
        if not media:
            return AddResult(
                ok=False,
                service="radarr",
                anilist_id=anilist_id,
                anilist_title="",
                status="error",
                error=f"AniList entry {anilist_id} not found",
            )

        title = _pick_title(media)
        links = media.get("externalLinks", [])
        tmdb_id = self._anilist.extract_tmdb_id(links)

        if not tmdb_id:
            # AniList didn't supply a numeric TMDB ID; fall back to Radarr title search
            logger.info(
                "No TMDB ID in AniList links for '%s'; searching Radarr by title", title
            )
            try:
                candidates = await radarr_client.lookup_movie(title)
                if candidates:
                    tmdb_id = candidates[0].get("tmdbId")
                    logger.info(
                        "Radarr title search found tmdbId=%d for '%s'", tmdb_id, title
                    )
            except Exception as exc:
                logger.warning("Radarr title search failed for '%s': %s", title, exc)

        if not tmdb_id:
            result = AddResult(
                ok=False,
                service="radarr",
                anilist_id=anilist_id,
                anilist_title=title,
                status="error",
                error=f"No TMDB ID found for '{title}' via AniList or Radarr search",
            )
            await self._log_request(
                result, quality_profile_id, root_folder_path, requested_by
            )
            return result

        try:
            radarr_data = await radarr_client.add_movie(
                tmdb_id=tmdb_id,
                title=title,
                quality_profile_id=quality_profile_id,
                root_folder_path=root_folder_path,
                monitored=monitored,
            )
            external_id = radarr_data.get("id")
            result = AddResult(
                ok=True,
                service="radarr",
                anilist_id=anilist_id,
                anilist_title=title,
                external_id=external_id,
                tmdb_id=tmdb_id,
                status="added",
            )
            logger.info(
                "Added '%s' (tmdbId=%d) to Radarr (id=%s)", title, tmdb_id, external_id
            )
        except MovieAlreadyExistsError as exc:
            result = AddResult(
                ok=True,
                service="radarr",
                anilist_id=anilist_id,
                anilist_title=title,
                tmdb_id=tmdb_id,
                status="exists",
                error=str(exc),
            )
            logger.info("Movie '%s' already in Radarr", title)
        except Exception as exc:
            result = AddResult(
                ok=False,
                service="radarr",
                anilist_id=anilist_id,
                anilist_title=title,
                tmdb_id=tmdb_id,
                status="error",
                error=str(exc),
            )
            logger.warning("Failed to add '%s' to Radarr: %s", title, exc)

        req_id = await self._log_request(
            result, quality_profile_id, root_folder_path, requested_by
        )
        result.download_request_id = req_id
        return result

    async def get_recent_requests(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent download requests from the DB."""
        return await self._db.get_download_requests(limit=limit)

    async def _log_request(
        self,
        result: AddResult,
        quality_profile_id: int,
        root_folder: str,
        requested_by: str,
    ) -> int:
        """Persist an add-request record and return its row ID."""
        now = datetime.now(timezone.utc).isoformat()
        executed_at = now if result.status in ("added", "exists") else None
        return await self._db.create_download_request(
            anilist_id=result.anilist_id,
            anilist_title=result.anilist_title,
            service=result.service,
            external_id=result.external_id,
            tvdb_id=result.tvdb_id,
            tmdb_id=result.tmdb_id,
            status=result.status,
            error_message=result.error,
            quality_profile_id=quality_profile_id,
            root_folder=root_folder,
            requested_by=requested_by,
            executed_at=executed_at,
        )


def _pick_title(media: dict[str, Any]) -> str:
    title = media.get("title", {})
    return title.get("english") or title.get("romaji") or title.get("native") or ""
