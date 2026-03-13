"""Resolves AniList entries to Sonarr/Radarr mappings.

Handles the lookup chain: AniList external links → title search fallback,
then calls the appropriate *arr client to add the series/movie.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.Clients.AnilistClient import AniListClient
from src.Clients.RadarrClient import RadarrClient
from src.Clients.SonarrClient import SonarrClient
from src.Database.Connection import DatabaseManager
from src.Utils.NamingTranslator import (
    get_preferred_title,
    is_movie_format,
    resolve_tmdb_id,
    resolve_tvdb_id,
)

logger = logging.getLogger(__name__)


@dataclass
class AddResult:
    """Result of a Sonarr/Radarr add operation."""

    ok: bool
    anilist_id: int
    service: str  # "sonarr" or "radarr"
    external_id: int | None  # tvdb_id or tmdb_id
    arr_id: int | None  # ID assigned by Sonarr/Radarr
    error: str = ""


class MappingResolver:
    """Resolves AniList entries to Sonarr or Radarr and stores mappings."""

    def __init__(
        self,
        db: DatabaseManager,
        anilist_client: AniListClient,
        sonarr_client: SonarrClient | None = None,
        radarr_client: RadarrClient | None = None,
    ) -> None:
        self._db = db
        self._anilist = anilist_client
        self._sonarr = sonarr_client
        self._radarr = radarr_client

    async def add_to_sonarr(
        self,
        anilist_id: int,
        title: str,
        tvdb_id: int,
        quality_profile_id: int,
        root_folder_path: str,
        monitored: bool = True,
        monitor_strategy: str = "future",
        search_immediately: bool = False,
    ) -> AddResult:
        """Add an anime series to Sonarr and store the mapping."""
        if not self._sonarr:
            return AddResult(
                ok=False,
                anilist_id=anilist_id,
                service="sonarr",
                external_id=tvdb_id,
                arr_id=None,
                error="Sonarr client not configured",
            )

        try:
            # Check if already in Sonarr
            existing = await self._sonarr.get_series_by_tvdb_id(tvdb_id)
            if existing:
                arr_id = existing.get("id")
                monitored_flag = existing.get("monitored", False)
                await self._store_sonarr_mapping(
                    anilist_id,
                    tvdb_id,
                    arr_id or 0,
                    title,
                    in_sonarr=True,
                    sonarr_monitored=monitored_flag,
                )
                logger.info(
                    "Series tvdb_id=%d already in Sonarr (id=%s)", tvdb_id, arr_id
                )
                return AddResult(
                    ok=True,
                    anilist_id=anilist_id,
                    service="sonarr",
                    external_id=tvdb_id,
                    arr_id=arr_id,
                )

            result = await self._sonarr.add_series(
                title=title,
                tvdb_id=tvdb_id,
                quality_profile_id=quality_profile_id,
                root_folder_path=root_folder_path,
                monitored=monitored,
                monitor_strategy=monitor_strategy,
                search_immediately=search_immediately,
                series_type="anime",
            )
            arr_id = result.get("id")
            await self._store_sonarr_mapping(
                anilist_id,
                tvdb_id,
                arr_id or 0,
                title,
                in_sonarr=True,
                sonarr_monitored=monitored,
            )
            logger.info("Added tvdb_id=%d to Sonarr as id=%s", tvdb_id, arr_id)
            return AddResult(
                ok=True,
                anilist_id=anilist_id,
                service="sonarr",
                external_id=tvdb_id,
                arr_id=arr_id,
            )
        except Exception as exc:
            logger.error("Failed to add tvdb_id=%d to Sonarr: %s", tvdb_id, exc)
            return AddResult(
                ok=False,
                anilist_id=anilist_id,
                service="sonarr",
                external_id=tvdb_id,
                arr_id=None,
                error=str(exc),
            )

    async def add_to_radarr(
        self,
        anilist_id: int,
        title: str,
        tmdb_id: int,
        quality_profile_id: int,
        root_folder_path: str,
        monitored: bool = True,
        search_immediately: bool = False,
    ) -> AddResult:
        """Add an anime movie to Radarr and store the mapping."""
        if not self._radarr:
            return AddResult(
                ok=False,
                anilist_id=anilist_id,
                service="radarr",
                external_id=tmdb_id,
                arr_id=None,
                error="Radarr client not configured",
            )

        try:
            existing = await self._radarr.get_movie_by_tmdb_id(tmdb_id)
            if existing:
                arr_id = existing.get("id")
                monitored_flag = existing.get("monitored", False)
                await self._store_radarr_mapping(
                    anilist_id,
                    tmdb_id,
                    arr_id or 0,
                    title,
                    in_radarr=True,
                    radarr_monitored=monitored_flag,
                )
                logger.info(
                    "Movie tmdb_id=%d already in Radarr (id=%s)", tmdb_id, arr_id
                )
                return AddResult(
                    ok=True,
                    anilist_id=anilist_id,
                    service="radarr",
                    external_id=tmdb_id,
                    arr_id=arr_id,
                )

            result = await self._radarr.add_movie(
                title=title,
                tmdb_id=tmdb_id,
                quality_profile_id=quality_profile_id,
                root_folder_path=root_folder_path,
                monitored=monitored,
                search_immediately=search_immediately,
            )
            arr_id = result.get("id")
            await self._store_radarr_mapping(
                anilist_id,
                tmdb_id,
                arr_id or 0,
                title,
                in_radarr=True,
                radarr_monitored=monitored,
            )
            logger.info("Added tmdb_id=%d to Radarr as id=%s", tmdb_id, arr_id)
            return AddResult(
                ok=True,
                anilist_id=anilist_id,
                service="radarr",
                external_id=tmdb_id,
                arr_id=arr_id,
            )
        except Exception as exc:
            logger.error("Failed to add tmdb_id=%d to Radarr: %s", tmdb_id, exc)
            return AddResult(
                ok=False,
                anilist_id=anilist_id,
                service="radarr",
                external_id=tmdb_id,
                arr_id=None,
                error=str(exc),
            )

    async def resolve_and_add(
        self,
        anilist_id: int,
        anilist_format: str,
        anilist_media: dict[str, Any],
        quality_profile_id: int,
        root_folder_path: str,
        monitored: bool = True,
        monitor_strategy: str = "future",
        search_immediately: bool = False,
    ) -> AddResult:
        """Resolve IDs and add entry to the appropriate *arr service."""
        title = get_preferred_title(anilist_media)

        if is_movie_format(anilist_format):
            tmdb_id = await resolve_tmdb_id(anilist_id, self._anilist)
            if not tmdb_id:
                return AddResult(
                    ok=False,
                    anilist_id=anilist_id,
                    service="radarr",
                    external_id=None,
                    arr_id=None,
                    error=f"Could not resolve TMDB ID for anilist_id={anilist_id}",
                )
            return await self.add_to_radarr(
                anilist_id=anilist_id,
                title=title,
                tmdb_id=tmdb_id,
                quality_profile_id=quality_profile_id,
                root_folder_path=root_folder_path,
                monitored=monitored,
                search_immediately=search_immediately,
            )
        else:
            tvdb_id = await resolve_tvdb_id(anilist_id, self._anilist)
            if not tvdb_id:
                return AddResult(
                    ok=False,
                    anilist_id=anilist_id,
                    service="sonarr",
                    external_id=None,
                    arr_id=None,
                    error=f"Could not resolve TVDB ID for anilist_id={anilist_id}",
                )
            return await self.add_to_sonarr(
                anilist_id=anilist_id,
                title=title,
                tvdb_id=tvdb_id,
                quality_profile_id=quality_profile_id,
                root_folder_path=root_folder_path,
                monitored=monitored,
                monitor_strategy=monitor_strategy,
                search_immediately=search_immediately,
            )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _store_sonarr_mapping(
        self,
        anilist_id: int,
        tvdb_id: int,
        sonarr_id: int,
        title: str,
        in_sonarr: bool = True,
        sonarr_monitored: bool = True,
    ) -> None:
        await self._db.execute(
            """INSERT INTO anilist_sonarr_mapping
                   (anilist_id, tvdb_id, sonarr_id, title, in_sonarr, sonarr_monitored)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(anilist_id) DO UPDATE SET
                   tvdb_id=excluded.tvdb_id,
                   sonarr_id=excluded.sonarr_id,
                   title=excluded.title,
                   in_sonarr=excluded.in_sonarr,
                   sonarr_monitored=excluded.sonarr_monitored,
                   updated_at=datetime('now')
            """,
            (
                anilist_id,
                tvdb_id,
                sonarr_id,
                title,
                int(in_sonarr),
                int(sonarr_monitored),
            ),
        )

    async def _store_radarr_mapping(
        self,
        anilist_id: int,
        tmdb_id: int,
        radarr_id: int,
        title: str,
        in_radarr: bool = True,
        radarr_monitored: bool = True,
    ) -> None:
        await self._db.execute(
            """INSERT INTO anilist_radarr_mapping
                   (anilist_id, tmdb_id, radarr_id, title, in_radarr, radarr_monitored)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(anilist_id) DO UPDATE SET
                   tmdb_id=excluded.tmdb_id,
                   radarr_id=excluded.radarr_id,
                   title=excluded.title,
                   in_radarr=excluded.in_radarr,
                   radarr_monitored=excluded.radarr_monitored,
                   updated_at=datetime('now')
            """,
            (
                anilist_id,
                tmdb_id,
                radarr_id,
                title,
                int(in_radarr),
                int(radarr_monitored),
            ),
        )
