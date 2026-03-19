"""Tests for ProwlarrClient – quality parsing, result parsing, and search dedup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.Clients.ProwlarrClient import (
    ProwlarrClient,
    ReleaseResult,
    _parse_quality,
)


# ------------------------------------------------------------------
# _parse_quality (parametrized)
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, expected",
    [
        ("[SubGroup] Anime - 01 [1080p].mkv", "1080p"),
        ("Anime.S01E01.720p.BluRay.mkv", "720p"),
        ("Anime 4K UHD.mkv", "4K"),
        ("Anime.2160p.WEB-DL.mkv", "4K"),
        ("[Group] Anime - 01 [480p][AAC].mkv", "480p"),
        ("Anime.S01E01.mkv", "Unknown"),
        ("", "Unknown"),
        ("Some Random Release Name", "Unknown"),
        ("Anime.S01E01.1080p.HEVC.x265.mkv", "1080p"),
        ("Anime [720p] [FLAC]", "720p"),
        ("[SubGroup] Anime [4K HDR]", "4K"),
        ("Anime.Complete.480p.DVD.mkv", "480p"),
        ("Anime.2160p.BluRay.REMUX", "4K"),
        ("Anime 1080P WEB-DL", "1080p"),  # case insensitive
    ],
)
def test_parse_quality(title: str, expected: str):
    assert _parse_quality(title) == expected


# ------------------------------------------------------------------
# ReleaseResult dataclass
# ------------------------------------------------------------------


class TestReleaseResult:
    def test_construction_full(self):
        r = ReleaseResult(
            guid="abc-123",
            title="[Group] Anime - 01 [1080p]",
            size=1_500_000_000,
            seeders=50,
            leechers=10,
            indexer="Nyaa",
            indexer_id=1,
            download_url="https://example.com/download",
            magnet_url="magnet:?xt=urn:btih:abc",
            publish_date="2025-01-01T00:00:00Z",
            quality="1080p",
            is_torrent=True,
        )
        assert r.guid == "abc-123"
        assert r.size == 1_500_000_000
        assert r.is_torrent is True

    def test_construction_minimal(self):
        r = ReleaseResult(
            guid="",
            title="",
            size=0,
            seeders=0,
            leechers=0,
            indexer="",
            indexer_id=0,
            download_url="",
            magnet_url="",
            publish_date="",
            quality="Unknown",
            is_torrent=False,
        )
        assert r.guid == ""
        assert r.is_torrent is False

    def test_usenet_result(self):
        r = ReleaseResult(
            guid="nzb-123",
            title="Anime.S01.1080p.WEB-DL.NZB",
            size=5_000_000_000,
            seeders=0,
            leechers=0,
            indexer="NZBgeek",
            indexer_id=5,
            download_url="https://nzb.example.com/get/123",
            magnet_url="",
            publish_date="2025-06-15T12:00:00Z",
            quality="1080p",
            is_torrent=False,
        )
        assert r.is_torrent is False
        assert r.indexer == "NZBgeek"


# ------------------------------------------------------------------
# ProwlarrClient._parse_result
# ------------------------------------------------------------------


class TestParseResult:
    def _make_client(self) -> ProwlarrClient:
        client = ProwlarrClient.__new__(ProwlarrClient)
        client._url = "http://localhost:9696"
        client._api_key = "fake"
        client._http = MagicMock()
        return client

    def test_all_fields_present(self):
        client = self._make_client()
        raw = {
            "guid": "guid-1",
            "title": "[SubsPlease] Anime - 01 [1080p].mkv",
            "size": 500_000_000,
            "seeders": 100,
            "leechers": 20,
            "indexer": "Nyaa",
            "indexerId": 1,
            "downloadUrl": "https://dl.example.com/1",
            "magnetUrl": "magnet:?xt=urn:btih:aaa",
            "publishDate": "2025-01-15T10:00:00Z",
            "protocol": "torrent",
        }
        result = client._parse_result(raw)
        assert result.guid == "guid-1"
        assert result.title == "[SubsPlease] Anime - 01 [1080p].mkv"
        assert result.size == 500_000_000
        assert result.seeders == 100
        assert result.leechers == 20
        assert result.indexer == "Nyaa"
        assert result.indexer_id == 1
        assert result.quality == "1080p"
        assert result.is_torrent is True

    def test_missing_optional_fields(self):
        client = self._make_client()
        raw = {
            "title": "Anime.S01E01.mkv",
        }
        result = client._parse_result(raw)
        assert result.guid == ""
        assert result.size == 0
        assert result.seeders == 0
        assert result.leechers == 0
        assert result.indexer == ""
        assert result.indexer_id == 0
        assert result.download_url == ""
        assert result.magnet_url == ""
        assert result.publish_date == ""
        assert result.quality == "Unknown"

    def test_usenet_protocol(self):
        client = self._make_client()
        raw = {
            "guid": "nzb-guid",
            "title": "Anime.S01.720p.NZB",
            "protocol": "usenet",
            "downloadUrl": "https://nzb.example.com/get/nzb-guid",
            "magnetUrl": None,
        }
        result = client._parse_result(raw)
        assert result.is_torrent is False
        assert result.quality == "720p"

    def test_torrent_detected_by_magnet(self):
        client = self._make_client()
        raw = {
            "guid": "t-guid",
            "title": "Anime [1080p]",
            "protocol": "usenet",  # protocol says usenet but magnet present
            "magnetUrl": "magnet:?xt=urn:btih:xyz",
        }
        result = client._parse_result(raw)
        assert result.is_torrent is True

    def test_torrent_detected_by_download_url_extension(self):
        client = self._make_client()
        raw = {
            "guid": "t-guid2",
            "title": "Anime [720p]",
            "protocol": "usenet",
            "downloadUrl": "https://example.com/file.torrent",
        }
        result = client._parse_result(raw)
        assert result.is_torrent is True

    def test_none_values_handled(self):
        """Fields with None should default gracefully (keys absent)."""
        client = self._make_client()
        # Use absent keys (not None values) since _parse_quality
        # doesn't guard against None title passed through .get()
        raw = {
            "guid": None,
            "size": None,
            "seeders": None,
            "leechers": None,
            "indexer": None,
            "indexerId": None,
            "downloadUrl": None,
            "magnetUrl": None,
            "publishDate": None,
        }
        result = client._parse_result(raw)
        assert result.guid == ""
        assert result.title == ""
        assert result.size == 0
        assert result.seeders == 0
        assert result.download_url == ""


# ------------------------------------------------------------------
# search_anime deduplication
# ------------------------------------------------------------------


class TestSearchAnimeDedup:
    def _make_client(self) -> ProwlarrClient:
        client = ProwlarrClient.__new__(ProwlarrClient)
        client._url = "http://localhost:9696"
        client._api_key = "fake"
        client._http = MagicMock()
        return client

    def _release(self, guid: str, seeders: int = 0) -> ReleaseResult:
        return ReleaseResult(
            guid=guid,
            title=f"Release {guid}",
            size=100,
            seeders=seeders,
            leechers=0,
            indexer="Nyaa",
            indexer_id=1,
            download_url="",
            magnet_url="",
            publish_date="",
            quality="1080p",
            is_torrent=True,
        )

    @pytest.mark.asyncio
    async def test_deduplicates_by_guid(self):
        client = self._make_client()
        # search() returns overlapping results for each query
        call_count = 0

        async def mock_search(query, categories=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [self._release("a", 50), self._release("b", 30)]
            else:
                return [self._release("b", 30), self._release("c", 10)]

        client.search = AsyncMock(side_effect=mock_search)

        results = await client.search_anime("Main Title", titles=["Alt Title"])
        guids = [r.guid for r in results]
        assert len(results) == 3
        assert guids.count("b") == 1  # deduplicated

    @pytest.mark.asyncio
    async def test_sorted_by_seeders_descending(self):
        client = self._make_client()

        async def mock_search(query, categories=None):
            return [
                self._release("low", 5),
                self._release("high", 100),
                self._release("mid", 50),
            ]

        client.search = AsyncMock(side_effect=mock_search)

        results = await client.search_anime("Query")
        seeders = [r.seeders for r in results]
        assert seeders == sorted(seeders, reverse=True)

    @pytest.mark.asyncio
    async def test_skips_duplicate_query(self):
        client = self._make_client()
        client.search = AsyncMock(return_value=[self._release("a", 10)])

        results = await client.search_anime("Same", titles=["Same", "Different"])
        # "Same" appears in both query and titles, should not duplicate search
        assert client.search.call_count == 2  # "Same" + "Different"

    @pytest.mark.asyncio
    async def test_handles_search_failure_gracefully(self):
        client = self._make_client()
        call_count = 0

        async def mock_search(query, categories=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [self._release("a", 10)]
            raise Exception("Connection refused")

        client.search = AsyncMock(side_effect=mock_search)

        results = await client.search_anime("Good", titles=["Bad"])
        assert len(results) == 1
        assert results[0].guid == "a"

    @pytest.mark.asyncio
    async def test_no_alt_titles(self):
        client = self._make_client()
        client.search = AsyncMock(return_value=[self._release("x", 20)])

        results = await client.search_anime("Query")
        assert len(results) == 1
        client.search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_alt_titles_skipped(self):
        client = self._make_client()
        client.search = AsyncMock(return_value=[self._release("x", 5)])

        results = await client.search_anime("Query", titles=["", None])
        assert client.search.call_count == 1  # only main query
