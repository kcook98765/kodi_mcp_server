import json

import pytest

from kodi_mcp_server.models.messages import ResponseMessage


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
        return ResponseMessage(
            request_id="fake-stop",
            result="OK",
            error=None,
        )


def _tool_payload(resp):
    return json.loads(resp.root.content[0].text)


@pytest.mark.asyncio
async def test_player_tools_are_listed_and_dispatch_through_jsonrpc():
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

    jsonrpc = _FakeJsonRpc()
    server, _ = build_mcp_server({"bridge": _FakeBridge(), "jsonrpc": jsonrpc, "notifications": None})

    list_resp = await server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
    tool_names = {tool.name for tool in list_resp.root.tools}
    assert {
        "kodi_player_active",
        "kodi_player_item",
        "kodi_player_seek",
        "kodi_player_pause",
        "kodi_player_stop",
    }.issubset(tool_names)

    active_resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(method="tools/call", params=CallToolRequestParams(name="kodi_player_active", arguments={}))
    )
    assert _tool_payload(active_resp)["ok"] is True

    item_resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="kodi_player_item", arguments={"playerid": 1}),
        )
    )
    assert _tool_payload(item_resp)["data"]["item"]["label"] == "Test Video"

    seek_resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="kodi_player_seek", arguments={"playerid": 1, "seconds": 17.3}),
        )
    )
    assert _tool_payload(seek_resp)["ok"] is True

    pause_resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="kodi_player_pause", arguments={"playerid": 1}),
        )
    )
    assert _tool_payload(pause_resp)["data"]["paused"] is True

    stop_resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="kodi_player_stop", arguments={"playerid": 1}),
        )
    )
    stop_env = _tool_payload(stop_resp)
    assert stop_env["ok"] is True
    assert stop_env["data"]["stopped"] is True
    assert ("seek_player_to_seconds", {"playerid": 1, "seconds": 17.3}) in jsonrpc.calls


@pytest.mark.asyncio
async def test_player_stop_fails_when_player_remains_active():
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    jsonrpc = _FakeJsonRpc()
    jsonrpc.active_players = [{"playerid": 1, "type": "video"}]
    server, _ = build_mcp_server({"bridge": _FakeBridge(), "jsonrpc": jsonrpc, "notifications": None})

    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="kodi_player_stop", arguments={"playerid": 1}),
        )
    )
    env = _tool_payload(resp)
    assert resp.root.isError is True
    assert env["ok"] is False
    assert env["error_type"] == "player_still_active"
    assert env["data"]["active_players"] == [{"playerid": 1, "type": "video"}]


@pytest.mark.asyncio
async def test_player_seek_rejects_missing_seconds():
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    server, _ = build_mcp_server({"bridge": _FakeBridge(), "jsonrpc": _FakeJsonRpc(), "notifications": None})

    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="kodi_player_seek", arguments={"playerid": 1}),
        )
    )
    env = _tool_payload(resp)
    assert resp.root.isError is True
    assert env["ok"] is False
    assert env["error_type"] == "invalid_params"
    assert "seconds" in env["error"]
