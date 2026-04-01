"""Library restructuring: consolidate Structure A folders into Structure B."""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from src.Database.Connection import DatabaseManager
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Utils.NamingTemplate import (
    DEFAULT_FILE_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    DEFAULT_ILLEGAL_CHAR_REPLACEMENT,
    DEFAULT_MOVIE_FILE_TEMPLATE,
    DEFAULT_SEASON_FOLDER_TEMPLATE,
    FORMAT_SHORT,
    NamingTemplate,
    parse_quality,
)

logger = logging.getLogger(__name__)

_SEASON_EP = re.compile(r"(?i)(S)\d{2}(E\d+(?:\.\d+)?)")
_SEASON_DIR_RE = re.compile(r"(?i)^season\s+\d+$")
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
# Formats that count as numbered seasons; OVA/ONA/SPECIAL/MOVIE go to Specials
_TV_FORMATS = {"TV", "TV_SHORT"}


def _find_video_subdirs(root: str) -> list[str]:
    """Return subdirectories of *root* that directly contain at least one video file.

    Used to detect Structure B roots (named season subfolders) during library
    indexing without a restructure.
    """
    result = []
    try:
        for name in os.listdir(root):
            subdir = os.path.join(root, name)
            if not os.path.isdir(subdir):
                continue
            try:
                if any(
                    os.path.splitext(f)[1].lower() in _VIDEO_EXTS
                    for f in os.listdir(subdir)
                ):
                    result.append(subdir)
            except OSError:
                pass
    except OSError:
        pass
    return result


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
    anilist_format: str = ""  # AniList format (TV, MOVIE, OVA, etc.)
    anilist_episodes: int | None = None  # Episode count from AniList


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
    anilist_id: int = 0  # AniList ID for this group (used to pre-seed library_items)


@dataclass
class RestructurePlan:
    groups: list[RestructureGroup]
    total_files: int = 0
    total_groups: int = 0
    operation_level: str = (
        "full_restructure"  # "folder_rename", "folder_file_rename", "full_restructure"
    )
    # Shows that were scanned and matched but don't need restructuring
    # (folder already matches target).  Stored so library seeding can
    # include them without a separate AniList search.
    unchanged_shows: list["ShowInput"] = field(default_factory=list)
    # Maps anilist_id → series_group_id for standalone/unchanged shows
    # so seed_library_items can detect Structure B subdirs.
    unchanged_group_ids: dict[int, int] = field(default_factory=dict)


@dataclass
class RestructureProgress:
    status: str = "pending"
    phase: str = ""
    processed: int = 0
    total: int = 0
    current_item: str = ""
    error_message: str = ""
    started_at: float = 0.0


# Primary pattern: SxxExx — tried first to avoid false matches from numeric titles
_EP_PRIMARY = re.compile(r"S(\d{1,2})E(\d{1,3}(?:\.\d(?!\d))?)")
# Fallback patterns: Episode keyword, " - NN", bare number
_EP_FALLBACK = re.compile(
    r"[Ee](?:pisode)?\.?\s*(\d{1,3}(?:\.\d(?!\d))?)"
    r"|\s-\s(\d{2,3}(?:\.\d(?!\d))?)(?:\s|$|\[)"
    r"|(?:^|[\s_\]])(\d{2,3})(?:v\d)?(?:[\s_.\[(\-]|$)"
)

_VARIANT_RE = re.compile(
    r"\b(Director'?s?\s*Cut|Extended|Uncut)\b",
    re.IGNORECASE,
)

# Detect season from title text like "2nd Season", "Season 2", "Part 2", "Cour 2"
_TITLE_SEASON_RE = re.compile(
    r"(?:(\d+)(?:st|nd|rd|th)\s+Season"
    r"|Season\s+(\d+)"
    r"|Part\s+(\d+)"
    r"|Cour\s+(\d+))",
    re.IGNORECASE,
)


@dataclass
class EpisodeInfo:
    """Parsed episode information from a filename."""

    number: str  # Episode number as string ("01", "20.5")
    source_season: int | None = (
        None  # Season from SxxExx (0 for specials), None otherwise
    )
    variant: str = ""  # "Director's Cut", etc.


def _extract_episode_info(filename: str) -> EpisodeInfo | None:
    """Extract episode info from a filename. Returns None if no pattern found.

    Tries SxxExx first (anywhere in the filename) to avoid false positives
    from numeric titles like "86 Eighty Six" or "91 Days".
    """
    source_season: int | None = None

    # Try the explicit SxxExx pattern first — most reliable
    m_primary = _EP_PRIMARY.search(filename)
    if m_primary:
        source_season = int(m_primary.group(1))
        ep_str = m_primary.group(2)
    else:
        # Fall back to looser patterns only when SxxExx is absent
        match = _EP_FALLBACK.search(filename)
        if not match:
            return None
        if match.group(1) is not None:
            ep_str = match.group(1)
        elif match.group(2) is not None:
            ep_str = match.group(2)
        elif match.group(3) is not None:
            ep_str = match.group(3)
        else:
            return None

    # If no season from SxxExx, check for title-based season indicators
    # like "2nd Season", "Season 2", "Part 2"
    if source_season is None:
        tsm = _TITLE_SEASON_RE.search(filename)
        if tsm:
            source_season = int(
                tsm.group(1) or tsm.group(2) or tsm.group(3) or tsm.group(4)
            )

    variant = ""
    vm = _VARIANT_RE.search(filename)
    if vm:
        variant = vm.group(1).strip()

    return EpisodeInfo(number=ep_str, source_season=source_season, variant=variant)


def _format_episode_number(ep_str: str) -> str:
    """Zero-pad episode number, preserving decimals. '5' → '05', '20.5' → '20.5'."""
    if "." in ep_str:
        int_part, dec_part = ep_str.split(".", 1)
        return f"{int(int_part):02d}.{dec_part}"
    return f"{int(ep_str):02d}"


def standardize_episode_filename(
    filename: str, show_title: str, season_num: int
) -> str:
    """Rename episode file to 'Show Title - S01E01.ext' format.

    Returns original filename if no episode pattern found or not a media file.
    """
    _name, ext = os.path.splitext(filename)
    if ext.lower() not in _MEDIA_EXTS:
        return filename

    ep_info = _extract_episode_info(filename)
    if ep_info is None:
        return filename

    safe_title = re.sub(r'[<>:"/\\|?*]', "", show_title).strip()
    ep_fmt = _format_episode_number(ep_info.number)
    actual_season = 0 if ep_info.source_season == 0 else season_num
    result = f"{safe_title} - S{actual_season:02d}E{ep_fmt}{ext}"
    if ep_info.variant:
        base, fext = os.path.splitext(result)
        result = f"{base} ({ep_info.variant}){fext}"
    return result


def _match_subdir_to_entry(
    subdir_name: str,
    entries: list[dict],
    *,
    consume: bool = False,
) -> dict | None:
    """Find the series group entry whose display_title best matches *subdir_name*.

    Uses a simple longest-common-substring ratio so it works without
    rapidfuzz (which may not be installed).  Returns None if no entry
    scores above a minimal threshold.

    When *consume* is True the matched entry is **removed** from *entries*
    so that subsequent calls cannot re-match the same entry to a different
    subdir.  This prevents two similarly-named subdirs from collapsing onto
    the same anilist_id.
    """
    # Strip year suffix like "(2009)" for comparison
    clean = re.sub(r"\s*\(\d{4}\)\s*$", "", subdir_name).strip().lower()
    if not clean:
        if entries:
            entry = entries[0]
            if consume:
                entries.remove(entry)
            return entry
        return None

    best_entry: dict | None = None
    best_score = 0.0
    for entry in entries:
        title = (entry.get("display_title") or "").lower()
        if not title:
            continue
        # Simple ratio: 2 * matching_chars / total_chars
        # Use SequenceMatcher for a quick approximation
        score = SequenceMatcher(None, clean, title).ratio()
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score > 0.4:
        if consume:
            entries.remove(best_entry)
        return best_entry
    return None


def _build_file_tokens(
    show: ShowInput,
    season_num: int,
    ep_info: EpisodeInfo | None,
    filename: str,
    title_pref: str = "romaji",
    replacement: str = "",
) -> dict[str, str]:
    """Build the token dict for file template rendering."""
    title = _resolve_display_title(show, title_pref)
    quality = parse_quality(filename)
    fmt = show.anilist_format or ""
    return {
        "title": NamingTemplate.sanitize(title, replacement),
        "title.romaji": NamingTemplate.sanitize(
            show.anilist_title_romaji or title, replacement
        ),
        "title.english": NamingTemplate.sanitize(
            show.anilist_title_english or title, replacement
        ),
        "year": str(show.year) if show.year else "",
        "season": f"{season_num:02d}",
        "episode": _format_episode_number(ep_info.number) if ep_info else "",
        "episode.title": "",  # future data source
        "quality": quality.full,
        "quality.resolution": quality.resolution,
        "quality.source": quality.source,
        "format": fmt,
        "format.short": FORMAT_SHORT.get(fmt, fmt),
    }


def _build_folder_tokens(
    show: ShowInput,
    title_pref: str = "romaji",
    replacement: str = "",
) -> dict[str, str]:
    """Build the token dict for folder template rendering."""
    title = _resolve_display_title(show, title_pref)
    fmt = show.anilist_format or ""
    return {
        "title": NamingTemplate.sanitize(title, replacement),
        "title.romaji": NamingTemplate.sanitize(
            show.anilist_title_romaji or title, replacement
        ),
        "title.english": NamingTemplate.sanitize(
            show.anilist_title_english or title, replacement
        ),
        "year": str(show.year) if show.year else "",
        "format": fmt,
        "format.short": FORMAT_SHORT.get(fmt, fmt),
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


def _log_analysis_summary(
    plan: "RestructurePlan",
    skipped: list[tuple[str, str]],
    level: str,
) -> None:
    """Log a detailed analysis summary for debugging."""
    total_files = sum(len(g.file_moves) for g in plan.groups)
    lines = [f"=== Restructure Analysis Summary (level={level}) ==="]
    lines.append(f"Groups: {len(plan.groups)}  |  Total files: {total_files}")

    for g in plan.groups:
        op = getattr(g, "operation_type", "group")
        warn_str = f"  WARNINGS: {g.warnings}" if g.warnings else ""
        src_info = ""
        if g.source_folders:
            src_info = f"  src={os.path.basename(g.source_folders[0])}"
        lines.append(
            f"  [{op}] {g.display_title}"
            f"  ({len(g.file_moves)} files){src_info}{warn_str}"
        )
        if g.target_folder:
            lines.append(f"    -> {g.target_folder}")
        for fm in g.file_moves:
            if fm.original_filename != fm.renamed_filename:
                lines.append(f"    {fm.original_filename} -> {fm.renamed_filename}")
            else:
                lines.append(f"    {fm.original_filename} (unchanged)")

    if skipped:
        lines.append(f"Skipped: {len(skipped)}")
        for title, reason in skipped:
            lines.append(f"  - {title}: {reason}")

    logger.info("\n".join(lines))


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
        movie_file_template: str = "",
        title_pref: str = "romaji",
        illegal_char_replacement: str = "",
    ) -> None:
        self._db = db
        self._group_builder = group_builder
        self._file_tmpl = NamingTemplate(file_template or DEFAULT_FILE_TEMPLATE)
        self._folder_tmpl = NamingTemplate(folder_template or DEFAULT_FOLDER_TEMPLATE)
        self._season_tmpl = NamingTemplate(
            season_folder_template or DEFAULT_SEASON_FOLDER_TEMPLATE
        )
        self._movie_file_tmpl = NamingTemplate(
            movie_file_template or DEFAULT_MOVIE_FILE_TEMPLATE
        )
        self._title_pref = title_pref
        self._repl = illegal_char_replacement or DEFAULT_ILLEGAL_CHAR_REPLACEMENT

    def _san(self, text: str) -> str:
        """Sanitize text using the configured illegal character replacement."""
        return NamingTemplate.sanitize(text, self._repl)

    @staticmethod
    def _is_single_item_entry(format_str: str, video_file_count: int) -> bool:
        """Return True if entry should use movie-style naming (no SxxExx)."""
        if format_str == "MOVIE":
            return True
        if format_str in ("OVA", "SPECIAL", "ONA") and video_file_count <= 1:
            return True
        return False

    async def analyze(
        self,
        shows: list[ShowInput],
        progress: RestructureProgress,
        level: str = "full_restructure",
        output_dir: str | None = None,
    ) -> RestructurePlan:
        """Analyze shows and build a restructure plan.

        Args:
            shows: Pre-gathered show inputs from a provider.
            progress: Mutable progress tracker.
            level: One of "folder_rename", "folder_file_rename", "full_restructure".
            output_dir: If provided, all target folders are placed under this
                directory instead of alongside their source folders.
        """
        progress.status = "analyzing"
        progress.phase = "Analyzing shows"
        progress.started_at = time.monotonic()
        progress.total = len(shows)
        progress.processed = 0

        plan = RestructurePlan(groups=[], operation_level=level)

        if level == "full_restructure":
            await self._analyze_full_restructure(shows, plan, progress, output_dir)
        else:
            await self._analyze_rename(shows, plan, progress, level, output_dir)

        plan.total_files = sum(len(g.file_moves) for g in plan.groups)
        plan.total_groups = len(plan.groups)

        progress.status = "complete"
        progress.phase = "Analysis complete"
        return plan

    @staticmethod
    def detect_conflicts(plan: "RestructurePlan") -> list[dict]:
        """Return conflicts where a planned destination already exists on disk.

        Each entry: {"group": str, "group_key": str, "source": str,
                     "destination": str, "conflict_type": "exists"}
        """
        conflicts: list[dict] = []
        for group in plan.groups:
            for fm in group.file_moves:
                if os.path.exists(fm.destination) and os.path.realpath(
                    fm.source
                ) != os.path.realpath(fm.destination):
                    conflicts.append(
                        {
                            "group": group.display_title,
                            "group_key": group.group_key,
                            "source": fm.source,
                            "destination": fm.destination,
                            "conflict_type": "exists",
                        }
                    )
        return conflicts

    async def _analyze_full_restructure(
        self,
        shows: list[ShowInput],
        plan: RestructurePlan,
        progress: RestructureProgress,
        output_dir: str | None = None,
    ) -> None:
        """Level 3: Group by series group, plan file moves into multi-season folder.

        Also handles standalone entries (single-season shows, movies, OVAs).
        """
        from typing import Any

        group_shows: dict[int, list[dict[str, Any]]] = {}
        # Keep a mapping from anilist_id to ShowInput for template token building
        show_by_anilist: dict[int, ShowInput] = {}
        # Standalone shows: no group or single-entry group
        standalone_shows: list[ShowInput] = []
        # Track which series group (if any) each anilist_id belongs to, so
        # standalone shows can still carry their group_id for library seeding.
        standalone_group_id: dict[int, int] = {}
        skipped: list[tuple[str, str]] = []  # (title, reason)

        for si in shows:
            progress.current_item = si.title
            progress.processed += 1

            if not si.anilist_id or not si.local_path:
                skipped.append((si.title, "no AniList match or no local path"))
                continue

            show_by_anilist[si.anilist_id] = si

            try:
                group_id, entries = await self._group_builder.get_or_build_group(
                    si.anilist_id
                )
            except Exception:
                skipped.append((si.title, "group build failed"))
                continue

            if not group_id or len(entries) < 2:
                # Standalone entry — process separately
                standalone_shows.append(si)
                continue

            if group_id not in group_shows:
                group_shows[group_id] = []
            # Remember the group for this anilist_id (used if demoted to standalone)
            standalone_group_id[si.anilist_id] = group_id

            season_order = 1
            tv_season_order = 1
            entry_format = ""
            entry_episodes: int | None = None
            tv_count = 0
            for entry in entries:
                fmt = entry.get("format") or ""
                if fmt in _TV_FORMATS:
                    tv_count += 1
                if entry["anilist_id"] == si.anilist_id:
                    season_order = entry["season_order"]
                    entry_format = fmt
                    entry_episodes = entry.get("episodes")
                    # TV entries get a TV-only season number; OVA/ONA/SPECIAL/MOVIE
                    # entries get 0 so the restructurer routes them to Specials.
                    tv_season_order = tv_count if fmt in _TV_FORMATS else 0
                    break

            group_shows[group_id].append(
                {
                    "title": si.title,
                    "local_path": si.local_path,
                    "anilist_id": si.anilist_id,
                    "season_order": season_order,
                    "tv_season_order": tv_season_order,
                    "source_id": si.source_id,
                    "format": entry_format,
                    "episodes": entry_episodes,
                }
            )

        progress.phase = "Building restructure plan"

        # --- Process multi-entry groups ---
        for group_id, shows_in_group in group_shows.items():
            if len(shows_in_group) < 2:
                # Single show in a multi-entry group — treat as standalone but
                # remember its group so seed_library_items can expand all seasons.
                aid = shows_in_group[0]["anilist_id"]
                found_show = show_by_anilist.get(aid)
                if found_show:
                    standalone_shows.append(found_show)
                    standalone_group_id[aid] = group_id
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
            parent_dir = output_dir if output_dir else os.path.dirname(first_path)

            root_anilist_id = group_info["root_anilist_id"] if group_info else 0
            root_cache = (
                await self._db.get_cached_metadata(root_anilist_id)
                if root_anilist_id
                else None
            )

            # Start with display_title (always the root's title) as baseline
            safe_display = self._san(display_title)
            folder_tokens: dict[str, str] = {
                "title": safe_display,
                "title.romaji": safe_display,
                "title.english": safe_display,
                "year": "",
                "format": "",
                "format.short": "",
            }

            if root_cache:
                # Rich data available from anilist_cache
                romaji = root_cache.get("title_romaji", "")
                english = root_cache.get("title_english", "")
                if romaji:
                    folder_tokens["title.romaji"] = self._san(romaji)
                if english:
                    folder_tokens["title.english"] = self._san(english)
                if self._title_pref == "english" and english:
                    folder_tokens["title"] = self._san(english)
                elif romaji:
                    folder_tokens["title"] = self._san(romaji)
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

            rendered_folder = self._san(self._folder_tmpl.render(folder_tokens))
            if not rendered_folder:
                rendered_folder = re.sub(r'[<>:"/\\|?*]', "", display_title).strip()

            target_folder = os.path.join(parent_dir, rendered_folder)

            source_folders = [s["local_path"] for s in shows_in_group]
            source_rating_keys = [s["source_id"] for s in shows_in_group]

            file_moves: list[FileMove] = []
            warnings: list[str] = []
            dest_filenames: dict[str, str] = {}

            for show_info in shows_in_group:
                # Use TV-only season number for folder naming so that OVA/ONA/
                # SPECIAL/MOVIE entries don't shift the numbered TV seasons.
                # tv_season_order == 0 means the entry is not a TV season; its
                # files are routed to the Specials folder.
                season_num = show_info["tv_season_order"]
                src_folder = show_info["local_path"]
                group_si = show_by_anilist.get(show_info["anilist_id"])

                # Render season folder name via template
                season_name = (
                    _resolve_display_title(group_si, self._title_pref)
                    if group_si
                    else show_info["title"]
                )
                season_year = str(group_si.year) if group_si and group_si.year else ""
                if season_num == 0:
                    # Non-TV entry (OVA, ONA, SPECIAL, MOVIE) — use the entry's
                    # own AniList title as {season.name} so that e.g.
                    # "Jujutsu Kaisen 0 (2021)" gets its own named folder
                    # rather than being lumped into a generic "Specials" folder.
                    sp_tokens = {
                        "season": "00",
                        "season.name": self._san(season_name),
                        "year": season_year,
                    }
                    season_folder_name = (
                        self._season_tmpl.render(sp_tokens)
                        or self._san(season_name)
                        or "Specials"
                    )
                else:
                    season_tokens = {
                        "season": f"{season_num:02d}",
                        "season.name": self._san(season_name),
                        "year": season_year,
                    }
                    season_folder_name = self._season_tmpl.render(season_tokens)
                    if not season_folder_name:
                        season_folder_name = f"Season {season_num}"
                season_dir = os.path.join(target_folder, season_folder_name)

                if not os.path.isdir(src_folder):
                    warnings.append(f"Source folder not found: {src_folder}")
                    continue

                try:
                    top_files = sorted(os.listdir(src_folder))
                except OSError:
                    warnings.append(f"Cannot read folder: {src_folder}")
                    continue

                # Collect files — may be directly in folder, inside season
                # subdirectories (e.g. "Season 1/"), or inside a same-named
                # nested folder (already-restructured layout).
                src_files: list[tuple[str, str, int | None]] = []
                for fs_entry in top_files:
                    entry_path = os.path.join(src_folder, fs_entry)
                    if os.path.isfile(entry_path):
                        src_files.append((entry_path, fs_entry, None))
                    elif os.path.isdir(entry_path):
                        if _SEASON_DIR_RE.match(fs_entry):
                            dir_sn = int(fs_entry.split()[-1])
                        else:
                            dir_sn = None
                        try:
                            sub = sorted(os.listdir(entry_path))
                        except OSError:
                            continue
                        for sf in sub:
                            sfull = os.path.join(entry_path, sf)
                            if os.path.isfile(sfull):
                                src_files.append((sfull, sf, dir_sn))

                # Count video files for single-item detection
                video_files = [
                    fname
                    for _fpath, fname, _dsn in src_files
                    if os.path.splitext(fname)[1].lower() in _VIDEO_EXTS
                ]
                entry_format = show_info.get("format", "") or ""
                use_movie_naming = self._is_single_item_entry(
                    entry_format, len(video_files)
                )

                for full_src, filename, file_dir_sn in src_files:

                    _name, ext = os.path.splitext(filename)
                    ext_lower = ext.lower()

                    if ext_lower not in _MEDIA_EXTS:
                        # Other non-media files (e.g. extras) — move as-is
                        renamed = filename
                        file_dest_dir = season_dir
                    elif group_si and use_movie_naming:
                        # Movies / single-item entries in series groups:
                        # use movie-style naming (no SxxExx).
                        tokens = _build_file_tokens(
                            group_si,
                            season_num,
                            None,
                            filename,
                            self._title_pref,
                            self._repl,
                        )
                        renamed = self._san(self._movie_file_tmpl.render(tokens)) + ext
                        file_dest_dir = season_dir
                    elif group_si:
                        ep_info = _extract_episode_info(filename)
                        if ep_info is not None:
                            # S00 specials go to a Specials subfolder
                            if ep_info.source_season == 0:
                                file_season = 0
                                sp_tokens = {
                                    "season": "00",
                                    "season.name": "Specials",
                                    "year": str(group_si.year) if group_si.year else "",
                                }
                                sp_folder = (
                                    self._season_tmpl.render(sp_tokens) or "Specials"
                                )
                                file_dest_dir = os.path.join(target_folder, sp_folder)
                            else:
                                # If the file came from a named season
                                # subdirectory (e.g. "Season 2/") within
                                # this source folder, use that directory's
                                # season number instead of the folder's
                                # group season_order.  This handles the case
                                # where a single Plex folder (matched to S1)
                                # contains Season 2/ and Season 3/ subfolders
                                # with content from later series entries.
                                effective_season = (
                                    file_dir_sn
                                    if file_dir_sn is not None
                                    else season_num
                                )
                                file_season = effective_season
                                if effective_season == season_num:
                                    file_dest_dir = season_dir
                                else:
                                    alt_tokens = {
                                        "season": f"{effective_season:02d}",
                                        "season.name": self._san(season_name),
                                        "year": season_year,
                                    }
                                    alt_folder = (
                                        self._season_tmpl.render(alt_tokens)
                                        or f"Season {effective_season:02d}"
                                    )
                                    file_dest_dir = os.path.join(
                                        target_folder, alt_folder
                                    )
                            tokens = _build_file_tokens(
                                group_si,
                                file_season,
                                ep_info,
                                filename,
                                self._title_pref,
                                self._repl,
                            )
                            rendered = self._san(self._file_tmpl.render(tokens))
                            if ep_info.variant:
                                rendered += f" ({ep_info.variant})"
                            renamed = rendered + ext
                        else:
                            renamed = rename_episode_file(filename, season_num)
                            if renamed == filename:
                                warnings.append(f"No episode pattern in: {filename}")
                            file_dest_dir = season_dir
                    else:
                        renamed = rename_episode_file(filename, season_num)
                        if renamed == filename and not _SEASON_EP.search(filename):
                            warnings.append(f"No S01Exx pattern in: {filename}")
                        file_dest_dir = season_dir

                    dest_path = os.path.join(file_dest_dir, renamed)

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
                # Track the shows so they can still be seeded into library
                for si_info in shows_in_group:
                    si_obj = show_by_anilist.get(si_info["anilist_id"])
                    if si_obj:
                        plan.unchanged_shows.append(si_obj)
                        plan.unchanged_group_ids[si_obj.anilist_id] = group_id
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
                    anilist_id=root_anilist_id,
                )
            )

        # --- Process standalone entries (single-season shows, movies, OVAs) ---
        for si in standalone_shows:
            folder_tokens = _build_folder_tokens(si, self._title_pref, self._repl)
            rendered_folder = self._san(self._folder_tmpl.render(folder_tokens))
            if not rendered_folder:
                rendered_folder = re.sub(
                    r'[<>:"/\\|?*]', "", si.anilist_title or si.title
                ).strip()

            parent_dir = output_dir if output_dir else os.path.dirname(si.local_path)
            target_folder = os.path.join(parent_dir, rendered_folder)

            if not os.path.isdir(si.local_path):
                skipped.append((si.title, "folder not found on disk"))
                continue

            try:
                top_entries = sorted(os.listdir(si.local_path))
            except OSError:
                skipped.append((si.title, "cannot read folder"))
                continue

            # Collect all source files — may be directly in the folder, inside
            # season subdirectories (e.g. "Season 1/"), or inside a same-named
            # nested folder (already-restructured layout).
            # Third tuple element is the directory season number (None = top-level).
            source_files: list[tuple[str, str, int | None]] = []
            for fs_entry in top_entries:
                full_path = os.path.join(si.local_path, fs_entry)
                if os.path.isfile(full_path):
                    source_files.append((full_path, fs_entry, None))
                elif os.path.isdir(full_path):
                    if _SEASON_DIR_RE.match(fs_entry):
                        dir_season = int(fs_entry.split()[-1])
                    else:
                        dir_season = None
                    try:
                        sub_files = sorted(os.listdir(full_path))
                    except OSError:
                        continue
                    for sf in sub_files:
                        sfull = os.path.join(full_path, sf)
                        if os.path.isfile(sfull):
                            source_files.append((sfull, sf, dir_season))

            video_files = [
                fname
                for _fpath, fname, _dsn in source_files
                if os.path.splitext(fname)[1].lower() in _VIDEO_EXTS
            ]
            fmt = si.anilist_format or ""
            use_movie_naming = self._is_single_item_entry(fmt, len(video_files))

            # Detect multi-season structure from EITHER Season N/ subdirectories
            # OR distinct SxxExx patterns in filenames (e.g. S01E01, S02E01).
            # Build a map from season number → series group entry so that each
            # season gets the correct AniList title/year in folders and filenames.
            _sg_dir_set = {dsn for _, _, dsn in source_files if dsn is not None}
            _sxxexx_seasons: set[int] = set()
            for _fp, _fn, _dsn in source_files:
                if os.path.splitext(_fn)[1].lower() in _VIDEO_EXTS:
                    _ei = _extract_episode_info(_fn)
                    if _ei and _ei.source_season is not None and _ei.source_season > 0:
                        _sxxexx_seasons.add(_ei.source_season)
            _all_distinct_seasons = _sg_dir_set | _sxxexx_seasons
            sg_season_map: dict[int, dict] = {}
            if len(_all_distinct_seasons) > 1 and si.anilist_id:
                try:
                    _, _group_entries = await self._group_builder.get_or_build_group(
                        si.anilist_id
                    )
                    _tv_entries = [
                        e
                        for e in _group_entries
                        if (e.get("format") or "") in _TV_FORMATS
                    ]
                    if len(_tv_entries) > 1:
                        for _sn, _entry in zip(
                            sorted(_all_distinct_seasons), _tv_entries
                        ):
                            sg_season_map[_sn] = _entry
                except Exception as _exc:
                    logger.warning(
                        "Series group lookup failed for %s: %s", si.title, _exc
                    )

            # Cache rendered season dirs for non-movie standalones
            _season_dir_cache: dict[int, str] = {}

            def _get_season_dir(snum: int) -> str:
                if snum in _season_dir_cache:
                    return _season_dir_cache[snum]
                if snum == 0:
                    # Specials — unchanged
                    sp_tokens = {
                        "season": "00",
                        "season.name": "Specials",
                        "year": str(si.year) if si.year else "",
                    }
                    name = self._season_tmpl.render(sp_tokens) or "Specials"
                elif snum in sg_season_map:
                    # Multi-season Sonarr: use the AniList entry for this season dir.
                    # No anti-nesting guard — same-name subfolder is intentional here
                    # (matches the Noragami/Ajin grouped output style).
                    sg = sg_season_map[snum]
                    sg_title = self._san(
                        sg.get("display_title")
                        or _resolve_display_title(si, self._title_pref)
                    )
                    sg_year = (sg.get("start_date") or "")[:4] or (
                        str(si.year) if si.year else ""
                    )
                    s_tokens = {
                        "season": f"{snum:02d}",
                        "season.name": sg_title,
                        "year": sg_year,
                    }
                    name = self._season_tmpl.render(s_tokens) or sg_title
                else:
                    # Single-season standalone: render using the show's AniList title
                    # as {season.name}. A same-named subfolder is intentional here —
                    # single-season shows mirror multi-season behaviour (e.g.
                    # Noragami/ → Noragami/).
                    s_tokens = {
                        "season": f"{snum:02d}",
                        "season.name": self._san(
                            _resolve_display_title(si, self._title_pref)
                        ),
                        "year": str(si.year) if si.year else "",
                    }
                    name = self._season_tmpl.render(s_tokens) or f"Season {snum:02d}"
                d = os.path.join(target_folder, name)
                _season_dir_cache[snum] = d
                return d

            file_moves = []
            standalone_warnings: list[str] = []
            dest_filenames_s: dict[str, str] = {}

            for full_src, filename, dir_season_num in source_files:
                _name, ext = os.path.splitext(filename)
                ext_lower = ext.lower()

                if ext_lower not in _MEDIA_EXTS:
                    renamed = filename
                    file_dest_dir = target_folder
                elif use_movie_naming:
                    # Standalone movies / single-item entries:
                    # use movie-style naming (no SxxExx).
                    tokens = _build_file_tokens(
                        si, 1, None, filename, self._title_pref, self._repl
                    )
                    renamed = self._san(self._movie_file_tmpl.render(tokens)) + ext
                    file_dest_dir = _get_season_dir(1)
                else:
                    ep_info = _extract_episode_info(filename)
                    if ep_info is not None:
                        # Determine season: if file came from a Season X/
                        # subdirectory, use that directory's season number
                        # (overrides the S01 in filename which restarts per
                        # season dir). Otherwise use the file's SxxExx season
                        # or default to S01 for absolute-numbered files.
                        if dir_season_num is not None and (
                            ep_info.source_season is None or ep_info.source_season == 1
                        ):
                            file_season = dir_season_num
                        elif ep_info.source_season is not None:
                            file_season = ep_info.source_season
                        else:
                            file_season = 1
                        file_dest_dir = _get_season_dir(file_season)
                        # For multi-season Sonarr structures, use the AniList
                        # entry for this season so filenames use the right title
                        # and year (e.g. "Code Geass R2 (2008) - S02E01").
                        token_si = si
                        if file_season in sg_season_map:
                            sg = sg_season_map[file_season]
                            sg_year_str = (sg.get("start_date") or "")[:4]
                            token_si = ShowInput(
                                title=sg.get("display_title") or si.title,
                                local_path=si.local_path,
                                source_id=si.source_id,
                                anilist_id=sg.get("anilist_id") or si.anilist_id,
                                anilist_title=sg.get("display_title")
                                or si.anilist_title,
                                year=int(sg_year_str) if sg_year_str else si.year,
                                anilist_title_romaji=sg.get("display_title")
                                or si.anilist_title_romaji,
                                anilist_title_english=si.anilist_title_english,
                                anilist_format=si.anilist_format,
                            )
                        tokens = _build_file_tokens(
                            token_si,
                            file_season,
                            ep_info,
                            filename,
                            self._title_pref,
                            self._repl,
                        )
                        rendered = self._san(self._file_tmpl.render(tokens))
                        if ep_info.variant:
                            rendered += f" ({ep_info.variant})"
                        renamed = rendered + ext
                    else:
                        # No episode pattern found — use movie-style naming
                        # as a fallback (covers movie files bundled inside a
                        # TV series folder, e.g. "Jujutsu Kaisen 0" inside
                        # the "Jujutsu Kaisen" folder).
                        if ext_lower in _MEDIA_EXTS:
                            tokens = _build_file_tokens(
                                si, 0, None, filename, self._title_pref, self._repl
                            )
                            # Use the original filename stem as the title token
                            # so the movie keeps its own name rather than the
                            # parent TV series name.
                            file_stem = os.path.splitext(filename)[0]
                            # Strip quality/codec tags in brackets for a cleaner title
                            clean_stem = re.sub(
                                r"\s*[\[\(][^\]\)]*[\]\)]", "", file_stem
                            ).strip()
                            if clean_stem:
                                san_stem = NamingTemplate.sanitize(
                                    clean_stem, self._repl
                                )
                                tokens["title"] = san_stem
                                tokens["title.romaji"] = san_stem
                                tokens["title.english"] = san_stem
                            # Try to recover the year from the original
                            # filename (e.g. "(2009)") so the movie keeps
                            # its own release year.
                            _yr_m = re.search(r"\((\d{4})\)", file_stem)
                            if _yr_m:
                                tokens["year"] = _yr_m.group(1)
                            renamed = (
                                self._san(self._movie_file_tmpl.render(tokens)) + ext
                            )
                            file_dest_dir = _get_season_dir(0)  # Specials folder
                        else:
                            renamed = filename
                            file_dest_dir = _get_season_dir(1)

                dest_path = os.path.join(file_dest_dir, renamed)

                dest_key = dest_path.lower()
                if dest_key in dest_filenames_s:
                    standalone_warnings.append(f"Filename collision: {renamed}")
                dest_filenames_s[dest_key] = full_src

                file_moves.append(
                    FileMove(
                        source=full_src,
                        destination=dest_path,
                        original_filename=filename,
                        renamed_filename=renamed,
                    )
                )

            # Skip if nothing changes (same folder name and all files unchanged)
            current_folder = os.path.basename(si.local_path)
            folder_changed = current_folder != rendered_folder
            files_changed = any(
                fm.original_filename != fm.renamed_filename for fm in file_moves
            )
            if not folder_changed and not files_changed:
                skipped.append(
                    (si.title, f"already matches target '{rendered_folder}'")
                )
                plan.unchanged_shows.append(si)
                gid = standalone_group_id.get(si.anilist_id, 0)
                if gid:
                    plan.unchanged_group_ids[si.anilist_id] = gid
                continue

            if not file_moves:
                skipped.append((si.title, "no files found in folder"))
                continue

            # Determine operation_type label for the preview
            if sg_season_map:
                op_type = "standalone_multiseries"
            elif fmt == "MOVIE":
                op_type = "standalone_movie"
            elif fmt in ("OVA", "SPECIAL", "ONA"):
                op_type = f"standalone_{fmt.lower()}"
            else:
                op_type = "standalone"

            plan.groups.append(
                RestructureGroup(
                    series_group_id=standalone_group_id.get(si.anilist_id, 0),
                    display_title=si.anilist_title or si.title,
                    target_folder=target_folder,
                    source_folders=[si.local_path],
                    file_moves=file_moves,
                    season_count=1,
                    warnings=standalone_warnings,
                    source_rating_keys=[si.source_id],
                    operation_type=op_type,
                    current_folder=current_folder,
                    group_key=f"standalone_{si.source_id}",
                    anilist_id=si.anilist_id,
                )
            )

        # --- Log analysis summary ---
        _log_analysis_summary(plan, skipped, "full_restructure")

    async def _analyze_rename(
        self,
        shows: list[ShowInput],
        plan: RestructurePlan,
        progress: RestructureProgress,
        level: str,
        output_dir: str | None = None,
    ) -> None:
        """Levels 1/2: Per-show folder rename (and optionally file rename)."""
        progress.phase = "Analyzing shows for renaming"
        skipped: list[tuple[str, str]] = []
        seen_targets: set[str] = set()  # guard against duplicate destinations

        for si in shows:
            progress.current_item = si.title
            progress.processed += 1

            if not si.anilist_id or not si.local_path:
                skipped.append((si.title, "no AniList match or no local path"))
                continue

            # Determine the target title (AniList title)
            anilist_title = si.anilist_title or si.title

            # Render folder name via template
            folder_tokens = _build_folder_tokens(si, self._title_pref, self._repl)
            rendered_folder = self._san(self._folder_tmpl.render(folder_tokens))
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
                # Collect files — may be directly in folder, inside season
                # subdirectories (e.g. "Season 1/"), or inside a same-named
                # nested folder (already-restructured layout).
                rename_files: list[tuple[str, str, str]] = []  # (full, name, subdir)
                try:
                    top_entries = sorted(os.listdir(si.local_path))
                except OSError:
                    warnings.append(f"Cannot read folder: {si.local_path}")
                    top_entries = []

                for fs_entry in top_entries:
                    entry_path = os.path.join(si.local_path, fs_entry)
                    if os.path.isfile(entry_path):
                        rename_files.append((entry_path, fs_entry, ""))
                    elif os.path.isdir(entry_path):
                        try:
                            sub = sorted(os.listdir(entry_path))
                        except OSError:
                            continue
                        for sf in sub:
                            sfull = os.path.join(entry_path, sf)
                            if os.path.isfile(sfull):
                                rename_files.append((sfull, sf, fs_entry))

                for full_src, filename, subdir in rename_files:
                    _name, ext = os.path.splitext(filename)
                    if ext.lower() not in _MEDIA_EXTS:
                        continue

                    ep_info = _extract_episode_info(filename)
                    if ep_info is None:
                        continue

                    file_season = 0 if ep_info.source_season == 0 else season_num
                    tokens = _build_file_tokens(
                        si, file_season, ep_info, filename, self._title_pref, self._repl
                    )
                    rendered = self._file_tmpl.render(tokens)
                    if ep_info.variant:
                        rendered += f" ({ep_info.variant})"
                    renamed = self._san(rendered) + ext

                    if renamed == filename:
                        continue  # No change needed

                    # Destination is in the target folder (may be renamed),
                    # preserving any subdirectory structure
                    target_dir = (
                        os.path.join(os.path.dirname(si.local_path), rendered_folder)
                        if folder_needs_rename
                        else si.local_path
                    )
                    if subdir:
                        target_dir = os.path.join(target_dir, subdir)
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
                skipped.append(
                    (si.title, f"already matches target '{rendered_folder}'")
                )
                plan.unchanged_shows.append(si)
                continue

            parent_dir = output_dir if output_dir else os.path.dirname(si.local_path)
            target_folder = os.path.join(parent_dir, rendered_folder)

            if target_folder in seen_targets:
                skipped.append(
                    (
                        si.title,
                        f"duplicate destination '{rendered_folder}' — "
                        f"another source folder already targets this path",
                    )
                )
                logger.warning(
                    "Skipping duplicate rename target '%s' for source '%s'",
                    target_folder,
                    si.local_path,
                )
                continue
            seen_targets.add(target_folder)

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
                    anilist_id=si.anilist_id,
                )
            )

        _log_analysis_summary(plan, skipped, level)

    async def _cached_metadata_kwargs(self, anilist_id: int) -> dict[str, object]:
        """Return cover_image/year/format/episodes from anilist_cache."""
        cached = await self._db.get_cached_metadata(anilist_id)
        if not cached:
            return {}
        return {
            "cover_image": cached.get("cover_image") or "",
            "year": cached.get("year") or 0,
            "anilist_format": cached.get("format") or "",
            "anilist_episodes": cached.get("episodes"),
        }

    async def seed_library_items(
        self,
        plan: RestructurePlan,
        library_id: int,
        from_source: bool = False,
    ) -> int:
        """Upsert library_items from a restructure plan.

        Args:
            plan: The analyze output (or the plan kept in memory post-execute).
            library_id: Target library to populate.
            from_source: If True, use source_folders (skip/rename path — files
                         haven't moved yet).  If False (default), use
                         target_folder with Season N subdirs (post-execute path).

        Returns the number of rows upserted.
        """
        upserted = 0
        for group in plan.groups:
            if not group.anilist_id:
                continue

            root_name = os.path.basename(group.target_folder.rstrip("/"))

            if from_source:
                # --- Skip / rename path ---
                # source_folders has one entry per scanned top-level folder.
                # For Structure A inputs this is one folder per season;
                # for Structure B inputs it is just the single root folder.
                source_folders = group.source_folders or []
                if not source_folders:
                    continue

                if len(source_folders) == 1:
                    root = source_folders[0]
                    root_basename = os.path.basename(root.rstrip("/"))
                    # Detect Structure B root: 2+ subdirs that each contain
                    # video files directly (named season subfolder layout).
                    season_subdirs = _find_video_subdirs(root)
                    if len(season_subdirs) >= 2 and group.series_group_id:
                        all_entries = await self._db.get_series_group_entries(
                            group.series_group_id
                        )
                        group_entries = [dict(e) for e in all_entries]
                        for subdir in sorted(season_subdirs):
                            subdir_name = os.path.basename(subdir.rstrip("/"))
                            entry = _match_subdir_to_entry(
                                subdir_name, group_entries, consume=True
                            )
                            aid = entry["anilist_id"] if entry else group.anilist_id
                            atitle = (
                                entry.get("display_title", "")
                                if entry
                                else group.display_title
                            )
                            await self._db.upsert_library_item(
                                library_id=library_id,
                                folder_path=subdir,
                                folder_name=root_basename,
                                anilist_id=aid,
                                anilist_title=atitle,
                                match_confidence=1.0,
                                match_method="restructure_plan",
                                series_group_id=group.series_group_id or None,
                                **(await self._cached_metadata_kwargs(aid)),
                            )
                            upserted += 1
                    else:
                        # One row for root; series_group_id stored so the
                        # unified library can expand to all group seasons.
                        await self._db.upsert_library_item(
                            library_id=library_id,
                            folder_path=root,
                            folder_name=root_basename,
                            anilist_id=group.anilist_id,
                            anilist_title=group.display_title,
                            match_confidence=1.0,
                            match_method="restructure_plan",
                            series_group_id=group.series_group_id or None,
                            **(await self._cached_metadata_kwargs(group.anilist_id)),
                        )
                        upserted += 1
                else:
                    # Multiple season folders — match by name.
                    all_entries = (
                        await self._db.get_series_group_entries(group.series_group_id)
                        if group.series_group_id
                        else []
                    )
                    group_entries = [dict(e) for e in all_entries]
                    for src_folder in sorted(source_folders):
                        src_name = os.path.basename(src_folder.rstrip("/"))
                        entry = _match_subdir_to_entry(
                            src_name, group_entries, consume=True
                        )
                        aid = entry["anilist_id"] if entry else group.anilist_id
                        atitle = (
                            entry.get("display_title", "")
                            if entry
                            else group.display_title
                        )
                        await self._db.upsert_library_item(
                            library_id=library_id,
                            folder_path=src_folder,
                            folder_name=root_name,
                            anilist_id=aid,
                            anilist_title=atitle,
                            match_confidence=1.0,
                            match_method="restructure_plan",
                            series_group_id=group.series_group_id or None,
                            **(await self._cached_metadata_kwargs(aid)),
                        )
                        upserted += 1
            else:
                # --- Post-execute path ---
                # target_folder contains Season N/ subdirs for multi-season,
                # or holds files directly for single-season / movie groups.
                if group.series_group_id and group.season_count > 1:
                    tv_entries = await self._tv_entries_for_group(
                        group.series_group_id, group.anilist_id
                    )
                    for i, entry in enumerate(tv_entries, start=1):
                        season_path = os.path.join(group.target_folder, f"Season {i}")
                        aid = entry["anilist_id"]
                        cached_kw = await self._cached_metadata_kwargs(aid)
                        # Prefer series_group_entries data over cache for
                        # format/episodes (more specific to this entry).
                        cached_kw["anilist_format"] = entry.get(
                            "format", ""
                        ) or cached_kw.get("anilist_format", "")
                        cached_kw["anilist_episodes"] = entry.get(
                            "episodes"
                        ) or cached_kw.get("anilist_episodes")
                        await self._db.upsert_library_item(
                            library_id=library_id,
                            folder_path=season_path,
                            folder_name=root_name,
                            anilist_id=aid,
                            anilist_title=entry.get("display_title", ""),
                            match_confidence=1.0,
                            match_method="restructure_plan",
                            series_group_id=group.series_group_id,
                            **cached_kw,
                        )
                        upserted += 1
                else:
                    await self._db.upsert_library_item(
                        library_id=library_id,
                        folder_path=group.target_folder,
                        folder_name=root_name,
                        anilist_id=group.anilist_id,
                        anilist_title=group.display_title,
                        match_confidence=1.0,
                        match_method="restructure_plan",
                        series_group_id=group.series_group_id or None,
                        **(await self._cached_metadata_kwargs(group.anilist_id)),
                    )
                    upserted += 1

        # Seed unchanged shows (scanned & matched but no restructure needed).
        # Detect Structure B (named season subdirs) and create per-subdir
        # entries so that multi-season series get individual library rows.
        for si in plan.unchanged_shows:
            if not si.anilist_id or not si.local_path:
                continue
            folder_name = os.path.basename(si.local_path.rstrip("/"))
            sg_id = plan.unchanged_group_ids.get(si.anilist_id, 0) or None

            # Check for Structure B: 2+ subdirs with video files
            season_subdirs = _find_video_subdirs(si.local_path)
            if len(season_subdirs) >= 2 and sg_id:
                # Use ALL group entries (not just TV) — subdirs may
                # include movies, OVAs, etc. alongside TV seasons.
                all_entries = await self._db.get_series_group_entries(sg_id)
                group_entries = [dict(e) for e in all_entries]

                for subdir in sorted(season_subdirs):
                    subdir_name = os.path.basename(subdir.rstrip("/"))
                    # Match subdir to the best group entry by name
                    # similarity rather than position — alphabetical
                    # order rarely matches chronological order.
                    entry = _match_subdir_to_entry(
                        subdir_name, group_entries, consume=True
                    )
                    aid = entry["anilist_id"] if entry else si.anilist_id
                    atitle = (
                        entry.get("display_title", "")
                        if entry
                        else (si.anilist_title or si.title)
                    )
                    await self._db.upsert_library_item(
                        library_id=library_id,
                        folder_path=subdir,
                        folder_name=folder_name,
                        anilist_id=aid,
                        anilist_title=atitle,
                        match_confidence=1.0,
                        match_method="restructure_unchanged",
                        series_group_id=sg_id,
                        **(await self._cached_metadata_kwargs(aid)),
                    )
                    upserted += 1
            else:
                # Single-season or no video subdirs — one row for root
                await self._db.upsert_library_item(
                    library_id=library_id,
                    folder_path=si.local_path,
                    folder_name=folder_name,
                    anilist_id=si.anilist_id,
                    anilist_title=si.anilist_title or si.title,
                    match_confidence=1.0,
                    match_method="restructure_unchanged",
                    series_group_id=sg_id,
                    **(await self._cached_metadata_kwargs(si.anilist_id)),
                )
                upserted += 1

        logger.info(
            "seed_library_items: upserted %d rows into library %d (from_source=%s)",
            upserted,
            library_id,
            from_source,
        )
        return upserted

    async def _tv_entries_for_group(
        self, series_group_id: int, fallback_anilist_id: int
    ) -> list[dict]:
        """Return TV/TV_SHORT series_group_entries ordered by season_order."""
        if not series_group_id:
            return []
        rows = await self._db.fetch_all(
            "SELECT anilist_id, display_title, format, episodes "
            "FROM series_group_entries "
            "WHERE group_id = ? AND format IN ('TV', 'TV_SHORT') "
            "ORDER BY season_order",
            (series_group_id,),
        )
        return [dict(r) for r in rows]

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
                # Guard: destination already exists with content (e.g. previous run,
                # or a duplicate source that slipped through analysis).
                if os.path.isdir(target_folder) and os.listdir(target_folder):
                    logger.warning(
                        "Skipping rename — destination already exists and is "
                        "non-empty: '%s' -> '%s'",
                        src_folder,
                        target_folder,
                    )
                    await self._db.log_restructure_operation(
                        group_title=group.display_title,
                        source_path=src_folder,
                        destination_path=target_folder,
                        operation="folder_rename",
                        status="skipped",
                        error_message="destination already exists with content",
                    )
                    stats["errors"] += 1
                    progress.processed += 1
                    continue
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
