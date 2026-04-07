"""Tests for PlexClient – pure/static helpers and HTTP-mocked async methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.Clients.PlexClient import (
    PlexClient,
    PlexEpisode,
    PlexLibrary,
    PlexSeason,
    PlexShow,
    _strip_html,
)


# ------------------------------------------------------------------
# _strip_html
# ------------------------------------------------------------------


class TestStripHtml:
    def test_removes_simple_tags(self):
        assert _strip_html("<b>bold</b>") == "bold"

    def test_removes_nested_tags(self):
        assert _strip_html("<div><p>hello</p></div>") == "hello"

    def test_removes_tags_with_attributes(self):
        assert _strip_html('<a href="http://x.com">link</a>') == "link"

    def test_no_html_unchanged(self):
        assert _strip_html("plain text") == "plain text"

    def test_empty_string(self):
        assert _strip_html("") == ""

    def test_strips_surrounding_whitespace(self):
        assert _strip_html("  <br/> hello <br/>  ") == "hello"

    def test_multiple_tags(self):
        assert _strip_html("<b>one</b> and <i>two</i>") == "one and two"

    def test_self_closing_tags(self):
        assert _strip_html("line1<br/>line2") == "line1line2"


# ------------------------------------------------------------------
# Dataclass construction
# ------------------------------------------------------------------


class TestDataclasses:
    def test_plex_library(self):
        lib = PlexLibrary(key="1", title="Anime", type="show", item_count=42)
        assert lib.key == "1"
        assert lib.title == "Anime"
        assert lib.type == "show"
        assert lib.item_count == 42

    def test_plex_show_defaults(self):
        show = PlexShow(
            rating_key="100",
            title="Naruto",
            year=2002,
            thumb="/thumb",
            summary="A ninja show",
            library_key="1",
        )
        assert show.locations == []
        assert show.year == 2002

    def test_plex_season(self):
        season = PlexSeason(
            rating_key="200",
            index=1,
            title="Season 1",
            episode_count=24,
            parent_rating_key="100",
        )
        assert season.index == 1
        assert season.parent_rating_key == "100"

    def test_plex_episode(self):
        ep = PlexEpisode(
            rating_key="300",
            grandparent_title="Naruto",
            parent_index=1,
            index=5,
            view_count=2,
        )
        assert ep.index == 5
        assert ep.view_count == 2


# ------------------------------------------------------------------
# PlexShow.folder_name
# ------------------------------------------------------------------


class TestFolderName:
    def test_extracts_basename(self):
        show = PlexShow(
            rating_key="1",
            title="Naruto",
            year=None,
            thumb="",
            summary="",
            library_key="1",
            locations=["/media/anime/Naruto Shippuden"],
        )
        assert show.folder_name == "Naruto Shippuden"

    def test_uses_first_location(self):
        show = PlexShow(
            rating_key="1",
            title="Naruto",
            year=None,
            thumb="",
            summary="",
            library_key="1",
            locations=["/media/anime/First", "/media/anime/Second"],
        )
        assert show.folder_name == "First"

    def test_falls_back_to_title_when_no_locations(self):
        show = PlexShow(
            rating_key="1",
            title="Fallback Title",
            year=None,
            thumb="",
            summary="",
            library_key="1",
            locations=[],
        )
        assert show.folder_name == "Fallback Title"

    def test_falls_back_to_title_when_basename_empty(self):
        # Trailing slash yields empty basename
        show = PlexShow(
            rating_key="1",
            title="Fallback",
            year=None,
            thumb="",
            summary="",
            library_key="1",
            locations=["/"],
        )
        assert show.folder_name == "Fallback"

    def test_deep_path(self):
        show = PlexShow(
            rating_key="1",
            title="X",
            year=None,
            thumb="",
            summary="",
            library_key="1",
            locations=["/a/b/c/d/My Show"],
        )
        assert show.folder_name == "My Show"


# ------------------------------------------------------------------
# build_metadata_params
# ------------------------------------------------------------------


class TestBuildMetadataParams:
    def test_empty_when_no_args(self):
        assert PlexClient.build_metadata_params() == {}

    def test_title_only(self):
        params = PlexClient.build_metadata_params(title="My Show")
        assert params == {"title.value": "My Show", "title.locked": "1"}

    def test_original_title(self):
        params = PlexClient.build_metadata_params(original_title="僕のヒーロー")
        assert params["originalTitle.value"] == "僕のヒーロー"
        assert params["originalTitle.locked"] == "1"

    def test_summary_strips_html(self):
        params = PlexClient.build_metadata_params(summary="<p>A <b>great</b> show</p>")
        assert params["summary.value"] == "A great show"
        assert params["summary.locked"] == "1"

    def test_genres(self):
        params = PlexClient.build_metadata_params(genres=["Action", "Comedy", "Sci-Fi"])
        assert params["genre[0].tag.tag"] == "Action"
        assert params["genre[1].tag.tag"] == "Comedy"
        assert params["genre[2].tag.tag"] == "Sci-Fi"
        assert params["genre.locked"] == "1"

    def test_empty_genres_list(self):
        params = PlexClient.build_metadata_params(genres=[])
        assert params == {"genre.locked": "1"}

    def test_rating_rounds(self):
        params = PlexClient.build_metadata_params(rating=8.567)
        assert params["rating.value"] == "8.6"
        assert params["rating.locked"] == "1"

    def test_studio(self):
        params = PlexClient.build_metadata_params(studio="MAPPA")
        assert params["studio.value"] == "MAPPA"
        assert params["studio.locked"] == "1"

    def test_all_fields(self):
        params = PlexClient.build_metadata_params(
            title="Title",
            original_title="OG Title",
            summary="<i>Summary</i>",
            genres=["A"],
            rating=7.0,
            studio="Studio",
        )
        assert "title.value" in params
        assert "originalTitle.value" in params
        assert params["summary.value"] == "Summary"
        assert "genre[0].tag.tag" in params
        assert params["rating.value"] == "7.0"
        assert "studio.value" in params

    def test_none_values_skipped(self):
        params = PlexClient.build_metadata_params(
            title=None, summary=None, rating=None
        )
        assert params == {}


# ------------------------------------------------------------------
# HTTP-mocked async tests
# ------------------------------------------------------------------


def _make_client() -> PlexClient:
    """Create a PlexClient and replace its HTTP client with a mock."""
    client = PlexClient.__new__(PlexClient)
    client._http = MagicMock()
    return client


def _mock_response(json_data: dict | list | None = None, status_code: int = 200):
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_test_connection():
    client = _make_client()
    resp = _mock_response({"MediaContainer": {"friendlyName": "MyPlex"}})
    client._http.get = AsyncMock(return_value=resp)

    name = await client.test_connection()
    assert name == "MyPlex"
    client._http.get.assert_awaited_once_with("/")


@pytest.mark.asyncio
async def test_test_connection_unknown():
    client = _make_client()
    resp = _mock_response({"MediaContainer": {}})
    client._http.get = AsyncMock(return_value=resp)

    name = await client.test_connection()
    assert name == "Unknown"


@pytest.mark.asyncio
async def test_get_libraries():
    client = _make_client()
    resp = _mock_response(
        {
            "MediaContainer": {
                "Directory": [
                    {"key": "1", "title": "Anime", "type": "show", "count": 50},
                    {"key": "2", "title": "Movies", "type": "movie", "count": 10},
                ]
            }
        }
    )
    client._http.get = AsyncMock(return_value=resp)

    libs = await client.get_libraries()
    assert len(libs) == 2
    assert libs[0].key == "1"
    assert libs[0].title == "Anime"
    assert libs[0].type == "show"
    assert libs[0].item_count == 50
    assert libs[1].key == "2"
    client._http.get.assert_awaited_once_with("/library/sections")


@pytest.mark.asyncio
async def test_get_libraries_empty():
    client = _make_client()
    resp = _mock_response({"MediaContainer": {}})
    client._http.get = AsyncMock(return_value=resp)

    libs = await client.get_libraries()
    assert libs == []


@pytest.mark.asyncio
async def test_get_library_shows():
    client = _make_client()
    resp = _mock_response(
        {
            "MediaContainer": {
                "Metadata": [
                    {
                        "ratingKey": "100",
                        "title": "Attack on Titan",
                        "year": 2013,
                        "thumb": "/thumb/100",
                        "summary": "Giants attack",
                        "Location": [{"path": "/anime/AoT"}],
                    },
                    {
                        "ratingKey": "101",
                        "title": "No Location Show",
                        "thumb": "",
                        "summary": "",
                    },
                ]
            }
        }
    )
    client._http.get = AsyncMock(return_value=resp)

    shows = await client.get_library_shows("1")
    assert len(shows) == 2
    assert shows[0].title == "Attack on Titan"
    assert shows[0].year == 2013
    assert shows[0].locations == ["/anime/AoT"]
    assert shows[0].library_key == "1"
    assert shows[1].locations == []
    # Client may call get multiple times (e.g., pagination)
    assert any(
        call.args == ("/library/sections/1/all",)
        for call in client._http.get.await_args_list
    )


@pytest.mark.asyncio
async def test_get_library_shows_missing_metadata():
    client = _make_client()
    resp = _mock_response({"MediaContainer": {}})
    client._http.get = AsyncMock(return_value=resp)

    shows = await client.get_library_shows("1")
    assert shows == []
