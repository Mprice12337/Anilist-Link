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
        r"[^\w\s\-:!?]",
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


def clean_title_for_search(title: str) -> str:
    """Remove season/part qualifiers so AniList returns broader results."""
    clean = re.sub(r"\s*-?\s*Season\s*\d+", "", title, flags=re.IGNORECASE)
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
