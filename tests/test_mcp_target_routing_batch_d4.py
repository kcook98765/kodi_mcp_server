"""Phase 4A Batch D4 stateless bridge marker-routing tests."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest
from mcp.types import CallToolRequestParams

import kodi_mcp_mcp.server_core as server_core
import kodi_mcp_mcp.tool_contract as tool_contract
from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_server.models.messages import ErrorType, ResponseMessage
from kodi_mcp_server.targets.registry import LegacyTargetSettings, TargetRegistry


D4_TOOL_NAMES = frozenset({"bridge_write_log_marker"})
TARGET_PROPERTY = {
    "type": "string",
    "pattern": r"^[a-z0-9](?:[a-z0-9._-]{0,63})$",
}
STILL_UNTARGETED = frozenset(
    {
        "managed_addon_build_publish_and_stage",
        "managed_addon_build_publish_stage_and_apply",
        "repo_publish_stage_apply_artifact",
        "repo_stage_and_apply_addon",
        "repo_stage_current_dev_repo",
        "repository_bootstrap_install",
    }
)


@dataclass(frozen=True)
class _Bundle:
    jsonrpc: Any
    bridge: Any
    notifications: Any | None = None


class _CountingRegistry(TargetRegistry):
    def __init__(self, source: TargetRegistry) -> None:
        super().__init__()
        self._targets = dict(source._targets)
        self.calls: list[str] = []

    def get(self, target_id: str):
        self.calls.append(target_id)
        return super().get(target_id)


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


class _Bridge:
    def __init__(
        self,
        label: str,
        *,
        error: str | None = None,
        error_type: ErrorType | None = None,
        error_code: int | None = None,
        tripwire: bool = False,
    ) -> None:
        self.label = label
        self.error = error
        self.error_type = error_type
        self.error_code = error_code
        self.tripwire = tripwire
        self.calls: list[str] = []

    async def write_bridge_log_marker(self, message: str) -> ResponseMessage:
        if self.tripwire:
            raise AssertionError(f"unexpected marker write through {self.label}")
        self.calls.append(message)
        await asyncio.sleep(0)
        result = None
        if self.error is None:
            result = {
                "written": True,
                "message": message,
                "endpoint": f"https://{self.label}.routing-secret.invalid/bridge",
                "transport": {"token": f"{self.label}-transport-secret"},
            }
        return ResponseMessage(
            request_id=f"marker-{self.label}",
            result=result,
            error=self.error,
            error_type=self.error_type,
            error_code=self.error_code,
            latency_ms=7,
        )


def _target(target_id: str) -> dict[str, Any]:
    return {
        "id": target_id,
        "name": f"Target {target_id}",
        "endpoints": {
            "jsonrpc_url": f"https://{target_id}.routing-secret.invalid/jsonrpc",
            "bridge_url": f"https://{target_id}.routing-secret.invalid/bridge",
        },
    }


def _runtime(
    *,
    default_bridge: Any | None = None,
    target_bridges: dict[str, Any] | None = None,
):
    source_registry = TargetRegistry.from_sources(
        legacy=LegacyTargetSettings(
            jsonrpc_url="http://default.invalid/jsonrpc",
            bridge_url="http://default.invalid/bridge",
        ),
        targets_json=json.dumps([_target("kodi19"), _target("kodi22")]),
    )
    registry = _CountingRegistry(source_registry)
    default_bridge = default_bridge or _Bridge("default")
    target_bridges = target_bridges or {
        "kodi19": _Bridge("kodi19"),
        "kodi22": _Bridge("kodi22"),
    }
    bundles = {
        target_id: _Bundle(object(), bridge)
        for target_id, bridge in target_bridges.items()
    }
    pool = _Pool(bundles)
    runtime = {
        "registry": registry,
        "transport_pool": pool,
        "jsonrpc": _NoAccess(),
        "bridge": default_bridge,
        "notifications": _NoAccess(),
    }
    return runtime, registry, pool, default_bridge, bundles


async def _call(server, arguments: dict[str, Any]):
    return await server.get_request_handler("tools/call").handler(
        None,
        CallToolRequestParams(name="bridge_write_log_marker", arguments=arguments),
    )


def _envelope(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_exact_d4_inventory_and_marker_schema():
    runtime, _, _, _, _ = _runtime()
    server, _ = build_mcp_server(runtime)
    listed = await server.get_request_handler("tools/list").handler(None, None)
    by_name = {tool.name: tool for tool in listed.tools}
    targeted = {
        name
        for name, tool in by_name.items()
        if "target" in tool.input_schema.get("properties", {})
    }

    assert len(by_name) == 56
    assert tool_contract.BATCH_D3_TARGET_TOOL_NAMES == frozenset(
        {"kodi_notifications_sample"}
    )
    assert getattr(tool_contract, "BATCH_D4_TARGET_TOOL_NAMES", None) == D4_TOOL_NAMES
    assert tool_contract.EXPLICIT_TARGET_TOOL_NAMES == (
        tool_contract.BATCH_A_TARGET_TOOL_NAMES
        | tool_contract.BATCH_B_TARGET_TOOL_NAMES
        | tool_contract.BATCH_C1_TARGET_TOOL_NAMES
        | tool_contract.BATCH_C2_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D1_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D2_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D3_TARGET_TOOL_NAMES
        | D4_TOOL_NAMES
        | tool_contract.BATCH_D5_TARGET_TOOL_NAMES
        | tool_contract.BATCH_P1_TARGET_TOOL_NAMES
    )
    assert targeted == tool_contract.EXPLICIT_TARGET_TOOL_NAMES
    assert len(targeted) == 37
    assert by_name["bridge_write_log_marker"].input_schema == {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "minLength": 1,
                "pattern": r"\S",
                "description": "Marker text to write into the log. Use a unique token for traceability.",
            },
            "target": TARGET_PROPERTY,
        },
        "required": ["message"],
        "additionalProperties": False,
    }
    assert all(
        "target" not in by_name[name].input_schema["properties"]
        for name in STILL_UNTARGETED
    )
    assert by_name["target_health"].input_schema["required"] == ["target_id"]
    assert "target" not in by_name["target_health"].input_schema["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [42, "Kodi19", "https://not-a-target.invalid"])
async def test_malformed_target_fails_before_registry_pool_or_bridge_access(target):
    runtime = {
        "registry": _NoAccess(),
        "transport_pool": _NoAccess(),
        "jsonrpc": _NoAccess(),
        "bridge": _NoAccess(),
        "notifications": _NoAccess(),
    }
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"message": "d4-marker", "target": target}))

    assert result["error_type"] == "invalid_params"
    assert result["error_code"] is None


@pytest.mark.asyncio
async def test_credential_url_target_is_sanitized_without_io_or_argument_mutation(monkeypatch):
    runtime = {
        "registry": _NoAccess(),
        "transport_pool": _NoAccess(),
        "jsonrpc": _NoAccess(),
        "bridge": _NoAccess(),
        "notifications": _NoAccess(),
    }
    server, _ = build_mcp_server(runtime)
    target_url = "https://user:secret@example.invalid/path"
    arguments = {"message": "d4-marker", "target": target_url}
    original = deepcopy(arguments)
    monkeypatch.setattr(
        server_core,
        "resolve_target_context",
        lambda *_: pytest.fail("malformed target must not be resolved"),
    )

    call_result = await _call(server, arguments)
    result = _envelope(call_result)
    structured = json.dumps(call_result.structured_content, sort_keys=True)

    assert result["error_type"] == "invalid_params"
    assert call_result.structured_content == result
    assert result["raw"]["arguments"] == {
        "message": "d4-marker",
        "target": "[redacted]",
    }
    for rendered in (call_result.content[0].text, structured):
        assert target_url not in rendered
        assert "user:secret" not in rendered
        assert "secret" not in rendered
    assert arguments == original


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{"message": "   "}, {}], ids=["blank", "missing"])
async def test_blank_or_missing_message_sanitizes_malformed_target_without_io(
    arguments, monkeypatch
):
    runtime = {
        "registry": _NoAccess(),
        "transport_pool": _NoAccess(),
        "jsonrpc": _NoAccess(),
        "bridge": _NoAccess(),
        "notifications": _NoAccess(),
    }
    server, _ = build_mcp_server(runtime)
    target_url = "https://user:secret@example.invalid/path"
    arguments = {**arguments, "target": target_url}
    original = deepcopy(arguments)
    monkeypatch.setattr(
        server_core,
        "resolve_target_context",
        lambda *_: pytest.fail("blank/missing message must fail before routing"),
    )

    call_result = await _call(server, arguments)
    result = _envelope(call_result)
    structured = json.dumps(call_result.structured_content, sort_keys=True)

    assert result["error_type"] == "invalid_params"
    assert result["error"] == "missing required argument: message"
    assert result["raw"]["arguments"]["target"] == "[redacted]"
    for rendered in (call_result.content[0].text, structured):
        assert target_url not in rendered
        assert "user:secret" not in rendered
        assert "secret" not in rendered
    assert arguments == original


@pytest.mark.asyncio
async def test_unknown_target_is_not_found_without_pool_or_default_fallback():
    default = _Bridge("default", tripwire=True)
    runtime, registry, pool, _, bundles = _runtime(default_bridge=default)
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(server, {"message": "unknown-target-marker", "target": "missing"})
    )

    assert result["error_type"] == "not_found"
    assert result["error_code"] == 404
    assert registry.calls == ["missing"]
    assert pool.targets == []
    assert default.calls == []
    assert all(bundle.bridge.calls == [] for bundle in bundles.values())


@pytest.mark.asyncio
async def test_explicit_a_b_then_default_keep_exact_bridge_objects_and_no_state():
    default = _Bridge("default", tripwire=True)
    runtime, registry, pool, _, bundles = _runtime(default_bridge=default)
    server, _ = build_mcp_server(runtime)

    result_a = _envelope(
        await _call(server, {"message": "marker-a", "target": "kodi19"})
    )
    result_b = _envelope(
        await _call(server, {"message": "marker-b", "target": "kodi22"})
    )
    default.tripwire = False
    result_default = _envelope(await _call(server, {"message": "marker-default"}))

    assert [result_a["raw"]["target_id"], result_b["raw"]["target_id"]] == [
        "kodi19",
        "kodi22",
    ]
    assert "target_id" not in result_default["raw"]
    assert bundles["kodi19"].bridge.calls == ["marker-a"]
    assert bundles["kodi22"].bridge.calls == ["marker-b"]
    assert default.calls == ["marker-default"]
    assert runtime["bridge"] is default
    assert registry.calls == ["kodi19", "kodi22"]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi22"]


@pytest.mark.asyncio
async def test_concurrent_a_b_calls_keep_bridge_writes_isolated():
    default = _Bridge("default", tripwire=True)
    runtime, registry, pool, _, bundles = _runtime(default_bridge=default)
    server, _ = build_mcp_server(runtime)

    first, second = await asyncio.gather(
        _call(server, {"message": "concurrent-a", "target": "kodi19"}),
        _call(server, {"message": "concurrent-b", "target": "kodi22"}),
    )
    results = [_envelope(first), _envelope(second)]

    assert [result["raw"]["target_id"] for result in results] == [
        "kodi19",
        "kodi22",
    ]
    assert bundles["kodi19"].bridge.calls == ["concurrent-a"]
    assert bundles["kodi22"].bridge.calls == ["concurrent-b"]
    assert default.calls == []
    assert registry.calls == ["kodi19", "kodi22"]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi22"]


@pytest.mark.asyncio
async def test_selected_bridge_unavailable_fails_without_default_fallback():
    default = _Bridge("default", tripwire=True)
    runtime, registry, pool, _, _ = _runtime(
        default_bridge=default,
        target_bridges={"kodi19": None, "kodi22": _Bridge("kodi22")},
    )
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(server, {"message": "unavailable-marker", "target": "kodi19"})
    )

    assert result["ok"] is False
    assert result["raw"]["target_id"] == "kodi19"
    assert default.calls == []
    assert registry.calls == ["kodi19"]
    assert [target.target_id for target in pool.targets] == ["kodi19"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "error_type", "error_code"),
    [
        ("connection refused", ErrorType.NETWORK_ERROR, None),
        ("authentication failed", ErrorType.AUTH_ERROR, 401),
        ("marker endpoint missing", ErrorType.NOT_FOUND, 404),
        ("bridge failed", ErrorType.SERVER_ERROR, 503),
        ("request timed out; marker outcome unknown", ErrorType.TIMEOUT, None),
        ("malformed bridge response", ErrorType.PARSE_ERROR, None),
    ],
    ids=["network", "auth", "http-404", "server-5xx", "timeout", "parse"],
)
async def test_selected_target_failures_call_marker_helper_at_most_once_without_fallback(
    error, error_type, error_code
):
    selected = _Bridge(
        "kodi19", error=error, error_type=error_type, error_code=error_code
    )
    default = _Bridge("default", tripwire=True)
    runtime, _, pool, _, _ = _runtime(
        default_bridge=default,
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22")},
    )
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(server, {"message": "one-shot-marker", "target": "kodi19"})
    )

    assert result["ok"] is False
    assert result["error_type"] == error_type.value
    assert result["error_code"] == error_code
    assert result["raw"]["target_id"] == "kodi19"
    assert selected.calls == ["one-shot-marker"]
    assert default.calls == []
    assert [target.target_id for target in pool.targets] == ["kodi19"]
    if error_type is ErrorType.TIMEOUT:
        assert "not written" not in result["error"].lower()


@pytest.mark.asyncio
async def test_omitted_target_uses_exact_legacy_bridge_once_without_lookup_or_attribution():
    default = _Bridge("default")
    runtime = {
        "registry": _NoAccess(),
        "transport_pool": _NoAccess(),
        "jsonrpc": _NoAccess(),
        "bridge": default,
        "notifications": _NoAccess(),
    }
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"message": "legacy-marker"}))

    assert result["ok"] is True
    assert default.calls == ["legacy-marker"]
    assert runtime["bridge"] is default
    assert result["data"]["message"] == "legacy-marker"
    assert "target_id" not in result["raw"]
    assert "target_name" not in result["raw"]


@pytest.mark.asyncio
async def test_explicit_output_redacts_transport_secrets_but_preserves_marker_content():
    marker = "caller chose https://caller:chosen@example.invalid/path token=visible"
    default = _Bridge("default", tripwire=True)
    runtime, _, _, _, _ = _runtime(default_bridge=default)
    server, _ = build_mcp_server(runtime)

    call_result = await _call(server, {"message": marker, "target": "kodi19"})
    result = _envelope(call_result)
    structured = json.dumps(call_result.structured_content, sort_keys=True)

    assert call_result.structured_content == result
    assert result["data"]["message"] == marker
    assert result["raw"]["result"]["message"] == marker
    assert result["data"]["endpoint"] == "[redacted]"
    assert result["data"]["transport"] == {"token": "[redacted]"}
    assert result["raw"]["target_id"] == "kodi19"
    assert result["raw"]["target_name"] == "Target kodi19"
    for rendered in (call_result.content[0].text, structured):
        assert marker in rendered
        assert "routing-secret.invalid" not in rendered
        assert "kodi19-transport-secret" not in rendered
