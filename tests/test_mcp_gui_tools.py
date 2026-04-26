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
    import kodi_mcp_mcp.server_core as server_core
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    def _fake_store(image_base64: str):
        return {
            "screenshot_id": "shot-1",
            "filename": "shot-1.png",
            "url": "http://server/screenshots/shot-1.png",
            "content_type": "image/png",
            "size_bytes": 4,
        }

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", _fake_store)
    server, _ = build_mcp_server({"bridge": _FakeBridge(), "jsonrpc": _FakeJsonRpc(), "notifications": None})

    try:
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
        assert screenshot_env["data"]["server_screenshot"]["url"] == "http://server/screenshots/shot-1.png"
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_gui_screenshot_defaults_to_server_stored_without_base64():
    import kodi_mcp_mcp.server_core as server_core
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequest, CallToolRequestParams

    def _fake_store(image_base64: str):
        return {
            "screenshot_id": "shot-2",
            "filename": "shot-2.png",
            "url": "http://server/screenshots/shot-2.png",
            "content_type": "image/png",
            "size_bytes": 4,
        }

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", _fake_store)
    server, _ = build_mcp_server({"bridge": _FakeBridge(), "jsonrpc": _FakeJsonRpc(), "notifications": None})

    try:
        screenshot_resp = await server.request_handlers[CallToolRequest](
            CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(name="kodi_gui_screenshot", arguments={}),
            )
        )
        screenshot_env = json.loads(screenshot_resp.root.content[0].text)
        assert screenshot_env["ok"] is True
        assert screenshot_env["data"]["server_screenshot"]["url"] == "http://server/screenshots/shot-2.png"
        assert "image_base64" not in screenshot_env["data"]
    finally:
        monkeypatch.undo()


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
