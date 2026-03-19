"""Comprehensive tests for src/Matching/Normalizer.py functions."""

from __future__ import annotations

import pytest

from src.Matching.Normalizer import (
    clean_title_for_search,
    extract_base_series_title,
    extract_base_title,
    extract_year_from_name,
    normalize_title,
    strip_bracket_tags,
)


# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------


class TestNormalizeTitle:
    @pytest.mark.parametrize(
        "title, expected",
        [
            # Basic lowercasing
            ("Attack on Titan", "attack on titan"),
            # Dub/sub tags removed
            ("Naruto (Dub)", "naruto"),
            ("Naruto (Sub)", "naruto"),
            ("One Piece (DUB)", "one piece"),
            # Year suffix removed
            ("Steins;Gate (2011)", "steins gate"),
            # Special characters stripped (non-word except - : ! ?)
            ("Fate/Stay Night", "fate stay night"),
            ("Sword Art Online #2", "sword art online 2"),
            # Keeps allowed special chars
            ("Re:Zero", "re:zero"),
            ("Is It Wrong!?", "is it wrong!?"),
            ("My-Hero", "my-hero"),
            # Unicode characters
            ("Kimi no Na wa\u3002", "kimi no na wa"),
            # Empty string
            ("", ""),
            # Whitespace only
            ("   ", ""),
            # Multiple spaces collapsed
            ("  Attack   on   Titan  ", "attack on titan"),
        ],
    )
    def test_normalize_title(self, title: str, expected: str) -> None:
        assert normalize_title(title) == expected


# ---------------------------------------------------------------------------
# extract_base_title
# ---------------------------------------------------------------------------


class TestExtractBaseTitle:
    @pytest.mark.parametrize(
        "title, expected",
        [
            # Season N
            ("Attack on Titan Season 2", "Attack on Titan"),
            ("My Hero Academia Season 3", "My Hero Academia"),
            # Nth Season
            ("My Hero Academia 2nd Season", "My Hero Academia"),
            ("My Hero Academia 3rd Season", "My Hero Academia"),
            ("Bungou Stray Dogs 4th Season", "Bungou Stray Dogs"),
            # Part N
            ("Attack on Titan Part 2", "Attack on Titan"),
            ("JoJo Part 3", "JoJo"),
            # Roman numerals
            ("Mushoku Tensei II", "Mushoku Tensei"),
            ("Overlord III", "Overlord"),
            ("JoJo IV", "JoJo"),
            # S-prefix season
            ("Demon Slayer S2", "Demon Slayer"),
            # Trailing number
            ("Mob Psycho 100 2", "Mob Psycho 100"),
            # No season indicator (unchanged)
            ("Spirited Away", "Spirited Away"),
            # Combined (Season removes first, trailing number removes second)
            ("Title Season 2 Part 3", "Title"),
        ],
    )
    def test_extract_base_title(self, title: str, expected: str) -> None:
        assert extract_base_title(title) == expected


# ---------------------------------------------------------------------------
# extract_year_from_name
# ---------------------------------------------------------------------------


class TestExtractYearFromName:
    @pytest.mark.parametrize(
        "title, expected",
        [
            # Bracketed years
            ("Steins;Gate [2011]", 2011),
            ("Naruto (2002)", 2002),
            ("Bleach {2004}", 2004),
            # Trailing year after separator
            ("Demon Slayer - 2019", 2019),
            ("One Punch Man.2015", 2015),
            # Bare trailing year
            ("Attack on Titan 2013", 2013),
            # No year present
            ("Spirited Away", 0),
            # Multiple years returns first (bracketed wins)
            ("Show [2020] Extra (2021)", 2020),
            # Year out of valid range
            ("Old Show [1800]", 0),
            ("Future Show [2200]", 0),
        ],
    )
    def test_extract_year_from_name(self, title: str, expected: int) -> None:
        assert extract_year_from_name(title) == expected


# ---------------------------------------------------------------------------
# strip_bracket_tags
# ---------------------------------------------------------------------------


class TestStripBracketTags:
    @pytest.mark.parametrize(
        "title, expected",
        [
            # Year in brackets
            ("Naruto [2002]", "Naruto"),
            ("Naruto (2002)", "Naruto"),
            ("Naruto {2002}", "Naruto"),
            # Quality tags
            ("Naruto [1080p]", "Naruto"),
            ("Naruto [720p HEVC]", "Naruto"),
            # Mixed tags
            ("Naruto [2002] [1080p]", "Naruto"),
            # Trailing year after separator
            ("Demon Slayer - 2019", "Demon Slayer"),
            ("One Piece.2022", "One Piece"),
            # No tags (unchanged)
            ("Attack on Titan", "Attack on Titan"),
            # BD tag in parens (year pattern)
            ("Show (2020)", "Show"),
        ],
    )
    def test_strip_bracket_tags(self, title: str, expected: str) -> None:
        assert strip_bracket_tags(title) == expected


# ---------------------------------------------------------------------------
# clean_title_for_search
# ---------------------------------------------------------------------------


class TestCleanTitleForSearch:
    @pytest.mark.parametrize(
        "title, expected",
        [
            # Season removed
            ("Demon Slayer Season 2", "Demon Slayer"),
            ("Demon Slayer - Season 2", "Demon Slayer"),
            # Part removed
            ("Attack on Titan Part 2", "Attack on Titan"),
            ("Attack on Titan - Part 2", "Attack on Titan"),
            # Nth Season removed
            ("My Hero Academia 2nd Season", "My Hero Academia"),
            ("Bungou Stray Dogs 4th Season", "Bungou Stray Dogs"),
            # S-prefix removed
            ("Demon Slayer S2", "Demon Slayer"),
            # Bracket tags also removed
            ("Naruto [2002] Season 2", "Naruto"),
            # No indicators (unchanged)
            ("Spirited Away", "Spirited Away"),
            # Trailing year removed
            ("One Piece - 2020", "One Piece"),
        ],
    )
    def test_clean_title_for_search(self, title: str, expected: str) -> None:
        assert clean_title_for_search(title) == expected


# ---------------------------------------------------------------------------
# extract_base_series_title
# ---------------------------------------------------------------------------


class TestExtractBaseSeriesTitle:
    @pytest.mark.parametrize(
        "title, expected",
        [
            # Season via colon-separator
            ("Demon Slayer: Season 2", "Demon Slayer"),
            # Part via space
            ("Attack on Titan Part 2", "Attack on Titan"),
            # Nth Season
            ("Mob Psycho 100 2nd Season", "Mob Psycho 100"),
            # Roman numerals
            ("Mushoku Tensei II", "Mushoku Tensei"),
            ("Overlord III", "Overlord"),
            # Arc/Cour via colon-separator
            ("One Piece: Arc 3 Saga", "One Piece"),
            # Subtitle via colon (stripped to main title)
            ("Jujutsu Kaisen: Shimetsu Kaiyuu", "Jujutsu Kaisen"),
            # Short main title before colon kept if >= 3 chars
            ("JoJo: Stardust Crusaders", "JoJo"),
            # No indicators (unchanged)
            ("Spirited Away", "Spirited Away"),
            # Season after dash
            ("Title - Season 2 Extra", "Title"),
        ],
    )
    def test_extract_base_series_title(self, title: str, expected: str) -> None:
        assert extract_base_series_title(title) == expected
