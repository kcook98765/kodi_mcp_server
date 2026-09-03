"""Bounded normalization of Kodi JSON-RPC ``-32602`` responses."""

import json

import pytest
from mcp.types import CallToolRequestParams

from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_server.models.messages import ErrorType, ResponseMessage
from kodi_mcp_server.tools.jsonrpc import JsonRpcTool


class _FixedTransport:
    def __init__(self, response: ResponseMessage):
        self.response = response
        self.requests = []

    async def send_request(self, request):
        self.requests.append(request)
        return self.response


class _UnusedBridge:
    pass


class _FailIfCalledTransport:
    def __init__(self):
        self.called = False

    async def send_request(self, request):
        self.called = True
        raise AssertionError("invalid input reached Kodi transport")


class _AddonExecuteTransport:
    async def send_request(self, request):
        if request.args["method"] == "Player.GetActivePlayers":
            return ResponseMessage(request_id="active", result=[], error=None)
        return _invalid_params_response()


def _invalid_params_response():
    return ResponseMessage(
        request_id="kodi-invalid-params",
        result=None,
        error="jsonrpc error -32602: Invalid params.",
        error_type=ErrorType.SERVER_ERROR,
        error_code=-32602,
        latency_ms=7,
    )


def _payload(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("addon_details", {}),
        ("addon_details", {"addonid": ""}),
        ("addon_details", {"addonid": "Plugin.Video.Invalid"}),
        ("kodi_player_item", {"playerid": "1"}),
        ("kodi_player_item", {"playerid": -1}),
        ("kodi_player_item", {"playerid": True}),
    ],
)
async def test_structurally_invalid_identifiers_are_rejected_before_kodi(
    tool_name, arguments
):
    transport = _FailIfCalledTransport()
    server, _ = build_mcp_server(
        {
            "bridge": _UnusedBridge(),
            "jsonrpc": JsonRpcTool(transport),
            "notifications": None,
        }
    )

    result = await server.get_request_handler("tools/call").handler(
        None, CallToolRequestParams(name=tool_name, arguments=arguments)
    )

    assert result.is_error is True
    assert _payload(result)["error_type"] == "invalid_params"
    assert transport.called is False


@pytest.mark.asyncio
async def test_normalized_identifier_messages_are_bounded():
    addon_response = await JsonRpcTool(
        _FixedTransport(_invalid_params_response())
    ).get_addon_details("plugin." + "x" * 1000)
    player_response = await JsonRpcTool(
        _FixedTransport(_invalid_params_response())
    ).get_player_item(10**1000)

    assert len(addon_response.error) < 256
    assert "…" in addon_response.error
    assert len(player_response.error) < 128
    assert "player supplied" in player_response.error


@pytest.mark.asyncio
async def test_addon_details_unknown_id_is_actionable_at_mcp_boundary():
    transport = _FixedTransport(_invalid_params_response())
    server, _ = build_mcp_server(
        {
            "bridge": _UnusedBridge(),
            "jsonrpc": JsonRpcTool(transport),
            "notifications": None,
        }
    )

    result = await server.get_request_handler("tools/call").handler(
        None,
        CallToolRequestParams(
            name="addon_details", arguments={"addonid": "plugin.video.missing"}
        ),
    )

    envelope = _payload(result)
    assert result.is_error is True
    assert envelope["error_type"] == "not_found"
    assert envelope["error_code"] == -32602
    assert envelope["error"] == (
        "addon 'plugin.video.missing' is not installed or not recognized by Kodi; "
        "call addon_list to discover installed addon IDs"
    )
    assert transport.requests[0].args["method"] == "Addons.GetAddonDetails"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "expected_method"),
    [
        (lambda tool: tool.get_player_item(playerid=99), "Player.GetItem"),
        (lambda tool: tool.pause_player(playerid=99), "Player.PlayPause"),
        (lambda tool: tool.stop_player(playerid=99), "Player.Stop"),
    ],
)
async def test_inactive_player_id_is_normalized_without_preflight(
    operation, expected_method
):
    transport = _FixedTransport(_invalid_params_response())

    response = await operation(JsonRpcTool(transport))

    assert response.error_type == ErrorType.NOT_FOUND
    assert response.error_code == -32602
    assert response.error == (
        "player 99 is not active; call kodi_player_active to discover active player IDs"
    )
    assert [request.args["method"] for request in transport.requests] == [expected_method]


@pytest.mark.asyncio
async def test_ambiguous_seek_invalid_params_is_actionable_but_not_mislabeled_inactive():
    transport = _FixedTransport(_invalid_params_response())

    response = await JsonRpcTool(transport).seek_player_to_seconds(
        playerid=99, seconds=12.5
    )

    assert response.error_type == ErrorType.INVALID_PARAMS
    assert response.error_code == -32602
    assert response.error == (
        "Kodi rejected the supplied parameters for Player.Seek; verify playerid with "
        "kodi_player_active and confirm the current item is seekable"
    )
    assert "not active" not in response.error


@pytest.mark.asyncio
async def test_ambiguous_addon_execute_invalid_params_is_not_mislabeled_not_found():
    transport = _FixedTransport(_invalid_params_response())

    response = await JsonRpcTool(transport).execute_addon(
        addonid="plugin.video.example", params={"mode": "unsupported"}
    )

    assert response.error_type == ErrorType.INVALID_PARAMS
    assert response.error_code == -32602
    assert response.error == (
        "Kodi rejected the supplied parameters for Addons.ExecuteAddon; verify the addon ID "
        "with addon_list and check addon-specific params"
    )
    assert response.error_type != ErrorType.NOT_FOUND


@pytest.mark.asyncio
async def test_ambiguous_addon_execute_error_reaches_mcp_boundary():
    server, _ = build_mcp_server(
        {
            "bridge": _UnusedBridge(),
            "jsonrpc": JsonRpcTool(_AddonExecuteTransport()),
            "notifications": None,
        }
    )

    result = await server.get_request_handler("tools/call").handler(
        None,
        CallToolRequestParams(
            name="addon_execute",
            arguments={
                "addonid": "plugin.video.example",
                "params": {"mode": "unsupported"},
                "observe_player_seconds": 0,
                "include_gui_state": False,
            },
        ),
    )

    envelope = _payload(result)
    assert result.is_error is True
    assert envelope["error_type"] == "invalid_params"
    assert envelope["error_code"] == -32602
    assert envelope["error"].startswith(
        "Kodi rejected the supplied parameters for Addons.ExecuteAddon"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("media_type", ["movie", "episode", "album", "song"])
async def test_typed_library_item_invalid_params_is_normalized_as_not_found(media_type):
    transport = _FixedTransport(_invalid_params_response())

    response = await JsonRpcTool(transport).open_library_item(
        media_type=media_type, item_id=999999
    )

    assert response.error == f"{media_type} 999999 was not found"
    assert response.error_type == ErrorType.NOT_FOUND
    assert response.error_code == -32602


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        ResponseMessage(
            request_id="method-not-found",
            result=None,
            error="jsonrpc error -32601: Method not found.",
            error_type=ErrorType.SERVER_ERROR,
            error_code=-32601,
        ),
        ResponseMessage(
            request_id="internal",
            result=None,
            error="jsonrpc error -32603: Internal error.",
            error_type=ErrorType.SERVER_ERROR,
            error_code=-32603,
        ),
        ResponseMessage(
            request_id="transport",
            result=None,
            error="connection error: refused",
            error_type=ErrorType.NETWORK_ERROR,
        ),
    ],
)
async def test_unrelated_kodi_and_transport_errors_pass_through_unchanged(response):
    transport = _FixedTransport(response)

    actual = await JsonRpcTool(transport).get_player_item(playerid=1)

    assert actual is response


@pytest.mark.asyncio
async def test_valid_player_input_still_reaches_kodi_and_succeeds_unchanged():
    success = ResponseMessage(
        request_id="success", result={"item": {"label": "Example"}}, error=None
    )
    transport = _FixedTransport(success)

    actual = await JsonRpcTool(transport).get_player_item(playerid=1)

    assert actual is success
    assert transport.requests[0].args == {
        "method": "Player.GetItem",
        "params": {
            "playerid": 1,
            "properties": ["title", "album", "artist", "season", "episode"],
        },
    }
