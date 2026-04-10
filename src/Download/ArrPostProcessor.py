"""Post-processor for Sonarr/Radarr download events.

After a file is downloaded, moves it into the AniList-structured path
({series_path}/{anilist_entry_title}/{filename}) and updates the arr
service's file record via API so it stays fully linked — no rescan needed.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from src.Clients.RadarrClient import RadarrClient
from src.Clients.SonarrClient import SonarrClient
from src.Database.Connection import DatabaseManager
from src.Utils.Config import AppConfig

logger = logging.getLogger(__name__)


class ArrPostProcessor:
    """Moves downloaded files to AniList-structured paths and updates arr records."""

    def __init__(self, db: DatabaseManager, config: AppConfig) -> None:
        self._db = db
        self._config = config

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def process_sonarr_download(self, payload: dict[str, Any]) -> None:
        """Handle a Sonarr 'Download' or 'EpisodeFileRenamed' webhook event."""
        event_type = payload.get("eventType", "")
        if event_type == "Test":
            logger.info("Sonarr webhook test received — OK")
            return
        if event_type not in ("Download",):
            logger.debug("Sonarr webhook event_type=%r ignored", event_type)
            return

        series = payload.get("series", {})
        episode_file = payload.get("episodeFile", {})
        episodes = payload.get("episodes", [])

        sonarr_id: int = series.get("id", 0)
        file_id: int = episode_file.get("id", 0)
        current_path: str = episode_file.get("path", "")
        series_path: str = series.get("path", "")
        season_number: int = episodes[0].get("seasonNumber", 1) if episodes else 1
        episode_number: int = episodes[0].get("episodeNumber", 0) if episodes else 0

        if not all([sonarr_id, file_id, current_path, series_path]):
            logger.warning(
                "Sonarr webhook payload missing required fields: %s", payload
            )
            return

        anilist_id = await self._resolve_sonarr_anilist_id(sonarr_id, season_number)
        if not anilist_id:
            logger.info(
                "No AniList mapping for sonarr_id=%d season=%d — skipping",
                sonarr_id,
                season_number,
            )
            return

        show_info, season_info = await self._get_show_and_season_info(anilist_id)
        if not season_info["title"]:
            logger.warning("No AniList title for anilist_id=%d — skipping", anilist_id)
            return

        original_name = Path(current_path).name
        filename = await self._get_file_name(
            original_name, season_info, season_number, episode_number
        )
        safe_dir = await self._get_folder_name(show_info)
        season_dir = await self._get_season_folder_name(season_number, season_info)

        # Path prefix translation for Docker/remote setups
        arr_prefix = self._config.sonarr.path_prefix
        local_prefix = self._config.sonarr.local_path_prefix
        local_current = self._to_local(current_path, arr_prefix, local_prefix)

        # When a library path is configured it is the library root — the show
        # folder (safe_dir) is appended beneath it.  When falling back to
        # series_path, that IS already the show-level folder; appending safe_dir
        # again would create a nested structure (show/show/Season N).
        library_path = await self._get_library_output_path()
        if library_path:
            local_series = str(
                Path(self._to_local(library_path, arr_prefix, local_prefix)) / safe_dir
            )
        else:
            local_series = self._to_local(series_path, arr_prefix, local_prefix)

        local_target = str(Path(local_series) / season_dir / filename)

        if Path(local_target).resolve() == Path(local_current).resolve():
            logger.debug("Sonarr file already at target path: %s", current_path)
            return

        if not self._move_file(local_current, local_target):
            return

        arr_series_path = self._to_arr(local_series, arr_prefix, local_prefix)
        arr_target_path = self._to_arr(local_target, arr_prefix, local_prefix)
        relative_path = str(Path(local_target).relative_to(local_series))

        sonarr = SonarrClient(
            url=self._config.sonarr.url, api_key=self._config.sonarr.api_key
        )
        try:
            # Only update the series path when it has actually changed (e.g. first
            # episode after add, or library root moved) — skip on subsequent downloads
            # where Sonarr already points at the right show folder.
            if arr_series_path != series_path:
                await sonarr.update_series_path(sonarr_id, arr_series_path)
                logger.info(
                    "Sonarr series id=%d path updated → %s", sonarr_id, arr_series_path
                )
            # Tell Sonarr exactly where this file landed — no full rescan needed.
            await sonarr.update_episode_file(file_id, relative_path, arr_target_path)
            logger.info(
                "Sonarr episode file id=%d path updated → %s", file_id, arr_target_path
            )
        except Exception as exc:
            logger.error(
                "Failed to update Sonarr after move for id=%d: %s", sonarr_id, exc
            )
        finally:
            await sonarr.close()

    async def process_radarr_download(self, payload: dict[str, Any]) -> None:
        """Handle a Radarr 'Download' webhook event."""
        event_type = payload.get("eventType", "")
        if event_type == "Test":
            logger.info("Radarr webhook test received — OK")
            return
        if event_type not in ("Download",):
            logger.debug("Radarr webhook event_type=%r ignored", event_type)
            return

        movie = payload.get("movie", {})
        movie_file = payload.get("movieFile", {})

        radarr_id: int = movie.get("id", 0)
        file_id: int = movie_file.get("id", 0)
        current_path: str = movie_file.get("path", "")
        # movie.folderPath is the movie's dedicated folder; its parent is the root
        folder_path: str = movie.get("folderPath", "")

        if not all([radarr_id, file_id, current_path]):
            logger.warning(
                "Radarr webhook payload missing required fields: %s", payload
            )
            return

        mapping = await self._db.fetch_one(
            "SELECT anilist_id FROM anilist_radarr_mapping WHERE radarr_id=?",
            (radarr_id,),
        )
        if not mapping:
            logger.info("No AniList mapping for radarr_id=%d — skipping", radarr_id)
            return

        anilist_id: int = mapping["anilist_id"]
        title_info = await self._get_anilist_title_info(anilist_id)
        if not title_info["title"]:
            logger.warning("No AniList title for anilist_id=%d — skipping", anilist_id)
            return

        original_name = Path(current_path).name
        filename = await self._get_movie_file_name(original_name, title_info)
        safe_dir = await self._get_folder_name(title_info)

        # Use library output path as target root; fall back to Radarr movie root
        library_path = await self._get_library_output_path()
        arr_prefix = self._config.radarr.path_prefix
        local_prefix = self._config.radarr.local_path_prefix

        if library_path:
            target_root = library_path
        else:
            # Fall back to parent of movie folder
            arr_root = (
                Path(folder_path).parent
                if folder_path
                else Path(current_path).parent.parent
            )
            target_root = str(arr_root)

        local_root = Path(self._to_local(target_root, arr_prefix, local_prefix))
        local_current = self._to_local(current_path, arr_prefix, local_prefix)
        local_target = str(local_root / safe_dir / filename)

        if Path(local_target).resolve() == Path(local_current).resolve():
            logger.debug("Radarr file already at target path: %s", current_path)
            return

        if not self._move_file(local_current, local_target):
            return

        arr_movie_path = self._to_arr(
            str(local_root / safe_dir), arr_prefix, local_prefix
        )
        radarr = RadarrClient(
            url=self._config.radarr.url, api_key=self._config.radarr.api_key
        )
        try:
            await radarr.update_movie_path(radarr_id, arr_movie_path)
            logger.info(
                "Radarr movie id=%d path updated → %s", radarr_id, arr_movie_path
            )
            await radarr.rescan_movie(radarr_id)
            logger.info("Radarr rescan triggered for movie id=%d", radarr_id)
        except Exception as exc:
            logger.error(
                "Failed to update Radarr after move for id=%d: %s", radarr_id, exc
            )
        finally:
            await radarr.close()

    # ------------------------------------------------------------------
    # Manual reprocess (existing entries)
    # ------------------------------------------------------------------

    @staticmethod
    def _to_local(path: str, arr_prefix: str, local_prefix: str) -> str:
        """Translate an arr-side path to the locally-writable equivalent."""
        if arr_prefix and local_prefix and path.startswith(arr_prefix):
            return local_prefix + path[len(arr_prefix) :]
        return path

    @staticmethod
    def _to_arr(path: str, arr_prefix: str, local_prefix: str) -> str:
        """Translate a local path back to the arr-side path."""
        if arr_prefix and local_prefix and path.startswith(local_prefix):
            return arr_prefix + path[len(local_prefix) :]
        return path

    async def reprocess_sonarr_series(
        self, sonarr_id: int, dry_run: bool = False
    ) -> dict[str, Any]:
        """Move all existing files for a Sonarr series into AniList-structured paths.

        For each episode file, resolves the AniList mapping per season, moves the
        file to {series_path}/{anilist_title}/{filename}, and updates Sonarr's record.
        Returns a summary with moved/skipped/error counts.
        When dry_run=True, returns the planned moves without executing them.
        """
        arr_prefix = self._config.sonarr.path_prefix
        local_prefix = self._config.sonarr.local_path_prefix

        sonarr = SonarrClient(
            url=self._config.sonarr.url, api_key=self._config.sonarr.api_key
        )
        try:
            series = await sonarr.get_series_by_id(sonarr_id)
            if not series:
                return {"ok": False, "error": f"Series {sonarr_id} not found in Sonarr"}

            series_path: str = series.get("path", "")
            if not series_path:
                return {"ok": False, "error": "Series has no path in Sonarr"}

            # When library_path is set it is the library root — safe_dir is appended.
            # When absent, series_path IS the show folder; don't nest safe_dir under it.
            library_path = await self._get_library_output_path()
            if library_path:
                local_target_root: str | None = self._to_local(
                    library_path, arr_prefix, local_prefix
                )
                local_series_fallback: str | None = None
            else:
                local_target_root = None
                local_series_fallback = self._to_local(
                    series_path, arr_prefix, local_prefix
                )

            # Build episodeFileId → (seasonNumber, episodeNumber) map
            episodes = await sonarr.get_episodes(sonarr_id)
            file_season: dict[int, int] = {}
            file_episode: dict[int, int] = {}
            for ep in episodes:
                fid = ep.get("episodeFileId", 0)
                if fid:
                    file_season[fid] = ep.get("seasonNumber", 1)
                    file_episode[fid] = ep.get("episodeNumber", 0)

            episode_files = await sonarr.get_episode_files(sonarr_id)

            if dry_run:
                plan: list[dict[str, Any]] = []
                for ef in episode_files:
                    file_id: int = ef.get("id", 0)
                    arr_current_path: str = ef.get("path", "")
                    if not file_id or not arr_current_path:
                        continue

                    season_number = file_season.get(file_id, 1)
                    episode_number = file_episode.get(file_id, 0)
                    anilist_id = await self._resolve_sonarr_anilist_id(
                        sonarr_id, season_number
                    )
                    if not anilist_id:
                        continue

                    show_info, season_info = await self._get_show_and_season_info(
                        anilist_id
                    )
                    if not season_info["title"]:
                        continue

                    original_name = Path(arr_current_path).name
                    filename = await self._get_file_name(
                        original_name, season_info, season_number, episode_number
                    )
                    safe_dir = await self._get_folder_name(show_info)
                    season_dir = await self._get_season_folder_name(
                        season_number, season_info
                    )

                    local_current = self._to_local(
                        arr_current_path, arr_prefix, local_prefix
                    )
                    if local_target_root:
                        local_series_dry = str(Path(local_target_root) / safe_dir)
                    else:
                        local_series_dry = local_series_fallback or ""
                    local_target = str(Path(local_series_dry) / season_dir / filename)
                    arr_target = self._to_arr(local_target, arr_prefix, local_prefix)

                    already_at_target = (
                        Path(local_target).resolve() == Path(local_current).resolve()
                    )
                    plan.append(
                        {
                            "file_id": file_id,
                            "season": season_number,
                            "anilist_id": anilist_id,
                            "anilist_title": season_info["title"],
                            "folder_name": safe_dir,
                            "arr_from": arr_current_path,
                            "arr_to": arr_target,
                            "local_from": local_current,
                            "local_to": local_target,
                            "action": "skip" if already_at_target else "move",
                        }
                    )
                return {
                    "ok": True,
                    "dry_run": True,
                    "series_path": series_path,
                    "files": plan,
                }

            moved = skipped = errors = 0

            # When a library path is configured, update the Sonarr series path once
            # upfront so it points at the correct show folder in our library.
            # When falling back to series_path, it is already the show folder — skip.
            if local_target_root and episode_files:
                for _probe in episode_files:
                    _probe_fid = _probe.get("id", 0)
                    _probe_sn = file_season.get(_probe_fid, 1)
                    _probe_aid = await self._resolve_sonarr_anilist_id(
                        sonarr_id, _probe_sn
                    )
                    if _probe_aid:
                        _probe_show, _ = await self._get_show_and_season_info(
                            _probe_aid
                        )
                        if _probe_show["title"]:
                            _probe_dir = await self._get_folder_name(_probe_show)
                            arr_series_path = self._to_arr(
                                str(Path(local_target_root) / _probe_dir),
                                arr_prefix,
                                local_prefix,
                            )
                            if arr_series_path != series_path:
                                try:
                                    await sonarr.update_series_path(
                                        sonarr_id, arr_series_path
                                    )
                                    series_path = arr_series_path
                                    logger.info(
                                        "Sonarr series id=%d path → %s",
                                        sonarr_id,
                                        arr_series_path,
                                    )
                                except Exception as exc:
                                    logger.warning(
                                        "Failed to update series path for id=%d: %s",
                                        sonarr_id,
                                        exc,
                                    )
                            break
                    break

            for ef in episode_files:
                file_id = ef.get("id", 0)
                arr_current_path = ef.get("path", "")
                if not file_id or not arr_current_path:
                    continue

                season_number = file_season.get(file_id, 1)
                episode_number = file_episode.get(file_id, 0)
                anilist_id = await self._resolve_sonarr_anilist_id(
                    sonarr_id, season_number
                )
                if not anilist_id:
                    logger.info(
                        "No AniList mapping for sonarr_id=%d season=%d — skipping %s",
                        sonarr_id,
                        season_number,
                        arr_current_path,
                    )
                    skipped += 1
                    continue

                show_info, season_info = await self._get_show_and_season_info(
                    anilist_id
                )
                if not season_info["title"]:
                    logger.warning(
                        "No title for anilist_id=%d — skipping %s",
                        anilist_id,
                        arr_current_path,
                    )
                    skipped += 1
                    continue

                original_name = Path(arr_current_path).name
                filename = await self._get_file_name(
                    original_name, season_info, season_number, episode_number
                )
                safe_dir = await self._get_folder_name(show_info)
                season_dir = await self._get_season_folder_name(
                    season_number, season_info
                )

                # Paths for local move
                local_current = self._to_local(
                    arr_current_path, arr_prefix, local_prefix
                )
                if local_target_root:
                    local_series_for_file = str(Path(local_target_root) / safe_dir)
                else:
                    local_series_for_file = local_series_fallback or ""
                local_target = str(Path(local_series_for_file) / season_dir / filename)

                if Path(local_target).resolve() == Path(local_current).resolve():
                    skipped += 1
                    continue

                # Source gone but target exists = already moved previously
                if not Path(local_current).exists() and Path(local_target).exists():
                    skipped += 1
                    continue

                if not self._move_file(local_current, local_target):
                    errors += 1
                    continue
                moved += 1

                # Tell Sonarr exactly where this file landed — no rescan needed.
                arr_target_path = self._to_arr(local_target, arr_prefix, local_prefix)
                relative_path = str(
                    Path(local_target).relative_to(local_series_for_file)
                )
                try:
                    await sonarr.update_episode_file(
                        file_id, relative_path, arr_target_path
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to update episode file id=%d in Sonarr: %s",
                        file_id,
                        exc,
                    )

            return {"ok": True, "moved": moved, "skipped": skipped, "errors": errors}
        finally:
            await sonarr.close()

    async def reprocess_radarr_movie(
        self, radarr_id: int, dry_run: bool = False
    ) -> dict[str, Any]:
        """Move the downloaded file for a Radarr movie into its AniList-structured path.

        When dry_run=True, returns the planned moves without executing them.
        """
        arr_prefix = self._config.radarr.path_prefix
        local_prefix = self._config.radarr.local_path_prefix

        radarr = RadarrClient(
            url=self._config.radarr.url, api_key=self._config.radarr.api_key
        )
        try:
            mapping = await self._db.fetch_one(
                "SELECT anilist_id FROM anilist_radarr_mapping WHERE radarr_id=?",
                (radarr_id,),
            )
            if not mapping:
                return {
                    "ok": False,
                    "error": f"No AniList mapping for radarr_id={radarr_id}",
                }

            anilist_id: int = mapping["anilist_id"]
            title_info = await self._get_anilist_title_info(anilist_id)
            if not title_info["title"]:
                return {
                    "ok": False,
                    "error": f"No title found for anilist_id={anilist_id}",
                }

            movie = await radarr.get_movie_by_id(radarr_id)
            if not movie:
                return {"ok": False, "error": f"Movie {radarr_id} not found in Radarr"}

            movie_files = await radarr.get_movie_files(radarr_id)
            if not movie_files:
                if dry_run:
                    return {"ok": True, "dry_run": True, "files": []}
                return {"ok": True, "moved": 0, "skipped": 0, "errors": 0}

            # Use library output path as target root; fall back to Radarr movie root
            library_path = await self._get_library_output_path()
            if library_path:
                target_root = library_path
            else:
                folder_path_str: str = movie.get("folderPath", "")
                arr_root = (
                    Path(folder_path_str).parent
                    if folder_path_str
                    else Path(movie_files[0].get("path", "")).parent.parent
                )
                target_root = str(arr_root)
            local_root = Path(self._to_local(target_root, arr_prefix, local_prefix))
            safe_dir = await self._get_folder_name(title_info)

            if dry_run:
                plan: list[dict[str, Any]] = []
                for mf in movie_files:
                    file_id: int = mf.get("id", 0)
                    arr_current: str = mf.get("path", "")
                    if not file_id or not arr_current:
                        continue

                    original_name = Path(arr_current).name
                    filename = await self._get_movie_file_name(
                        original_name, title_info
                    )
                    local_current = self._to_local(
                        arr_current, arr_prefix, local_prefix
                    )
                    local_target = str(local_root / safe_dir / filename)
                    arr_target = self._to_arr(local_target, arr_prefix, local_prefix)

                    already_at_target = (
                        Path(local_target).resolve() == Path(local_current).resolve()
                    )
                    plan.append(
                        {
                            "file_id": file_id,
                            "anilist_id": anilist_id,
                            "anilist_title": title_info["title"],
                            "folder_name": safe_dir,
                            "arr_from": arr_current,
                            "arr_to": arr_target,
                            "local_from": local_current,
                            "local_to": local_target,
                            "action": "skip" if already_at_target else "move",
                        }
                    )
                return {"ok": True, "dry_run": True, "files": plan}

            moved = skipped = errors = 0
            movie_path_updated = False

            for mf in movie_files:
                file_id = mf.get("id", 0)
                arr_current = mf.get("path", "")
                if not file_id or not arr_current:
                    continue

                original_name = Path(arr_current).name
                filename = await self._get_movie_file_name(original_name, title_info)
                local_current = self._to_local(arr_current, arr_prefix, local_prefix)
                local_target = str(local_root / safe_dir / filename)

                if Path(local_target).resolve() == Path(local_current).resolve():
                    skipped += 1
                    continue

                if not Path(local_current).exists() and Path(local_target).exists():
                    skipped += 1
                    continue

                if not self._move_file(local_current, local_target):
                    errors += 1
                    continue

                # Update movie path in Radarr once
                if not movie_path_updated:
                    arr_movie_path = self._to_arr(
                        str(local_root / safe_dir), arr_prefix, local_prefix
                    )
                    try:
                        await radarr.update_movie_path(radarr_id, arr_movie_path)
                        logger.info(
                            "Radarr movie id=%d path → %s",
                            radarr_id,
                            arr_movie_path,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to update movie path for id=%d: %s",
                            radarr_id,
                            exc,
                        )
                    movie_path_updated = True
                moved += 1

            # Always rescan so Radarr discovers files at their current paths
            try:
                await radarr.rescan_movie(radarr_id)
                logger.info("Radarr rescan triggered for movie id=%d", radarr_id)
            except Exception as exc:
                logger.warning(
                    "Failed to trigger Radarr rescan for id=%d: %s",
                    radarr_id,
                    exc,
                )

            return {"ok": True, "moved": moved, "skipped": skipped, "errors": errors}
        finally:
            await radarr.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_sonarr_anilist_id(
        self, sonarr_id: int, season_number: int
    ) -> int | None:
        """Return the AniList ID for a given Sonarr series + season."""
        # Per-season mapping takes precedence (multi-season TVDB series)
        row = await self._db.fetch_one(
            "SELECT anilist_id FROM anilist_sonarr_season_mapping"
            " WHERE sonarr_id=? AND season_number=?",
            (sonarr_id, season_number),
        )
        if row:
            return int(row["anilist_id"])

        # Fall back to series-level mapping (covers 1:1 TVDB:AniList case)
        row = await self._db.fetch_one(
            "SELECT anilist_id FROM anilist_sonarr_mapping WHERE sonarr_id=?",
            (sonarr_id,),
        )
        return int(row["anilist_id"]) if row else None

    async def _get_library_output_path(self) -> str | None:
        """Return the first configured library path, or None."""
        libraries = await self._db.get_all_libraries()
        if not libraries:
            return None
        import json

        paths = json.loads(libraries[0].get("paths", "[]"))
        return paths[0] if paths else None

    async def _get_show_and_season_info(self, anilist_id: int) -> tuple[dict, dict]:
        """Return (show_title_info, season_title_info) for an AniList entry.

        If the entry belongs to a series group, show_title_info uses the
        root entry's titles (for the top-level folder).  season_title_info
        always uses this entry's own titles (for the season subfolder).

        If no series group exists, both dicts are identical.
        """
        entry_info = await self._get_anilist_title_info(anilist_id)

        group = await self._db.get_series_group_by_anilist_id(anilist_id)
        if group:
            root_id = group.get("root_anilist_id")
            if root_id and root_id != anilist_id:
                root_info = await self._get_anilist_title_info(root_id)
                if root_info["title"]:
                    return root_info, entry_info

        return entry_info, entry_info

    async def _get_anilist_title_and_year(self, anilist_id: int) -> tuple[str, int]:
        """Return the best available (title, year) for an AniList entry."""
        info = await self._get_anilist_title_info(anilist_id)
        return info["title"], info["year"]

    async def _get_anilist_title_info(self, anilist_id: int) -> dict:
        """Return title variants and year for an AniList entry.

        Returns dict with keys: title, title_romaji, title_english, year.
        ``title`` is resolved according to the user's app.title_display pref.
        """
        title_pref = await self._db.get_setting("app.title_display") or "romaji"
        romaji = ""
        english = ""
        year = 0

        cached = await self._db.get_cached_metadata(anilist_id)
        if cached:
            romaji = cached.get("title_romaji") or ""
            english = cached.get("title_english") or ""
            year = int(cached.get("year") or 0)

        # Watchlist entry may have a better title (user-facing)
        users = await self._db.get_users_by_service("anilist")
        if users:
            entry = await self._db.get_watchlist_entry(users[0]["user_id"], anilist_id)
            if entry and entry.get("anilist_title"):
                # Watchlist stores a single display title — use as fallback
                if not romaji:
                    romaji = entry["anilist_title"]
                year = year or int(entry.get("start_year") or 0)

        # Resolve display title based on user preference
        if title_pref == "english" and english:
            title = english
        elif romaji:
            title = romaji
        else:
            title = english or romaji

        return {
            "title": title,
            "title_romaji": romaji,
            "title_english": english,
            "year": year,
        }

    @staticmethod
    def _move_file(src: str, dst: str) -> bool:
        """Move src to dst, creating parent directories as needed. True on success."""
        try:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dst)
            logger.info("Moved %s → %s", src, dst)
            return True
        except Exception as exc:
            logger.error("Failed to move %s → %s: %s", src, dst, exc)
            return False

    async def _get_season_folder_name(
        self, season_number: int, title_info: dict
    ) -> str:
        """Render the season subfolder name using the user's season folder template."""
        from src.Utils.NamingTemplate import (
            DEFAULT_ILLEGAL_CHAR_REPLACEMENT,
            DEFAULT_SEASON_FOLDER_TEMPLATE,
            NamingTemplate,
        )

        tmpl_str = await self._db.get_setting("naming.season_folder_template") or ""
        illegal_repl = (
            await self._db.get_setting("naming.illegal_char_replacement") or ""
        )
        tmpl = NamingTemplate(tmpl_str or DEFAULT_SEASON_FOLDER_TEMPLATE)
        year = title_info["year"]
        tokens = {
            "season": str(season_number),
            "season.name": title_info["title"],
            "year": str(year) if year else "",
        }
        rendered = tmpl.render(tokens)
        return (
            NamingTemplate.sanitize(
                rendered, illegal_repl or DEFAULT_ILLEGAL_CHAR_REPLACEMENT
            )
            or f"Season {season_number}"
        )

    async def _get_folder_name(self, title_info: dict) -> str:
        """Render the AniList subfolder name using the user's folder naming template."""
        from src.Utils.NamingTemplate import (
            DEFAULT_FOLDER_TEMPLATE,
            DEFAULT_ILLEGAL_CHAR_REPLACEMENT,
            NamingTemplate,
        )

        folder_tmpl_str = await self._db.get_setting("naming.folder_template") or ""
        illegal_repl = (
            await self._db.get_setting("naming.illegal_char_replacement") or ""
        )
        tmpl = NamingTemplate(folder_tmpl_str or DEFAULT_FOLDER_TEMPLATE)
        year = title_info["year"]
        tokens = {
            "title": title_info["title"],
            "title.romaji": title_info["title_romaji"] or title_info["title"],
            "title.english": title_info["title_english"] or title_info["title"],
            "year": str(year) if year else "",
        }
        rendered = tmpl.render(tokens)
        return NamingTemplate.sanitize(
            rendered, illegal_repl or DEFAULT_ILLEGAL_CHAR_REPLACEMENT
        ) or NamingTemplate.sanitize(title_info["title"])

    async def _get_file_name(
        self,
        original_filename: str,
        title_info: dict,
        season_number: int,
        episode_number: int,
    ) -> str:
        """Render the episode file name using the user's file template.

        Returns the original filename unchanged if no file template is set.
        """
        import os

        from src.Utils.NamingTemplate import (
            DEFAULT_FILE_TEMPLATE,
            DEFAULT_ILLEGAL_CHAR_REPLACEMENT,
            NamingTemplate,
            parse_quality,
        )

        tmpl_str = await self._db.get_setting("naming.file_template") or ""
        if not tmpl_str:
            return original_filename  # No template configured — keep original

        illegal_repl = (
            await self._db.get_setting("naming.illegal_char_replacement") or ""
        )
        _name, ext = os.path.splitext(original_filename)
        quality = parse_quality(original_filename)
        year = title_info["year"]

        tokens = {
            "title": title_info["title"],
            "title.romaji": title_info["title_romaji"] or title_info["title"],
            "title.english": title_info["title_english"] or title_info["title"],
            "year": str(year) if year else "",
            "season": f"{season_number:02d}",
            "episode": f"{episode_number:02d}",
            "quality": quality.full,
            "quality.resolution": quality.resolution,
            "quality.source": quality.source,
        }
        tmpl = NamingTemplate(tmpl_str or DEFAULT_FILE_TEMPLATE)
        rendered = tmpl.render(tokens)
        sanitized = NamingTemplate.sanitize(
            rendered, illegal_repl or DEFAULT_ILLEGAL_CHAR_REPLACEMENT
        )
        return (sanitized + ext) if sanitized else original_filename

    async def _get_movie_file_name(
        self, original_filename: str, title_info: dict
    ) -> str:
        """Render the movie file name using the user's movie file template.

        Returns the original filename unchanged if no template is set.
        """
        import os

        from src.Utils.NamingTemplate import (
            DEFAULT_ILLEGAL_CHAR_REPLACEMENT,
            DEFAULT_MOVIE_FILE_TEMPLATE,
            NamingTemplate,
            parse_quality,
        )

        tmpl_str = await self._db.get_setting("naming.movie_file_template") or ""
        if not tmpl_str:
            return original_filename

        illegal_repl = (
            await self._db.get_setting("naming.illegal_char_replacement") or ""
        )
        _name, ext = os.path.splitext(original_filename)
        quality = parse_quality(original_filename)
        year = title_info["year"]

        tokens = {
            "title": title_info["title"],
            "title.romaji": title_info["title_romaji"] or title_info["title"],
            "title.english": title_info["title_english"] or title_info["title"],
            "year": str(year) if year else "",
            "quality": quality.full,
            "quality.resolution": quality.resolution,
            "quality.source": quality.source,
        }
        tmpl = NamingTemplate(tmpl_str or DEFAULT_MOVIE_FILE_TEMPLATE)
        rendered = tmpl.render(tokens)
        sanitized = NamingTemplate.sanitize(
            rendered, illegal_repl or DEFAULT_ILLEGAL_CHAR_REPLACEMENT
        )
        return (sanitized + ext) if sanitized else original_filename
