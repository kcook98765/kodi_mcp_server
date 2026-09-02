"""Remote-oriented video library discovery MCP coverage."""

import json

import pytest
from jsonschema import Draft202012Validator
from mcp.types import CallToolRequestParams, PaginatedRequestParams

from kodi_mcp_server.models.messages import ErrorType, ResponseMessage


class _Bridge:
    pass


class _LibraryJsonRpc:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def execute_jsonrpc(self, method, params=None):
        self.calls.append((method, params or {}))
        response = self.responses[method]
        if isinstance(response, list):
            response = response.pop(0)
        return response


def _success(method, result):
    return ResponseMessage(request_id=method, result=result, error=None)


def _count_responses(counts):
    method_keys = {
        "VideoLibrary.GetMovies": "movies",
        "VideoLibrary.GetTVShows": "tvshows",
        "VideoLibrary.GetSeasons": "seasons",
        "VideoLibrary.GetEpisodes": "episodes",
    }
    return {
        method: _success(
            method,
            {
                key: [] if total == 0 else [{"label": "bounded count sentinel"}],
                "limits": {"start": 0, "end": min(1, total), "total": total},
            },
        )
        for method, (key, total) in (
            (method, (key, counts[key])) for method, key in method_keys.items()
        )
    }


async def _call(jsonrpc, name, arguments=None):
    from kodi_mcp_mcp.server_core import build_mcp_server

    server, _ = build_mcp_server(
        {"bridge": _Bridge(), "jsonrpc": jsonrpc, "notifications": None}
    )
    return await server.get_request_handler("tools/call").handler(
        None, CallToolRequestParams(name=name, arguments=arguments or {})
    )


def _envelope(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_library_summary_uses_native_totals_without_bulk_fetching():
    counts = {"movies": 9279, "tvshows": 837, "seasons": 2467, "episodes": 28894}
    jsonrpc = _LibraryJsonRpc(_count_responses(counts))

    result = await _call(jsonrpc, "kodi_library_summary")

    assert result.is_error is False
    assert _envelope(result)["data"] == {"counts": counts}
    assert len(jsonrpc.calls) == 4
    assert all(params == {"limits": {"start": 0, "end": 1}} for _, params in jsonrpc.calls)


@pytest.mark.asyncio
async def test_library_summary_supports_an_empty_library():
    counts = {"movies": 0, "tvshows": 0, "seasons": 0, "episodes": 0}

    result = await _call(_LibraryJsonRpc(_count_responses(counts)), "kodi_library_summary")

    assert result.is_error is False
    assert _envelope(result)["data"] == {"counts": counts}


@pytest.mark.asyncio
async def test_library_summary_rejects_a_malformed_kodi_response():
    responses = _count_responses(
        {"movies": 1, "tvshows": 2, "seasons": 3, "episodes": 4}
    )
    responses["VideoLibrary.GetTVShows"] = _success(
        "VideoLibrary.GetTVShows", {"tvshows": []}
    )

    result = await _call(_LibraryJsonRpc(responses), "kodi_library_summary")

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["error_type"] == "invalid_response"
    assert "VideoLibrary.GetTVShows" in envelope["error"]


@pytest.mark.asyncio
async def test_library_summary_preserves_a_kodi_transport_error():
    responses = _count_responses(
        {"movies": 1, "tvshows": 2, "seasons": 3, "episodes": 4}
    )
    responses["VideoLibrary.GetMovies"] = ResponseMessage(
        request_id="failed",
        result=None,
        error="connection error: refused",
        error_type=ErrorType.NETWORK_ERROR,
    )

    result = await _call(_LibraryJsonRpc(responses), "kodi_library_summary")

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["error"] == "connection error: refused"
    assert envelope["error_type"] == "network_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "method", "result_key", "id_key"),
    [
        ("movie", "VideoLibrary.GetMovies", "movies", "movieid"),
        ("tvshow", "VideoLibrary.GetTVShows", "tvshows", "tvshowid"),
        ("episode", "VideoLibrary.GetEpisodes", "episodes", "episodeid"),
    ],
)
async def test_library_search_finds_each_supported_media_type(
    media_type, method, result_key, id_key
):
    raw_item = {
        id_key: 42,
        "title": "Rock & Roll",
        "label": "ignored label",
        "year": 2001,
        "playcount": 1,
        "runtime": 3600,
        "dateadded": "2026-01-02 03:04:05",
        "genre": ["Drama"],
        "showtitle": "Example Show",
        "season": 2,
        "episode": 3,
        "tvshowid": 42 if id_key == "tvshowid" else 7,
        "seasonid": 8,
        "file": "smb://user:password@nas/private/video.mkv",
        "art": {
            "poster": "image://https%3a%2f%2fimages.example%2fposter.jpg/",
            "fanart": "image://https%3a%2f%2fuser%3apass%40images.example%2ffan.jpg/",
            "thumb": "image://smb%3a%2f%2fuser%3apassword%40nas%2fprivate.jpg/",
            "banner": "image://https%3a%2f%2fimages.example%2fbanner.jpg/",
        },
    }
    jsonrpc = _LibraryJsonRpc(
        {
            method: _success(
                method,
                {
                    result_key: [raw_item],
                    "limits": {"start": 0, "end": 1, "total": 1},
                },
            )
        }
    )

    result = await _call(
        jsonrpc,
        "kodi_library_search",
        {"query": "Rock & Roll", "media_type": media_type},
    )

    envelope = _envelope(result)
    assert result.is_error is False
    assert envelope["data"]["media_type"] == media_type
    assert envelope["data"]["search"] == {
        "field": "title",
        "operator": "contains",
    }
    assert envelope["data"]["items"][0]["id"] == 42
    assert envelope["data"]["items"][0]["media_type"] == media_type
    assert envelope["data"]["items"][0]["title"] == "Rock & Roll"
    assert envelope["data"]["items"][0]["watched"] is True
    assert "file" not in envelope["data"]["items"][0]
    assert "password" not in json.dumps(envelope)
    assert envelope["data"]["items"][0]["artwork"] == {
        "poster": "image://https%3a%2f%2fimages.example%2fposter.jpg/"
    }
    assert [call[0] for call in jsonrpc.calls] == [method]


@pytest.mark.asyncio
async def test_library_search_returns_explicit_empty_page():
    method = "VideoLibrary.GetMovies"
    jsonrpc = _LibraryJsonRpc(
        {
            method: _success(
                method,
                {"movies": [], "limits": {"start": 0, "end": 0, "total": 0}},
            )
        }
    )

    result = await _call(
        jsonrpc,
        "kodi_library_search",
        {"query": "not present", "media_type": "movie"},
    )

    data = _envelope(result)["data"]
    assert data["items"] == []
    assert data["empty"] is True
    assert data["pagination"]["total"] == 0
    assert data["pagination"]["has_more"] is False


@pytest.mark.asyncio
async def test_library_search_uses_kodi_side_pagination_and_preserves_special_characters():
    method = "VideoLibrary.GetTVShows"
    query = "'\"&<>[]{}?*"
    jsonrpc = _LibraryJsonRpc(
        {
            method: _success(
                method,
                {
                    "tvshows": [{"tvshowid": 9, "title": query, "playcount": 0}],
                    "limits": {"start": 7, "end": 8, "total": 30},
                },
            )
        }
    )

    result = await _call(
        jsonrpc,
        "kodi_library_search",
        {"query": query, "media_type": "tvshow", "start": 7, "limit": 25},
    )

    assert result.is_error is False
    assert jsonrpc.calls == [
        (
            method,
            {
                "properties": [
                    "title",
                    "year",
                    "playcount",
                    "runtime",
                    "dateadded",
                    "genre",
                    "art",
                    "episode",
                    "season",
                    "watchedepisodes",
                ],
                "filter": {"field": "title", "operator": "contains", "value": query},
                "limits": {"start": 7, "end": 32},
                "sort": {"method": "title", "order": "ascending"},
            },
        )
    ]
    assert _envelope(result)["data"]["pagination"] == {
        "start": 7,
        "end": 8,
        "total": 30,
        "limit": 25,
        "has_more": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"media_type": "movie"},
        {"query": "   ", "media_type": "movie"},
        {"query": "x", "media_type": "musicvideo"},
        {"query": "x", "media_type": "movie", "limit": 51},
        {"query": "x", "media_type": "movie", "start": -1},
    ],
)
async def test_library_search_rejects_malformed_or_unbounded_arguments(arguments):
    jsonrpc = _LibraryJsonRpc({})

    result = await _call(jsonrpc, "kodi_library_search", arguments)

    assert result.is_error is True
    assert _envelope(result)["error_type"] == "invalid_params"
    assert jsonrpc.calls == []


@pytest.mark.asyncio
async def test_library_search_rejects_malformed_kodi_pagination():
    method = "VideoLibrary.GetEpisodes"
    jsonrpc = _LibraryJsonRpc(
        {method: _success(method, {"episodes": [{"episodeid": 1, "title": "Pilot"}]})}
    )

    result = await _call(
        jsonrpc,
        "kodi_library_search",
        {"query": "Pilot", "media_type": "episode"},
    )

    assert result.is_error is True
    assert _envelope(result)["error_type"] == "invalid_response"


@pytest.mark.asyncio
async def test_library_search_rejects_a_kodi_page_larger_than_requested():
    method = "VideoLibrary.GetMovies"
    jsonrpc = _LibraryJsonRpc(
        {
            method: _success(
                method,
                {
                    "movies": [
                        {"movieid": 1, "title": "One"},
                        {"movieid": 2, "title": "Two"},
                    ],
                    "limits": {"start": 0, "end": 2, "total": 2},
                },
            )
        }
    )

    result = await _call(
        jsonrpc,
        "kodi_library_search",
        {"query": "o", "media_type": "movie", "limit": 1},
    )

    assert result.is_error is True
    assert _envelope(result)["error_type"] == "invalid_response"


def _show_details(tvshow_id=7, title="Example Show"):
    return _success(
        "VideoLibrary.GetTVShowDetails",
        {"tvshowdetails": {"tvshowid": tvshow_id, "title": title}},
    )


@pytest.mark.asyncio
async def test_tv_show_to_seasons_returns_bounded_normalized_page():
    jsonrpc = _LibraryJsonRpc(
        {
            "VideoLibrary.GetTVShowDetails": _show_details(),
            "VideoLibrary.GetSeasons": _success(
                "VideoLibrary.GetSeasons",
                {
                    "seasons": [
                        {
                            "seasonid": 81,
                            "title": "Season 2",
                            "showtitle": "Example Show",
                            "tvshowid": 7,
                            "season": 2,
                            "playcount": 0,
                            "episode": 10,
                            "watchedepisodes": 3,
                            "art": {"poster": "image://https%3a%2f%2fimages.example%2fs2.jpg/"},
                        }
                    ],
                    "limits": {"start": 0, "end": 1, "total": 1},
                },
            ),
        }
    )

    result = await _call(
        jsonrpc, "kodi_tv_seasons", {"tvshow_id": 7, "start": 0, "limit": 5}
    )

    data = _envelope(result)["data"]
    assert result.is_error is False
    assert data["tvshow"] == {"id": 7, "title": "Example Show"}
    assert data["items"] == [
        {
            "id": 81,
            "media_type": "season",
            "title": "Season 2",
            "show_title": "Example Show",
            "tvshow_id": 7,
            "season": 2,
            "watched": False,
            "playcount": 0,
            "episode_count": 10,
            "watched_episode_count": 3,
            "artwork": {"poster": "image://https%3a%2f%2fimages.example%2fs2.jpg/"},
        }
    ]
    assert data["pagination"] == {
        "start": 0,
        "end": 1,
        "total": 1,
        "limit": 5,
        "has_more": False,
    }
    assert jsonrpc.calls == [
        (
            "VideoLibrary.GetTVShowDetails",
            {"tvshowid": 7, "properties": ["title"]},
        ),
        (
            "VideoLibrary.GetSeasons",
            {
                "tvshowid": 7,
                "properties": [
                    "title",
                    "season",
                    "showtitle",
                    "playcount",
                    "episode",
                    "watchedepisodes",
                    "art",
                    "tvshowid",
                ],
                "limits": {"start": 0, "end": 5},
                "sort": {"method": "season", "order": "ascending"},
            },
        ),
    ]


@pytest.mark.asyncio
async def test_tv_season_to_episodes_returns_bounded_normalized_page():
    jsonrpc = _LibraryJsonRpc(
        {
            "VideoLibrary.GetTVShowDetails": _show_details(),
            "VideoLibrary.GetEpisodes": _success(
                "VideoLibrary.GetEpisodes",
                {
                    "episodes": [
                        {
                            "episodeid": 123,
                            "title": "The Pilot",
                            "showtitle": "Example Show",
                            "tvshowid": 7,
                            "seasonid": 81,
                            "season": 2,
                            "episode": 1,
                            "playcount": 1,
                            "runtime": 1800,
                            "dateadded": "2026-01-02 03:04:05",
                            "art": {},
                        }
                    ],
                    "limits": {"start": 5, "end": 6, "total": 12},
                },
            ),
        }
    )

    result = await _call(
        jsonrpc,
        "kodi_tv_episodes",
        {"tvshow_id": 7, "season": 2, "start": 5, "limit": 5},
    )

    data = _envelope(result)["data"]
    assert result.is_error is False
    assert data["tvshow"] == {"id": 7, "title": "Example Show"}
    assert data["season"] == 2
    assert data["items"][0] == {
        "id": 123,
        "media_type": "episode",
        "title": "The Pilot",
        "watched": True,
        "playcount": 1,
        "runtime_seconds": 1800,
        "date_added": "2026-01-02 03:04:05",
        "artwork": {},
        "show_title": "Example Show",
        "tvshow_id": 7,
        "season_id": 81,
        "season": 2,
        "episode": 1,
    }
    assert data["pagination"]["has_more"] is True
    assert jsonrpc.calls[1] == (
        "VideoLibrary.GetEpisodes",
        {
            "tvshowid": 7,
            "season": 2,
            "properties": [
                "title",
                "showtitle",
                "season",
                "episode",
                "playcount",
                "runtime",
                "dateadded",
                "art",
                "tvshowid",
                "seasonid",
            ],
            "limits": {"start": 5, "end": 10},
            "sort": {"method": "episode", "order": "ascending"},
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "downstream_method", "arguments", "result_key"),
    [
        ("kodi_tv_seasons", "VideoLibrary.GetSeasons", {"tvshow_id": 7}, "seasons"),
        (
            "kodi_tv_episodes",
            "VideoLibrary.GetEpisodes",
            {"tvshow_id": 7, "season": 99},
            "episodes",
        ),
    ],
)
async def test_tv_hierarchy_returns_explicit_empty_page(
    tool_name, downstream_method, arguments, result_key
):
    jsonrpc = _LibraryJsonRpc(
        {
            "VideoLibrary.GetTVShowDetails": _show_details(),
            downstream_method: _success(
                downstream_method,
                {result_key: [], "limits": {"start": 0, "end": 0, "total": 0}},
            ),
        }
    )

    result = await _call(jsonrpc, tool_name, arguments)

    data = _envelope(result)["data"]
    assert result.is_error is False
    assert data["items"] == []
    assert data["empty"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("kodi_tv_seasons", {"tvshow_id": 999}),
        ("kodi_tv_episodes", {"tvshow_id": 999, "season": 1}),
    ],
)
async def test_tv_hierarchy_reports_nonexistent_show_id(tool_name, arguments):
    jsonrpc = _LibraryJsonRpc(
        {
            "VideoLibrary.GetTVShowDetails": ResponseMessage(
                request_id="missing",
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
    assert "TV show 999 was not found" in envelope["error"]
    assert len(jsonrpc.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("kodi_tv_seasons", {"tvshow_id": -1}),
        ("kodi_tv_seasons", {"tvshow_id": 1, "limit": 51}),
        ("kodi_tv_episodes", {"tvshow_id": 1}),
        ("kodi_tv_episodes", {"tvshow_id": 1, "season": -1}),
        ("kodi_tv_episodes", {"tvshow_id": 1, "season": 1, "limit": 51}),
    ],
)
async def test_tv_hierarchy_rejects_invalid_or_unbounded_arguments(tool_name, arguments):
    jsonrpc = _LibraryJsonRpc({})

    result = await _call(jsonrpc, tool_name, arguments)

    assert result.is_error is True
    assert _envelope(result)["error_type"] == "invalid_params"
    assert jsonrpc.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("category", "method", "result_key", "id_key", "expected_media_type"),
    [
        (
            "recent_movies",
            "VideoLibrary.GetRecentlyAddedMovies",
            "movies",
            "movieid",
            "movie",
        ),
        (
            "recent_episodes",
            "VideoLibrary.GetRecentlyAddedEpisodes",
            "episodes",
            "episodeid",
            "episode",
        ),
        ("movie_genres", "VideoLibrary.GetGenres", "genres", "genreid", "genre"),
        ("tvshow_genres", "VideoLibrary.GetGenres", "genres", "genreid", "genre"),
        ("movie_sets", "VideoLibrary.GetMovieSets", "sets", "setid", "movie_set"),
        ("movie_tags", "VideoLibrary.GetTags", "tags", "tagid", "tag"),
        ("tvshow_tags", "VideoLibrary.GetTags", "tags", "tagid", "tag"),
    ],
)
async def test_library_browse_supports_compact_discovery_categories(
    category, method, result_key, id_key, expected_media_type
):
    raw_item = {id_key: 4, "title": "Discovery Item", "playcount": 0, "art": {}}
    jsonrpc = _LibraryJsonRpc(
        {
            method: _success(
                method,
                {
                    result_key: [raw_item],
                    "limits": {"start": 3, "end": 4, "total": 9},
                },
            )
        }
    )

    result = await _call(
        jsonrpc,
        "kodi_library_browse",
        {"category": category, "start": 3, "limit": 2},
    )

    data = _envelope(result)["data"]
    assert result.is_error is False
    assert data["category"] == category
    assert data["items"][0]["id"] == 4
    assert data["items"][0]["media_type"] == expected_media_type
    assert data["pagination"] == {
        "start": 3,
        "end": 4,
        "total": 9,
        "limit": 2,
        "has_more": True,
    }
    assert len(jsonrpc.calls) == 1
    called_method, params = jsonrpc.calls[0]
    assert called_method == method
    assert params["limits"] == {"start": 3, "end": 5}
    if category.startswith("recent_"):
        assert params["sort"] == {"method": "dateadded", "order": "descending"}
    else:
        assert params["sort"] == {"method": "title", "order": "ascending"}
    if category in {"movie_genres", "movie_tags"}:
        assert params["type"] == "movie"
    if category in {"tvshow_genres", "tvshow_tags"}:
        assert params["type"] == "tvshow"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"category": "songs"},
        {"category": "recent_movies", "limit": 51},
        {"category": "recent_movies", "start": -1},
    ],
)
async def test_library_browse_rejects_invalid_or_unbounded_arguments(arguments):
    jsonrpc = _LibraryJsonRpc({})

    result = await _call(jsonrpc, "kodi_library_browse", arguments)

    assert result.is_error is True
    assert _envelope(result)["error_type"] == "invalid_params"
    assert jsonrpc.calls == []


@pytest.mark.asyncio
async def test_library_browse_rejects_malformed_kodi_response():
    method = "VideoLibrary.GetMovieSets"
    jsonrpc = _LibraryJsonRpc(
        {method: _success(method, {"sets": "not-a-list", "limits": {"total": 1}})}
    )

    result = await _call(
        jsonrpc, "kodi_library_browse", {"category": "movie_sets"}
    )

    assert result.is_error is True
    assert _envelope(result)["error_type"] == "invalid_response"


@pytest.mark.asyncio
async def test_library_tools_advertise_and_emit_truthful_structured_contracts():
    from kodi_mcp_mcp.server_core import build_mcp_server

    counts = {"movies": 1, "tvshows": 2, "seasons": 3, "episodes": 4}
    server, _ = build_mcp_server(
        {
            "bridge": _Bridge(),
            "jsonrpc": _LibraryJsonRpc(_count_responses(counts)),
            "notifications": None,
        }
    )
    tools = {
        tool.name: tool
        for tool in (
            await server.get_request_handler("tools/list").handler(
                None, PaginatedRequestParams()
            )
        ).tools
    }
    names = {
        "kodi_library_summary",
        "kodi_library_search",
        "kodi_library_browse",
        "kodi_tv_seasons",
        "kodi_tv_episodes",
    }
    for name in names:
        Draft202012Validator.check_schema(tools[name].output_schema)
        assert tools[name].annotations.read_only_hint is True
        assert tools[name].annotations.destructive_hint is not True

    summary = await server.get_request_handler("tools/call").handler(
        None, CallToolRequestParams(name="kodi_library_summary", arguments={})
    )
    Draft202012Validator(tools["kodi_library_summary"].output_schema).validate(
        summary.structured_content
    )
    assert summary.structured_content["data"] == {"counts": counts}

    failure = await server.get_request_handler("tools/call").handler(
        None,
        CallToolRequestParams(
            name="kodi_library_search",
            arguments={"query": "x", "media_type": "movie", "limit": 51},
        ),
    )
    assert failure.is_error is True
    Draft202012Validator(tools["kodi_library_search"].output_schema).validate(
        failure.structured_content
    )
    assert failure.structured_content["error_type"] == "invalid_params"
