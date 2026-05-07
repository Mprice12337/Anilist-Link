"""Comprehensive unit tests for the TitleMatcher module."""

from __future__ import annotations

import pytest

from src.Matching.TitleMatcher import TitleMatcher, get_primary_title

# ======================================================================
# Helpers — realistic AniList-style candidate factory
# ======================================================================


def _make_candidate(
    id: int,
    romaji: str | None = None,
    english: str | None = None,
    native: str | None = None,
    synonyms: list[str] | None = None,
    format: str = "TV",
    episodes: int = 12,
    season_year: int | None = 2022,
    year: int | None = None,
    month: int | None = 1,
    day: int | None = 1,
) -> dict:
    """Build an AniList-style candidate dict."""
    y = year if year is not None else season_year
    return {
        "id": id,
        "title": {
            "romaji": romaji,
            "english": english,
            "native": native,
        },
        "synonyms": synonyms or [],
        "format": format,
        "episodes": episodes,
        "seasonYear": season_year,
        "startDate": {"year": y, "month": month, "day": day},
        "relations": {"edges": []},
    }


# ======================================================================
# 1. get_primary_title — standalone function
# ======================================================================


class TestGetPrimaryTitle:
    """Tests for the module-level get_primary_title helper."""

    @pytest.mark.parametrize(
        "anime, expected",
        [
            pytest.param(
                {
                    "title": {
                        "romaji": "Shingeki no Kyojin",
                        "english": None,
                        "native": None,
                    }
                },
                "Shingeki no Kyojin",
                id="romaji_only",
            ),
            pytest.param(
                {
                    "title": {
                        "romaji": None,
                        "english": "Attack on Titan",
                        "native": None,
                    }
                },
                "Attack on Titan",
                id="english_only",
            ),
            pytest.param(
                {
                    "title": {
                        "romaji": "Shingeki no Kyojin",
                        "english": "Attack on Titan",
                        "native": None,
                    }
                },
                "Shingeki no Kyojin",
                id="both_romaji_preferred",
            ),
            pytest.param(
                {
                    "title": {
                        "romaji": None,
                        "english": None,
                        "native": "\u9032\u6483\u306e\u5de8\u4eba",
                    }
                },
                "\u9032\u6483\u306e\u5de8\u4eba",
                id="native_only",
            ),
            pytest.param(
                {"title": {"romaji": None, "english": None, "native": None}},
                "Unknown",
                id="all_none",
            ),
            pytest.param(
                {"title": {"romaji": "", "english": "", "native": ""}},
                "Unknown",
                id="all_empty_strings",
            ),
            pytest.param(
                {},
                "Unknown",
                id="no_title_key",
            ),
            pytest.param(
                {"title": "Flat String Title"},
                "Flat String Title",
                id="title_is_plain_string",
            ),
        ],
    )
    def test_get_primary_title(self, anime: dict, expected: str) -> None:
        assert get_primary_title(anime) == expected


# ======================================================================
# 2. TitleMatcher.calculate_title_similarity
# ======================================================================


class TestCalculateTitleSimilarity:
    """Tests for calculate_title_similarity."""

    def setup_method(self) -> None:
        self.matcher = TitleMatcher()

    def test_exact_match_romaji(self) -> None:
        candidate = _make_candidate(1, romaji="Mob Psycho 100")
        score = self.matcher.calculate_title_similarity("Mob Psycho 100", candidate)
        assert score >= 0.99

    def test_exact_match_english(self) -> None:
        candidate = _make_candidate(2, english="Attack on Titan")
        score = self.matcher.calculate_title_similarity("Attack on Titan", candidate)
        assert score >= 0.99

    def test_completely_different(self) -> None:
        candidate = _make_candidate(3, romaji="Naruto")
        score = self.matcher.calculate_title_similarity(
            "Fullmetal Alchemist Brotherhood", candidate
        )
        assert score < 0.3

    def test_partial_match(self) -> None:
        candidate = _make_candidate(
            4, romaji="Jujutsu Kaisen 2nd Season", english="Jujutsu Kaisen Season 2"
        )
        score = self.matcher.calculate_title_similarity("Jujutsu Kaisen", candidate)
        assert 0.4 < score < 1.0

    def test_synonym_matching(self) -> None:
        candidate = _make_candidate(
            5,
            romaji="Ore no Imouto ga Konnani Kawaii Wake ga Nai",
            english="My Little Sister Can't Be This Cute",
            synonyms=["Oreimo"],
        )
        score = self.matcher.calculate_title_similarity("Oreimo", candidate)
        assert score >= 0.9

    def test_match_via_native_title(self) -> None:
        candidate = _make_candidate(
            6, romaji="Bleach", native="\u30d6\u30ea\u30fc\u30c1"
        )
        score = self.matcher.calculate_title_similarity(
            "\u30d6\u30ea\u30fc\u30c1", candidate
        )
        assert score >= 0.99

    def test_case_insensitive(self) -> None:
        candidate = _make_candidate(7, romaji="Spy x Family")
        score = self.matcher.calculate_title_similarity("spy x family", candidate)
        assert score >= 0.95

    def test_dub_tag_ignored(self) -> None:
        candidate = _make_candidate(8, romaji="One Punch Man")
        score = self.matcher.calculate_title_similarity(
            "One Punch Man (Dub)", candidate
        )
        assert score >= 0.9

    def test_empty_target(self) -> None:
        candidate = _make_candidate(9, romaji="Bleach")
        score = self.matcher.calculate_title_similarity("", candidate)
        assert score == 0.0

    def test_candidate_with_all_empty_titles(self) -> None:
        candidate = _make_candidate(10)
        score = self.matcher.calculate_title_similarity("Anything", candidate)
        assert score == 0.0


# ======================================================================
# 3. TitleMatcher.find_best_match_with_season
# ======================================================================


class TestFindBestMatchWithSeason:
    """Tests for find_best_match_with_season."""

    def setup_method(self) -> None:
        self.matcher = TitleMatcher(similarity_threshold=0.75)

    # -- basic matching --

    def test_single_candidate_good_match(self) -> None:
        candidates = [_make_candidate(1, romaji="Vinland Saga", english="Vinland Saga")]
        result = self.matcher.find_best_match_with_season("Vinland Saga", candidates)
        assert result is not None
        entry, similarity, season = result
        assert entry["id"] == 1
        assert similarity >= 0.75
        assert season == 1

    def test_multiple_candidates_picks_best(self) -> None:
        candidates = [
            _make_candidate(1, romaji="Naruto", english="Naruto"),
            _make_candidate(2, romaji="Naruto: Shippuuden", english="Naruto Shippuden"),
            _make_candidate(3, romaji="Boruto: Naruto Next Generations"),
        ]
        result = self.matcher.find_best_match_with_season("Naruto", candidates)
        assert result is not None
        entry, _, _ = result
        assert entry["id"] == 1

    # -- season detection from title --

    def test_season_2_detection(self) -> None:
        candidates = [
            _make_candidate(
                1,
                romaji="Mushoku Tensei",
                english="Mushoku Tensei: Jobless Reincarnation",
                episodes=11,
                season_year=2021,
            ),
            _make_candidate(
                2,
                romaji="Mushoku Tensei 2nd Season",
                english="Mushoku Tensei: Jobless Reincarnation Season 2",
                episodes=12,
                season_year=2023,
            ),
        ]
        result = self.matcher.find_best_match_with_season(
            "Mushoku Tensei", candidates, target_season=2
        )
        assert result is not None
        entry, _, season = result
        assert entry["id"] == 2
        assert season == 2

    def test_season_3_via_part(self) -> None:
        candidates = [
            _make_candidate(
                1,
                romaji="Re:Zero kara Hajimeru Isekai Seikatsu",
                episodes=25,
                season_year=2016,
            ),
            _make_candidate(
                2,
                romaji="Re:Zero kara Hajimeru Isekai Seikatsu Part 2",
                episodes=25,
                season_year=2021,
            ),
            _make_candidate(
                3,
                romaji="Re:Zero kara Hajimeru Isekai Seikatsu Part 3",
                episodes=16,
                season_year=2024,
            ),
        ]
        result = self.matcher.find_best_match_with_season(
            "Re:Zero kara Hajimeru Isekai Seikatsu", candidates, target_season=3
        )
        assert result is not None
        entry, _, season = result
        assert entry["id"] == 3
        assert season == 3

    def test_season_detection_roman_numeral(self) -> None:
        candidates = [
            _make_candidate(1, romaji="Overlord", episodes=13, season_year=2015),
            _make_candidate(2, romaji="Overlord II", episodes=13, season_year=2018),
            _make_candidate(3, romaji="Overlord III", episodes=13, season_year=2018),
        ]
        result = self.matcher.find_best_match_with_season(
            "Overlord", candidates, target_season=3
        )
        assert result is not None
        entry, _, season = result
        assert entry["id"] == 3
        assert season == 3

    # -- year_hint disambiguation --

    def test_year_hint_boosts_correct_year(self) -> None:
        candidates = [
            _make_candidate(
                1,
                romaji="Uzaki-chan wa Asobitai!",
                english="Uzaki-chan Wants to Hang Out!",
                episodes=12,
                season_year=2020,
            ),
            _make_candidate(
                2,
                romaji="Uzaki-chan wa Asobitai! Double",
                english="Uzaki-chan Wants to Hang Out! Double",
                episodes=13,
                season_year=2022,
            ),
        ]
        result = self.matcher.find_best_match_with_season(
            "Uzaki-chan wa Asobitai!", candidates, year_hint=2022
        )
        assert result is not None
        entry, _, _ = result
        assert entry["id"] == 2

    def test_year_hint_zero_no_effect(self) -> None:
        """year_hint=0 (default) should not alter scoring."""
        candidates = [_make_candidate(1, romaji="Spy x Family", season_year=2022)]
        result = self.matcher.find_best_match_with_season(
            "Spy x Family", candidates, year_hint=0
        )
        assert result is not None

    def test_year_hint_penalizes_distant_year(self) -> None:
        candidates = [
            _make_candidate(1, romaji="Hunter x Hunter", episodes=62, season_year=1999),
            _make_candidate(
                2,
                romaji="Hunter x Hunter",
                english="Hunter x Hunter (2011)",
                episodes=148,
                season_year=2011,
            ),
        ]
        result = self.matcher.find_best_match_with_season(
            "Hunter x Hunter", candidates, year_hint=2011
        )
        assert result is not None
        entry, _, _ = result
        assert entry["id"] == 2

    # -- threshold filtering --

    def test_below_threshold_returns_none(self) -> None:
        candidates = [_make_candidate(1, romaji="Completely Unrelated Show")]
        result = self.matcher.find_best_match_with_season("Dragon Ball Z", candidates)
        assert result is None

    def test_custom_threshold(self) -> None:
        matcher_strict = TitleMatcher(similarity_threshold=0.95)
        candidates = [
            _make_candidate(1, romaji="Mob Psycho 100", english="Mob Psycho 100")
        ]
        result = matcher_strict.find_best_match_with_season("Mob Psycho", candidates)
        # "Mob Psycho" vs "Mob Psycho 100" — close but may not hit 0.95 + season boost
        # The test validates that threshold is respected; result may or may not match.
        if result is not None:
            _, similarity, _ = result
            assert similarity >= 0.95

    # -- include_all_formats --

    def test_movie_format_excluded_by_default(self) -> None:
        candidates = [
            _make_candidate(1, romaji="Suzume no Tojimari", format="MOVIE"),
        ]
        result = self.matcher.find_best_match_with_season(
            "Suzume no Tojimari", candidates
        )
        assert result is None

    def test_movie_format_included_when_flag_set(self) -> None:
        candidates = [
            _make_candidate(1, romaji="Suzume no Tojimari", format="MOVIE"),
        ]
        result = self.matcher.find_best_match_with_season(
            "Suzume no Tojimari", candidates, include_all_formats=True
        )
        assert result is not None
        assert result[0]["id"] == 1

    def test_ova_excluded_by_default(self) -> None:
        candidates = [
            _make_candidate(1, romaji="Attack on Titan OVA", format="OVA", episodes=5),
        ]
        result = self.matcher.find_best_match_with_season(
            "Attack on Titan OVA", candidates
        )
        assert result is None

    def test_special_excluded_by_default(self) -> None:
        candidates = [
            _make_candidate(
                1, romaji="Demon Slayer Special", format="SPECIAL", episodes=1
            ),
        ]
        result = self.matcher.find_best_match_with_season(
            "Demon Slayer Special", candidates
        )
        assert result is None

    def test_include_all_formats_with_ova(self) -> None:
        candidates = [
            _make_candidate(1, romaji="Attack on Titan OVA", format="OVA", episodes=5),
        ]
        result = self.matcher.find_best_match_with_season(
            "Attack on Titan OVA", candidates, include_all_formats=True
        )
        assert result is not None

    # -- edge cases --

    def test_empty_candidates_returns_none(self) -> None:
        result = self.matcher.find_best_match_with_season("Bleach", [])
        assert result is None

    def test_empty_title_returns_none(self) -> None:
        candidates = [_make_candidate(1, romaji="Bleach")]
        result = self.matcher.find_best_match_with_season("", candidates)
        assert result is None

    def test_none_format_treated_as_non_movie(self) -> None:
        """Candidates with format=None should not be filtered as MOVIE."""
        candidate = _make_candidate(1, romaji="Test Show", format=None)
        candidate["format"] = None
        result = self.matcher.find_best_match_with_season("Test Show", [candidate])
        assert result is not None

    # -- movie matching (target_season=0) --

    def test_movie_match_season_zero(self) -> None:
        candidates = [
            _make_candidate(1, romaji="Kimetsu no Yaiba", format="TV", episodes=26),
            _make_candidate(
                2,
                romaji="Kimetsu no Yaiba: Mugen Ressha-hen",
                english="Demon Slayer: Mugen Train",
                format="MOVIE",
                episodes=1,
            ),
        ]
        result = self.matcher.find_best_match_with_season(
            "Kimetsu no Yaiba Movie", candidates, target_season=0
        )
        assert result is not None
        entry, _, season = result
        assert entry["id"] == 2
        assert season == 0

    def test_movie_match_prefers_movie_over_special(self) -> None:
        candidates = [
            _make_candidate(
                1,
                romaji="Violet Evergarden Movie",
                english="Violet Evergarden: The Movie",
                format="MOVIE",
                episodes=1,
            ),
            _make_candidate(
                2, romaji="Violet Evergarden Special", format="SPECIAL", episodes=1
            ),
        ]
        result = self.matcher.find_best_match_with_season(
            "Violet Evergarden Movie", candidates, target_season=0
        )
        assert result is not None
        entry, _, _ = result
        # MOVIE format gets a +0.15 boost, so should beat SPECIAL
        assert entry["id"] == 1


# ======================================================================
# 4. TitleMatcher._detect_season_from_entry
# ======================================================================


class TestDetectSeasonFromEntry:
    """Tests for _detect_season_from_entry (static method)."""

    @pytest.mark.parametrize(
        "romaji, english, expected_season",
        [
            pytest.param("Boku no Hero Academia 2nd Season", None, 2, id="2nd_season"),
            pytest.param("Boku no Hero Academia 3rd Season", None, 3, id="3rd_season"),
            pytest.param("Boku no Hero Academia 4th Season", None, 4, id="4th_season"),
            pytest.param(None, "My Hero Academia Season 2", 2, id="season_N_english"),
            pytest.param("Shingeki no Kyojin Season 3", None, 3, id="season_N_romaji"),
            pytest.param(
                "Re:Zero kara Hajimeru Isekai Seikatsu Part 2", None, 2, id="part_N"
            ),
            pytest.param("Overlord II", None, 2, id="roman_II"),
            pytest.param("Overlord III", None, 3, id="roman_III"),
            pytest.param("Overlord IV", None, 4, id="roman_IV"),
            pytest.param("Jujutsu Kaisen", None, 1, id="no_season_indicator"),
            pytest.param(
                "Steins;Gate", "Steins;Gate", 1, id="no_indicator_both_titles"
            ),
            pytest.param(None, None, 1, id="no_titles_at_all"),
            pytest.param("Mob Psycho 100", None, 1, id="number_in_title_not_season"),
        ],
    )
    def test_detect_season(
        self, romaji: str | None, english: str | None, expected_season: int
    ) -> None:
        entry = _make_candidate(
            99,
            romaji=romaji,
            english=english,
        )
        assert TitleMatcher._detect_season_from_entry(entry) == expected_season

    def test_roman_numeral_V(self) -> None:
        entry = _make_candidate(100, romaji="Some Anime V")
        assert TitleMatcher._detect_season_from_entry(entry) == 5

    def test_roman_numeral_VI(self) -> None:
        entry = _make_candidate(101, romaji="Some Anime VI")
        assert TitleMatcher._detect_season_from_entry(entry) == 6

    def test_ordinal_1st_season(self) -> None:
        entry = _make_candidate(102, romaji="Some Anime 1st Season")
        assert TitleMatcher._detect_season_from_entry(entry) == 1

    def test_english_title_fallback(self) -> None:
        """When romaji has no indicator but english does, english is used."""
        entry = _make_candidate(
            103, romaji="Kimetsu no Yaiba", english="Demon Slayer Season 3"
        )
        assert TitleMatcher._detect_season_from_entry(entry) == 3


# ======================================================================
# 5. TitleMatcher.build_season_structure
# ======================================================================


class TestBuildSeasonStructure:
    """Tests for build_season_structure season grouping and primary detection."""

    def setup_method(self) -> None:
        self.matcher = TitleMatcher()

    def test_picks_correct_primary_among_substring_collisions(self) -> None:
        """Regression: 'Kaiju No. 8' must beat 'minute! kaiju no. 8'.

        Both groups' titles contain the search title as a substring, so a
        binary primary flag plus dict-iteration order would pick the wrong
        one. The fix scores primary candidates by similarity.
        """
        candidates = [
            _make_candidate(
                1,
                romaji="Kaijuu 8-gou",
                english="Kaiju No. 8",
                episodes=12,
                season_year=2024,
                year=2024,
                month=4,
            ),
            _make_candidate(
                2,
                romaji="Kaijuu 8-gou 2nd Season",
                english="Kaiju No. 8 Season 2",
                episodes=12,
                season_year=2025,
                year=2025,
                month=7,
            ),
            _make_candidate(
                3,
                romaji="minute! Kaijuu 8-gou",
                english="minute! Kaiju No. 8",
                episodes=31,
                season_year=2024,
                year=2024,
                month=4,
            ),
        ]
        structure = self.matcher.build_season_structure(candidates, "Kaiju No. 8")
        assert 1 in structure
        # Primary group must be the real Kaiju No. 8, not the "minute!" spinoff.
        assert structure[1]["id"] == 1
        assert "minute" not in structure[1]["title"].lower()

    def test_primary_match_via_english_title_when_romaji_differs(self) -> None:
        """When the AniList romaji differs from the search title, the english
        title or synonyms should still flag the entry as a primary match.
        """
        candidates = [
            _make_candidate(
                1,
                romaji="Tsue to Tsurugi no Wistoria",
                english="Wistoria: Wand and Sword",
                episodes=12,
                season_year=2024,
                year=2024,
                month=7,
            ),
            _make_candidate(
                2,
                romaji="Tsue to Tsurugi no Wistoria Season 2",
                english="Wistoria: Wand and Sword Season 2",
                episodes=None,  # currently airing
                season_year=2026,
                year=2026,
                month=4,
            ),
        ]
        structure = self.matcher.build_season_structure(
            candidates, "Wistoria: Wand and Sword"
        )
        assert 1 in structure and 2 in structure
        assert structure[1]["id"] == 1
        assert structure[2]["id"] == 2


# ======================================================================
# 6. TitleMatcher.determine_correct_entry_and_episode
# ======================================================================


class TestDetermineCorrectEntryAndEpisode:
    """Tests for absolute-vs-relative episode mapping."""

    @staticmethod
    def _make_structure(
        seasons: list[tuple[int, str, int | None]],
    ) -> dict[int, dict]:
        """Build a season_structure dict from (season_num, title, episodes)."""
        struct: dict[int, dict] = {}
        for sn, title, eps in seasons:
            entry = _make_candidate(sn * 100, romaji=title, episodes=eps)
            struct[sn] = {
                "entry": entry,
                "episodes": eps,
                "title": title,
                "similarity": 1.0,
                "id": entry["id"],
                "release_order": 20240000 + sn,
            }
        return struct

    def test_direct_match_when_episode_fits(self) -> None:
        """Normal case: cr_episode within S1 max → return cr_season directly."""
        struct = self._make_structure(
            [(1, "Aldnoah.Zero", 12), (2, "Aldnoah.Zero Part 2", 12)]
        )
        entry, season, episode = TitleMatcher.determine_correct_entry_and_episode(
            "Aldnoah.Zero", cr_season=1, cr_episode=5, season_structure=struct
        )
        assert entry is not None
        assert season == 1
        assert episode == 5

    def test_absolute_numbering_cr_season_one(self) -> None:
        """Regression: Aldnoah.Zero S1E16 (absolute) should map to S2E4."""
        struct = self._make_structure(
            [(1, "Aldnoah.Zero", 12), (2, "Aldnoah.Zero Part 2", 12)]
        )
        entry, season, episode = TitleMatcher.determine_correct_entry_and_episode(
            "Aldnoah.Zero", cr_season=1, cr_episode=16, season_structure=struct
        )
        assert entry is not None
        assert season == 2
        assert episode == 4

    def test_absolute_with_unknown_target_season_episodes(self) -> None:
        """Regression: Wistoria S2E15 (absolute, S2 episodes None) → S2E3."""
        struct = self._make_structure(
            [
                (1, "Tsue to Tsurugi no Wistoria", 12),
                (2, "Tsue to Tsurugi no Wistoria Season 2", None),
            ]
        )
        entry, season, episode = TitleMatcher.determine_correct_entry_and_episode(
            "Wistoria: Wand and Sword",
            cr_season=2,
            cr_episode=15,
            season_structure=struct,
        )
        assert entry is not None
        assert season == 2
        assert episode == 3

    def test_relative_numbering_with_unknown_target_episodes(self) -> None:
        """When cr_episode is small and target season eps unknown, trust direct."""
        struct = self._make_structure(
            [
                (1, "Tsue to Tsurugi no Wistoria", 12),
                (2, "Tsue to Tsurugi no Wistoria Season 2", None),
            ]
        )
        entry, season, episode = TitleMatcher.determine_correct_entry_and_episode(
            "Wistoria: Wand and Sword",
            cr_season=2,
            cr_episode=3,
            season_structure=struct,
        )
        assert entry is not None
        assert season == 2
        assert episode == 3

    def test_three_seasons_absolute(self) -> None:
        """Absolute episode 30 with S1=12, S2=12, S3=12 → S3E6."""
        struct = self._make_structure(
            [(1, "Show", 12), (2, "Show Season 2", 12), (3, "Show Season 3", 12)]
        )
        entry, season, episode = TitleMatcher.determine_correct_entry_and_episode(
            "Show", cr_season=1, cr_episode=30, season_structure=struct
        )
        assert season == 3
        assert episode == 6

    def test_empty_structure_returns_none(self) -> None:
        result = TitleMatcher.determine_correct_entry_and_episode(
            "Anything", cr_season=1, cr_episode=1, season_structure={}
        )
        assert result == (None, 0, 0)

    def test_season_one_fallback_when_cr_season_missing(self) -> None:
        """If cr_season isn't in structure and no absolute mapping fits, fall back."""
        struct = self._make_structure([(1, "Show", 12)])
        entry, season, episode = TitleMatcher.determine_correct_entry_and_episode(
            "Show", cr_season=5, cr_episode=3, season_structure=struct
        )
        assert season == 1
        assert episode == 3
