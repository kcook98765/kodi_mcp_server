import json

import pytest

from kodi_mcp_server.models.messages import ResponseMessage


class _FakeBridge:
    async def gui_action(self, action: str):
        return ResponseMessage(
            request_id="fake-gui-action",
            result={"ok": True, "action": action, "method": "Input.%s" % action.title()},
            error=None,
        )

    async def gui_screenshot(self, include_image: bool = False):
        result = {
            "ok": True,
            "path": "/tmp/kodi-screen.png",
            "content_type": "image/png",
            "size_bytes": 12,
        }
        if include_image:
            result["image_base64"] = "ZmFrZQ=="
        return ResponseMessage(
            request_id="fake-gui-screenshot",
            result=result,
            error=None,
        )


class _FakeJsonRpc:
    pass


@pytest.mark.asyncio
async def test_gui_tools_dispatch_through_bridge():
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    server, _ = build_mcp_server({"bridge": _FakeBridge(), "jsonrpc": _FakeJsonRpc(), "notifications": None})

    action_resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="kodi_gui_action", arguments={"action": "down"}),
        )
    )
    action_env = json.loads(action_resp.root.content[0].text)
    assert action_env["ok"] is True
    assert action_env["data"]["action"] == "down"

    screenshot_resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="kodi_gui_screenshot", arguments={"include_image": True}),
        )
    )
    screenshot_env = json.loads(screenshot_resp.root.content[0].text)
    assert screenshot_env["ok"] is True
    assert screenshot_env["data"]["content_type"] == "image/png"
    assert screenshot_env["data"]["image_base64"] == "ZmFrZQ=="


@pytest.mark.asyncio
async def test_gui_action_rejects_invalid_action():
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    server, _ = build_mcp_server({"bridge": _FakeBridge(), "jsonrpc": _FakeJsonRpc(), "notifications": None})

    resp = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="kodi_gui_action", arguments={"action": "launch"}),
        )
    )
    env = json.loads(resp.root.content[0].text)
    assert resp.root.isError is True
    assert env["ok"] is False
    assert env["error_type"] == "invalid_params"
    assert "select" in env["raw"]["allowed"]
