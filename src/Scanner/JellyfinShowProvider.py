"""Jellyfin show provider: gathers shows from Jellyfin libraries as ShowInput."""

from __future__ import annotations

import logging

from src.Clients.JellyfinClient import JellyfinClient
from src.Database.Connection import DatabaseManager
from src.Scanner.LibraryRestructurer import RestructureProgress, ShowInput

logger = logging.getLogger(__name__)


class JellyfinShowProvider:
    """Fetches Jellyfin library shows and converts them to ShowInput objects."""

    def __init__(
        self,
        jellyfin_client: JellyfinClient,
        db: DatabaseManager,
        jellyfin_path_prefix: str,
        local_path_prefix: str,
    ) -> None:
        self._jellyfin = jellyfin_client
        self._db = db
        self._jellyfin_prefix = jellyfin_path_prefix.rstrip("/")
        self._local_prefix = local_path_prefix.rstrip("/")

    def _translate_path(self, jellyfin_path: str) -> str:
        """Translate a Jellyfin-reported path to a local filesystem path."""
        if self._jellyfin_prefix and jellyfin_path.startswith(self._jellyfin_prefix):
            return self._local_prefix + jellyfin_path[len(self._jellyfin_prefix) :]
        return jellyfin_path

    async def get_shows(
        self,
        library_ids: list[str],
        progress: RestructureProgress,
    ) -> list[ShowInput]:
        """Fetch shows from Jellyfin libraries and return as ShowInput list.

        For each show, looks up existing media_mappings to populate anilist_id
        and related metadata fields.
        """
        progress.phase = "Fetching Jellyfin library data"
        results: list[ShowInput] = []

        for lib_id in library_ids:
            try:
                shows = await self._jellyfin.get_library_shows(lib_id)
            except Exception:
                logger.exception("Failed to get shows from Jellyfin library %s", lib_id)
                continue

            for show in shows:
                if not show.path:
                    logger.debug("Skipping %s: no filesystem path", show.name)
                    continue

                local_path = self._translate_path(show.path)

                anilist_id = 0
                anilist_title = ""
                year = 0
                romaji = ""
                english = ""
                anilist_format = ""
                anilist_episodes = None

                mapping = await self._db.get_mapping_by_source("jellyfin", show.item_id)
                if mapping and mapping.get("anilist_id"):
                    anilist_id = mapping["anilist_id"]
                    anilist_title = mapping.get("anilist_title", "")
                    cache = await self._db.get_cached_metadata(anilist_id)
                    if cache:
                        year = cache.get("year", 0) or 0
                        romaji = cache.get("title_romaji", "")
                        english = cache.get("title_english", "")
                    sge_row = await self._db.fetch_one(
                        "SELECT format, episodes FROM series_group_entries"
                        " WHERE anilist_id=? LIMIT 1",
                        (anilist_id,),
                    )
                    if sge_row:
                        anilist_format = sge_row.get("format", "") or ""
                        anilist_episodes = sge_row.get("episodes")

                results.append(
                    ShowInput(
                        title=show.name,
                        local_path=local_path,
                        source_id=show.item_id,
                        anilist_id=anilist_id,
                        anilist_title=anilist_title,
                        year=year,
                        anilist_title_romaji=romaji,
                        anilist_title_english=english,
                        anilist_format=anilist_format,
                        anilist_episodes=anilist_episodes,
                    )
                )

        progress.total = len(results)
        return results
