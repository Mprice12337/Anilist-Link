"""Anime-specific title normalization utilities.

Ported from the original Crunchyroll-Anilist-Sync AnimeMatcher and SyncManager.
"""

from __future__ import annotations

import re


def normalize_title(title: str) -> str:
    """Normalize a title for fuzzy matching.

    Lowercases, strips dub/sub tags, year suffixes, and non-word characters.
    """
    if not title:
        return ""

    normalized = title.lower()

    patterns_to_remove = [
        r"\s*\(dub\)\s*",
        r"\s*\(sub\)\s*",
        r"\s*\(\d{4}\)\s*$",
        r"[^\w\s\-!?]",  # Strip non-word chars (colons removed here too)
    ]

    for pattern in patterns_to_remove:
        normalized = re.sub(pattern, " ", normalized)

    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def extract_base_title(title: str) -> str:
    """Strip season / part indicators from a title."""
    base = title

    patterns_to_remove = [
        r"Season\s*\d+",
        r"\d+(?:st|nd|rd|th)?\s*Season",
        r"\bS\d+\b",
        r"Part\s*\d+",
        r"\b(?:II|III|IV|V|VI)\b",
        r"\s+\d+$",
    ]

    for pattern in patterns_to_remove:
        base = re.sub(pattern, "", base, flags=re.IGNORECASE)

    return base.strip()


def extract_year_from_name(title: str) -> int:
    """Extract a year from a folder/file name. Returns 0 if none found.

    Recognises these formats (in priority order):
      [2020]  (2020)  {2020}          — bracketed
      Title - 2020  Title.2020        — separator then year
      Title 2020                      — bare year at end of string
    """
    # 1. Bracketed: [2020] (2020) {2020}
    m = re.search(r"[\[\(\{](\d{4})[\]\)\}]", title)
    if m:
        year = int(m.group(1))
        if 1950 <= year <= 2100:
            return year

    # 2. After a separator (- or .) near the end, or bare trailing year
    m = re.search(r"(?:[\s.\-]+)(\d{4})\s*$", title)
    if m:
        year = int(m.group(1))
        if 1950 <= year <= 2100:
            return year

    return 0


def strip_bracket_tags(title: str) -> str:
    """Strip only bracketed year/quality tags, preserving season qualifiers."""
    clean = title
    clean = re.sub(r"\s*\[\d{4}\]", "", clean)
    clean = re.sub(r"\s*\(\d{4}\)", "", clean)
    clean = re.sub(r"\s*\{\d{4}\}", "", clean)
    clean = re.sub(r"\s*\[\d{3,4}p[^\]]*\]", "", clean, flags=re.IGNORECASE)
    # Strip bare trailing year (e.g. "Title - 2020", "Title.2020", "Title 2020")
    clean = re.sub(r"[\s.\-]+\d{4}\s*$", "", clean)
    return clean.strip()


def clean_title_for_search(title: str) -> str:
    """Remove season/part qualifiers and bracketed tags for broader AniList results."""
    clean = strip_bracket_tags(title)
    # Strip season/part qualifiers
    clean = re.sub(r"\s*-?\s*Season\s*\d+", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*-?\s*S\d+", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*-?\s*Part\s*\d+", "", clean, flags=re.IGNORECASE)
    clean = re.sub(
        r"\s*-?\s*\d+(?:st|nd|rd|th)\s*Season", "", clean, flags=re.IGNORECASE
    )
    return clean.strip()


def extract_base_series_title(title: str) -> str:
    """Extract the base series name without season/part/arc/subtitle indicators."""
    base = title

    patterns = [
        r"\s*[-:]\s*.*(?:Season|Part)\s*\d+.*$",
        r"\s+(?:Season|Part)\s*\d+.*$",
        r"\s+\d+(?:st|nd|rd|th)\s+Season.*$",
        r"\s+(?:II|III|IV|V|VI)(?:\s|$).*$",
        r"\s*[-:]\s*.*(?:Cour|Arc)\s*\d+.*$",
    ]

    for pattern in patterns:
        base = re.sub(pattern, "", base, flags=re.IGNORECASE)

    # For titles with colons (subtitles/arcs), extract just the main title.
    # e.g. "Jujutsu Kaisen: Shimetsu Kaiyuu" -> "Jujutsu Kaisen"
    if ":" in base:
        parts = base.split(":", 1)
        main_part = parts[0].strip()
        # Only use the main part if it's substantial (>= 3 chars)
        # to avoid stripping important parts like "Re:Zero"
        if len(main_part) >= 3:
            base = main_part

    return base.strip()
