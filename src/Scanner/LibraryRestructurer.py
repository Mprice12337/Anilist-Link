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
_SEASON_DIR_RE = re.compile(r"(?i)^season\s+(\d+)")
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
# Extensions for metadata / artwork files that media servers generate.
# These become stale after restructure/rename and should be removed so the
# media server re-indexes cleanly.
_SUPPORT_EXTS = {
    ".nfo",
    ".jpg",
    ".jpeg",
    ".png",
    ".tbn",
    ".xml",
    ".bif",
}
_SUPPORT_FILENAMES = {
    "folder.jpg",
    "poster.jpg",
    "banner.jpg",
    "fanart.jpg",
    "logo.jpg",
    "landscape.jpg",
    "thumb.jpg",
    "clearart.png",
    "clearlogo.png",
    "backdrop.jpg",
}
# NFO files that must survive support-file cleanup — they tell media servers
# how to classify the directory and are written by us, not by media servers.
_PROTECTED_NFO_FILENAMES = {"tvshow.nfo", "movie.nfo"}
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


def _is_support_file(filename: str) -> bool:
    """Return True if *filename* is a media-server metadata/artwork file."""
    name_lower = filename.lower()
    if name_lower in _PROTECTED_NFO_FILENAMES:
        return False
    if name_lower in _SUPPORT_FILENAMES:
        return True
    ext = os.path.splitext(name_lower)[1]
    return ext in _SUPPORT_EXTS


def _count_support_files(directory: str) -> int:
    """Count metadata/artwork files in *directory* and its subdirectories."""
    count = 0
    try:
        for entry in os.scandir(directory):
            if entry.is_file() and _is_support_file(entry.name):
                count += 1
            elif entry.is_dir():
                count += _count_support_files(entry.path)
    except OSError:
        pass
    return count


def _delete_support_files(directory: str) -> int:
    """Delete metadata/artwork files from *directory* and its subdirectories.

    Returns the number of files deleted.  Only deletes files matching
    ``_SUPPORT_EXTS`` or ``_SUPPORT_FILENAMES`` — media and subtitle files
    are never touched.
    """
    deleted = 0
    try:
        for entry in os.scandir(directory):
            if entry.is_file() and _is_support_file(entry.name):
                try:
                    os.remove(entry.path)
                    logger.info("Deleted support file: %s", entry.path)
                    deleted += 1
                except OSError as exc:
                    logger.warning(
                        "Could not delete support file %s: %s", entry.path, exc
                    )
            elif entry.is_dir():
                deleted += _delete_support_files(entry.path)
    except OSError:
        pass
    return deleted


def _write_tvshow_nfo(folder_path: str, title: str) -> None:
    """Write a minimal tvshow.nfo so Jellyfin classifies the folder as a TV show.

    Safe to call on every restructure/rename — already-correct files are
    overwritten with the same content so the title stays current.
    """
    nfo_path = os.path.join(folder_path, "tvshow.nfo")
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    content = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        "<tvshow>\n"
        f"  <title>{safe_title}</title>\n"
        "</tvshow>\n"
    )
    try:
        with open(nfo_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        logger.info("Wrote tvshow.nfo to %s", folder_path)
    except OSError as exc:
        logger.warning("Could not write tvshow.nfo to %s: %s", folder_path, exc)


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
    is_dir: bool = False  # True for season folder renames


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
    support_file_count: int = 0  # metadata files that will be removed
    # Maps renamed season subdir name → anilist_id.  Populated during
    # L1/L2 rename analysis so post-execute library seeding can assign
    # the correct per-season anilist_id without re-matching via fuzzy
    # name similarity (which fails when subdirs are renamed with custom
    # templates like romaji titles).
    season_dir_anilist_map: dict[str, int] = field(default_factory=dict)


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
    # Shows that were scanned but had no AniList match.  Stored so
    # library seeding can create placeholder rows for them (matching
    # LibraryScanner behaviour which seeds unmatched shows too).
    unmatched_shows: list["ShowInput"] = field(default_factory=list)


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
# Fallback patterns: Episode keyword, " - NN", bracket-enclosed NN, bare number
_EP_FALLBACK = re.compile(
    r"[Ee](?:pisode)?\.?\s*(\d{1,3}(?:\.\d(?!\d))?)"  # E05 / Episode 5
    r"|\s-\s(\d{2,3}(?:\.\d(?!\d))?)(?:\s|$|\[)"  # " - 05 " format
    r"|\]\[(\d{2,3})(?:v\d)?\]\["  # ][05][ fansub bracket format
    r"|(?:^|[\s_\]])(\d{2,3})(?:v\d)?(?:[\s_.\[(\-]|$)"  # bare number
)

# Specials/extras: S##OVA## or S##S## (e.g. "S01OVA03", "S02S06")
_EP_SPECIAL = re.compile(r"S(\d{1,2})(?:OVA|S)(\d{1,3})", re.IGNORECASE)

# Creditless OP/ED and similar extras that have no episode number
_EP_EXTRAS_ONLY = re.compile(
    r"\b(NC(?:OP|ED)\d*|Creditless\s+(?:Opening|Ending)|Clean\s+(?:OP|ED)"
    r"|(?:Opening|Ending)\s+(?:Theme|Animation)|Textless)\b",
    re.IGNORECASE,
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

    Special handling:
    - S##OVA## / S##S## patterns → routed to S00 (specials)
    - Creditless OP/ED and similar no-number extras → routed to S00 with
      ep number "00" so they land in a specials folder without a rename
    - [##][ fansub bracket notation → extracted as bare episode number
    """
    source_season: int | None = None

    # Try the explicit SxxExx pattern first — most reliable
    m_primary = _EP_PRIMARY.search(filename)
    if m_primary:
        source_season = int(m_primary.group(1))
        ep_str = m_primary.group(2)
    else:
        # S##OVA## or S##S## → treat as specials (S00)
        m_special = _EP_SPECIAL.search(filename)
        if m_special:
            ep_str = m_special.group(2)
            source_season = 0
        else:
            # Fall back to looser patterns only when SxxExx is absent
            match = _EP_FALLBACK.search(filename)
            if not match:
                # No numeric episode — check for known no-number extras
                # (NCOP, NCED, Creditless Opening/Ending, etc.)
                if _EP_EXTRAS_ONLY.search(filename):
                    return EpisodeInfo(number="00", source_season=0)
                return None
            # Groups: 1=E##, 2=" - ##", 3=][##][, 4=bare
            ep_str = (
                match.group(1) or match.group(2) or match.group(3) or match.group(4)
            )
            if ep_str is None:
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


def _cumulative_episodes_before(
    season: int,
    shows_in_group: list[dict],
) -> int | None:
    """Sum AniList episode counts for all group entries before *season*.

    Returns None if any prior entry has an unknown episode count (None or 0),
    since we cannot safely compute the absolute-to-relative offset in that case.
    """
    seen_seasons: set[int] = set()
    total = 0
    for entry in shows_in_group:
        order = entry["tv_season_order"]
        if order >= season or order in seen_seasons:
            continue
        ep_count = entry.get("episodes")
        if not ep_count:  # None or 0 — unknown, bail out
            return None
        seen_seasons.add(order)
        total += ep_count
    return total


_NON_TV_FORMATS: frozenset[str] = frozenset({"MOVIE", "OVA", "SPECIAL"})


def _build_tv_season_map(
    full_group_entries: list[dict],
) -> dict[int, dict]:
    """Map 1-based TV season number to its series group entry.

    Skips MOVIE/OVA/SPECIAL entries so that on-disk season directory numbers
    (Season 1/, Season 2/, …) map correctly to AniList entries even when
    movies appear between seasons in the relation graph.

    Example — JJK series group has season_order 1=S1, 2=JJK0(movie), 3=S2, 4=S3.
    tv_season_map → {1: S1-entry, 2: S2-entry, 3: S3-entry}
    """
    tv_num = 0
    result: dict[int, dict] = {}
    for entry in sorted(full_group_entries, key=lambda e: e["season_order"]):
        fmt = (entry.get("format") or "").upper()
        if fmt in _NON_TV_FORMATS:
            continue
        tv_num += 1
        result[tv_num] = entry
    return result


def _cumulative_tv_episodes(
    before_tv_season: int,
    tv_season_map: dict[int, dict],
) -> int | None:
    """Sum episode counts for all TV seasons before *before_tv_season*.

    Uses the tv_season_map built by ``_build_tv_season_map`` (movies excluded).
    Returns None if any prior TV season has an unknown episode count or is
    missing from the map.
    """
    total = 0
    for n in range(1, before_tv_season):
        entry = tv_season_map.get(n)
        if entry is None:
            return None
        ep_count = entry.get("episodes")
        if not ep_count:
            return None
        total += ep_count
    return total


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

    Matching strategy (highest confidence first):
    1. Year match — extract "(YYYY)" from the subdir name and find an entry
       whose start_date begins with that year.  Unambiguous when exactly one
       entry matches; skipped when 0 or multiple entries share the same year.
    2. Title similarity — SequenceMatcher ratio against display_title,
       title_romaji, and title_english.  Threshold 0.4.

    When *consume* is True the matched entry is **removed** from *entries*
    so that subsequent calls cannot re-match the same entry to a different
    subdir.  This prevents two similarly-named subdirs from collapsing onto
    the same anilist_id.
    """
    # --- 1. Year-based matching (high confidence) ---
    year_m = re.search(r"\((\d{4})\)", subdir_name)
    if year_m:
        year_str = year_m.group(1)
        year_matches = [
            e for e in entries if (e.get("start_date") or "").startswith(year_str)
        ]
        if len(year_matches) == 1:
            entry = year_matches[0]
            if consume:
                entries.remove(entry)
            return entry

    # --- 2. Title-based matching (fallback) ---
    # Strip trailing year suffix like "(2009)" for cleaner comparison
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
        # display_title is English; title_romaji / title_english are enriched
        # from anilist_cache before this function is called.  Check all three
        # variants and take the highest score so that romaji-named folders
        # match correctly even when display_title is English (and vice versa).
        candidate_titles = [
            t.lower()
            for t in [
                entry.get("display_title") or "",
                entry.get("title_romaji") or "",
                entry.get("title_english") or "",
            ]
            if t
        ]
        if not candidate_titles:
            continue
        score = max(SequenceMatcher(None, clean, t).ratio() for t in candidate_titles)
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score > 0.4:
        if consume:
            entries.remove(best_entry)
        return best_entry
    return None


def _entry_dict_to_show_input(entry: dict) -> ShowInput:
    """Construct a minimal ShowInput from a series_group_entries row dict.

    Used when building file tokens for season-subdir files whose AniList entry
    is not locally matched (and therefore not in ``show_by_anilist``).
    """
    display_title = entry.get("display_title") or ""
    start_date = entry.get("start_date") or ""
    year = 0
    if start_date:
        try:
            year = int(start_date[:4])
        except (ValueError, IndexError):
            year = 0
    romaji = entry.get("title_romaji") or ""
    english = entry.get("title_english") or ""
    return ShowInput(
        title=display_title,
        local_path="",
        source_id="",
        anilist_id=entry.get("anilist_id", 0),
        anilist_title=display_title,
        year=year,
        anilist_title_romaji=romaji,
        anilist_title_english=english,
        anilist_format=entry.get("format") or "",
        anilist_episodes=entry.get("episodes"),
    )


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
            # Show destination relative to the group target folder so season
            # subfolders are visible (e.g. "Season 02/Show - S02E01.mkv").
            if g.target_folder and fm.destination.startswith(g.target_folder):
                rel_dest = fm.destination[len(g.target_folder) :].lstrip(os.sep)
            else:
                rel_dest = fm.renamed_filename
            if fm.original_filename != rel_dest:
                lines.append(f"    {fm.original_filename} -> {rel_dest}")
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

    @classmethod
    async def from_settings(
        cls,
        db: "DatabaseManager",
        anilist_client: object,
    ) -> "LibraryRestructurer":
        """Construct a LibraryRestructurer from DB-stored naming settings.

        Centralises the repeated pattern of loading 6 naming settings then
        building a ``LibraryRestructurer``.  Both the onboarding and tools
        execute flows should call this instead of duplicating the logic.
        """
        file_tmpl = await db.get_setting("naming.file_template") or ""
        folder_tmpl = await db.get_setting("naming.folder_template") or ""
        season_tmpl = await db.get_setting("naming.season_folder_template") or ""
        movie_tmpl = await db.get_setting("naming.movie_file_template") or ""
        title_pref = await db.get_setting("app.title_display") or "romaji"
        illegal_char_repl = (
            await db.get_setting("naming.illegal_char_replacement") or ""
        )
        group_builder = SeriesGroupBuilder(db, anilist_client)
        return cls(
            db=db,
            group_builder=group_builder,
            file_template=file_tmpl,
            folder_template=folder_tmpl,
            season_folder_template=season_tmpl,
            movie_file_template=movie_tmpl,
            title_pref=title_pref,
            illegal_char_replacement=illegal_char_repl,
        )

    def _san(self, text: str) -> str:
        """Sanitize text using the configured illegal character replacement."""
        return NamingTemplate.sanitize(text, self._repl)

    def _render_season_folder(
        self,
        season_num: int,
        show_or_entry: ShowInput | dict | None,
    ) -> str:
        """Render a season folder name from a season number and show/entry data.

        *show_or_entry* can be a ``ShowInput``, a series-group entry dict,
        or ``None`` (falls back to ``Season {season_num}``).

        When a dict is passed, the method respects ``self._title_pref`` if
        the dict contains ``title_romaji`` / ``title_english`` keys (added
        by the rename code from anilist_cache).  Falls back to
        ``display_title`` when those aren't available.

        Shared by ``_analyze_full_restructure`` and ``_analyze_rename``
        so that season folder naming is always consistent.
        """
        if isinstance(show_or_entry, ShowInput):
            season_name = _resolve_display_title(show_or_entry, self._title_pref)
            season_year = str(show_or_entry.year) if show_or_entry.year else ""
        elif isinstance(show_or_entry, dict):
            # Apply title_pref if both title variants are available
            romaji = show_or_entry.get("title_romaji", "")
            english = show_or_entry.get("title_english", "")
            if self._title_pref == "english" and english:
                season_name = english
            elif romaji:
                season_name = romaji
            else:
                season_name = show_or_entry.get("display_title") or ""
            season_year = (show_or_entry.get("start_date") or "")[:4]
        else:
            season_name = ""
            season_year = ""

        if season_num == 0:
            tokens = {
                "season": "00",
                "season.name": self._san(season_name),
                "year": season_year,
            }
            return (
                self._season_tmpl.render(tokens) or self._san(season_name) or "Specials"
            )

        tokens = {
            "season": f"{season_num:02d}",
            "season.name": self._san(season_name),
            "year": season_year,
        }
        return self._season_tmpl.render(tokens) or f"Season {season_num}"

    @staticmethod
    def _is_single_item_entry(format_str: str, video_file_count: int) -> bool:
        """Return True if entry should use movie-style naming (no SxxExx)."""
        if format_str == "MOVIE":
            return True
        if format_str in ("OVA", "SPECIAL", "ONA") and video_file_count <= 1:
            return True
        return False

    def _is_already_structured(
        self,
        source_folder: str,
        target_folder: str,
        expected_season_folders: list[str],
    ) -> bool:
        """Return True if the on-disk layout already matches the intended output.

        All checks are purely filesystem-based — no DB state, no run history.
        This supports first-time runs on pre-organised libraries as well as
        re-runs on previously restructured content.

        Args:
            source_folder: Current folder on disk.
            target_folder: What the restructurer would create.
            expected_season_folders: Rendered season folder names that should
                exist as subdirectories (e.g. ["Season 1", "Season 2"]).
        """
        # 1. Folder name must already match
        if os.path.basename(source_folder) != os.path.basename(target_folder):
            return False

        # 2. Source must be a real directory
        if not os.path.isdir(source_folder):
            return False

        # 3. Check expected season subdirectories exist on disk
        if expected_season_folders:
            try:
                existing_dirs = {
                    name
                    for name in os.listdir(source_folder)
                    if os.path.isdir(os.path.join(source_folder, name))
                }
            except OSError:
                return False
            for expected_name in expected_season_folders:
                if expected_name not in existing_dirs:
                    return False

        return True

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
        # Full series group entries keyed by group_id, used for tv_season_map
        all_group_entries: dict[int, list[dict]] = {}
        skipped: list[tuple[str, str]] = []  # (title, reason)

        for si in shows:
            progress.current_item = si.title
            progress.processed += 1

            if not si.anilist_id or not si.local_path:
                skipped.append((si.title, "no AniList match or no local path"))
                # Keep shows with a local path so library seeding can
                # create placeholder rows (matching LibraryScanner).
                if si.local_path:
                    plan.unmatched_shows.append(si)
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

            # Store full entries once per group for tv_season_map lookups
            if group_id not in all_group_entries:
                all_group_entries[group_id] = entries

            if group_id not in group_shows:
                group_shows[group_id] = []
            # Remember the group for this anilist_id (used if demoted to standalone)
            standalone_group_id[si.anilist_id] = group_id

            season_order = 1
            tv_season_order = 1
            entry_format = ""
            entry_episodes: int | None = None
            for entry in entries:
                fmt = entry.get("format") or ""
                if entry["anilist_id"] == si.anilist_id:
                    season_order = entry["season_order"]
                    entry_format = fmt
                    entry_episodes = entry.get("episodes")
                    # Use the chronological season_order for ALL formats so that
                    # OVA/ONA/SPECIAL/MOVIE entries are never routed to S00 /
                    # Specials — they keep their position in the series group.
                    tv_season_order = season_order
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

            # Build a TV-only season map so on-disk Season X/ directories
            # map correctly even when movies/OVAs sit between TV seasons in
            # the AniList relation graph.
            full_entries = all_group_entries.get(group_id, [])
            tv_season_map = _build_tv_season_map(full_entries)

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

            # --- Early skip: check if on-disk layout already matches ---
            # Purely filesystem-based — supports first-time runs on
            # pre-organised libraries and re-runs alike.
            if len(source_folders) == 1:
                # Build expected season folder names from the group entries
                expected_seasons: list[str] = []
                for si_info in shows_in_group:
                    sn = si_info["tv_season_order"]
                    _si_obj = show_by_anilist.get(si_info["anilist_id"])
                    expected_seasons.append(self._render_season_folder(sn, _si_obj))
                if self._is_already_structured(
                    source_folders[0], target_folder, expected_seasons
                ):
                    logger.info(
                        "Skipping already-structured group: %s",
                        display_title,
                    )
                    for si_info in shows_in_group:
                        si_obj = show_by_anilist.get(si_info["anilist_id"])
                        if si_obj:
                            plan.unchanged_shows.append(si_obj)
                            plan.unchanged_group_ids[si_obj.anilist_id] = group_id
                    continue

            file_moves: list[FileMove] = []
            warnings: list[str] = []
            dest_filenames: dict[str, str] = {}

            for show_info in shows_in_group:
                # Use the chronological season_order for all entries (TV and non-TV).
                # No entry is routed to S00/Specials; each keeps its group position.
                season_num = show_info["tv_season_order"]
                src_folder = show_info["local_path"]
                group_si = show_by_anilist.get(show_info["anilist_id"])

                season_folder_name = self._render_season_folder(season_num, group_si)
                season_dir = os.path.join(target_folder, season_folder_name)

                # Keep season_name/season_year for cross-season file routing below
                season_name = (
                    _resolve_display_title(group_si, self._title_pref)
                    if group_si
                    else show_info["title"]
                )
                season_year = str(group_si.year) if group_si and group_si.year else ""

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
                        _m = _SEASON_DIR_RE.match(fs_entry)
                        if _m:
                            dir_sn = int(_m.group(1))
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
                        # Non-media files (metadata, artwork) — skip them.
                        # They'll be cleaned up when the source folder is
                        # deleted after execution (shutil.rmtree).
                        continue
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
                            # Use the entry's assigned season_order for all
                            # files, including S00Exx-named sources.  S00 in
                            # a source filename is a download-convention
                            # artifact; the entry's tv_season_order is the
                            # authoritative season number.
                            # If the file came from a named season
                            # subdirectory (e.g. "Season 2/") within
                            # this source folder, use that directory's
                            # season number instead of the folder's
                            # group season_order.  This handles the case
                            # where a single Plex folder (matched to S1)
                            # contains Season 2/ and Season 3/ subfolders
                            # with content from later series entries.
                            effective_season = (
                                file_dir_sn if file_dir_sn is not None else season_num
                            )
                            file_season = effective_season
                            if effective_season == season_num:
                                file_dest_dir = season_dir
                                file_si = group_si
                            else:
                                # Use the TV-season-aware entry for folder
                                # naming so movies/OVAs in the group don't
                                # shift Season 2/ → wrong AniList title.
                                alt_entry = tv_season_map.get(effective_season)
                                alt_folder = self._render_season_folder(
                                    effective_season, alt_entry or group_si
                                )
                                file_dest_dir = os.path.join(target_folder, alt_folder)
                                # File tokens should use the alt-season entry's
                                # title/year so filenames reflect the correct
                                # season, not the locally-matched S1 entry.
                                if alt_entry:
                                    file_si = show_by_anilist.get(
                                        alt_entry.get("anilist_id", 0), None
                                    ) or _entry_dict_to_show_input(alt_entry)
                                else:
                                    file_si = group_si
                            # Absolute-to-season-relative episode translation.
                            # Only fires when:
                            #   - file has no SxxExx tag (source_season is None)
                            #   - file is in S2 or later
                            #   - all prior-season episode counts are known
                            #   - episode number exceeds the cumulative prior count
                            #     (unambiguous signature of absolute numbering)
                            if ep_info.source_season is None and file_season > 1:
                                # Prefer the full-group TV map (excludes movies,
                                # works even when a season has no local folder).
                                # Fall back to locally-matched shows only.
                                prior_eps = (
                                    _cumulative_tv_episodes(file_season, tv_season_map)
                                    if tv_season_map
                                    else _cumulative_episodes_before(
                                        file_season, shows_in_group
                                    )
                                )
                                if prior_eps is not None:
                                    try:
                                        ep_int = int(float(ep_info.number))
                                    except ValueError:
                                        ep_int = 0
                                    # Only translate if the episode number
                                    # exceeds both the prior-season cumulative
                                    # AND the current season's own episode count.
                                    # If ep_int fits within the current season's
                                    # count it is almost certainly relative
                                    # numbering (e.g. S2 E13-of-13), not
                                    # absolute — applying the offset would
                                    # produce a collision with E01.
                                    cur_season_eps = file_si.anilist_episodes or 0
                                    is_absolute = ep_int > prior_eps and (
                                        not cur_season_eps or ep_int > cur_season_eps
                                    )
                                    if is_absolute:
                                        relative = ep_int - prior_eps
                                        logger.debug(
                                            "Absolute ep translation: '%s' "
                                            "ep %s in S%02d → ep %02d "
                                            "(cumulative prior seasons: %d eps)",
                                            filename,
                                            ep_info.number,
                                            file_season,
                                            relative,
                                            prior_eps,
                                        )
                                        ep_info = EpisodeInfo(
                                            number=str(relative),
                                            source_season=None,
                                            variant=ep_info.variant,
                                        )
                            tokens = _build_file_tokens(
                                file_si,
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

            # Count support files that will be removed from source folders
            sf_count = sum(_count_support_files(sf) for sf in source_folders)

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
                    current_folder=", ".join(
                        os.path.basename(s) for s in source_folders
                    ),
                    support_file_count=sf_count,
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
                    _m = _SEASON_DIR_RE.match(fs_entry)
                    if _m:
                        dir_season = int(_m.group(1))
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
                    # Build a season_order → entry map for ALL formats (TV,
                    # OVA, ONA, SPECIAL, MOVIE).  Each entry's season_order
                    # is its authoritative season number after restructuring,
                    # so direct matching is correct for both first-time runs
                    # and re-runs on already-restructured media.
                    _so_map: dict[int, dict] = {}
                    for _e in _group_entries:
                        _so = _e.get("season_order")
                        if _so and _so > 0:
                            _so_map[_so] = _e
                    if len(_so_map) > 1:
                        for _sn in _all_distinct_seasons:
                            if _sn in _so_map:
                                sg_season_map[_sn] = _so_map[_sn]
                except Exception as _exc:
                    logger.warning(
                        "Series group lookup failed for %s: %s", si.title, _exc
                    )

            # Count video files per detected season.  Used below to avoid
            # applying a MOVIE-format entry to a season that contains multiple
            # TV episodes (e.g. JJK 0 at season_order=2 vs S02Exx TV files).
            _season_video_count: dict[int, int] = {}
            for _fp, _fn, _file_dir_sn in source_files:
                if os.path.splitext(_fn)[1].lower() in _VIDEO_EXTS:
                    _ei_cnt = _extract_episode_info(_fn)
                    if _file_dir_sn is not None:
                        _cnt_sn = _file_dir_sn
                    elif _ei_cnt and _ei_cnt.source_season is not None:
                        _cnt_sn = _ei_cnt.source_season
                    else:
                        _cnt_sn = 1
                    _season_video_count[_cnt_sn] = (
                        _season_video_count.get(_cnt_sn, 0) + 1
                    )

            # Enrich sg_season_map entries with romaji/english from anilist_cache
            # so that file/folder naming respects the user's title_pref instead of
            # always using the English-preferring display_title from series_groups.
            # Also skip MOVIE-format group entries for seasons with multiple video
            # files — that season contains TV episodes, not a movie.
            if sg_season_map:
                _enriched: dict[int, dict] = {}
                for _sn, _sg_entry in sg_season_map.items():
                    _sg_fmt = (_sg_entry.get("format") or "").upper()
                    _vid_count = _season_video_count.get(_sn, 0)
                    if _sg_fmt == "MOVIE" and _vid_count > 1:
                        # This season has multiple files but the group entry is a
                        # movie — user's SxxExx numbering is TV-based. Skip so
                        # those files keep the parent show's title instead.
                        continue
                    _sg_aid = _sg_entry.get("anilist_id")
                    if _sg_aid:
                        _sg_cache = await self._db.get_cached_metadata(_sg_aid)
                        if _sg_cache:
                            _sg_entry = dict(_sg_entry)  # copy; don't mutate shared
                            _sg_entry["title_romaji"] = (
                                _sg_cache.get("title_romaji") or ""
                            )
                            _sg_entry["title_english"] = (
                                _sg_cache.get("title_english") or ""
                            )
                    _enriched[_sn] = _sg_entry
                sg_season_map = _enriched

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
                    if self._title_pref == "english":
                        _sg_title_raw = (
                            sg.get("title_english")
                            or sg.get("display_title")
                            or _resolve_display_title(si, self._title_pref)
                        )
                    else:
                        _sg_title_raw = (
                            sg.get("title_romaji")
                            or sg.get("display_title")
                            or _resolve_display_title(si, self._title_pref)
                        )
                    sg_title = self._san(_sg_title_raw)
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

            # Precompute the sanitized root-show title prefix once so that
            # the per-file loop can cheaply detect files that are already
            # named using the root show's title rather than a per-season
            # series-group entry title (e.g. flat JJK folder where every
            # file starts with "Jujutsu Kaisen (2020)" even for S03Exx).
            _sg_root_title = self._san(_resolve_display_title(si, self._title_pref))
            _sg_root_prefix = (
                f"{_sg_root_title} ({si.year})" if si.year else _sg_root_title
            )

            file_moves = []
            standalone_warnings: list[str] = []
            dest_filenames_s: dict[str, str] = {}

            for full_src, filename, dir_season_num in source_files:
                _name, ext = os.path.splitext(filename)
                ext_lower = ext.lower()

                if ext_lower not in _MEDIA_EXTS:
                    # Non-media files (metadata, artwork) — skip them.
                    # Source folder cleanup will remove them.
                    continue
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
                        # S00 in source filenames is a download-convention
                        # artifact for standalone entries — treat it the same
                        # as "no season encoded" and default to season 1.
                        if dir_season_num is not None and (
                            ep_info.source_season is None or ep_info.source_season <= 1
                        ):
                            file_season = dir_season_num
                        elif (
                            ep_info.source_season is not None
                            and ep_info.source_season > 0
                        ):
                            file_season = ep_info.source_season
                        else:
                            file_season = 1
                        file_dest_dir = _get_season_dir(file_season)
                        # For multi-season Sonarr structures, use the AniList
                        # entry for this season so filenames use the right title
                        # and year (e.g. "Code Geass R2 (2008) - S02E01").
                        # Guard: if the source file already uses the root show
                        # title (e.g. flat JJK folder — "Jujutsu Kaisen (2020)
                        # - S03E01") do NOT substitute a series-group entry
                        # whose title differs ("Jujutsu Kaisen 2nd Season").
                        # Files using per-season titles (e.g. "Ajin 2 (2016)")
                        # won't match the root prefix so they still get the
                        # correct per-season sg entry applied.
                        _src_base = os.path.splitext(filename)[0]
                        _src_uses_root = _src_base.startswith(_sg_root_prefix)
                        token_si = si
                        if file_season in sg_season_map and not _src_uses_root:
                            sg = sg_season_map[file_season]
                            sg_year_str = (sg.get("start_date") or "")[:4]
                            _sg_romaji = (
                                sg.get("title_romaji")
                                or sg.get("display_title")
                                or si.anilist_title_romaji
                            )
                            _sg_english = (
                                sg.get("title_english") or si.anilist_title_english
                            )
                            token_si = ShowInput(
                                title=sg.get("display_title") or si.title,
                                local_path=si.local_path,
                                source_id=si.source_id,
                                anilist_id=sg.get("anilist_id") or si.anilist_id,
                                anilist_title=sg.get("display_title")
                                or si.anilist_title,
                                year=int(sg_year_str) if sg_year_str else si.year,
                                anilist_title_romaji=_sg_romaji,
                                anilist_title_english=_sg_english,
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
                            # Place the bundled movie flat in the series root
                            # rather than a Specials subfolder.  The flat layout
                            # is already established by the episode files; adding
                            # a "Specials/" subdir would break idempotency (the
                            # file would be proposed for re-move on every run).
                            file_dest_dir = target_folder
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

            # Skip if nothing changes (same folder name and all files unchanged).
            # Use realpath comparison so symlinks and path normalisation
            # don't cause false positives on already-structured content.
            current_folder = os.path.basename(si.local_path)
            folder_changed = current_folder != rendered_folder
            files_changed = any(
                fm.original_filename != fm.renamed_filename
                and os.path.realpath(fm.source) != os.path.realpath(fm.destination)
                for fm in file_moves
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
                    support_file_count=_count_support_files(si.local_path),
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
        """L1/L2: folder + season folder rename (optionally files)."""
        progress.phase = "Analyzing shows for renaming"
        skipped: list[tuple[str, str]] = []
        seen_targets: set[str] = set()  # guard against duplicate destinations

        for si in shows:
            progress.current_item = si.title
            progress.processed += 1

            if not si.anilist_id or not si.local_path:
                skipped.append((si.title, "no AniList match or no local path"))
                # Keep shows with a local path so library seeding can
                # create placeholder rows (matching LibraryScanner).
                if si.local_path:
                    plan.unmatched_shows.append(si)
                continue

            # Determine the display title using the user's title preference
            anilist_title = _resolve_display_title(si, self._title_pref)

            # Render folder name via template
            folder_tokens = _build_folder_tokens(si, self._title_pref, self._repl)
            rendered_folder = self._san(self._folder_tmpl.render(folder_tokens))
            if not rendered_folder:
                rendered_folder = re.sub(r'[<>:"/\\|?*]', "", anilist_title).strip()
            current_folder = os.path.basename(si.local_path)

            # Determine season number and series group for this show
            season_num = 1
            sg_group_id = 0
            try:
                sg_group_id, entries = await self._group_builder.get_or_build_group(
                    si.anilist_id
                )
                if sg_group_id and entries:
                    for entry in entries:
                        if entry["anilist_id"] == si.anilist_id:
                            season_num = entry["season_order"]
                            break
            except Exception:
                pass  # season_num stays 1, sg_group_id stays 0

            # Skip if folder already matches target
            folder_needs_rename = current_folder != rendered_folder

            # Build season folder renames (L1+L2) and file renames (L2)
            file_moves: list[FileMove] = []
            warnings: list[str] = []
            detected_season_count = 1
            # Populated below when video subdirs are detected and matched
            # to series group entries.  Carried on the RestructureGroup so
            # post-execute seeding can assign per-season anilist_ids.
            season_dir_anilist_map: dict[str, int] = {}

            if os.path.isdir(si.local_path):
                # Scan subdirectories for season folder renames (L1 + L2)
                # and collect files for renaming (L2 only).
                #
                # Use _find_video_subdirs to detect ALL season-like subdirs,
                # not just those matching "Season N".  This handles folders
                # already renamed to custom templates in prior runs.
                rename_files: list[tuple[str, str, str, int | None]] = []
                try:
                    top_entries = sorted(os.listdir(si.local_path))
                except OSError:
                    warnings.append(f"Cannot read folder: {si.local_path}")
                    top_entries = []

                video_subdirs = _find_video_subdirs(si.local_path)

                # Build a mapping: subdir_name → (season_number, entry_dict)
                # using series group entries matched by name similarity.
                subdir_season_info: dict[str, tuple[int, dict | None]] = {}

                if len(video_subdirs) >= 2 and sg_group_id and si.anilist_id:
                    detected_season_count = len(video_subdirs)
                    try:
                        _, _group_entries = (
                            await self._group_builder.get_or_build_group(si.anilist_id)
                        )
                        # Use ALL group entries (not just TV) — subdirs may
                        # include movies, OVAs, specials alongside TV seasons.
                        _all_entries = [dict(e) for e in _group_entries]
                        # Enrich entries with romaji/english titles from
                        # anilist_cache so _render_season_folder can apply
                        # the user's title_pref (the DB entries only store
                        # a single display_title).
                        for _te in _all_entries:
                            _aid = _te.get("anilist_id")
                            if _aid:
                                _cached = await self._db.get_cached_metadata(_aid)
                                if _cached:
                                    _te["title_romaji"] = (
                                        _cached.get("title_romaji") or ""
                                    )
                                    _te["title_english"] = (
                                        _cached.get("title_english") or ""
                                    )
                        if _all_entries:
                            match_pool = list(_all_entries)
                            # Build a lookup by season_order so "Season N"
                            # maps to the correct entry even when non-TV
                            # entries (OVAs, movies) are interspersed.
                            _by_season_order = {
                                e["season_order"]: e for e in _all_entries
                            }
                            for sd in sorted(video_subdirs):
                                sd_name = os.path.basename(sd.rstrip("/"))
                                # Try Season N regex first
                                m = _SEASON_DIR_RE.match(sd_name)
                                if m:
                                    dir_sn = int(m.group(1))
                                    entry = _by_season_order.get(dir_sn)
                                    if entry and entry in match_pool:
                                        match_pool.remove(entry)
                                else:
                                    entry = _match_subdir_to_entry(
                                        sd_name, match_pool, consume=True
                                    )
                                    dir_sn = entry["season_order"] if entry else 0
                                if entry:
                                    subdir_season_info[sd_name] = (
                                        dir_sn,
                                        entry,
                                    )
                    except Exception as _exc:
                        logger.warning(
                            "Rename: series group lookup failed for %s: %s",
                            si.title,
                            _exc,
                        )
                elif len(video_subdirs) >= 2:
                    detected_season_count = len(video_subdirs)
                    # No series group — assign sequential season numbers
                    for sn_idx, sd in enumerate(sorted(video_subdirs), start=1):
                        sd_name = os.path.basename(sd.rstrip("/"))
                        m = _SEASON_DIR_RE.match(sd_name)
                        dir_sn = int(m.group(1)) if m else sn_idx
                        subdir_season_info[sd_name] = (dir_sn, None)
                elif len(video_subdirs) == 1:
                    # Single season subdir — still needs to be renamed
                    sd_name = os.path.basename(video_subdirs[0].rstrip("/"))
                    m = _SEASON_DIR_RE.match(sd_name)
                    dir_sn = int(m.group(1)) if m else season_num
                    subdir_season_info[sd_name] = (dir_sn, None)

                # L2: collect files for renaming (from top level + subdirs)
                if level == "folder_file_rename":
                    for fs_entry in top_entries:
                        entry_path = os.path.join(si.local_path, fs_entry)
                        if os.path.isfile(entry_path):
                            rename_files.append((entry_path, fs_entry, "", None))
                        elif os.path.isdir(entry_path):
                            dir_sn_val: int | None = None
                            info = subdir_season_info.get(fs_entry)
                            if info:
                                dir_sn_val = info[0]
                            else:
                                _m2 = _SEASON_DIR_RE.match(fs_entry)
                                if _m2:
                                    dir_sn_val = int(_m2.group(1))
                            try:
                                sub = sorted(os.listdir(entry_path))
                            except OSError:
                                continue
                            for sf in sub:
                                sfull = os.path.join(entry_path, sf)
                                if os.path.isfile(sfull):
                                    rename_files.append(
                                        (sfull, sf, fs_entry, dir_sn_val)
                                    )

                # Build sg_season_map from subdir_season_info for file renames
                sg_season_map: dict[int, dict] = {}
                for _sd_name, (_sn, _ent) in subdir_season_info.items():
                    if _ent:
                        sg_season_map[_sn] = _ent

                # Season folder renames (L1 + L2): rename ANY video-containing
                # subdir that doesn't match the expected template name.
                # Uses the same _render_season_folder as full restructure.
                # Build a map old_name → new_name so file destinations
                # reference the renamed subdir path (not the stale name).
                # Also capture renamed_subdir_name → anilist_id so post-execute
                # library seeding can assign the correct per-season anilist_id
                # without re-running fuzzy name matching.
                subdir_rename_map: dict[str, str] = {}
                for sd_name, (dir_sn, entry) in subdir_season_info.items():
                    entry_path = os.path.join(si.local_path, sd_name)
                    expected_name = self._render_season_folder(dir_sn, entry or si)
                    if entry and entry.get("anilist_id"):
                        season_dir_anilist_map[expected_name] = entry["anilist_id"]
                    if sd_name != expected_name:
                        subdir_rename_map[sd_name] = expected_name
                        base_dir = (
                            os.path.join(
                                os.path.dirname(si.local_path),
                                rendered_folder,
                            )
                            if folder_needs_rename
                            else si.local_path
                        )
                        file_moves.append(
                            FileMove(
                                source=entry_path,
                                destination=os.path.join(base_dir, expected_name),
                                original_filename=sd_name,
                                renamed_filename=expected_name,
                                is_dir=True,
                            )
                        )

                # File renames (L2 only)
                if level == "folder_file_rename" and rename_files:
                    # Count video files for movie-style detection
                    video_count = sum(
                        1
                        for _, fn, _, _ in rename_files
                        if os.path.splitext(fn)[1].lower() in _VIDEO_EXTS
                    )
                    use_movie_naming = self._is_single_item_entry(
                        si.anilist_format, video_count
                    )

                    for full_src, filename, subdir, dir_season_num in rename_files:
                        _name, ext = os.path.splitext(filename)
                        if ext.lower() not in _MEDIA_EXTS:
                            continue

                        ep_info = _extract_episode_info(filename)

                        if ep_info is not None:
                            # Determine effective season from dir or episode info
                            if ep_info.source_season == 0:
                                file_season = 0
                            elif dir_season_num is not None:
                                file_season = dir_season_num
                            else:
                                file_season = season_num

                            # Use per-season AniList entry data if available
                            token_si = si
                            if file_season in sg_season_map:
                                sg = sg_season_map[file_season]
                                sg_year_str = (sg.get("start_date") or "")[:4]
                                # Prefer enriched romaji/english titles from
                                # anilist_cache; fall back to display_title.
                                _sg_romaji = (
                                    sg.get("title_romaji")
                                    or sg.get("display_title")
                                    or si.anilist_title_romaji
                                )
                                _sg_english = (
                                    sg.get("title_english") or si.anilist_title_english
                                )
                                _sg_display = sg.get("display_title") or si.title
                                token_si = ShowInput(
                                    title=_sg_display,
                                    local_path=si.local_path,
                                    source_id=si.source_id,
                                    anilist_id=sg.get("anilist_id") or si.anilist_id,
                                    anilist_title=_sg_display,
                                    year=int(sg_year_str) if sg_year_str else si.year,
                                    anilist_title_romaji=_sg_romaji,
                                    anilist_title_english=_sg_english,
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
                            rendered = self._file_tmpl.render(tokens)
                            if ep_info.variant:
                                rendered += f" ({ep_info.variant})"
                            renamed = self._san(rendered) + ext
                        elif use_movie_naming:
                            # No episode pattern — use movie-style naming
                            tokens = _build_file_tokens(
                                si, 0, None, filename, self._title_pref, self._repl
                            )
                            renamed = (
                                self._san(self._movie_file_tmpl.render(tokens)) + ext
                            )
                        else:
                            # Multi-episode show but no recognisable pattern —
                            # safest to leave the file alone.
                            continue

                        if renamed == filename:
                            continue  # No change needed

                        # Destination is in the target folder (may be renamed),
                        # preserving any subdirectory structure.
                        # Use the renamed subdir name if it was renamed so
                        # preview paths and execution paths are consistent.
                        target_dir = (
                            os.path.join(
                                os.path.dirname(si.local_path), rendered_folder
                            )
                            if folder_needs_rename
                            else si.local_path
                        )
                        if subdir:
                            effective_subdir = subdir_rename_map.get(subdir, subdir)
                            target_dir = os.path.join(target_dir, effective_subdir)
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
                if sg_group_id:
                    plan.unchanged_group_ids[si.anilist_id] = sg_group_id
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
                    series_group_id=sg_group_id or 0,
                    display_title=anilist_title,
                    target_folder=target_folder,
                    source_folders=[si.local_path],
                    file_moves=file_moves,
                    season_count=detected_season_count,
                    warnings=warnings,
                    source_rating_keys=[si.source_id],
                    operation_type=op_type,
                    current_folder=current_folder,
                    group_key=si.source_id,
                    anilist_id=si.anilist_id,
                    support_file_count=_count_support_files(si.local_path),
                    season_dir_anilist_map=season_dir_anilist_map,
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
                        all_entries = (
                            await self._db.get_series_group_entries_with_titles(
                                group.series_group_id
                            )
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
                        await self._db.get_series_group_entries_with_titles(
                            group.series_group_id
                        )
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
                # target_folder contains season subdirs for multi-season,
                # or holds files directly for single-season / movie groups.
                # Season dirs may be named "Season N" (full restructure) or
                # custom names from naming templates (L1/L2 rename).
                if group.series_group_id and group.season_count > 1:
                    # Detect actual season subdirs (handles both
                    # "Season N" and template-named season folders).
                    season_subdirs = _find_video_subdirs(group.target_folder)
                    if season_subdirs:
                        all_entries = (
                            await self._db.get_series_group_entries_with_titles(
                                group.series_group_id
                            )
                        )
                        group_entries = [dict(e) for e in all_entries]
                        # Index by anilist_id for authoritative-map lookups
                        entries_by_aid = {e["anilist_id"]: e for e in group_entries}
                        for subdir in sorted(season_subdirs):
                            subdir_name = os.path.basename(subdir.rstrip("/"))
                            # Prefer the authoritative map populated during
                            # analysis — fuzzy name matching fails when
                            # users rename subdirs to custom templates
                            # (e.g. romaji titles that don't resemble the
                            # English display_title stored in the DB).
                            entry: dict | None = None
                            mapped_aid = group.season_dir_anilist_map.get(subdir_name)
                            if mapped_aid:
                                entry = entries_by_aid.get(mapped_aid)
                                # Consume from the fallback pool so later
                                # subdirs that do fall through to fuzzy
                                # matching can't re-assign the same entry.
                                if entry and entry in group_entries:
                                    group_entries.remove(entry)
                            if entry is None:
                                entry = _match_subdir_to_entry(
                                    subdir_name,
                                    group_entries,
                                    consume=True,
                                )
                            aid = entry["anilist_id"] if entry else group.anilist_id
                            atitle = (
                                entry.get("display_title", "")
                                if entry
                                else group.display_title
                            )
                            cached_kw = await self._cached_metadata_kwargs(aid)
                            if entry:
                                cached_kw["anilist_format"] = entry.get(
                                    "format", ""
                                ) or cached_kw.get("anilist_format", "")
                                cached_kw["anilist_episodes"] = entry.get(
                                    "episodes"
                                ) or cached_kw.get("anilist_episodes")
                            await self._db.upsert_library_item(
                                library_id=library_id,
                                folder_path=subdir,
                                folder_name=root_name,
                                anilist_id=aid,
                                anilist_title=atitle,
                                match_confidence=1.0,
                                match_method="restructure_plan",
                                series_group_id=group.series_group_id,
                                **cached_kw,
                            )
                            upserted += 1
                    else:
                        # No subdirs found — single row at root
                        await self._db.upsert_library_item(
                            library_id=library_id,
                            folder_path=group.target_folder,
                            folder_name=root_name,
                            anilist_id=group.anilist_id,
                            anilist_title=group.display_title,
                            match_confidence=1.0,
                            match_method="restructure_plan",
                            series_group_id=group.series_group_id,
                            **(await self._cached_metadata_kwargs(group.anilist_id)),
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
                all_entries = await self._db.get_series_group_entries_with_titles(sg_id)
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

        # Seed unmatched shows (scanned but no AniList match).  Create
        # placeholder rows so the library contains every folder, matching
        # how LibraryScanner handles unmatched shows during re-index.
        for si in plan.unmatched_shows:
            if not si.local_path:
                continue
            folder_name = os.path.basename(si.local_path.rstrip("/"))
            await self._db.upsert_library_item(
                library_id=library_id,
                folder_path=si.local_path,
                folder_name=folder_name,
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
        plan_id: int | None = None,
    ) -> dict[str, int]:
        """Execute the restructure plan — move/rename files."""
        if plan.operation_level == "full_restructure":
            return await self._execute_full_restructure(plan, progress, plan_id)
        return await self._execute_rename(plan, progress, plan_id)

    async def _execute_full_restructure(
        self,
        plan: RestructurePlan,
        progress: RestructureProgress,
        plan_id: int | None = None,
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
                        plan_id=plan_id,
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
                        plan_id=plan_id,
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

            # Prune media-free subdirectories within the target folder.
            # Handles cases like an old "Season 1/" subfolder whose files were
            # all moved out flat — leaving only cover art / metadata behind.
            # Never removes the target root itself.
            if os.path.isdir(group.target_folder):
                _media_exts = frozenset(
                    {
                        ".mkv",
                        ".mp4",
                        ".avi",
                        ".m4v",
                        ".mov",
                        ".wmv",
                        ".ts",
                        ".flv",
                        ".webm",
                        ".m2ts",
                        ".mpg",
                        ".mpeg",
                    }
                )

                def _subdir_has_media(dirpath: str) -> bool:
                    for _root, _, _files in os.walk(dirpath):
                        if any(
                            os.path.splitext(f)[1].lower() in _media_exts
                            for f in _files
                        ):
                            return True
                    return False

                for _entry in os.listdir(group.target_folder):
                    _sub = os.path.join(group.target_folder, _entry)
                    if not os.path.isdir(_sub):
                        continue
                    if not _subdir_has_media(_sub):
                        try:
                            shutil.rmtree(_sub)
                            logger.info("Pruned media-free subfolder: %s", _sub)
                        except OSError as _exc:
                            logger.warning(
                                "Could not prune subfolder %s: %s", _sub, _exc
                            )

            # Write tvshow.nfo so Jellyfin classifies this folder as a TV show
            # rather than grouping its contents as movie versions.
            if os.path.isdir(group.target_folder):
                _write_tvshow_nfo(group.target_folder, group.display_title)

            stats["groups"] += 1

        return stats

    async def _execute_rename(
        self,
        plan: RestructurePlan,
        progress: RestructureProgress,
        plan_id: int | None = None,
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
                        plan_id=plan_id,
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
                        plan_id=plan_id,
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
                        plan_id=plan_id,
                    )
                    stats["errors"] += 1
                    progress.processed += 1
                    continue  # Skip file renames if folder rename failed

            progress.processed += 1

            # Rename season folders first (so file paths under them are
            # correct), then rename individual files.
            # Use the is_dir flag set during analysis — checking
            # os.path.isdir(fm.source) at execution time is unreliable
            # because the parent folder may already have been renamed.
            dir_renames = [fm for fm in group.file_moves if fm.is_dir]
            file_renames = [fm for fm in group.file_moves if not fm.is_dir]

            # Track season folder renames so file source paths can be adjusted
            dir_rename_map: dict[str, str] = {}
            for fm in dir_renames:
                try:
                    actual_src = (
                        os.path.join(target_folder, fm.original_filename)
                        if folder_renamed
                        else fm.source
                    )
                    if not os.path.isdir(actual_src):
                        raise FileNotFoundError(f"Source folder missing: {actual_src}")
                    os.rename(actual_src, fm.destination)
                    dir_rename_map[fm.original_filename] = fm.renamed_filename
                    await self._db.log_restructure_operation(
                        group_title=group.display_title,
                        source_path=actual_src,
                        destination_path=fm.destination,
                        operation="season_folder_rename",
                        status="success",
                        plan_id=plan_id,
                    )
                    stats["files_moved"] += 1
                except Exception as exc:
                    logger.error(
                        "Failed to rename season folder %s: %s",
                        fm.source,
                        exc,
                    )
                    await self._db.log_restructure_operation(
                        group_title=group.display_title,
                        source_path=fm.source,
                        destination_path=fm.destination,
                        operation="season_folder_rename",
                        status="error",
                        error_message=str(exc),
                        plan_id=plan_id,
                    )
                    stats["errors"] += 1
                progress.processed += 1

            for fm in file_renames:
                try:
                    # Adjust source path if folder was just renamed
                    actual_src = fm.source
                    if folder_renamed:
                        # Replace old show folder with renamed show folder
                        rel = os.path.relpath(fm.source, src_folder)
                        actual_src = os.path.join(target_folder, rel)
                    # Also adjust for season folder renames
                    for old_dir, new_dir in dir_rename_map.items():
                        actual_src = actual_src.replace(
                            os.sep + old_dir + os.sep,
                            os.sep + new_dir + os.sep,
                        )
                    if not os.path.isfile(actual_src):
                        raise FileNotFoundError(f"Source file missing: {actual_src}")

                    # Adjust destination for season folder renames too
                    actual_dest = fm.destination
                    for old_dir, new_dir in dir_rename_map.items():
                        actual_dest = actual_dest.replace(
                            os.sep + old_dir + os.sep,
                            os.sep + new_dir + os.sep,
                        )
                    # Ensure destination directory exists
                    dest_parent = os.path.dirname(actual_dest)
                    if not os.path.isdir(dest_parent):
                        os.makedirs(dest_parent, exist_ok=True)

                    os.rename(actual_src, actual_dest)
                    await self._db.log_restructure_operation(
                        group_title=group.display_title,
                        source_path=actual_src,
                        destination_path=actual_dest,
                        operation="file_rename",
                        status="success",
                        plan_id=plan_id,
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
                        plan_id=plan_id,
                    )
                    stats["errors"] += 1

                progress.processed += 1

            # Delete stale metadata/artwork files only when actual changes
            # were applied (folder renamed or files renamed).  Never delete
            # if no renames happened for this group.
            group_had_changes = folder_renamed or len(file_renames) > 0
            if group_had_changes:
                cleanup_dir = target_folder if folder_renamed else src_folder
                deleted = _delete_support_files(cleanup_dir)
                if deleted:
                    logger.info(
                        "Deleted %d support files from %s", deleted, cleanup_dir
                    )

            # Write tvshow.nfo so Jellyfin classifies this folder as a TV show.
            if os.path.isdir(target_folder):
                _write_tvshow_nfo(target_folder, group.display_title)

            stats["groups"] += 1

        return stats
