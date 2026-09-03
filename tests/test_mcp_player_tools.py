import json

import pytest

from kodi_mcp_server.models.messages import ErrorType, ResponseMessage
from kodi_mcp_server.tools.jsonrpc import JsonRpcTool


class _FakeBridge:
    pass


class _FakeJsonRpc:
    def __init__(self):
        self.calls = []
        self.active_players = []

    async def get_active_players(self):
        self.calls.append(("get_active_players", {}))
        return ResponseMessage(
            request_id="fake-active",
            result=list(self.active_players),
            error=None,
        )

    async def get_player_item(self, playerid: int = 1):
        self.calls.append(("get_player_item", {"playerid": playerid}))
        return ResponseMessage(
            request_id="fake-item",
            result={"item": {"label": "Test Video"}, "playerid": playerid},
            error=None,
        )

    async def open_library_item(self, media_type: str, item_id: int):
        self.calls.append(("open_library_item", {"media_type": media_type, "item_id": item_id}))
        self.active_players = [
            {
                "playerid": 0 if media_type in {"song", "album"} else 1,
                "type": "audio" if media_type in {"song", "album"} else "video",
            }
        ]
        return ResponseMessage(request_id="fake-open", result="OK", error=None)

    async def seek_player_to_seconds(self, playerid: int = 1, seconds: float = 0):
        self.calls.append(("seek_player_to_seconds", {"playerid": playerid, "seconds": seconds}))
        return ResponseMessage(
            request_id="fake-seek",
            result="OK",
            error=None,
        )

    async def pause_player(self, playerid: int = 1):
        self.calls.append(("pause_player", {"playerid": playerid}))
        return ResponseMessage(
            request_id="fake-pause",
            result={"playerid": playerid, "paused": True},
            error=None,
        )

    async def stop_player(self, playerid: int = 1):
        self.calls.append(("stop_player", {"playerid": playerid}))
        self.active_players = [
            player for player in self.active_players if player.get("playerid") != playerid
        ]
        return ResponseMessage(
            request_id="fake-stop",
            result="OK",
            error=None,
        )


def _tool_payload(resp):
    return json.loads(resp.content[0].text)


class _RecordingTransport:
    def __init__(self, response: ResponseMessage):
        self.response = response
        self.requests = []

    async def send_request(self, request):
        self.requests.append(request)
        return self.response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "item_id", "id_key"),
    [
        ("movie", 7, "movieid"),
        ("episode", 8, "episodeid"),
        ("album", 9, "albumid"),
        ("song", 10, "songid"),
    ],
)
async def test_open_library_item_uses_exact_typed_player_open_request(
    media_type, item_id, id_key
):
    transport = _RecordingTransport(ResponseMessage(request_id="open", result="OK", error=None))

    response = await JsonRpcTool(transport).open_library_item(
        media_type=media_type, item_id=item_id
    )

    assert response.error is None
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.command == "execute_jsonrpc"
    assert request.args == {
        "method": "Player.Open",
        "params": {"item": {id_key: item_id}},
    }


@pytest.mark.asyncio
async def test_missing_library_item_is_normalized_for_music_and_video():
    missing = ResponseMessage(
        request_id="missing",
        result=None,
        error="jsonrpc error -32602: Invalid params.",
        error_type=ErrorType.SERVER_ERROR,
        error_code=-32602,
    )

    song = await JsonRpcTool(_RecordingTransport(missing)).open_library_item(
        media_type="song", item_id=999999
    )
    movie = await JsonRpcTool(_RecordingTransport(missing)).open_library_item(
        media_type="movie", item_id=999999
    )

    assert song.error == "song 999999 was not found"
    assert song.error_type == ErrorType.NOT_FOUND
    assert song.error_code == -32602
    assert movie.error == "movie 999999 was not found"
    assert movie.error_type == ErrorType.NOT_FOUND
    assert movie.error_code == -32602


@pytest.mark.asyncio
async def test_player_open_transport_error_uses_standard_envelope():
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequestParams

    jsonrpc = _FakeJsonRpc()

    async def fail_open(media_type: str, item_id: int):
        return ResponseMessage(
            request_id="fake-open",
            result=None,
            error="Kodi rejected item",
            error_type=ErrorType.SERVER_ERROR,
        )

    jsonrpc.open_library_item = fail_open
    server, _ = build_mcp_server({"bridge": _FakeBridge(), "jsonrpc": jsonrpc, "notifications": None})

    resp = await server.get_request_handler("tools/call").handler(
        None, CallToolRequestParams(name="kodi_player_open", arguments={"media_type": "movie", "item_id": 7})
    )

    env = _tool_payload(resp)
    assert resp.is_error is True
    assert env["ok"] is False
    assert env["error"] == "Kodi rejected item"
    assert env["error_type"] == "server_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments, expected_error",
    [({}, "media_type"), ({"media_type": "artist", "item_id": 7}, "media_type"), ({"media_type": "movie"}, "item_id")],
)
async def test_player_open_rejects_invalid_or_missing_input(arguments, expected_error):
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequestParams

    jsonrpc = _FakeJsonRpc()
    server, _ = build_mcp_server({"bridge": _FakeBridge(), "jsonrpc": jsonrpc, "notifications": None})

    resp = await server.get_request_handler("tools/call").handler(
        None, CallToolRequestParams(name="kodi_player_open", arguments=arguments)
    )

    env = _tool_payload(resp)
    assert resp.is_error is True
    assert env["error_type"] == "invalid_params"
    assert expected_error in env["error"]
    assert not jsonrpc.calls


@pytest.mark.asyncio
async def test_player_tools_are_listed_and_dispatch_through_jsonrpc():
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequestParams

    jsonrpc = _FakeJsonRpc()
    server, _ = build_mcp_server({"bridge": _FakeBridge(), "jsonrpc": jsonrpc, "notifications": None})

    list_resp = await server.get_request_handler("tools/list").handler(None, None)
    tool_names = {tool.name for tool in list_resp.tools}
    assert {
        "kodi_player_open",
        "kodi_player_active",
        "kodi_player_item",
        "kodi_player_seek",
        "kodi_player_pause",
        "kodi_player_stop",
    }.issubset(tool_names)

    open_resp = await server.get_request_handler("tools/call").handler(None,
        CallToolRequestParams(name="kodi_player_open", arguments={"media_type": "episode", "item_id": 12})
    )
    assert _tool_payload(open_resp)["ok"] is True

    active_resp = await server.get_request_handler("tools/call").handler(None,
        CallToolRequestParams(name="kodi_player_active", arguments={})
    )
    assert _tool_payload(active_resp)["ok"] is True

    item_resp = await server.get_request_handler("tools/call").handler(None,
        CallToolRequestParams(name="kodi_player_item", arguments={"playerid": 1})
    )
    assert _tool_payload(item_resp)["data"]["item"]["label"] == "Test Video"

    seek_resp = await server.get_request_handler("tools/call").handler(None,
        CallToolRequestParams(name="kodi_player_seek", arguments={"playerid": 1, "seconds": 17.3})
    )
    assert _tool_payload(seek_resp)["ok"] is True

    pause_resp = await server.get_request_handler("tools/call").handler(None,
        CallToolRequestParams(name="kodi_player_pause", arguments={"playerid": 1})
    )
    assert _tool_payload(pause_resp)["data"]["paused"] is True

    stop_resp = await server.get_request_handler("tools/call").handler(None,
        CallToolRequestParams(
                name="kodi_player_stop",
                arguments={"playerid": 1, "verify_attempts": 1, "verify_delay_ms": 0, "stable_checks": 1},
            )
    )
    stop_env = _tool_payload(stop_resp)
    assert stop_env["ok"] is True
    assert stop_env["data"]["stopped"] is True
    assert ("seek_player_to_seconds", {"playerid": 1, "seconds": 17.3}) in jsonrpc.calls
    assert ("open_library_item", {"media_type": "episode", "item_id": 12}) in jsonrpc.calls


@pytest.mark.asyncio
async def test_discovered_song_uses_audio_player_zero_without_regressing_video_player_one():
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequestParams

    jsonrpc = _FakeJsonRpc()
    server, _ = build_mcp_server(
        {"bridge": _FakeBridge(), "jsonrpc": jsonrpc, "notifications": None}
    )
    call = server.get_request_handler("tools/call").handler

    song_open = await call(
        None,
        CallToolRequestParams(
            name="kodi_player_open",
            arguments={"media_type": "song", "item_id": 33},
        ),
    )
    active_audio = await call(
        None, CallToolRequestParams(name="kodi_player_active", arguments={})
    )
    item_audio = await call(
        None,
        CallToolRequestParams(name="kodi_player_item", arguments={"playerid": 0}),
    )
    await call(
        None,
        CallToolRequestParams(
            name="kodi_player_seek", arguments={"playerid": 0, "seconds": 5}
        ),
    )
    await call(
        None,
        CallToolRequestParams(name="kodi_player_pause", arguments={"playerid": 0}),
    )
    stop_audio = await call(
        None,
        CallToolRequestParams(
            name="kodi_player_stop",
            arguments={
                "playerid": 0,
                "verify_attempts": 1,
                "verify_delay_ms": 0,
                "stable_checks": 1,
            },
        ),
    )

    assert song_open.is_error is False
    assert _tool_payload(active_audio)["data"] == [
        {"playerid": 0, "type": "audio"}
    ]
    assert _tool_payload(item_audio)["data"]["playerid"] == 0
    assert _tool_payload(stop_audio)["data"]["stopped"] is True
    assert ("seek_player_to_seconds", {"playerid": 0, "seconds": 5.0}) in jsonrpc.calls
    assert ("pause_player", {"playerid": 0}) in jsonrpc.calls
    assert ("stop_player", {"playerid": 0}) in jsonrpc.calls

    movie_open = await call(
        None,
        CallToolRequestParams(
            name="kodi_player_open",
            arguments={"media_type": "movie", "item_id": 7},
        ),
    )
    active_video = await call(
        None, CallToolRequestParams(name="kodi_player_active", arguments={})
    )
    assert movie_open.is_error is False
    assert _tool_payload(active_video)["data"] == [
        {"playerid": 1, "type": "video"}
    ]


@pytest.mark.asyncio
async def test_invalid_music_id_fails_cleanly_and_next_valid_open_recovers():
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequestParams

    jsonrpc = _FakeJsonRpc()
    outcomes = [
        ResponseMessage(
            request_id="missing-song",
            result=None,
            error="song 999999 was not found",
            error_type=ErrorType.NOT_FOUND,
            error_code=-32602,
        ),
        ResponseMessage(request_id="valid-song", result="OK", error=None),
    ]

    async def open_with_outcomes(media_type: str, item_id: int):
        jsonrpc.calls.append(
            ("open_library_item", {"media_type": media_type, "item_id": item_id})
        )
        return outcomes.pop(0)

    jsonrpc.open_library_item = open_with_outcomes
    server, _ = build_mcp_server(
        {"bridge": _FakeBridge(), "jsonrpc": jsonrpc, "notifications": None}
    )
    call = server.get_request_handler("tools/call").handler

    missing = await call(
        None,
        CallToolRequestParams(
            name="kodi_player_open",
            arguments={"media_type": "song", "item_id": 999999},
        ),
    )
    valid = await call(
        None,
        CallToolRequestParams(
            name="kodi_player_open",
            arguments={"media_type": "song", "item_id": 33},
        ),
    )

    assert missing.is_error is True
    assert _tool_payload(missing)["error_type"] == "not_found"
    assert _tool_payload(missing)["error_code"] == -32602
    assert valid.is_error is False
    assert _tool_payload(valid)["data"] == "OK"


@pytest.mark.asyncio
async def test_player_stop_fails_when_player_remains_active():
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequestParams

    jsonrpc = _FakeJsonRpc()
    jsonrpc.active_players = [{"playerid": 1, "type": "video"}]

    async def sticky_stop(playerid: int = 1):
        jsonrpc.calls.append(("stop_player", {"playerid": playerid}))
        return ResponseMessage(request_id="fake-stop", result="OK", error=None)

    jsonrpc.stop_player = sticky_stop
    server, _ = build_mcp_server({"bridge": _FakeBridge(), "jsonrpc": jsonrpc, "notifications": None})

    resp = await server.get_request_handler("tools/call").handler(None,
        CallToolRequestParams(
                name="kodi_player_stop",
                arguments={"playerid": 1, "verify_attempts": 1, "verify_delay_ms": 0, "stable_checks": 1},
            )
    )
    env = _tool_payload(resp)
    assert resp.is_error is True
    assert env["ok"] is False
    assert env["error_type"] == "player_still_active"
    assert env["data"]["active_players"] == [{"playerid": 1, "type": "video"}]


@pytest.mark.asyncio
async def test_player_seek_rejects_missing_seconds():
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequestParams

    server, _ = build_mcp_server({"bridge": _FakeBridge(), "jsonrpc": _FakeJsonRpc(), "notifications": None})

    resp = await server.get_request_handler("tools/call").handler(None,
        CallToolRequestParams(name="kodi_player_seek", arguments={"playerid": 1})
    )
    env = _tool_payload(resp)
    assert resp.is_error is True
    assert env["ok"] is False
    assert env["error_type"] == "invalid_params"
    assert "seconds" in env["error"]
