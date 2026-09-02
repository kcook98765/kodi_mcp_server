"""Issue #8 bounded music discovery and navigation contracts."""

import json

import pytest

from kodi_mcp_server.models.messages import ErrorType, ResponseMessage


class _Bridge:
    pass


class _MusicJsonRpc:
    def __init__(self, responses):
        self.responses = {
            method: list(values) if isinstance(values, list) else [values]
            for method, values in responses.items()
        }
        self.calls = []

    async def execute_jsonrpc(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        values = self.responses.get(method)
        if not values:
            raise AssertionError(f"unexpected JSON-RPC method: {method}")
        return values.pop(0)


def _ok(method, result):
    return ResponseMessage(request_id=method, result=result, error=None)


def _page(method, key, items, *, start=0, total=None):
    total = len(items) if total is None else total
    return _ok(
        method,
        {
            key: items,
            "limits": {"start": start, "end": start + len(items), "total": total},
        },
    )


async def _call(jsonrpc, tool_name, arguments=None):
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequestParams

    server, _ = build_mcp_server(
        {"bridge": _Bridge(), "jsonrpc": jsonrpc, "notifications": None}
    )
    return await server.get_request_handler("tools/call").handler(
        None,
        CallToolRequestParams(name=tool_name, arguments=arguments or {}),
    )


def _envelope(result):
    return json.loads(result.content[0].text)


def _count_responses(counts):
    return {
        "AudioLibrary.GetArtists": _page(
            "AudioLibrary.GetArtists", "artists", [], total=counts["artists"]
        ),
        "AudioLibrary.GetAlbums": _page(
            "AudioLibrary.GetAlbums", "albums", [], total=counts["albums"]
        ),
        "AudioLibrary.GetSongs": _page(
            "AudioLibrary.GetSongs", "songs", [], total=counts["songs"]
        ),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "counts",
    [
        {"artists": 133, "albums": 254, "songs": 3302},
        {"artists": 0, "albums": 0, "songs": 0},
    ],
)
async def test_music_summary_uses_native_bounded_totals(counts):
    jsonrpc = _MusicJsonRpc(_count_responses(counts))

    result = await _call(jsonrpc, "kodi_music_summary")

    assert result.is_error is False
    assert _envelope(result)["data"] == {"counts": counts}
    assert jsonrpc.calls == [
        (
            "AudioLibrary.GetArtists",
            {"albumartistsonly": False, "limits": {"start": 0, "end": 1}},
        ),
        (
            "AudioLibrary.GetAlbums",
            {"includesingles": True, "limits": {"start": 0, "end": 1}},
        ),
        (
            "AudioLibrary.GetSongs",
            {"includesingles": True, "limits": {"start": 0, "end": 1}},
        ),
    ]


@pytest.mark.asyncio
async def test_music_summary_rejects_malformed_result():
    responses = _count_responses({"artists": 1, "albums": 2, "songs": 3})
    responses["AudioLibrary.GetAlbums"] = _ok(
        "AudioLibrary.GetAlbums", {"albums": []}
    )

    result = await _call(_MusicJsonRpc(responses), "kodi_music_summary")

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["error_type"] == "invalid_response"
    assert "AudioLibrary.GetAlbums" in envelope["error"]
    assert "limits.total" in envelope["error"]


@pytest.mark.asyncio
async def test_music_summary_preserves_downstream_error():
    responses = _count_responses({"artists": 1, "albums": 2, "songs": 3})
    responses["AudioLibrary.GetArtists"] = ResponseMessage(
        request_id="artists-error",
        result=None,
        error="music database unavailable",
        error_type=ErrorType.NETWORK_ERROR,
    )

    result = await _call(_MusicJsonRpc(responses), "kodi_music_summary")

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["error"] == "music database unavailable"
    assert envelope["error_type"] == "network_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "method", "result_key", "item", "expected"),
    [
        (
            "artist",
            "AudioLibrary.GetArtists",
            "artists",
            {
                "artistid": 11,
                "artist": "Björk",
                "genre": ["Electronic"],
                "isalbumartist": True,
                "art": {"thumb": "image://artist-thumb", "fanart": "smb://private/art"},
            },
            {
                "id": 11,
                "media_type": "artist",
                "name": "Björk",
                "genres": ["Electronic"],
                "is_album_artist": True,
                "artwork": {"thumb": "image://artist-thumb"},
            },
        ),
        (
            "album",
            "AudioLibrary.GetAlbums",
            "albums",
            {
                "albumid": 22,
                "title": "Debut",
                "artist": ["Björk", "Guest"],
                "artistid": [11, 12],
                "year": 1993,
                "genre": ["Electronic"],
                "playcount": 2,
                "compilation": False,
                "albumduration": 1234,
                "art": {},
            },
            {
                "id": 22,
                "media_type": "album",
                "title": "Debut",
                "artists": ["Björk", "Guest"],
                "artist_ids": [11, 12],
                "year": 1993,
                "genres": ["Electronic"],
                "playcount": 2,
                "compilation": False,
                "duration_seconds": 1234,
                "artwork": {},
            },
        ),
        (
            "song",
            "AudioLibrary.GetSongs",
            "songs",
            {
                "songid": 33,
                "title": "Human Behaviour",
                "artist": ["Björk", "Guest"],
                "album": "Debut",
                "albumid": 22,
                "track": 1,
                "disc": 1,
                "duration": 252,
                "playcount": 3,
                "year": 1993,
                "genre": ["Electronic"],
                "art": {},
            },
            {
                "id": 33,
                "media_type": "song",
                "title": "Human Behaviour",
                "artists": ["Björk", "Guest"],
                "album": "Debut",
                "album_id": 22,
                "track": 1,
                "disc": 1,
                "duration_seconds": 252,
                "playcount": 3,
                "year": 1993,
                "genres": ["Electronic"],
                "artwork": {},
            },
        ),
    ],
)
async def test_music_search_finds_each_media_type(
    media_type, method, result_key, item, expected
):
    jsonrpc = _MusicJsonRpc(
        {method: _page(method, result_key, [item], start=7, total=32)}
    )

    result = await _call(
        jsonrpc,
        "kodi_music_search",
        {"query": "Björk & Co.", "media_type": media_type, "start": 7, "limit": 25},
    )

    data = _envelope(result)["data"]
    assert result.is_error is False
    assert data == {
        "query": "Björk & Co.",
        "media_type": media_type,
        "search": {
            "field": {"artist": "artist", "album": "album", "song": "title"}[media_type],
            "operator": "contains",
        },
        "items": [expected],
        "empty": False,
        "pagination": {
            "start": 7,
            "end": 8,
            "total": 32,
            "limit": 25,
            "has_more": True,
        },
    }
    assert [call[0] for call in jsonrpc.calls] == [method]
    params = jsonrpc.calls[0][1]
    assert params["filter"] == {
        "field": data["search"]["field"],
        "operator": "contains",
        "value": "Björk & Co.",
    }
    assert params["limits"] == {"start": 7, "end": 32}


@pytest.mark.asyncio
async def test_music_search_returns_explicit_empty_page():
    method = "AudioLibrary.GetSongs"
    jsonrpc = _MusicJsonRpc(
        {
            method: _ok(
                method,
                {"limits": {"start": 0, "end": 0, "total": 0}},
            )
        }
    )

    result = await _call(
        jsonrpc,
        "kodi_music_search",
        {"query": "not present", "media_type": "song"},
    )

    data = _envelope(result)["data"]
    assert result.is_error is False
    assert data["items"] == []
    assert data["empty"] is True
    assert data["pagination"]["total"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"media_type": "artist"},
        {"query": "   ", "media_type": "artist"},
        {"query": "x", "media_type": "musicvideo"},
        {"query": "x", "media_type": "song", "limit": 51},
        {"query": "x", "media_type": "album", "start": -1},
    ],
)
async def test_music_search_rejects_malformed_or_unbounded_arguments(arguments):
    jsonrpc = _MusicJsonRpc({})

    result = await _call(jsonrpc, "kodi_music_search", arguments)

    assert result.is_error is True
    assert _envelope(result)["error_type"] == "invalid_params"
    assert jsonrpc.calls == []


@pytest.mark.asyncio
async def test_music_search_rejects_malformed_or_oversized_page():
    method = "AudioLibrary.GetSongs"
    response = _ok(
        method,
        {
            "songs": [
                {"songid": 1, "title": "One"},
                {"songid": 2, "title": "Two"},
            ],
            "limits": {"start": 0, "end": 2, "total": 2},
        },
    )

    result = await _call(
        _MusicJsonRpc({method: response}),
        "kodi_music_search",
        {"query": "o", "media_type": "song", "limit": 1},
    )

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["error_type"] == "invalid_response"
    assert "AudioLibrary.GetSongs" in envelope["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("category", "method", "result_key", "item", "expected"),
    [
        (
            "recent_albums",
            "AudioLibrary.GetRecentlyAddedAlbums",
            "albums",
            {
                "albumid": 22,
                "title": "Debut",
                "artist": ["Björk"],
                "artistid": [11],
                "playcount": 0,
                "compilation": False,
            },
            {"media_type": "album", "id": 22, "title": "Debut"},
        ),
        (
            "recent_songs",
            "AudioLibrary.GetRecentlyAddedSongs",
            "songs",
            {"songid": 33, "title": "Human Behaviour", "artist": ["Björk"]},
            {"media_type": "song", "id": 33, "title": "Human Behaviour"},
        ),
        (
            "genres",
            "AudioLibrary.GetGenres",
            "genres",
            {"genreid": 44, "title": "Alternative", "thumbnail": "image://genre"},
            {
                "media_type": "genre",
                "id": 44,
                "title": "Alternative",
                "artwork": {"thumb": "image://genre"},
            },
        ),
    ],
)
async def test_music_browse_supports_bounded_categories(
    category, method, result_key, item, expected
):
    jsonrpc = _MusicJsonRpc(
        {method: _page(method, result_key, [item], start=10, total=30)}
    )

    result = await _call(
        jsonrpc,
        "kodi_music_browse",
        {"category": category, "start": 10, "limit": 5},
    )

    data = _envelope(result)["data"]
    assert result.is_error is False
    assert data["category"] == category
    assert data["items"][0] | expected == data["items"][0]
    assert data["pagination"] == {
        "start": 10,
        "end": 11,
        "total": 30,
        "limit": 5,
        "has_more": True,
    }
    assert [call[0] for call in jsonrpc.calls] == [method]
    assert jsonrpc.calls[0][1]["limits"] == {"start": 10, "end": 15}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"category": "albums"},
        {"category": "genres", "limit": 51},
        {"category": "recent_songs", "start": -1},
    ],
)
async def test_music_browse_rejects_invalid_or_unbounded_arguments(arguments):
    jsonrpc = _MusicJsonRpc({})

    result = await _call(jsonrpc, "kodi_music_browse", arguments)

    assert result.is_error is True
    assert _envelope(result)["error_type"] == "invalid_params"
    assert jsonrpc.calls == []


@pytest.mark.asyncio
async def test_music_browse_rejects_malformed_response():
    method = "AudioLibrary.GetGenres"

    result = await _call(
        _MusicJsonRpc({method: _ok(method, {"genres": []})}),
        "kodi_music_browse",
        {"category": "genres"},
    )

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["error_type"] == "invalid_response"
    assert method in envelope["error"]


@pytest.mark.asyncio
async def test_artist_to_albums_preserves_compilation_and_multi_artist_relationships():
    artist = {
        "artistid": 11,
        "artist": "Björk",
        "genre": ["Electronic"],
        "isalbumartist": True,
        "art": {},
    }
    album = {
        "albumid": 22,
        "title": "Compilation",
        "artist": ["Björk", "Guest"],
        "artistid": [11, 12],
        "year": 2000,
        "genre": ["Electronic"],
        "playcount": 0,
        "compilation": True,
        "albumduration": 3600,
        "art": {},
    }
    jsonrpc = _MusicJsonRpc(
        {
            "AudioLibrary.GetArtistDetails": _ok(
                "AudioLibrary.GetArtistDetails", {"artistdetails": artist}
            ),
            "AudioLibrary.GetAlbums": _page(
                "AudioLibrary.GetAlbums", "albums", [album], total=1
            ),
        }
    )

    result = await _call(
        jsonrpc,
        "kodi_artist_albums",
        {"artist_id": 11, "limit": 10},
    )

    data = _envelope(result)["data"]
    assert result.is_error is False
    assert data["artist"]["id"] == 11
    assert data["items"][0]["artist_ids"] == [11, 12]
    assert data["items"][0]["artists"] == ["Björk", "Guest"]
    assert data["items"][0]["compilation"] is True
    assert jsonrpc.calls[1][0] == "AudioLibrary.GetAlbums"
    assert jsonrpc.calls[1][1]["filter"] == {"artistid": 11}
    assert jsonrpc.calls[1][1]["allroles"] is False
    assert jsonrpc.calls[1][1]["limits"] == {"start": 0, "end": 10}


@pytest.mark.asyncio
async def test_album_to_songs_returns_track_order_and_stable_ids():
    album = {
        "albumid": 22,
        "title": "Debut",
        "artist": ["Björk"],
        "artistid": [11],
        "playcount": 0,
        "compilation": False,
        "art": {},
    }
    songs = [
        {
            "songid": 33,
            "title": "Human Behaviour",
            "artist": ["Björk"],
            "album": "Debut",
            "albumid": 22,
            "disc": 1,
            "track": 1,
        },
        {
            "songid": 34,
            "title": "Crying",
            "artist": ["Björk"],
            "album": "Debut",
            "albumid": 22,
            "disc": 1,
            "track": 2,
        },
    ]
    jsonrpc = _MusicJsonRpc(
        {
            "AudioLibrary.GetAlbumDetails": _ok(
                "AudioLibrary.GetAlbumDetails", {"albumdetails": album}
            ),
            "AudioLibrary.GetSongs": _page(
                "AudioLibrary.GetSongs", "songs", songs, total=2
            ),
        }
    )

    result = await _call(
        jsonrpc,
        "kodi_album_songs",
        {"album_id": 22, "start": 0, "limit": 10},
    )

    data = _envelope(result)["data"]
    assert result.is_error is False
    assert data["album"]["id"] == 22
    assert [item["id"] for item in data["items"]] == [33, 34]
    assert [item["track"] for item in data["items"]] == [1, 2]
    assert jsonrpc.calls[1][1]["filter"] == {"albumid": 22}
    assert jsonrpc.calls[1][1]["sort"] == {
        "method": "track",
        "order": "ascending",
    }
    assert jsonrpc.calls[1][1]["limits"] == {"start": 0, "end": 10}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "detail_method", "detail_key", "detail_item", "list_method", "list_key", "arguments"),
    [
        (
            "kodi_artist_albums",
            "AudioLibrary.GetArtistDetails",
            "artistdetails",
            {"artistid": 11, "artist": "Björk"},
            "AudioLibrary.GetAlbums",
            "albums",
            {"artist_id": 11},
        ),
        (
            "kodi_album_songs",
            "AudioLibrary.GetAlbumDetails",
            "albumdetails",
            {"albumid": 22, "title": "Debut"},
            "AudioLibrary.GetSongs",
            "songs",
            {"album_id": 22},
        ),
    ],
)
async def test_music_hierarchy_returns_explicit_empty_page(
    tool_name, detail_method, detail_key, detail_item, list_method, list_key, arguments
):
    jsonrpc = _MusicJsonRpc(
        {
            detail_method: _ok(detail_method, {detail_key: detail_item}),
            list_method: _page(list_method, list_key, [], total=0),
        }
    )

    result = await _call(jsonrpc, tool_name, arguments)

    data = _envelope(result)["data"]
    assert result.is_error is False
    assert data["items"] == []
    assert data["empty"] is True
    assert data["pagination"]["total"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "detail_method", "arguments", "identity"),
    [
        (
            "kodi_artist_albums",
            "AudioLibrary.GetArtistDetails",
            {"artist_id": 999999},
            "artist 999999",
        ),
        (
            "kodi_album_songs",
            "AudioLibrary.GetAlbumDetails",
            {"album_id": 999999},
            "album 999999",
        ),
    ],
)
async def test_music_hierarchy_reports_nonexistent_ids(
    tool_name, detail_method, arguments, identity
):
    jsonrpc = _MusicJsonRpc(
        {
            detail_method: ResponseMessage(
                request_id="not-found",
                result=None,
                error="jsonrpc error -32602: Invalid params.",
                error_type=ErrorType.SERVER_ERROR,
                error_code=-32602,
            )
        }
    )

    result = await _call(jsonrpc, tool_name, arguments)

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["error_type"] == "not_found"
    assert identity in envelope["error"]
    assert [call[0] for call in jsonrpc.calls] == [detail_method]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("kodi_artist_albums", {}),
        ("kodi_artist_albums", {"artist_id": True}),
        ("kodi_artist_albums", {"artist_id": 1, "limit": 51}),
        ("kodi_album_songs", {}),
        ("kodi_album_songs", {"album_id": -1}),
        ("kodi_album_songs", {"album_id": 1, "start": -1}),
    ],
)
async def test_music_hierarchy_rejects_invalid_or_unbounded_arguments(
    tool_name, arguments
):
    jsonrpc = _MusicJsonRpc({})

    result = await _call(jsonrpc, tool_name, arguments)

    assert result.is_error is True
    assert _envelope(result)["error_type"] == "invalid_params"
    assert jsonrpc.calls == []


@pytest.mark.asyncio
async def test_single_song_without_album_uses_null_album_identity():
    method = "AudioLibrary.GetSongs"
    jsonrpc = _MusicJsonRpc(
        {
            method: _page(
                method,
                "songs",
                [{"songid": 40, "title": "Standalone", "album": "", "albumid": -1}],
                total=1,
            )
        }
    )

    result = await _call(
        jsonrpc,
        "kodi_music_search",
        {"query": "Standalone", "media_type": "song"},
    )

    assert result.is_error is False
    song = _envelope(result)["data"]["items"][0]
    assert song["album"] == ""
    assert song["album_id"] is None
