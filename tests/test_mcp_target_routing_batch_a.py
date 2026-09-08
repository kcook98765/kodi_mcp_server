"""Phase 4A Batch A central stateless target-routing tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest
from mcp.client import Client
from mcp.types import CallToolRequestParams

import kodi_mcp_mcp.server_core as server_core
from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_mcp.target_routing import (
    finalize_target_envelope,
    resolve_target_context,
)
from kodi_mcp_mcp.tool_contract import BATCH_A_TARGET_TOOL_NAMES
from kodi_mcp_server.models.messages import ResponseMessage
from kodi_mcp_server.targets.registry import LegacyTargetSettings, TargetRegistry
from kodi_mcp_server.targets.resolver import TargetNotFoundError


@dataclass(frozen=True)
class _Bundle:
    jsonrpc: Any
    bridge: Any
    notifications: Any | None = None


class _Pool:
    def __init__(self, bundles: dict[str, _Bundle]) -> None:
        self.bundles = bundles
        self.targets = []

    def get_for_target(self, target):
        self.targets.append(target)
        return self.bundles[target.target_id]


class _NoAccess:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected access: {name}")


class _JsonRpc:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[str] = []

    def _response(self, operation: str, result: Any) -> ResponseMessage:
        self.calls.append(operation)
        return ResponseMessage(request_id=f"{operation}-{self.label}", result=result, error=None)

    async def get_active_players(self):
        return self._response("get_active_players", [{"playerid": 1, "label": self.label}])

    async def get_player_item(self, playerid: int = 1):
        return self._response("get_player_item", {"item": {"label": self.label}, "playerid": playerid})

    async def list_addons(self, **_kwargs):
        return self._response("list_addons", {"addons": [{"addonid": f"plugin.{self.label}"}]})

    async def get_addon_details(self, addonid: str):
        return self._response("get_addon_details", {"addon": {"addonid": addonid, "name": self.label}})


class _Bridge:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[str] = []

    async def get_bridge_health(self):
        self.calls.append("get_bridge_health")
        return ResponseMessage(request_id=f"health-{self.label}", result={"status": "ok", "label": self.label}, error=None)

    async def get_bridge_runtime_info(self):
        self.calls.append("get_bridge_runtime_info")
        return ResponseMessage(request_id=f"runtime-{self.label}", result={"label": self.label}, error=None)


class _RuntimeInfoBridge(_Bridge):
    async def get_bridge_runtime_info(self):
        self.calls.append("get_bridge_runtime_info")
        return ResponseMessage(
            request_id=f"runtime-{self.label}",
            result={
                "status": "ok",
                "label": "Kodi MCP bridge runtime",
                "service": "service.kodi_mcp",
                "addon_id": "service.kodi_mcp",
                "addon_version": "0.2.40",
                "bind_host": "0.0.0.0",
                "bind_port": 8765,
                "network": {
                    "endpoint_url": "http://runtime-node.internal:8765/status",
                    "hostname": "runtime-node.internal",
                    "service_port": 8765,
                },
            },
            error=None,
        )


VALID_ARGUMENTS = {
    "addon_details": {"addonid": "plugin.test"},
    "addon_list": {},
    "bridge_health": {},
    "bridge_log_markers": {},
    "bridge_log_recent_errors": {},
    "bridge_log_tail": {},
    "bridge_runtime_info": {},
    "bridge_status": {},
    "jsonrpc_introspect": {},
    "kodi_album_songs": {"album_id": 1},
    "kodi_artist_albums": {"artist_id": 1},
    "kodi_gui_state": {},
    "kodi_library_browse": {"category": "recent_movies"},
    "kodi_library_search": {"query": "test", "media_type": "movie"},
    "kodi_library_summary": {},
    "kodi_music_browse": {"category": "recent_albums"},
    "kodi_music_search": {"query": "test", "media_type": "song"},
    "kodi_music_summary": {},
    "kodi_player_active": {},
    "kodi_player_item": {},
    "kodi_setting_get": {"setting_id": "filelists.showextensions"},
    "kodi_status": {},
    "kodi_tv_episodes": {"tvshow_id": 1, "season": 1},
    "kodi_tv_seasons": {"tvshow_id": 1},
}

assert VALID_ARGUMENTS.keys() == BATCH_A_TARGET_TOOL_NAMES


def _target(target_id: str) -> dict[str, Any]:
    upper = target_id.upper()
    return {
        "id": target_id,
        "name": f"Target {target_id}",
        "endpoints": {
            "jsonrpc_url": f"https://{target_id}.routing-secret.invalid/jsonrpc",
            "bridge_url": f"https://{target_id}.routing-secret.invalid/bridge",
        },
        "auth": {
            "jsonrpc_username": f"env:{upper}_USERNAME",
            "jsonrpc_password": f"env:{upper}_PASSWORD",
            "bridge_token": f"env:{upper}_TOKEN",
        },
    }


def _runtime():
    registry = TargetRegistry.from_sources(
        legacy=LegacyTargetSettings(
            jsonrpc_url="http://default.invalid/jsonrpc",
            bridge_url="http://default.invalid/bridge",
        ),
        targets_json=json.dumps([_target("kodi19"), _target("kodi21")]),
    )
    default = _Bundle(_JsonRpc("default"), _Bridge("default"))
    bundles = {
        "kodi19": _Bundle(_JsonRpc("kodi19"), _Bridge("kodi19")),
        "kodi21": _Bundle(_JsonRpc("kodi21"), _Bridge("kodi21")),
    }
    pool = _Pool(bundles)
    return {
        "registry": registry,
        "transport_pool": pool,
        "jsonrpc": default.jsonrpc,
        "bridge": default.bridge,
        "notifications": default.notifications,
    }, pool, default, bundles


async def _call(server, name: str, arguments: dict[str, Any]):
    return await server.get_request_handler("tools/call").handler(
        None, CallToolRequestParams(name=name, arguments=arguments)
    )


def _envelope(result):
    return json.loads(result.content[0].text)


def test_omitted_target_uses_exact_legacy_objects_without_registry_or_pool():
    jsonrpc = object()
    bridge = object()
    notifications = object()
    runtime = {
        "registry": _NoAccess(),
        "transport_pool": _NoAccess(),
        "jsonrpc": jsonrpc,
        "bridge": bridge,
        "notifications": notifications,
    }

    context = resolve_target_context(runtime, {})

    assert context.explicit is False
    assert context.target is None
    assert context.transports.jsonrpc is jsonrpc
    assert context.transports.bridge is bridge
    assert context.transports.notifications is notifications


def test_explicit_target_resolves_once_and_uses_the_target_bundle():
    runtime, pool, _, bundles = _runtime()

    context = resolve_target_context(runtime, {"target": "kodi21"})

    assert context.explicit is True
    assert context.target.target_id == "kodi21"
    assert context.transports is bundles["kodi21"]
    assert [target.target_id for target in pool.targets] == ["kodi21"]


def test_unknown_explicit_target_fails_without_pool_or_default_fallback():
    runtime, pool, default, _ = _runtime()

    with pytest.raises(TargetNotFoundError):
        resolve_target_context(runtime, {"target": "missing"})

    assert pool.targets == []
    assert default.jsonrpc.calls == []
    assert default.bridge.calls == []


def test_central_finalizer_is_field_aware_and_adds_safe_identity(monkeypatch):
    runtime, _, _, _ = _runtime()
    monkeypatch.setenv("KODI21_USERNAME", "kodi")
    monkeypatch.setenv("KODI21_PASSWORD", "status")
    monkeypatch.setenv("KODI21_TOKEN", "mcp")
    context = resolve_target_context(runtime, {"target": "kodi21"})
    envelope = {
        "ok": False,
        "tool": "kodi_status",
        "data": {"addon_id": "service.kodi_mcp", "version": "21.0-status"},
        "error": "https://kodi21.routing-secret.invalid/bridge status mcp",
        "error_type": "network_error",
        "error_code": None,
        "latency_ms": 1,
        "request_id": None,
        "raw": {"ordinary": "kodi_status", "target_id": "downstream-spoof"},
    }

    routed = finalize_target_envelope(envelope, context)
    rendered = json.dumps(routed)

    assert routed["tool"] == "kodi_status"
    assert routed["data"]["addon_id"] == "service.kodi_mcp"
    assert routed["data"]["version"] == "21.0-status"
    assert routed["raw"]["ordinary"] == "kodi_status"
    assert routed["raw"]["target_id"] == "kodi21"
    assert routed["raw"]["target_name"] == "Target kodi21"
    for forbidden in ("routing-secret.invalid", " status mcp", "downstream-spoof"):
        assert forbidden not in rendered


@pytest.mark.asyncio
async def test_explicit_bridge_runtime_info_redacts_location_fields_and_keeps_contract():
    runtime, _, _, bundles = _runtime()
    bundles["kodi21"] = _Bundle(
        bundles["kodi21"].jsonrpc,
        _RuntimeInfoBridge("kodi21"),
    )
    server, _ = build_mcp_server(runtime)

    async with Client(server, mode="auto") as client:
        result = await client.call_tool(
            "bridge_runtime_info", {"target": "kodi21"}
        )

    envelope = _envelope(result)
    assert result.is_error is False
    assert result.structured_content is not None
    assert envelope["tool"] == "bridge_runtime_info"
    assert envelope["raw"]["target_id"] == "kodi21"
    assert envelope["raw"]["target_name"] == "Target kodi21"
    assert envelope["data"]["service"] == "service.kodi_mcp"
    assert envelope["data"]["addon_id"] == "service.kodi_mcp"
    assert envelope["data"]["addon_version"] == "0.2.40"
    assert envelope["data"]["label"] == "Kodi MCP bridge runtime"
    assert envelope["data"]["bind_host"] == "[redacted]"
    assert envelope["data"]["bind_port"] == "[redacted]"
    assert envelope["data"]["network"] == {
        "endpoint_url": "[redacted]",
        "hostname": "[redacted]",
        "service_port": "[redacted]",
    }
    for public_branch in (envelope["data"], envelope["raw"]):
        rendered = json.dumps(public_branch, sort_keys=True)
        for forbidden in (
            "0.0.0.0",
            "runtime-node.internal",
            "http://runtime-node.internal:8765/status",
            "8765",
        ):
            assert forbidden not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "result_key", "expected"),
    [
        ("bridge_health", "label", "kodi21"),
        ("bridge_runtime_info", "label", "kodi21"),
        ("kodi_player_active", 0, "kodi21"),
        ("kodi_player_item", "item", "kodi21"),
        ("addon_list", "addons", "plugin.kodi21"),
        ("addon_details", "addon", "kodi21"),
    ],
)
async def test_representative_batch_a_dispatch_uses_explicit_bundle(
    tool_name, result_key, expected
):
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)
    arguments = {"target": "kodi21"}
    if tool_name == "kodi_player_item":
        arguments["playerid"] = 1
    elif tool_name == "addon_details":
        arguments["addonid"] = "plugin.requested"

    result = _envelope(await _call(server, tool_name, arguments))

    assert result["ok"] is True
    assert result["raw"]["target_id"] == "kodi21"
    if tool_name == "kodi_player_active":
        assert result["data"][result_key]["label"] == expected
    elif tool_name == "kodi_player_item":
        assert result["data"][result_key]["label"] == expected
    elif tool_name == "addon_list":
        assert result["data"][result_key][0]["addonid"] == expected
    elif tool_name == "addon_details":
        assert result["data"][result_key]["name"] == expected
    else:
        assert result["data"][result_key] == expected
    assert [target.target_id for target in pool.targets] == ["kodi21"]
    assert default.jsonrpc.calls == []
    assert default.bridge.calls == []
    assert bundles["kodi19"].jsonrpc.calls == []
    assert bundles["kodi19"].bridge.calls == []
    if tool_name.startswith("bridge_"):
        assert bundles["kodi21"].jsonrpc.calls == []
    else:
        assert bundles["kodi21"].bridge.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", sorted(BATCH_A_TARGET_TOOL_NAMES))
async def test_every_batch_a_tool_rejects_malformed_target_before_dispatch(tool_name):
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)
    arguments = {**VALID_ARGUMENTS[tool_name], "target": "https://not-a-target.invalid"}

    result = _envelope(await _call(server, tool_name, arguments))

    assert result["ok"] is False
    assert result["error_type"] == "invalid_params"
    assert pool.targets == []
    assert default.jsonrpc.calls == []
    assert default.bridge.calls == []
    assert bundles["kodi19"].jsonrpc.calls == []
    assert bundles["kodi21"].bridge.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", sorted(BATCH_A_TARGET_TOOL_NAMES))
async def test_every_batch_a_tool_rejects_unknown_target_without_fallback(tool_name):
    runtime, pool, default, _ = _runtime()
    server, _ = build_mcp_server(runtime)
    arguments = {**VALID_ARGUMENTS[tool_name], "target": "missing"}

    result = _envelope(await _call(server, tool_name, arguments))

    assert result["ok"] is False
    assert result["error_type"] == "not_found"
    assert result["error_code"] == 404
    assert pool.targets == []
    assert default.jsonrpc.calls == []
    assert default.bridge.calls == []


@pytest.mark.asyncio
async def test_bridge_route_interleaves_a_b_a_and_default_without_state_leakage():
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    labels = []
    for arguments in (
        {"target": "kodi19"},
        {"target": "kodi21"},
        {"target": "kodi19"},
        {},
    ):
        result = _envelope(await _call(server, "bridge_health", arguments))
        labels.append(result["data"]["label"])

    assert labels == ["kodi19", "kodi21", "kodi19", "default"]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi21", "kodi19"]
    assert bundles["kodi19"].bridge.calls == ["get_bridge_health", "get_bridge_health"]
    assert bundles["kodi21"].bridge.calls == ["get_bridge_health"]
    assert default.bridge.calls == ["get_bridge_health"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "class_name", "method_name", "arguments"),
    [
        ("kodi_library_summary", "LibraryTool", "summary", {}),
        ("kodi_music_summary", "MusicTool", "summary", {}),
        (
            "kodi_setting_get",
            "SettingsTool",
            "get_setting",
            {"setting_id": "filelists.showextensions"},
        ),
    ],
)
async def test_batch_a_wrappers_receive_the_explicit_jsonrpc_dependency(
    monkeypatch, tool_name, class_name, method_name, arguments
):
    runtime, _, default, bundles = _runtime()
    captured = []

    class _ProbeTool:
        def __init__(self, jsonrpc):
            captured.append(jsonrpc)

    async def _operation(self, *_args, **_kwargs):
        return ResponseMessage(request_id="probe", result=None, error="probe complete")

    setattr(_ProbeTool, method_name, _operation)
    monkeypatch.setattr(server_core, class_name, _ProbeTool)
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(server, tool_name, {**arguments, "target": "kodi21"})
    )

    assert result["ok"] is False
    assert result["raw"]["target_id"] == "kodi21"
    assert captured == [bundles["kodi21"].jsonrpc]
    assert captured[0] is not default.jsonrpc


@pytest.mark.asyncio
async def test_concurrent_read_only_targets_keep_their_own_transport_bundles():
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    first, second = await asyncio.gather(
        _call(server, "bridge_health", {"target": "kodi19"}),
        _call(server, "bridge_health", {"target": "kodi21"}),
    )

    assert [_envelope(first)["data"]["label"], _envelope(second)["data"]["label"]] == [
        "kodi19",
        "kodi21",
    ]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi21"]
    assert bundles["kodi19"].bridge.calls == ["get_bridge_health"]
    assert bundles["kodi21"].bridge.calls == ["get_bridge_health"]
    assert default.bridge.calls == []


@pytest.mark.asyncio
async def test_explicit_resolution_performs_no_hidden_health_probe():
    runtime, _, default, bundles = _runtime()

    class _TripwireJsonRpc(_JsonRpc):
        async def get_jsonrpc_version(self):
            raise AssertionError("ordinary routing must not probe JSON-RPC health")

    class _TripwireBridge(_Bridge):
        async def get_bridge_health(self):
            raise AssertionError("ordinary routing must not probe bridge health")

    routed_jsonrpc = _TripwireJsonRpc("kodi21")
    routed_bridge = _TripwireBridge("kodi21")
    bundles["kodi21"] = _Bundle(routed_jsonrpc, routed_bridge)
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(server, "kodi_player_active", {"target": "kodi21"})
    )

    assert result["ok"] is True
    assert result["data"][0]["label"] == "kodi21"
    assert routed_jsonrpc.calls == ["get_active_players"]
    assert routed_bridge.calls == []
    assert default.jsonrpc.calls == []
    assert default.bridge.calls == []
