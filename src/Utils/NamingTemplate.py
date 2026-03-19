"""Configurable naming template system for file and folder names."""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Quality parsing
# ---------------------------------------------------------------------------

_RE_RESOLUTION = re.compile(r"\b(2160p|1080p|720p|480p|4K)\b", re.IGNORECASE)
_RE_SOURCE = re.compile(
    r"\b(BluRay|Blu-?Ray|BDRip|WEB-DL|WEBRip|WEB|HDTV|DVDRip)\b", re.IGNORECASE
)
_RE_CODEC = re.compile(r"\b(x265|H\.?265|HEVC|x264|H\.?264|AV1|AVC)\b", re.IGNORECASE)

_SOURCE_NORMALIZE: dict[str, str] = {
    "blu-ray": "BluRay",
    "bluray": "BluRay",
    "bdrip": "BluRay",
    "web-dl": "WEB-DL",
    "webrip": "WEBRip",
    "web": "WEB",
    "hdtv": "HDTV",
    "dvdrip": "DVDRip",
}

_CODEC_NORMALIZE: dict[str, str] = {
    "h.265": "HEVC",
    "h265": "HEVC",
    "hevc": "HEVC",
    "x265": "x265",
    "h.264": "x264",
    "h264": "x264",
    "x264": "x264",
    "avc": "x264",
    "av1": "AV1",
}

_RESOLUTION_NORMALIZE: dict[str, str] = {
    "4k": "2160p",
}


@dataclass
class QualityInfo:
    """Parsed quality metadata from a filename."""

    resolution: str = ""
    source: str = ""
    codec: str = ""

    @property
    def full(self) -> str:
        return " ".join(p for p in [self.resolution, self.source, self.codec] if p)


def parse_quality(filename: str) -> QualityInfo:
    """Extract quality information (resolution, source, codec) from a filename."""
    resolution = ""
    source = ""
    codec = ""

    m = _RE_RESOLUTION.search(filename)
    if m:
        raw = m.group(1).lower()
        resolution = _RESOLUTION_NORMALIZE.get(raw, m.group(1))
        # Normalize casing: ensure "p" suffix is lowercase
        if resolution.endswith("P"):
            resolution = resolution[:-1] + "p"

    m = _RE_SOURCE.search(filename)
    if m:
        source = _SOURCE_NORMALIZE.get(m.group(1).lower(), m.group(1))

    m = _RE_CODEC.search(filename)
    if m:
        codec = _CODEC_NORMALIZE.get(m.group(1).lower(), m.group(1))

    return QualityInfo(resolution=resolution, source=source, codec=codec)


# ---------------------------------------------------------------------------
# Credentials.md template
# ---------------------------------------------------------------------------

# Regex to find {token} or {token.sub} placeholders
_TOKEN_RE = re.compile(r"\{([a-z]+(?:\.[a-z]+)?)\}")

# Characters not allowed in filenames
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')

# Empty bracket/paren pairs after token substitution
_EMPTY_BRACKETS = re.compile(r"\[\s*\]|\(\s*\)")

# Repeated separators ( - , ., multiple spaces)
_REPEATED_SEP = re.compile(r"(\s*-\s*){2,}")
_REPEATED_DOTS = re.compile(r"\.{2,}")
_REPEATED_SPACES = re.compile(r" {2,}")

# Trailing separators
_TRAILING_SEP = re.compile(r"[\s.\-]+$")
_LEADING_SEP = re.compile(r"^[\s.\-]+")


class NamingTemplate:
    """Renders naming templates with token substitution and cleanup."""

    def __init__(self, template: str) -> None:
        self._template = template

    @property
    def template(self) -> str:
        return self._template

    def render(self, tokens: dict[str, str]) -> str:
        """Substitute tokens and clean up the result."""

        def _replace(m: re.Match[str]) -> str:
            key = m.group(1)
            return tokens.get(key, "")

        result = _TOKEN_RE.sub(_replace, self._template)
        return self._cleanup(result)

    @staticmethod
    def _cleanup(text: str) -> str:
        """Remove empty brackets, collapse separators, strip edges."""
        text = _EMPTY_BRACKETS.sub("", text)
        text = _REPEATED_SEP.sub(" - ", text)
        text = _REPEATED_DOTS.sub(".", text)
        text = _REPEATED_SPACES.sub(" ", text)
        text = _TRAILING_SEP.sub("", text)
        text = _LEADING_SEP.sub("", text)
        return text.strip()

    @staticmethod
    def sanitize(text: str, replacement: str = "") -> str:
        """Remove or replace filesystem-unsafe characters."""
        return _UNSAFE_CHARS.sub(replacement, text).strip()


# ---------------------------------------------------------------------------
# Presets and defaults
# ---------------------------------------------------------------------------

DEFAULT_ILLEGAL_CHAR_REPLACEMENT = (
    ""  # "" = remove, "-" = hyphen, " " = space, "_" = underscore
)

DEFAULT_FILE_TEMPLATE = "{title} - S{season}E{episode}"
DEFAULT_FOLDER_TEMPLATE = "{title}"
DEFAULT_SEASON_FOLDER_TEMPLATE = "Season {season}"
DEFAULT_MOVIE_FILE_TEMPLATE = "{title} [{year}]"

FORMAT_SHORT: dict[str, str] = {
    "TV": "TV",
    "MOVIE": "Movie",
    "OVA": "OVA",
    "ONA": "ONA",
    "SPECIAL": "Special",
    "TV_SHORT": "Short",
    "MUSIC": "Music",
}

NAMING_PRESETS: dict[str, dict[str, str]] = {
    "standard": {
        "file": "{title} - S{season}E{episode}",
        "folder": "{title}",
        "season_folder": "Season {season}",
        "movie_file": "{title} [{year}]",
    },
    "with_year": {
        "file": "{title} [{year}] - S{season}E{episode}",
        "folder": "{title} [{year}]",
        "season_folder": "Season {season}",
        "movie_file": "{title} [{year}]",
    },
    "with_quality": {
        "file": "{title} [{year}] - S{season}E{episode} [{quality}]",
        "folder": "{title} [{year}]",
        "season_folder": "Season {season}",
        "movie_file": "{title} [{year}] [{quality}]",
    },
    "dots": {
        "file": "{title}.S{season}E{episode}.{quality.resolution}",
        "folder": "{title}.({year})",
        "season_folder": "Season.{season}",
        "movie_file": "{title}.({year})",
    },
}
