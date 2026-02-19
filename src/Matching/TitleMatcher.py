"""Multi-algorithm fuzzy title matching engine with season awareness.

Ported from the original Crunchyroll-Anilist-Sync AnimeMatcher and
the season-structure builder from SyncManager.  Uses SequenceMatcher
(not rapidfuzz) to preserve the exact matching behaviour of the 99%-tested
original codebase.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any

from src.Matching.Normalizer import (
    clean_title_for_search,
    extract_base_series_title,
    extract_base_title,
    normalize_title,
)

logger = logging.getLogger(__name__)


class TitleMatcher:
    """Matches anime titles between Crunchyroll and AniList with season awareness."""

    MOVIE_FORMATS = ["MOVIE", "SPECIAL", "OVA", "ONA"]

    def __init__(self, similarity_threshold: float = 0.75) -> None:
        self.similarity_threshold = similarity_threshold

    # ------------------------------------------------------------------
    # Public matching API
    # ------------------------------------------------------------------

    def find_best_match_with_season(
        self,
        target_title: str,
        candidates: list[dict[str, Any]],
        target_season: int = 1,
        year_hint: int = 0,
        include_all_formats: bool = False,
    ) -> tuple[dict[str, Any], float, int] | None:
        """Find the best AniList match for *target_title* with season awareness.

        When *year_hint* is provided (e.g. extracted from ``[2022]`` in folder
        name), candidates whose ``seasonYear`` matches get a +0.15 boost while
        those off by 2+ years get a -0.1 penalty.  This helps disambiguate
        multi-season entries like "Uzaki-chan S1 [2020]" vs "S2 [2022]".

        When *include_all_formats* is True, MOVIE/OVA/ONA/SPECIAL formats are
        not filtered out.  Use this for local directory scanning where any
        format may appear as a folder.

        Returns ``(matched_entry, similarity, detected_season)`` or ``None``.
        """
        if not target_title or not candidates:
            return None

        if target_season == 0:
            return self._find_best_movie_match(target_title, candidates)

        best_match: dict[str, Any] | None = None
        best_similarity = 0.0
        best_season = target_season

        for candidate in candidates:
            format_type = (candidate.get("format", "") or "").upper()
            if not include_all_formats and format_type in self.MOVIE_FORMATS:
                continue

            similarity = self.calculate_title_similarity(target_title, candidate)
            detected_season = self._detect_season_from_entry(candidate)

            if detected_season == target_season:
                similarity += 0.1

            # Year-based disambiguation
            if year_hint:
                candidate_year = candidate.get("seasonYear") or (
                    (candidate.get("startDate") or {}).get("year") or 0
                )
                if candidate_year:
                    if candidate_year == year_hint:
                        similarity += 0.15
                    elif abs(candidate_year - year_hint) == 1:
                        pass  # close enough, no adjustment
                    else:
                        similarity -= 0.1

            if similarity > best_similarity and similarity >= self.similarity_threshold:
                best_similarity = similarity
                best_match = candidate
                best_season = detected_season

        if best_match:
            anime_title = get_primary_title(best_match)
            logger.info(
                "Matched '%s' to '%s' S%d (similarity: %.2f)",
                target_title,
                anime_title,
                best_season,
                best_similarity,
            )
            return best_match, best_similarity, best_season

        return None

    # ------------------------------------------------------------------
    # Movie matching
    # ------------------------------------------------------------------

    def _find_best_movie_match(
        self,
        target_title: str,
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], float, int] | None:
        """Find best match for movies and specials."""
        clean_target = re.sub(
            r"\s*-?\s*movie\s*", "", target_title, flags=re.IGNORECASE
        )
        clean_target = re.sub(r"\s*-?\s*0\s*$", "", clean_target)

        best_match: dict[str, Any] | None = None
        best_similarity = 0.0

        for candidate in candidates:
            format_type = (candidate.get("format", "") or "").upper()
            if format_type not in self.MOVIE_FORMATS:
                continue

            # Skip obvious commercials / promotional content
            title_obj = candidate.get("title", {})
            all_titles = " ".join(
                [
                    title_obj.get("romaji", ""),
                    title_obj.get("english", ""),
                    title_obj.get("native", ""),
                ]
            ).lower()

            commercial_indicators = [
                "cm",
                "commercial",
                "pv",
                "promotional",
                "advertisement",
                "ad",
            ]
            if any(indicator in all_titles for indicator in commercial_indicators):
                continue

            similarity = self.calculate_title_similarity(clean_target, candidate)

            # Strongly prefer MOVIE format over SPECIAL/OVA/ONA
            if format_type == "MOVIE":
                similarity += 0.15

            if similarity > best_similarity and similarity >= 0.75:
                best_similarity = similarity
                best_match = candidate

        if best_match:
            anime_title = get_primary_title(best_match)
            format_type = best_match.get("format", "Unknown")
            logger.info(
                "Found movie match: '%s' (%s) - similarity: %.2f",
                anime_title,
                format_type,
                best_similarity,
            )
            return best_match, best_similarity, 0

        return None

    # ------------------------------------------------------------------
    # Season detection from an AniList entry
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_season_from_entry(entry: dict[str, Any]) -> int:
        """Detect season number from AniList entry title."""
        title_obj = entry.get("title", {})
        romaji = title_obj.get("romaji", "")
        english = title_obj.get("english", "")

        for title in [romaji, english]:
            if not title:
                continue

            patterns: list[tuple[str, Any]] = [
                (r"(\d+)(?:st|nd|rd|th)\s+Season", lambda m: int(m.group(1))),
                (r"Season\s+(\d+)", lambda m: int(m.group(1))),
                (r"\bPart\s+(\d+)", lambda m: int(m.group(1))),
                (r"\b(II|III|IV|V|VI)\b", _roman_to_int),
            ]

            for pattern, extractor in patterns:
                match = re.search(pattern, title, re.IGNORECASE)
                if match:
                    season = extractor(match)
                    if 1 <= season <= 10:
                        return season

        return 1

    # ------------------------------------------------------------------
    # Title similarity (ported verbatim from AnimeMatcher)
    # ------------------------------------------------------------------

    def calculate_title_similarity(
        self, target_title: str, candidate: dict[str, Any]
    ) -> float:
        """Calculate similarity score between *target_title* and a candidate entry."""
        target_normalized = normalize_title(target_title)
        target_base = extract_base_title(target_normalized)
        target_no_space = target_normalized.replace(" ", "")

        max_similarity = 0.0
        titles_to_check = _extract_titles(candidate)

        for title in titles_to_check:
            if not title:
                continue
            candidate_normalized = normalize_title(title)
            candidate_base = extract_base_title(candidate_normalized)
            candidate_no_space = candidate_normalized.replace(" ", "")

            full_similarity = _calculate_string_similarity(
                target_normalized, candidate_normalized
            )
            base_similarity = _calculate_string_similarity(target_base, candidate_base)

            space_removed_similarity = 0.0
            if (
                target_no_space != target_normalized
                or candidate_no_space != candidate_normalized
            ):
                space_removed_similarity = _calculate_string_similarity(
                    target_no_space, candidate_no_space
                )
                if space_removed_similarity >= 0.95:
                    space_removed_similarity = 1.0

            combined_similarity = max(
                (base_similarity * 0.7) + (full_similarity * 0.3),
                space_removed_similarity,
            )
            max_similarity = max(max_similarity, combined_similarity)

        return max_similarity

    # ------------------------------------------------------------------
    # Season structure builder (ported from SyncManager)
    # ------------------------------------------------------------------

    def build_season_structure(
        self,
        search_results: list[dict[str, Any]],
        series_title: str,
    ) -> dict[int, dict[str, Any]]:
        """Build a season-number → entry mapping from AniList search results.

        The returned dict maps season numbers (1, 2, …) to dicts containing:
        ``entry``, ``episodes``, ``title``, ``similarity``, ``id``,
        ``release_order``.
        """
        season_structure: dict[int, dict[str, Any]] = {}
        base_title = clean_title_for_search(series_title)
        no_space_title = series_title.replace(" ", "").lower()

        MIN_SIMILARITY_THRESHOLD = 0.7

        series_groups: dict[str, dict[str, Any]] = {}

        for result in search_results:
            format_type = (result.get("format", "") or "").upper()
            episode_count = result.get("episodes")

            if format_type in ["MOVIE", "SPECIAL", "OVA"]:
                continue
            # ONA: allow if episodes unknown or >= 3
            if format_type == "ONA" and episode_count is not None and episode_count < 3:
                continue

            result_title = get_primary_title(result)
            result_title_lower = result_title.lower()
            supplemental_keywords = [
                "kaisetsu",
                "commentary",
                "recap",
                "digest",
                "summary",
            ]

            if format_type == "ONA":
                if any(kw in result_title_lower for kw in supplemental_keywords):
                    logger.debug("Excluding supplemental ONA: %s", result_title)
                    continue
                if ":" in result_title:
                    base_part = result_title.split(":")[0].strip()
                    if (
                        self.calculate_title_similarity(
                            series_title, {"title": {"romaji": base_part}}
                        )
                        > 0.8
                    ):
                        logger.debug("Excluding ONA with subtitle: %s", result_title)
                        continue

            # Pre-filter by similarity
            similarity = self.calculate_title_similarity(series_title, result)
            if similarity < MIN_SIMILARITY_THRESHOLD:
                logger.debug(
                    "Excluding %s from season structure (similarity %.2f < %.2f)",
                    get_primary_title(result),
                    similarity,
                    MIN_SIMILARITY_THRESHOLD,
                )
                continue

            result_base = extract_base_series_title(result_title)

            is_primary_match = (
                no_space_title in result_title.lower().replace(" ", "")
                or base_title.lower() in result_base.lower()
            )

            if result_base not in series_groups:
                series_groups[result_base] = {
                    "entries": [],
                    "is_primary": is_primary_match,
                }

            series_groups[result_base]["entries"].append(result)

            if is_primary_match:
                series_groups[result_base]["is_primary"] = True

        # Find the primary group
        primary_group: list[dict[str, Any]] | None = None
        for group_name, group_data in series_groups.items():
            if group_data["is_primary"]:
                primary_group = group_data["entries"]
                logger.debug("Found primary series group: %s", group_name)
                break

        if not primary_group:
            primary_group = []
            for group_data in series_groups.values():
                primary_group.extend(group_data["entries"])

        # Build TV series list sorted by release date
        tv_series: list[dict[str, Any]] = []
        for result in primary_group:
            format_type = (result.get("format", "") or "").upper()
            if format_type in ["MOVIE", "SPECIAL", "OVA"]:
                continue
            if format_type == "ONA":
                ep_count = result.get("episodes")
                if ep_count is not None and ep_count < 3:
                    continue

            result_title_lower = get_primary_title(result).lower()
            is_space_removed_match = (
                no_space_title != series_title.lower()
                and no_space_title in result_title_lower.replace(" ", "")
            )

            start_date = result.get("startDate", {}) or {}
            year = (
                start_date.get("year") if start_date.get("year") is not None else 9999
            )
            month = (
                start_date.get("month") if start_date.get("month") is not None else 12
            )
            day = start_date.get("day") if start_date.get("day") is not None else 31
            release_order = year * 10000 + month * 100 + day

            tv_series.append(
                {
                    "entry": result,
                    "release_order": release_order,
                    "title": get_primary_title(result),
                    "episodes": result.get("episodes", 0),
                    "has_explicit_season": _has_explicit_season_number(result),
                    "is_space_removed_match": is_space_removed_match,
                }
            )

        tv_series.sort(key=lambda x: x["release_order"])

        season_num = 1
        for series_data in tv_series:
            result = series_data["entry"]

            detected_season = _detect_season_from_anilist_entry(result, base_title)

            if series_data["has_explicit_season"] and detected_season > 1:
                actual_season = detected_season
            else:
                actual_season = season_num
                season_num += 1

            sim = self.calculate_title_similarity(series_title, result)
            if series_data["is_space_removed_match"]:
                sim += 0.3

            # Decide whether to add or replace this season slot
            should_add = False
            current_format = result.get("format", "").upper()

            if actual_season not in season_structure:
                should_add = True
            else:
                existing_entry = season_structure[actual_season]["entry"]
                existing_format = existing_entry.get("format", "").upper()

                if current_format == "TV" and existing_format == "ONA":
                    should_add = True
                    logger.debug("Replacing ONA with TV for Season %d", actual_season)
                elif current_format == existing_format and sim > season_structure[
                    actual_season
                ].get("similarity", 0):
                    should_add = True
                    logger.debug(
                        "Replacing with higher similarity entry for Season %d",
                        actual_season,
                    )

            if should_add:
                season_structure[actual_season] = {
                    "entry": result,
                    "episodes": series_data["episodes"],
                    "title": series_data["title"],
                    "similarity": sim,
                    "id": result["id"],
                    "release_order": series_data["release_order"],
                }
                logger.debug(
                    "  Season %d: %s (%s episodes)",
                    actual_season,
                    series_data["title"],
                    series_data["episodes"],
                )

        # Fallback: include TV entries if structure is empty
        if not season_structure and search_results:
            logger.debug(
                "Season structure empty - retrying with relaxed threshold for TV"
            )
            tv_fallback: list[dict[str, Any]] = []
            for result in search_results:
                fmt = (result.get("format", "") or "").upper()
                if fmt != "TV":
                    continue
                rt = get_primary_title(result).lower()
                if any(
                    kw in rt
                    for kw in [
                        "kaisetsu",
                        "commentary",
                        "recap",
                        "digest",
                        "summary",
                    ]
                ):
                    continue

                sd = result.get("startDate", {}) or {}
                y = sd.get("year") if sd.get("year") is not None else 9999
                mo = sd.get("month") if sd.get("month") is not None else 12
                d = sd.get("day") if sd.get("day") is not None else 31
                ro = y * 10000 + mo * 100 + d

                tv_fallback.append(
                    {
                        "entry": result,
                        "release_order": ro,
                        "episodes": result.get("episodes", 0),
                    }
                )

            tv_fallback.sort(key=lambda x: x["release_order"])
            for idx, sd2 in enumerate(tv_fallback, 1):
                res = sd2["entry"]
                sim2 = self.calculate_title_similarity(series_title, res)
                season_structure[idx] = {
                    "entry": res,
                    "episodes": sd2["episodes"],
                    "title": get_primary_title(res),
                    "similarity": sim2,
                    "id": res["id"],
                    "release_order": sd2["release_order"],
                }
                logger.debug(
                    "  Fallback Season %d: %s (similarity: %.2f)",
                    idx,
                    get_primary_title(res),
                    sim2,
                )

        return season_structure

    # ------------------------------------------------------------------
    # Determine correct entry & episode (cumulative mapping)
    # ------------------------------------------------------------------

    @staticmethod
    def determine_correct_entry_and_episode(
        series_title: str,
        cr_season: int,
        cr_episode: int,
        season_structure: dict[int, dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, int, int]:
        """Map a CR season+episode to the correct AniList entry.

        Returns ``(entry, season_num, episode_num)`` or ``(None, 0, 0)``.
        """
        if cr_season > 1 and season_structure:
            base_title_normalized = series_title.lower().replace(" ", "")

            best_entry: dict[str, Any] | None = None
            best_similarity = 0.0

            for season_num, season_data in season_structure.items():
                entry_title = season_data["title"].lower().replace(" ", "")

                if (
                    base_title_normalized in entry_title
                    or entry_title in base_title_normalized
                ):
                    similarity = season_data.get("similarity", 0)
                    max_episodes = season_data["episodes"] or 999

                    if season_num == 1 and cr_episode > max_episodes:
                        continue

                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_entry = season_data["entry"]

                        if cr_episode <= max_episodes:
                            logger.info(
                                "Found matching series: %s - using as season %d",
                                season_data["title"],
                                season_num,
                            )
                            return best_entry, season_num, cr_episode

            # Try cumulative episode conversion
            should_try_cumulative = False
            if cr_season in season_structure:
                target_season_eps = season_structure[cr_season].get("episodes") or 999
                if cr_episode > target_season_eps:
                    should_try_cumulative = True
                    logger.debug(
                        "Episode %d exceeds S%d max (%d), trying cumulative mapping",
                        cr_episode,
                        cr_season,
                        target_season_eps,
                    )

            if best_entry or should_try_cumulative:
                cumulative_episodes = 0
                sorted_seasons = sorted(season_structure.keys())

                for sn in sorted_seasons:
                    sd = season_structure[sn]
                    season_episodes = sd["episodes"] or 0

                    if cr_episode <= cumulative_episodes + season_episodes:
                        episode_in_season = cr_episode - cumulative_episodes
                        if episode_in_season > 0:
                            logger.info(
                                "Episode %d maps to Season %d Episode %d",
                                cr_episode,
                                sn,
                                episode_in_season,
                            )
                            return sd["entry"], sn, episode_in_season

                    cumulative_episodes += season_episodes

        # Direct season lookup
        if cr_season in season_structure:
            sd = season_structure[cr_season]
            max_episodes = sd["episodes"] or cr_episode
            capped_episode = min(cr_episode, max_episodes)
            logger.warning(
                "Could not map episode %d, using S%dE%d",
                cr_episode,
                cr_season,
                capped_episode,
            )
            return sd["entry"], cr_season, capped_episode

        # Season 1 fallback
        if 1 in season_structure:
            sd = season_structure[1]
            logger.warning("Falling back to Season 1 for %s", series_title)
            return sd["entry"], 1, cr_episode

        return None, 0, 0


# ======================================================================
# Module-level helpers (not part of the class to keep them simple)
# ======================================================================


def get_primary_title(anime: dict[str, Any]) -> str:
    """Return romaji > english > native > 'Unknown'."""
    title_obj = anime.get("title", {})
    if isinstance(title_obj, dict):
        return (
            title_obj.get("romaji")
            or title_obj.get("english")
            or title_obj.get("native")
            or "Unknown"
        )
    if isinstance(title_obj, str):
        return title_obj
    return "Unknown"


def _extract_titles(anime: dict[str, Any]) -> list[str]:
    """Extract all possible titles including synonyms."""
    titles: list[str] = []

    title_obj = anime.get("title", {})
    if isinstance(title_obj, dict):
        for key in ["romaji", "english", "native"]:
            t = title_obj.get(key)
            if t:
                titles.append(t)
    elif isinstance(title_obj, str):
        titles.append(title_obj)

    synonyms = anime.get("synonyms", [])
    if synonyms:
        titles.extend(synonyms)

    return [t for t in titles if t]


def _calculate_string_similarity(str1: str, str2: str) -> float:
    """Calculate similarity using SequenceMatcher + word overlap.

    Weights: 60 % sequence, 40 % word similarity.
    """
    if not str1 or not str2:
        return 0.0
    if str1 == str2:
        return 1.0

    # Substring containment bonus
    if str1 in str2 or str2 in str1:
        shorter, longer = (str1, str2) if len(str1) < len(str2) else (str2, str1)
        return max(0.9, len(shorter) / len(longer))

    sequence_similarity = SequenceMatcher(None, str1, str2).ratio()

    words1 = set(str1.split())
    words2 = set(str2.split())

    if words1 and words2:
        common_words = words1.intersection(words2)
        total_words = words1.union(words2)

        if total_words:
            word_overlap = len(common_words) / len(total_words)
            coverage1 = len(common_words) / len(words1)
            coverage2 = len(common_words) / len(words2)
            word_coverage = (coverage1 + coverage2) / 2

            word_similarity = (word_overlap * 0.4) + (word_coverage * 0.6)
            final_similarity = (sequence_similarity * 0.6) + (word_similarity * 0.4)
        else:
            final_similarity = sequence_similarity
    else:
        final_similarity = sequence_similarity

    return final_similarity


def _roman_to_int(match: re.Match[str]) -> int:
    """Convert Roman numerals II-VI to ints."""
    roman_map = {"II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}
    return roman_map.get(match.group(1), 1)


def _has_explicit_season_number(entry: dict[str, Any]) -> bool:
    """Check if entry has explicit season number in title."""
    title_obj = entry.get("title", {})
    romaji = title_obj.get("romaji", "")
    english = title_obj.get("english", "")

    patterns = [
        r"(\d+)(?:st|nd|rd|th)\s+Season",
        r"Season\s+(\d+)",
        r"\bPart\s+(\d+)",
        r"\b(?:II|III|IV|V|VI)\b",
    ]

    for title in [romaji, english]:
        if title:
            for pattern in patterns:
                if re.search(pattern, title, re.IGNORECASE):
                    return True

    return False


def _detect_season_from_anilist_entry(entry: dict[str, Any], base_title: str) -> int:
    """Detect which season number an AniList entry represents."""
    title_obj = entry.get("title", {})
    romaji = title_obj.get("romaji", "")
    english = title_obj.get("english", "")

    for title in [romaji, english]:
        if not title:
            continue

        patterns: list[tuple[str, int]] = [
            (r"(\d+)(?:st|nd|rd|th)\s+Season", 1),
            (r"Season\s+(\d+)", 1),
            (r"\bPart\s+(\d+)", 1),
            (r"\b(?:II|III|IV|V|VI)\b", 0),
        ]

        for pattern, group in patterns:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                if group == 0:
                    roman_map = {"II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}
                    return roman_map.get(match.group(0), 1)
                else:
                    return int(match.group(group))

    base_clean = base_title.lower().strip()
    title_clean = romaji.lower().strip()

    if base_clean in title_clean and title_clean == base_clean:
        return 1

    return 1
