"""Auto-sync: adds AniList watchlist entries to Sonarr/Radarr based on configured
statuses."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.Clients.AnilistClient import AniListClient
from src.Clients.RadarrClient import RadarrClient
from src.Clients.SonarrClient import SonarrClient
from src.Database.Connection import DatabaseManager
from src.Download.MappingResolver import MappingResolver
from src.Utils.Config import AppConfig
from src.Utils.NamingTranslator import is_movie_format

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result summary from a DownloadSyncer run."""

    added_to_sonarr: int = 0
    added_to_radarr: int = 0
    skipped: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)


class DownloadSyncer:
    """Orchestrates automatic Sonarr/Radarr add from AniList watchlist."""

    def __init__(
        self,
        db: DatabaseManager,
        anilist_client: AniListClient,
        config: AppConfig,
    ) -> None:
        self._db = db
        self._anilist = anilist_client
        self._config = config

    async def run_sync(self) -> SyncResult:
        """Main entry point: sync all linked users' watchlists into Sonarr/Radarr."""
        result = SyncResult()

        # Resolve auto_statuses from DB settings (fallback to config)
        raw_statuses = await self._db.get_setting("downloads.auto_statuses")
        if raw_statuses:
            auto_statuses = [s.strip() for s in raw_statuses.split(",") if s.strip()]
        else:
            auto_statuses = list(self._config.download_sync.auto_statuses)

        if not auto_statuses:
            logger.info("DownloadSyncer: no auto_statuses configured, skipping")
            return result

        monitor_mode = (
            await self._db.get_setting("downloads.monitor_mode")
            or self._config.download_sync.monitor_mode
            or "future"
        )
        auto_search_raw = await self._db.get_setting("downloads.auto_search") or "false"
        auto_search = auto_search_raw.lower() in ("true", "1", "yes")

        # Build *arr clients if configured
        sonarr_client: SonarrClient | None = None
        radarr_client: RadarrClient | None = None

        if self._config.sonarr.url and self._config.sonarr.api_key:
            sonarr_client = SonarrClient(
                url=self._config.sonarr.url,
                api_key=self._config.sonarr.api_key,
            )
        if self._config.radarr.url and self._config.radarr.api_key:
            radarr_client = RadarrClient(
                url=self._config.radarr.url,
                api_key=self._config.radarr.api_key,
            )

        if not sonarr_client and not radarr_client:
            logger.info(
                "DownloadSyncer: neither Sonarr nor Radarr configured, skipping"
            )
            return result

        resolver = MappingResolver(
            db=self._db,
            anilist_client=self._anilist,
            sonarr_client=sonarr_client,
            radarr_client=radarr_client,
        )

        # Determine root folders and quality profiles
        sonarr_root = ""
        sonarr_quality_id = 1
        radarr_root = ""
        radarr_quality_id = 1

        if sonarr_client:
            try:
                roots = await sonarr_client.get_root_folders()
                if roots:
                    sonarr_root = roots[0].get("path", "")
                profiles = await sonarr_client.get_quality_profiles()
                if profiles:
                    sonarr_quality_id = profiles[0].get("id", 1)
            except Exception:
                logger.warning("Could not fetch Sonarr root folders/quality profiles")

        if radarr_client:
            try:
                roots = await radarr_client.get_root_folders()
                if roots:
                    radarr_root = roots[0].get("path", "")
                profiles = await radarr_client.get_quality_profiles()
                if profiles:
                    radarr_quality_id = profiles[0].get("id", 1)
            except Exception:
                logger.warning("Could not fetch Radarr root folders/quality profiles")

        # Process each linked AniList user
        users = await self._db.get_users_by_service("anilist")
        for user in users:
            user_id = user["user_id"]
            anilist_id = user.get("anilist_id", 0)
            access_token = user.get("access_token", "")

            if not anilist_id:
                continue

            # Refresh watchlist if needed
            await self._maybe_refresh_watchlist(user_id, anilist_id, access_token)

            # Get entries matching auto_statuses
            entries = await self._db.get_watchlist(user_id, list_statuses=auto_statuses)

            for entry in entries:
                entry_anilist_id: int = entry["anilist_id"]
                anilist_format: str = entry.get("anilist_format", "") or ""
                title: str = entry.get("anilist_title", "") or ""

                # Skip if already mapped
                is_movie = is_movie_format(anilist_format)
                already_mapped = await self._is_already_mapped(
                    entry_anilist_id, is_movie
                )
                if already_mapped:
                    result.skipped += 1
                    continue

                # Determine root folder and quality profile
                if is_movie:
                    if not radarr_client or not radarr_root:
                        result.skipped += 1
                        continue
                    root_folder = radarr_root
                    quality_id = radarr_quality_id
                else:
                    if not sonarr_client or not sonarr_root:
                        result.skipped += 1
                        continue
                    root_folder = sonarr_root
                    quality_id = sonarr_quality_id

                # Minimal media dict for resolver
                media: dict[str, Any] = {
                    "title": {"romaji": title},
                    "synonyms": [],
                }

                try:
                    add_result = await resolver.resolve_and_add(
                        anilist_id=entry_anilist_id,
                        anilist_format=anilist_format,
                        anilist_media=media,
                        quality_profile_id=quality_id,
                        root_folder_path=root_folder,
                        monitored=True,
                        monitor_strategy=monitor_mode,
                        search_immediately=auto_search,
                    )
                    if add_result.ok:
                        if add_result.service == "sonarr":
                            result.added_to_sonarr += 1
                        else:
                            result.added_to_radarr += 1
                        logger.info(
                            "Auto-added anilist_id=%d (%s) to %s",
                            entry_anilist_id,
                            title,
                            add_result.service,
                        )
                    else:
                        result.errors += 1
                        result.error_messages.append(
                            f"anilist_id={entry_anilist_id}: {add_result.error}"
                        )
                except Exception as exc:
                    result.errors += 1
                    msg = f"anilist_id={entry_anilist_id}: {exc}"
                    result.error_messages.append(msg)
                    logger.error("DownloadSyncer error — %s", msg)

        # Clean up clients
        if sonarr_client:
            await sonarr_client.close()
        if radarr_client:
            await radarr_client.close()

        logger.info(
            "DownloadSyncer complete: +%d sonarr, +%d radarr, %d skipped, %d errors",
            result.added_to_sonarr,
            result.added_to_radarr,
            result.skipped,
            result.errors,
        )
        return result

    async def _maybe_refresh_watchlist(
        self,
        user_id: str,
        anilist_user_id: int,
        access_token: str,
    ) -> None:
        """Refresh watchlist from AniList if last_synced_at is > 1 hour old."""
        row = await self._db.fetch_one(
            "SELECT MIN(last_synced_at) as oldest FROM user_watchlist WHERE user_id=?",
            (user_id,),
        )
        needs_refresh = True
        if row and row.get("oldest"):
            needs_refresh = False
            stale = await self._db.fetch_one(
                "SELECT 1 FROM user_watchlist WHERE user_id=?"
                " AND last_synced_at < datetime('now', '-1 hour') LIMIT 1",
                (user_id,),
            )
            if stale:
                needs_refresh = True

        if needs_refresh:
            try:
                entries = await self._anilist.get_user_watchlist(
                    anilist_user_id, access_token or None
                )
                await self._db.bulk_upsert_watchlist(user_id, entries)
                logger.info(
                    "Refreshed watchlist for user_id=%s: %d entries",
                    user_id,
                    len(entries),
                )
            except Exception:
                logger.warning("Could not refresh watchlist for user_id=%s", user_id)

    async def _is_already_mapped(self, anilist_id: int, is_movie: bool) -> bool:
        """Return True if this entry is already tracked in the *arr mapping tables."""
        if is_movie:
            row = await self._db.fetch_one(
                "SELECT 1 FROM anilist_radarr_mapping"
                " WHERE anilist_id=? AND in_radarr=1",
                (anilist_id,),
            )
        else:
            row = await self._db.fetch_one(
                "SELECT 1 FROM anilist_sonarr_mapping"
                " WHERE anilist_id=? AND in_sonarr=1",
                (anilist_id,),
            )
        return row is not None
