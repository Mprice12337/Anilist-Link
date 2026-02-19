"""Library restructuring: consolidate Structure A folders into Structure B."""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field

from src.Database.Connection import DatabaseManager
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Utils.NamingTemplate import (
    DEFAULT_FILE_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    DEFAULT_SEASON_FOLDER_TEMPLATE,
    NamingTemplate,
    parse_quality,
)

logger = logging.getLogger(__name__)

_SEASON_EP = re.compile(r"(?i)(S)\d{2}(E\d+)")
_VIDEO_EXTS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".m4v",
    ".wmv",
    ".flv",
    ".ts",
    ".webm",
}
_SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt"}
_MEDIA_EXTS = _VIDEO_EXTS | _SUBTITLE_EXTS


@dataclass
class ShowInput:
    """Source-agnostic input for the restructurer.

    Produced by PlexShowProvider (Plex mode) or LocalDirectoryScanner (local mode).
    """

    title: str  # Display name (folder name or Plex title)
    local_path: str  # Filesystem path (already translated if needed)
    source_id: str  # Unique ID (rating_key for Plex, folder path for local)
    anilist_id: int = 0  # Mapped AniList ID (0 = unmapped)
    anilist_title: str = ""  # Matched AniList title
    year: int = 0  # AniList seasonYear or startDate.year
    anilist_title_romaji: str = ""  # AniList romaji title
    anilist_title_english: str = ""  # AniList English title


@dataclass
class FileMove:
    source: str
    destination: str
    original_filename: str
    renamed_filename: str


@dataclass
class RestructureGroup:
    series_group_id: int
    display_title: str
    target_folder: str
    source_folders: list[str]
    file_moves: list[FileMove]
    season_count: int
    enabled: bool = True
    warnings: list[str] = field(default_factory=list)
    source_rating_keys: list[str] = field(default_factory=list)
    operation_type: str = "move"  # "rename_folder", "rename_file", "move"
    current_folder: str = ""  # current folder basename (for L1/L2 preview)
    group_key: str = ""  # unique ID for form checkboxes


@dataclass
class RestructurePlan:
    groups: list[RestructureGroup]
    total_files: int = 0
    total_groups: int = 0
    operation_level: str = (
        "full_restructure"  # "folder_rename", "folder_file_rename", "full_restructure"
    )


@dataclass
class RestructureProgress:
    status: str = "pending"
    phase: str = ""
    processed: int = 0
    total: int = 0
    current_item: str = ""
    error_message: str = ""
    started_at: float = 0.0


_EP_PATTERN = re.compile(
    r"S(\d{1,2})E(\d{1,3})"  # S01E01
    r"|[Ee](?:pisode)?\.?\s*(\d{1,3})"  # Episode 01, E01
    r"|\s-\s(\d{2,3})(?:\s|$|\[)"  # " - 01 " absolute
)


def _extract_episode_number(filename: str) -> int | None:
    """Extract episode number from a filename. Returns None if not found."""
    match = _EP_PATTERN.search(filename)
    if not match:
        return None

    if match.group(1) is not None:
        return int(match.group(2).lstrip("E"))
    elif match.group(3) is not None:
        return int(match.group(3))
    elif match.group(4) is not None:
        return int(match.group(4))
    return None


def standardize_episode_filename(
    filename: str, show_title: str, season_num: int
) -> str:
    """Rename episode file to 'Show Title - S01E01.ext' format.

    Returns original filename if no episode pattern found or not a media file.
    """
    _name, ext = os.path.splitext(filename)
    if ext.lower() not in _MEDIA_EXTS:
        return filename

    ep_num = _extract_episode_number(filename)
    if ep_num is None:
        return filename

    safe_title = re.sub(r'[<>:"/\\|?*]', "", show_title).strip()
    return f"{safe_title} - S{season_num:02d}E{ep_num:02d}{ext}"


def _build_file_tokens(
    show: ShowInput,
    season_num: int,
    ep_num: int,
    filename: str,
    title_pref: str = "romaji",
) -> dict[str, str]:
    """Build the token dict for file template rendering."""
    title = _resolve_display_title(show, title_pref)
    quality = parse_quality(filename)
    return {
        "title": NamingTemplate.sanitize(title),
        "title.romaji": NamingTemplate.sanitize(show.anilist_title_romaji or title),
        "title.english": NamingTemplate.sanitize(show.anilist_title_english or title),
        "year": str(show.year) if show.year else "",
        "season": f"{season_num:02d}",
        "episode": f"{ep_num:02d}",
        "episode.title": "",  # future data source
        "quality": quality.full,
        "quality.resolution": quality.resolution,
        "quality.source": quality.source,
    }


def _build_folder_tokens(
    show: ShowInput,
    title_pref: str = "romaji",
) -> dict[str, str]:
    """Build the token dict for folder template rendering."""
    title = _resolve_display_title(show, title_pref)
    return {
        "title": NamingTemplate.sanitize(title),
        "title.romaji": NamingTemplate.sanitize(show.anilist_title_romaji or title),
        "title.english": NamingTemplate.sanitize(show.anilist_title_english or title),
        "year": str(show.year) if show.year else "",
    }


def _resolve_display_title(show: ShowInput, pref: str = "romaji") -> str:
    """Pick the display title based on preference setting."""
    if pref == "english" and show.anilist_title_english:
        return show.anilist_title_english
    if show.anilist_title_romaji:
        return show.anilist_title_romaji
    return show.anilist_title or show.title


def rename_episode_file(filename: str, new_season: int) -> str:
    """Rename S01Exx -> SxxExx in a filename."""
    if _SEASON_EP.search(filename):
        return _SEASON_EP.sub(rf"S{new_season:02d}\2", filename, count=1)
    return filename


class LibraryRestructurer:
    """Analyzes and executes library restructuring from Structure A to B.

    Source-agnostic: accepts ``list[ShowInput]`` from any provider.
    """

    def __init__(
        self,
        db: DatabaseManager,
        group_builder: SeriesGroupBuilder,
        file_template: str = "",
        folder_template: str = "",
        season_folder_template: str = "",
        title_pref: str = "romaji",
    ) -> None:
        self._db = db
        self._group_builder = group_builder
        self._file_tmpl = NamingTemplate(file_template or DEFAULT_FILE_TEMPLATE)
        self._folder_tmpl = NamingTemplate(folder_template or DEFAULT_FOLDER_TEMPLATE)
        self._season_tmpl = NamingTemplate(
            season_folder_template or DEFAULT_SEASON_FOLDER_TEMPLATE
        )
        self._title_pref = title_pref

    async def analyze(
        self,
        shows: list[ShowInput],
        progress: RestructureProgress,
        level: str = "full_restructure",
    ) -> RestructurePlan:
        """Analyze shows and build a restructure plan.

        Args:
            shows: Pre-gathered show inputs from a provider.
            progress: Mutable progress tracker.
            level: One of "folder_rename", "folder_file_rename", "full_restructure".
        """
        progress.status = "analyzing"
        progress.phase = "Analyzing shows"
        progress.started_at = time.monotonic()
        progress.total = len(shows)

        plan = RestructurePlan(groups=[], operation_level=level)

        if level == "full_restructure":
            await self._analyze_full_restructure(shows, plan, progress)
        else:
            await self._analyze_rename(shows, plan, progress, level)

        plan.total_files = sum(len(g.file_moves) for g in plan.groups)
        plan.total_groups = len(plan.groups)

        progress.status = "complete"
        progress.phase = "Analysis complete"
        return plan

    async def _analyze_full_restructure(
        self,
        shows: list[ShowInput],
        plan: RestructurePlan,
        progress: RestructureProgress,
    ) -> None:
        """Level 3: Group by series group, plan file moves into multi-season folder."""
        from typing import Any

        group_shows: dict[int, list[dict[str, Any]]] = {}
        # Keep a mapping from anilist_id to ShowInput for template token building
        show_by_anilist: dict[int, ShowInput] = {}

        for si in shows:
            progress.current_item = si.title
            progress.processed += 1

            if not si.anilist_id or not si.local_path:
                continue

            show_by_anilist[si.anilist_id] = si

            try:
                group_id, entries = await self._group_builder.get_or_build_group(
                    si.anilist_id
                )
            except Exception:
                logger.debug("Could not build group for %s", si.title)
                continue

            if not group_id or len(entries) < 2:
                continue

            if group_id not in group_shows:
                group_shows[group_id] = []

            season_order = 1
            for entry in entries:
                if entry["anilist_id"] == si.anilist_id:
                    season_order = entry["season_order"]
                    break

            group_shows[group_id].append(
                {
                    "title": si.title,
                    "local_path": si.local_path,
                    "anilist_id": si.anilist_id,
                    "season_order": season_order,
                    "source_id": si.source_id,
                }
            )

        progress.phase = "Building restructure plan"
        for group_id, shows_in_group in group_shows.items():
            if len(shows_in_group) < 2:
                continue

            shows_in_group.sort(key=lambda s: s["season_order"])

            group_info = await self._db.fetch_one(
                "SELECT display_title, root_anilist_id FROM series_groups WHERE id=?",
                (group_id,),
            )
            display_title = (
                group_info["display_title"]
                if group_info
                else shows_in_group[0]["title"]
            )

            # Build folder tokens from the group ROOT entry (not first-on-disk)
            # so the merged folder always uses the canonical series name
            first_path = shows_in_group[0]["local_path"]
            parent_dir = os.path.dirname(first_path)

            root_anilist_id = group_info["root_anilist_id"] if group_info else 0
            root_cache = (
                await self._db.get_cached_metadata(root_anilist_id)
                if root_anilist_id
                else None
            )

            # Start with display_title (always the root's title) as baseline
            safe_display = NamingTemplate.sanitize(display_title)
            folder_tokens: dict[str, str] = {
                "title": safe_display,
                "title.romaji": safe_display,
                "title.english": safe_display,
                "year": "",
            }

            if root_cache:
                # Rich data available from anilist_cache
                romaji = root_cache.get("title_romaji", "")
                english = root_cache.get("title_english", "")
                if romaji:
                    folder_tokens["title.romaji"] = NamingTemplate.sanitize(romaji)
                if english:
                    folder_tokens["title.english"] = NamingTemplate.sanitize(english)
                if self._title_pref == "english" and english:
                    folder_tokens["title"] = NamingTemplate.sanitize(english)
                elif romaji:
                    folder_tokens["title"] = NamingTemplate.sanitize(romaji)
                year = root_cache.get("year", 0) or 0
                if year:
                    folder_tokens["year"] = str(year)
            else:
                # No cache — pull year from the root's series_group_entries row
                root_entry_row = await self._db.fetch_one(
                    "SELECT start_date FROM series_group_entries"
                    " WHERE group_id=? ORDER BY season_order LIMIT 1",
                    (group_id,),
                )
                if root_entry_row and root_entry_row.get("start_date"):
                    try:
                        folder_tokens["year"] = root_entry_row["start_date"][:4]
                    except (IndexError, TypeError):
                        pass

            rendered_folder = NamingTemplate.sanitize(
                self._folder_tmpl.render(folder_tokens)
            )
            if not rendered_folder:
                rendered_folder = re.sub(r'[<>:"/\\|?*]', "", display_title).strip()

            target_folder = os.path.join(parent_dir, rendered_folder)

            source_folders = [s["local_path"] for s in shows_in_group]
            source_rating_keys = [s["source_id"] for s in shows_in_group]

            file_moves: list[FileMove] = []
            warnings: list[str] = []
            dest_filenames: dict[str, str] = {}

            for show_info in shows_in_group:
                season_num = show_info["season_order"]
                src_folder = show_info["local_path"]
                si = show_by_anilist.get(show_info["anilist_id"])

                # Render season folder name via template
                season_name = (
                    _resolve_display_title(si, self._title_pref)
                    if si
                    else show_info["title"]
                )
                season_tokens = {
                    "season": f"{season_num:02d}",
                    "season.name": NamingTemplate.sanitize(season_name),
                }
                season_folder_name = self._season_tmpl.render(season_tokens)
                if not season_folder_name:
                    season_folder_name = f"Season {season_num}"
                season_dir = os.path.join(target_folder, season_folder_name)

                if not os.path.isdir(src_folder):
                    warnings.append(f"Source folder not found: {src_folder}")
                    continue

                try:
                    files = sorted(os.listdir(src_folder))
                except OSError:
                    warnings.append(f"Cannot read folder: {src_folder}")
                    continue

                for filename in files:
                    full_src = os.path.join(src_folder, filename)
                    if not os.path.isfile(full_src):
                        continue

                    _name, ext = os.path.splitext(filename)
                    ext_lower = ext.lower()

                    if ext_lower not in _MEDIA_EXTS:
                        renamed = filename
                    elif si:
                        ep_num = _extract_episode_number(filename)
                        if ep_num is not None:
                            tokens = _build_file_tokens(
                                si,
                                season_num,
                                ep_num,
                                filename,
                                self._title_pref,
                            )
                            renamed = (
                                NamingTemplate.sanitize(self._file_tmpl.render(tokens))
                                + ext
                            )
                        else:
                            renamed = rename_episode_file(filename, season_num)
                            if renamed == filename:
                                warnings.append(f"No episode pattern in: {filename}")
                    else:
                        renamed = rename_episode_file(filename, season_num)
                        if renamed == filename and not _SEASON_EP.search(filename):
                            warnings.append(f"No S01Exx pattern in: {filename}")

                    dest_path = os.path.join(season_dir, renamed)

                    dest_key = dest_path.lower()
                    if dest_key in dest_filenames:
                        warnings.append(
                            f"Filename collision: {renamed} in {season_folder_name}"
                        )
                    dest_filenames[dest_key] = full_src

                    file_moves.append(
                        FileMove(
                            source=full_src,
                            destination=dest_path,
                            original_filename=filename,
                            renamed_filename=renamed,
                        )
                    )

            if not file_moves:
                continue

            plan.groups.append(
                RestructureGroup(
                    series_group_id=group_id,
                    display_title=display_title,
                    target_folder=target_folder,
                    source_folders=source_folders,
                    file_moves=file_moves,
                    season_count=len(shows_in_group),
                    warnings=warnings,
                    source_rating_keys=source_rating_keys,
                    group_key=str(group_id),
                )
            )

    async def _analyze_rename(
        self,
        shows: list[ShowInput],
        plan: RestructurePlan,
        progress: RestructureProgress,
        level: str,
    ) -> None:
        """Levels 1/2: Per-show folder rename (and optionally file rename)."""
        progress.phase = "Analyzing shows for renaming"

        for si in shows:
            progress.current_item = si.title
            progress.processed += 1

            if not si.anilist_id or not si.local_path:
                continue

            # Determine the target title (AniList title)
            anilist_title = si.anilist_title or si.title

            # Render folder name via template
            folder_tokens = _build_folder_tokens(si, self._title_pref)
            rendered_folder = NamingTemplate.sanitize(
                self._folder_tmpl.render(folder_tokens)
            )
            if not rendered_folder:
                rendered_folder = re.sub(r'[<>:"/\\|?*]', "", anilist_title).strip()
            current_folder = os.path.basename(si.local_path)

            # Determine season number for file renaming
            season_num = 1
            try:
                group_id, entries = await self._group_builder.get_or_build_group(
                    si.anilist_id
                )
                if group_id and entries:
                    for entry in entries:
                        if entry["anilist_id"] == si.anilist_id:
                            season_num = entry["season_order"]
                            break
            except Exception:
                pass  # season_num stays 1

            # Skip if folder already matches target
            folder_needs_rename = current_folder != rendered_folder

            # Build file renames for Level 2
            file_moves: list[FileMove] = []
            warnings: list[str] = []

            if level == "folder_file_rename" and os.path.isdir(si.local_path):
                try:
                    files = sorted(os.listdir(si.local_path))
                except OSError:
                    warnings.append(f"Cannot read folder: {si.local_path}")
                    files = []

                for filename in files:
                    full_src = os.path.join(si.local_path, filename)
                    if not os.path.isfile(full_src):
                        continue

                    _name, ext = os.path.splitext(filename)
                    if ext.lower() not in _MEDIA_EXTS:
                        continue

                    ep_num = _extract_episode_number(filename)
                    if ep_num is None:
                        continue

                    tokens = _build_file_tokens(
                        si, season_num, ep_num, filename, self._title_pref
                    )
                    renamed = self._file_tmpl.render(tokens) + ext
                    renamed = NamingTemplate.sanitize(renamed)

                    if renamed == filename:
                        continue  # No change needed

                    # Destination is in the target folder (may be renamed)
                    target_dir = (
                        os.path.join(os.path.dirname(si.local_path), rendered_folder)
                        if folder_needs_rename
                        else si.local_path
                    )
                    file_moves.append(
                        FileMove(
                            source=full_src,
                            destination=os.path.join(target_dir, renamed),
                            original_filename=filename,
                            renamed_filename=renamed,
                        )
                    )

            # Skip if nothing to do
            if not folder_needs_rename and not file_moves:
                continue

            parent_dir = os.path.dirname(si.local_path)
            target_folder = os.path.join(parent_dir, rendered_folder)
            op_type = "rename_file" if file_moves else "rename_folder"

            plan.groups.append(
                RestructureGroup(
                    series_group_id=0,
                    display_title=anilist_title,
                    target_folder=target_folder,
                    source_folders=[si.local_path],
                    file_moves=file_moves,
                    season_count=1,
                    warnings=warnings,
                    source_rating_keys=[si.source_id],
                    operation_type=op_type,
                    current_folder=current_folder,
                    group_key=si.source_id,
                )
            )

    async def execute(
        self,
        plan: RestructurePlan,
        progress: RestructureProgress,
    ) -> dict[str, int]:
        """Execute the restructure plan — move/rename files."""
        if plan.operation_level == "full_restructure":
            return await self._execute_full_restructure(plan, progress)
        return await self._execute_rename(plan, progress)

    async def _execute_full_restructure(
        self,
        plan: RestructurePlan,
        progress: RestructureProgress,
    ) -> dict[str, int]:
        """Level 3: Move files into multi-season structure."""
        progress.status = "executing"
        progress.phase = "Moving files"
        progress.started_at = time.monotonic()
        progress.processed = 0
        progress.total = sum(len(g.file_moves) for g in plan.groups if g.enabled)

        stats = {"groups": 0, "files_moved": 0, "errors": 0}

        for group in plan.groups:
            if not group.enabled:
                continue

            progress.current_item = group.display_title

            # Season dirs are already encoded in file_move destinations;
            # makedirs below handles creation per-file. No pre-creation needed.

            for fm in group.file_moves:
                try:
                    if not os.path.isfile(fm.source):
                        raise FileNotFoundError(f"Source file missing: {fm.source}")

                    dest_dir = os.path.dirname(fm.destination)
                    os.makedirs(dest_dir, exist_ok=True)
                    shutil.move(fm.source, fm.destination)

                    await self._db.log_restructure_operation(
                        group_title=group.display_title,
                        source_path=fm.source,
                        destination_path=fm.destination,
                        operation="move",
                        status="success",
                    )
                    stats["files_moved"] += 1
                except Exception as exc:
                    logger.error("Failed to move %s: %s", fm.source, exc)
                    await self._db.log_restructure_operation(
                        group_title=group.display_title,
                        source_path=fm.source,
                        destination_path=fm.destination,
                        operation="move",
                        status="error",
                        error_message=str(exc),
                    )
                    stats["errors"] += 1

                progress.processed += 1

            # Clean up source folders after all media has been moved out.
            # Use shutil.rmtree for non-empty remnants (metadata dirs, thumbs)
            # but skip the target folder itself if a source happens to overlap.
            for src_folder in group.source_folders:
                try:
                    if not os.path.isdir(src_folder):
                        continue
                    # Never delete the target folder (source may overlap)
                    if os.path.realpath(src_folder) == os.path.realpath(
                        group.target_folder
                    ):
                        continue
                    shutil.rmtree(src_folder)
                    logger.info("Removed source folder: %s", src_folder)
                except OSError as exc:
                    logger.warning(
                        "Could not remove source folder %s: %s", src_folder, exc
                    )

            stats["groups"] += 1

        return stats

    async def _execute_rename(
        self,
        plan: RestructurePlan,
        progress: RestructureProgress,
    ) -> dict[str, int]:
        """Levels 1/2: Rename folders and optionally files."""
        progress.status = "executing"
        progress.phase = "Renaming"
        progress.started_at = time.monotonic()
        progress.processed = 0

        enabled_groups = [g for g in plan.groups if g.enabled]
        progress.total = len(enabled_groups) + sum(
            len(g.file_moves) for g in enabled_groups
        )

        stats = {"groups": 0, "files_moved": 0, "errors": 0}

        for group in enabled_groups:
            progress.current_item = group.display_title
            src_folder = group.source_folders[0]
            target_folder = group.target_folder
            folder_renamed = False

            # Rename folder if needed
            if src_folder != target_folder:
                try:
                    os.rename(src_folder, target_folder)
                    folder_renamed = True
                    await self._db.log_restructure_operation(
                        group_title=group.display_title,
                        source_path=src_folder,
                        destination_path=target_folder,
                        operation="folder_rename",
                        status="success",
                    )
                except Exception as exc:
                    logger.error("Failed to rename folder %s: %s", src_folder, exc)
                    await self._db.log_restructure_operation(
                        group_title=group.display_title,
                        source_path=src_folder,
                        destination_path=target_folder,
                        operation="folder_rename",
                        status="error",
                        error_message=str(exc),
                    )
                    stats["errors"] += 1
                    progress.processed += 1
                    continue  # Skip file renames if folder rename failed

            progress.processed += 1

            # Rename files (Level 2 only — file_moves is empty for Level 1)
            for fm in group.file_moves:
                try:
                    # Adjust source path if folder was just renamed
                    actual_src = (
                        os.path.join(target_folder, fm.original_filename)
                        if folder_renamed
                        else fm.source
                    )
                    if not os.path.isfile(actual_src):
                        raise FileNotFoundError(f"Source file missing: {actual_src}")

                    os.rename(actual_src, fm.destination)
                    await self._db.log_restructure_operation(
                        group_title=group.display_title,
                        source_path=actual_src,
                        destination_path=fm.destination,
                        operation="file_rename",
                        status="success",
                    )
                    stats["files_moved"] += 1
                except Exception as exc:
                    logger.error("Failed to rename %s: %s", fm.source, exc)
                    await self._db.log_restructure_operation(
                        group_title=group.display_title,
                        source_path=fm.source,
                        destination_path=fm.destination,
                        operation="file_rename",
                        status="error",
                        error_message=str(exc),
                    )
                    stats["errors"] += 1

                progress.processed += 1

            stats["groups"] += 1

        return stats
