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
from src.Utils.NamingTemplate import NamingTemplate

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

        anilist_title = await self._get_anilist_title(anilist_id)
        if not anilist_title:
            logger.warning("No AniList title for anilist_id=%d — skipping", anilist_id)
            return

        filename = Path(current_path).name
        safe_dir = NamingTemplate.sanitize(anilist_title)
        target_path = str(Path(series_path) / safe_dir / filename)

        if Path(target_path).resolve() == Path(current_path).resolve():
            logger.debug("Sonarr file already at target path: %s", current_path)
            return

        if not self._move_file(current_path, target_path):
            return

        relative_path = str(Path(target_path).relative_to(series_path))
        sonarr = SonarrClient(
            url=self._config.sonarr.url, api_key=self._config.sonarr.api_key
        )
        try:
            await sonarr.update_episode_file(file_id, relative_path, target_path)
            logger.info("Sonarr file id=%d updated → %s", file_id, target_path)
        except Exception as exc:
            logger.error("Failed to update Sonarr file record id=%d: %s", file_id, exc)
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
        anilist_title = await self._get_anilist_title(anilist_id)
        if not anilist_title:
            logger.warning("No AniList title for anilist_id=%d — skipping", anilist_id)
            return

        filename = Path(current_path).name
        safe_dir = NamingTemplate.sanitize(anilist_title)

        # Root = parent of movie folder (or parent of current file if no folderPath)
        root = (
            Path(folder_path).parent
            if folder_path
            else Path(current_path).parent.parent
        )
        target_path = str(root / safe_dir / filename)

        if Path(target_path).resolve() == Path(current_path).resolve():
            logger.debug("Radarr file already at target path: %s", current_path)
            return

        if not self._move_file(current_path, target_path):
            return

        # relativePath for movies is just the filename within the movie folder
        relative_path = filename
        radarr = RadarrClient(
            url=self._config.radarr.url, api_key=self._config.radarr.api_key
        )
        try:
            await radarr.update_movie_file(file_id, relative_path, target_path)
            logger.info("Radarr file id=%d updated → %s", file_id, target_path)
        except Exception as exc:
            logger.error("Failed to update Radarr file record id=%d: %s", file_id, exc)
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
        if not arr_prefix or not local_prefix:
            return {
                "ok": False,
                "error": (
                    "Reprocess requires local filesystem access. "
                    "Configure 'Sonarr path prefix' and "
                    "'Local path prefix' in Settings."
                ),
            }

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

            local_series_path = self._to_local(series_path, arr_prefix, local_prefix)

            # Build episodeFileId → seasonNumber map
            episodes = await sonarr.get_episodes(sonarr_id)
            file_season: dict[int, int] = {}
            for ep in episodes:
                fid = ep.get("episodeFileId", 0)
                if fid:
                    file_season[fid] = ep.get("seasonNumber", 1)

            episode_files = await sonarr.get_episode_files(sonarr_id)

            if dry_run:
                plan: list[dict[str, Any]] = []
                for ef in episode_files:
                    file_id: int = ef.get("id", 0)
                    arr_current_path: str = ef.get("path", "")
                    if not file_id or not arr_current_path:
                        continue

                    season_number = file_season.get(file_id, 1)
                    anilist_id = await self._resolve_sonarr_anilist_id(
                        sonarr_id, season_number
                    )
                    if not anilist_id:
                        continue

                    anilist_title, anilist_year = (
                        await self._get_anilist_title_and_year(anilist_id)
                    )
                    if not anilist_title:
                        continue

                    filename = Path(arr_current_path).name
                    safe_dir = await self._get_folder_name(
                        anilist_title, year=anilist_year
                    )

                    local_current = self._to_local(
                        arr_current_path, arr_prefix, local_prefix
                    )
                    local_target = str(Path(local_series_path) / safe_dir / filename)
                    arr_target = self._to_arr(local_target, arr_prefix, local_prefix)

                    already_at_target = (
                        Path(local_target).resolve() == Path(local_current).resolve()
                    )
                    plan.append(
                        {
                            "file_id": file_id,
                            "season": season_number,
                            "anilist_id": anilist_id,
                            "anilist_title": anilist_title,
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

            for ef in episode_files:
                file_id = ef.get("id", 0)
                arr_current_path = ef.get("path", "")
                if not file_id or not arr_current_path:
                    continue

                season_number = file_season.get(file_id, 1)
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

                anilist_title, anilist_year = await self._get_anilist_title_and_year(
                    anilist_id
                )
                if not anilist_title:
                    logger.warning(
                        "No title for anilist_id=%d — skipping %s",
                        anilist_id,
                        arr_current_path,
                    )
                    skipped += 1
                    continue

                filename = Path(arr_current_path).name
                safe_dir = await self._get_folder_name(anilist_title, year=anilist_year)

                # Paths for local move
                local_current = self._to_local(
                    arr_current_path, arr_prefix, local_prefix
                )
                local_target = str(Path(local_series_path) / safe_dir / filename)

                if Path(local_target).resolve() == Path(local_current).resolve():
                    skipped += 1
                    continue

                if not self._move_file(local_current, local_target):
                    errors += 1
                    continue

                # Report back to Sonarr using its own path scheme
                arr_target = self._to_arr(local_target, arr_prefix, local_prefix)
                relative_path = str(Path(arr_target).relative_to(series_path))
                try:
                    await sonarr.update_episode_file(file_id, relative_path, arr_target)
                    moved += 1
                except Exception as exc:
                    logger.error(
                        "Failed to update Sonarr file record id=%d: %s", file_id, exc
                    )
                    errors += 1

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
        if not arr_prefix or not local_prefix:
            return {
                "ok": False,
                "error": (
                    "Reprocess requires local filesystem access. "
                    "Configure 'Radarr path prefix' and "
                    "'Local path prefix' in Settings."
                ),
            }

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
            anilist_title, anilist_year = await self._get_anilist_title_and_year(
                anilist_id
            )
            if not anilist_title:
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

            folder_path: str = movie.get("folderPath", "")
            arr_root = (
                Path(folder_path).parent
                if folder_path
                else Path(movie_files[0].get("path", "")).parent.parent
            )
            local_root = Path(self._to_local(str(arr_root), arr_prefix, local_prefix))
            safe_dir = await self._get_folder_name(anilist_title, year=anilist_year)

            if dry_run:
                plan: list[dict[str, Any]] = []
                for mf in movie_files:
                    file_id: int = mf.get("id", 0)
                    arr_current: str = mf.get("path", "")
                    if not file_id or not arr_current:
                        continue

                    filename = Path(arr_current).name
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
                            "anilist_title": anilist_title,
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

            for mf in movie_files:
                file_id = mf.get("id", 0)
                arr_current = mf.get("path", "")
                if not file_id or not arr_current:
                    continue

                filename = Path(arr_current).name
                local_current = self._to_local(arr_current, arr_prefix, local_prefix)
                local_target = str(local_root / safe_dir / filename)

                if Path(local_target).resolve() == Path(local_current).resolve():
                    skipped += 1
                    continue

                if not self._move_file(local_current, local_target):
                    errors += 1
                    continue

                arr_target = self._to_arr(local_target, arr_prefix, local_prefix)
                try:
                    await radarr.update_movie_file(file_id, filename, arr_target)
                    moved += 1
                except Exception as exc:
                    logger.error(
                        "Failed to update Radarr file record id=%d: %s", file_id, exc
                    )
                    errors += 1

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

    async def _get_anilist_title(self, anilist_id: int) -> str:
        """Return the best available title for an AniList entry."""
        title, _ = await self._get_anilist_title_and_year(anilist_id)
        return title

    async def _get_anilist_title_and_year(self, anilist_id: int) -> tuple[str, int]:
        """Return the best available (title, year) for an AniList entry."""
        users = await self._db.get_users_by_service("anilist")
        if users:
            entry = await self._db.get_watchlist_entry(users[0]["user_id"], anilist_id)
            if entry and entry.get("anilist_title"):
                year = entry.get("start_year") or 0
                return entry["anilist_title"], int(year) if year else 0

        cached = await self._db.get_cached_metadata(anilist_id)
        if cached:
            title = cached.get("title_english") or cached.get("title_romaji") or ""
            year = cached.get("year") or 0
            return title, int(year) if year else 0
        return "", 0

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

    async def _get_folder_name(self, anilist_title: str, year: int = 0) -> str:
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
        tokens = {
            "title": anilist_title,
            "title.romaji": anilist_title,
            "title.english": anilist_title,
            "year": str(year) if year else "",
        }
        rendered = tmpl.render(tokens)
        return NamingTemplate.sanitize(
            rendered, illegal_repl or DEFAULT_ILLEGAL_CHAR_REPLACEMENT
        ) or NamingTemplate.sanitize(anilist_title)
