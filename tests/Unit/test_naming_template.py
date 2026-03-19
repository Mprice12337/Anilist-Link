"""Tests for parse_quality() in NamingTemplate."""

import pytest

from src.Utils.NamingTemplate import QualityInfo, parse_quality


# ---------------------------------------------------------------------------
# Parametrized tests for parse_quality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename, expected_resolution, expected_source, expected_codec",
    [
        # Basic resolution in brackets
        (
            "[SubGroup] Anime Title - 01 [1080p].mkv",
            "1080p",
            "",
            "",
        ),
        # Full quality string with dots
        (
            "Anime.S01E01.720p.BluRay.x264.mkv",
            "720p",
            "BluRay",
            "x264",
        ),
        # HEVC codec in brackets
        (
            "Anime S01E01 [HEVC 10bit].mkv",
            "",
            "",
            "HEVC",
        ),
        # WEB-DL source
        (
            "[CR] Anime - 01 [WEB-DL].mkv",
            "",
            "WEB-DL",
            "",
        ),
        # No quality info at all
        (
            "Anime.Episode.01.mp4",
            "",
            "",
            "",
        ),
        # Empty string
        (
            "",
            "",
            "",
            "",
        ),
        # No extension
        (
            "Anime Title 1080p BluRay x265",
            "1080p",
            "BluRay",
            "x265",
        ),
        # Multiple resolution-like tokens (first match wins)
        (
            "Anime 720p reenc 1080p.mkv",
            "720p",
            "",
            "",
        ),
        # 4K normalizes to 2160p
        (
            "Anime.Title.4K.WEBRip.AV1.mkv",
            "2160p",
            "WEBRip",
            "AV1",
        ),
        # 2160p explicit
        (
            "Anime 2160p HDTV H.265.mkv",
            "2160p",
            "HDTV",
            "HEVC",
        ),
        # 480p with DVDRip
        (
            "[Group] Anime - 01 [480p DVDRip x264].mkv",
            "480p",
            "DVDRip",
            "x264",
        ),
        # H.264 normalizes to x264
        (
            "Anime.S02E05.H.264.WEB.mkv",
            "",
            "WEB",
            "x264",
        ),
        # BDRip normalizes to BluRay
        (
            "Anime [BDRip 1080p HEVC].mkv",
            "1080p",
            "BluRay",
            "HEVC",
        ),
        # Case insensitivity: lowercase
        (
            "anime.s01e01.bluray.hevc.mkv",
            "",
            "BluRay",
            "HEVC",
        ),
        # AVC normalizes to x264
        (
            "Show.720p.WEB-DL.AVC.mkv",
            "720p",
            "WEB-DL",
            "x264",
        ),
        # Blu-Ray with hyphen normalizes to BluRay
        (
            "Anime [Blu-Ray 1080p].mkv",
            "1080p",
            "BluRay",
            "",
        ),
        # H265 without dot normalizes to HEVC
        (
            "Anime.1080p.H265.mkv",
            "1080p",
            "",
            "HEVC",
        ),
        # Only codec present
        (
            "Anime - 01 [x265].mkv",
            "",
            "",
            "x265",
        ),
    ],
    ids=[
        "resolution_in_brackets",
        "full_quality_dots",
        "hevc_codec",
        "web_dl_source",
        "no_quality_info",
        "empty_string",
        "no_extension",
        "multiple_resolutions_first_wins",
        "4k_normalizes_to_2160p",
        "2160p_hdtv_h265",
        "480p_dvdrip_x264",
        "h264_normalizes_to_x264",
        "bdrip_normalizes_to_bluray",
        "case_insensitive_lowercase",
        "avc_normalizes_to_x264",
        "blu_ray_hyphen_normalizes",
        "h265_no_dot_normalizes",
        "only_codec",
    ],
)
def test_parse_quality(
    filename: str,
    expected_resolution: str,
    expected_source: str,
    expected_codec: str,
) -> None:
    result = parse_quality(filename)
    assert result.resolution == expected_resolution
    assert result.source == expected_source
    assert result.codec == expected_codec


# ---------------------------------------------------------------------------
# QualityInfo.full property
# ---------------------------------------------------------------------------


def test_quality_info_full_all_fields() -> None:
    qi = QualityInfo(resolution="1080p", source="BluRay", codec="x265")
    assert qi.full == "1080p BluRay x265"


def test_quality_info_full_partial() -> None:
    qi = QualityInfo(resolution="720p", source="", codec="x264")
    assert qi.full == "720p x264"


def test_quality_info_full_empty() -> None:
    qi = QualityInfo()
    assert qi.full == ""
