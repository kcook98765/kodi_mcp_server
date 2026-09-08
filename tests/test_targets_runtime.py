from unittest.mock import AsyncMock

import pytest

from kodi_mcp_mcp import server_core
from kodi_mcp_server import config
from kodi_mcp_server.models.messages import ResponseMessage


def _patch_legacy_config(monkeypatch):
    values = {
        "KODI_JSONRPC_URL": "http://legacy:8080/jsonrpc",
        "KODI_BRIDGE_BASE_URL": "http://legacy:8765",
        "KODI_WEBSOCKET_URL": "ws://legacy:9090/jsonrpc",
        "KODI_TCP_HOST": "legacy",
        "KODI_TCP_PORT": 9090,
        "KODI_TIMEOUT": 19,
        "KODI_JSONRPC_USERNAME": "legacy-user",
        "KODI_JSONRPC_PASSWORD": "legacy-password",
        "KODI_BRIDGE_TOKEN": "legacy-token",
        "KODI_MCP_TARGETS": "",
        "KODI_MCP_TARGETS_FILE": "",
    }
    for name, value in values.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(server_core, "KODI_JSONRPC_URL", values["KODI_JSONRPC_URL"])
    monkeypatch.setattr(server_core, "KODI_BRIDGE_BASE_URL", values["KODI_BRIDGE_BASE_URL"])
    return values


def test_build_runtime_exposes_default_pool_transports_through_legacy_keys(monkeypatch):
    values = _patch_legacy_config(monkeypatch)

    runtime = server_core.build_runtime()
    transports = runtime["transport_pool"].get("default")

    assert runtime["registry"].get("default").endpoints.jsonrpc_url == values["KODI_JSONRPC_URL"]
    assert runtime["jsonrpc"] is transports.jsonrpc
    assert runtime["bridge"] is transports.bridge
    assert runtime["notifications"] is transports.notifications
    assert runtime["jsonrpc"].transport.username == "legacy-user"
    assert runtime["jsonrpc"].transport.password == "legacy-password"
    assert runtime["bridge"].client.token == "legacy-token"


@pytest.mark.asyncio
async def test_kodi_status_contract_and_default_routing_are_unchanged(monkeypatch):
    _patch_legacy_config(monkeypatch)
    monkeypatch.setattr(server_core, "VISION_ENABLED", False)
    runtime = server_core.build_runtime()
    runtime["jsonrpc"].get_jsonrpc_version = AsyncMock(
        return_value=ResponseMessage(
            request_id="version",
            result={"version": {"major": 13, "minor": 0, "patch": 0}},
        )
    )
    runtime["jsonrpc"].get_application_properties = AsyncMock(
        return_value=ResponseMessage(
            request_id="application",
            result={"name": "Kodi", "version": {"major": 21, "minor": 3}},
        )
    )
    runtime["bridge"].get_bridge_health = AsyncMock(
        return_value=ResponseMessage(request_id="bridge", result={"status": "ok"})
    )

    result = await server_core._kodi_status(runtime)

    assert result == {
        "server": {"status": "running"},
        "config": {"loaded": True},
        "jsonrpc": {
            "status": "ok",
            "url": "http://legacy:8080/jsonrpc",
            "version": {"major": 13, "minor": 0, "patch": 0},
        },
        "kodi": {
            "status": "ok",
            "name": "Kodi",
            "version": {"major": 21, "minor": 3},
        },
        "bridge": {"status": "ok", "url": "http://legacy:8765"},
        "vision": {
            "enabled": False,
            "tools_available": [],
            "note": "Screenshot capture is available; vision analysis tools require explicit vision model configuration.",
        },
    }
