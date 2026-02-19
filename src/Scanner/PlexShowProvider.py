"""Plex show provider: gathers shows from Plex libraries as ShowInput."""

from __future__ import annotations

import logging

from src.Clients.PlexClient import PlexClient
from src.Database.Connection import DatabaseManager
from src.Scanner.LibraryRestructurer import RestructureProgress, ShowInput

logger = logging.getLogger(__name__)


class PlexShowProvider:
    """Fetches Plex library shows and converts them to ShowInput objects."""

    def __init__(
        self,
        plex_client: PlexClient,
        db: DatabaseManager,
        plex_path_prefix: str,
        local_path_prefix: str,
    ) -> None:
        self._plex = plex_client
        self._db = db
        self._plex_prefix = plex_path_prefix.rstrip("/")
        self._local_prefix = local_path_prefix.rstrip("/")

    def _translate_path(self, plex_path: str) -> str:
        """Translate a Plex-reported path to a local filesystem path."""
        if self._plex_prefix and plex_path.startswith(self._plex_prefix):
            return self._local_prefix + plex_path[len(self._plex_prefix) :]
        return plex_path

    async def get_shows(
        self,
        library_keys: list[str],
        progress: RestructureProgress,
    ) -> list[ShowInput]:
        """Fetch shows from Plex libraries and return as ShowInput list.

        For each show, looks up existing media_mappings to populate anilist_id
        and anilist_title.
        """
        progress.phase = "Fetching library data"
        results: list[ShowInput] = []

        for key in library_keys:
            try:
                shows = await self._plex.get_library_shows(key)
            except Exception:
                logger.exception("Failed to get shows from library %s", key)
                continue

            for show in shows:
                locations = show.locations
                if not locations:
                    locations = await self._plex.get_show_locations(show.rating_key)
                if not locations:
                    continue

                plex_path = locations[0]
                local_path = self._translate_path(plex_path)

                anilist_id = 0
                anilist_title = ""
                year = 0
                romaji = ""
                english = ""
                mapping = await self._db.get_mapping_by_source("plex", show.rating_key)
                if mapping and mapping.get("anilist_id"):
                    anilist_id = mapping["anilist_id"]
                    anilist_title = mapping.get("anilist_title", "")
                    cache = await self._db.get_cached_metadata(anilist_id)
                    if cache:
                        year = cache.get("year", 0) or 0
                        romaji = cache.get("title_romaji", "")
                        english = cache.get("title_english", "")

                results.append(
                    ShowInput(
                        title=show.title,
                        local_path=local_path,
                        source_id=show.rating_key,
                        anilist_id=anilist_id,
                        anilist_title=anilist_title,
                        year=year,
                        anilist_title_romaji=romaji,
                        anilist_title_english=english,
                    )
                )

        progress.total = len(results)
        return results
