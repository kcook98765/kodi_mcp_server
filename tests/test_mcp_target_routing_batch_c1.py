"""Phase 4A Batch C1 stateless settings-mutation routing tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest
from mcp.client import Client
from mcp.types import CallToolRequestParams

from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_mcp.tool_contract import (
    BATCH_A_TARGET_TOOL_NAMES,
    BATCH_B_TARGET_TOOL_NAMES,
    BATCH_D1_TARGET_TOOL_NAMES,
    BATCH_D2_TARGET_TOOL_NAMES,
    BATCH_D3_TARGET_TOOL_NAMES,
    BATCH_D4_TARGET_TOOL_NAMES,
    BATCH_D5_TARGET_TOOL_NAMES,
    BATCH_P1_TARGET_TOOL_NAMES,
)
from kodi_mcp_server.models.messages import ErrorType, ResponseMessage
from kodi_mcp_server.targets.registry import LegacyTargetSettings, TargetRegistry
from kodi_mcp_server.transport.http_jsonrpc import is_safe_to_retry


C1_TOOL_NAMES = frozenset({"kodi_setting_set"})
SETTING_ID = "filelists.showextensions"
DEFERRED_TOOL_NAMES = frozenset(
    {
        "managed_addon_build_publish_stage_and_apply",
        "repo_stage_and_apply_addon",
        "repository_bootstrap_install",
    }
)


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


class _BridgeTripwire:
    def __getattr__(self, name):
        raise AssertionError(f"settings mutation touched bridge method {name}")


class _SettingsJsonRpc:
    def __init__(
        self,
        label: str,
        value: bool,
        *,
        fail_set: str | None = None,
        apply_write: bool = True,
    ) -> None:
        self.label = label
        self.value = value
        self.fail_set = fail_set
        self.apply_write = apply_write
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_jsonrpc(self, method: str, params=None):
        params = params or {}
        self.calls.append((method, params))
        await asyncio.sleep(0)
        if method == "Settings.GetSettings":
            return ResponseMessage(
                request_id=f"get-{self.label}",
                result={
                    "settings": [
                        {
                            "id": SETTING_ID,
                            "label": "Show file extensions",
                            "help": "fixture",
                            "type": "boolean",
                            "level": "standard",
                            "enabled": True,
                            "parent": "",
                            "default": False,
                            "value": self.value,
                        }
                    ]
                },
                error=None,
            )
        if method == "Settings.SetSettingValue":
            if self.fail_set is not None:
                return ResponseMessage(
                    request_id=f"set-{self.label}",
                    result=None,
                    error=self.fail_set,
                    error_type=ErrorType.TIMEOUT,
                )
            if self.apply_write:
                self.value = params["value"]
            return ResponseMessage(
                request_id=f"set-{self.label}", result=True, error=None
            )
        raise AssertionError(f"unexpected JSON-RPC method {method}")


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
        targets_json=json.dumps([_target("kodi19"), _target("kodi20")]),
    )
    bridge = _BridgeTripwire()
    default = _Bundle(_SettingsJsonRpc("default", False), bridge)
    bundles = {
        "kodi19": _Bundle(_SettingsJsonRpc("kodi19", False), bridge),
        "kodi20": _Bundle(_SettingsJsonRpc("kodi20", True), bridge),
    }
    pool = _Pool(bundles)
    runtime = {
        "registry": registry,
        "transport_pool": pool,
        "jsonrpc": default.jsonrpc,
        "bridge": default.bridge,
        "notifications": None,
    }
    return runtime, pool, default, bundles


async def _call(server, arguments: dict[str, Any]):
    return await server.get_request_handler("tools/call").handler(
        None,
        CallToolRequestParams(name="kodi_setting_set", arguments=arguments),
    )


def _envelope(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_exact_schema_inventory_through_c2_and_deferred_exclusions():
    runtime, _, _, _ = _runtime()
    server, _ = build_mcp_server(runtime)
    listed = await server.get_request_handler("tools/list").handler(None, None)
    targeted = {
        tool.name
        for tool in listed.tools
        if "target" in tool.input_schema.get("properties", {})
    }

    assert targeted == (
        BATCH_A_TARGET_TOOL_NAMES
        | BATCH_B_TARGET_TOOL_NAMES
        | C1_TOOL_NAMES
        | {"addon_execute"}
        | BATCH_D1_TARGET_TOOL_NAMES
        | BATCH_D2_TARGET_TOOL_NAMES
        | BATCH_D3_TARGET_TOOL_NAMES
        | BATCH_D4_TARGET_TOOL_NAMES
        | BATCH_D5_TARGET_TOOL_NAMES
        | BATCH_P1_TARGET_TOOL_NAMES
    )
    by_name = {tool.name: tool for tool in listed.tools}
    assert by_name["kodi_setting_set"].input_schema["properties"]["target"] == {
        "type": "string",
        "pattern": r"^[a-z0-9](?:[a-z0-9._-]{0,63})$",
    }
    assert all(
        "target" not in by_name[name].input_schema["properties"]
        for name in DEFERRED_TOOL_NAMES
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "error_type", "error_code"),
    [
        ("Kodi19", "invalid_params", None),
        ("https://not-a-target.invalid", "invalid_params", None),
        ("missing", "not_found", 404),
    ],
)
async def test_c1_target_errors_fail_before_mutation(target, error_type, error_code):
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server,
            {"setting_id": SETTING_ID, "value": True, "target": target},
        )
    )

    assert result["error_type"] == error_type
    assert result["error_code"] == error_code
    assert pool.targets == []
    assert default.jsonrpc.calls == []
    assert bundles["kodi19"].jsonrpc.calls == []
    assert bundles["kodi20"].jsonrpc.calls == []


@pytest.mark.asyncio
async def test_c1_mutation_and_readback_use_one_explicit_target():
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server,
            {"setting_id": SETTING_ID, "value": True, "target": "kodi19"},
        )
    )

    assert result["data"] == {
        "setting_id": SETTING_ID,
        "before": False,
        "requested": True,
        "after": True,
        "changed": True,
        "verified": True,
    }
    assert result["raw"]["target_id"] == "kodi19"
    assert [target.target_id for target in pool.targets] == ["kodi19"]
    assert [method for method, _ in bundles["kodi19"].jsonrpc.calls] == [
        "Settings.GetSettings",
        "Settings.SetSettingValue",
        "Settings.GetSettings",
    ]
    assert default.jsonrpc.calls == []
    assert bundles["kodi20"].jsonrpc.calls == []


@pytest.mark.asyncio
async def test_c1_interleaves_a_b_default_without_state_leakage():
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    calls = [
        ({"setting_id": SETTING_ID, "value": True, "target": "kodi19"}, "kodi19"),
        ({"setting_id": SETTING_ID, "value": False, "target": "kodi20"}, "kodi20"),
        ({"setting_id": SETTING_ID, "value": True}, None),
    ]
    results = [_envelope(await _call(server, arguments)) for arguments, _ in calls]

    assert [result["raw"].get("target_id") for result in results] == [
        expected for _, expected in calls
    ]
    assert bundles["kodi19"].jsonrpc.value is True
    assert bundles["kodi20"].jsonrpc.value is False
    assert default.jsonrpc.value is True
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi20"]


@pytest.mark.asyncio
async def test_c1_transient_mutation_failure_is_not_retried():
    runtime, _, default, bundles = _runtime()
    routed = bundles["kodi19"].jsonrpc
    routed.fail_set = "request timeout at https://kodi19.routing-secret.invalid/jsonrpc"
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server,
            {"setting_id": SETTING_ID, "value": True, "target": "kodi19"},
        )
    )

    assert result["error_type"] == "timeout"
    assert [method for method, _ in routed.calls] == [
        "Settings.GetSettings",
        "Settings.SetSettingValue",
    ]
    assert is_safe_to_retry("Settings.GetSettings") is True
    assert is_safe_to_retry("Settings.SetSettingValue") is False
    assert default.jsonrpc.calls == []


@pytest.mark.asyncio
async def test_c1_failed_readback_stays_on_explicit_target():
    runtime, _, default, bundles = _runtime()
    routed = bundles["kodi19"].jsonrpc
    routed.apply_write = False
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server,
            {"setting_id": SETTING_ID, "value": True, "target": "kodi19"},
        )
    )

    assert result["ok"] is False
    assert result["data"]["verified"] is False
    assert [method for method, _ in routed.calls] == [
        "Settings.GetSettings",
        "Settings.SetSettingValue",
        "Settings.GetSettings",
    ]
    assert default.jsonrpc.calls == []
    assert bundles["kodi20"].jsonrpc.calls == []


@pytest.mark.asyncio
async def test_c1_concurrent_mutations_keep_target_state_isolated():
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    first, second = await asyncio.gather(
        _call(
            server,
            {"setting_id": SETTING_ID, "value": True, "target": "kodi19"},
        ),
        _call(
            server,
            {"setting_id": SETTING_ID, "value": False, "target": "kodi20"},
        ),
    )

    assert [_envelope(first)["raw"]["target_id"], _envelope(second)["raw"]["target_id"]] == [
        "kodi19",
        "kodi20",
    ]
    assert bundles["kodi19"].jsonrpc.value is True
    assert bundles["kodi20"].jsonrpc.value is False
    assert {target.target_id for target in pool.targets} == {"kodi19", "kodi20"}
    assert default.jsonrpc.calls == []


@pytest.mark.asyncio
async def test_c1_policy_rejection_never_dispatches_setting_io():
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server,
            {
                "setting_id": "filelists.showhidden",
                "value": True,
                "target": "kodi19",
            },
        )
    )

    assert result["ok"] is False
    assert result["error_type"] == "invalid_operation"
    assert [target.target_id for target in pool.targets] == ["kodi19"]
    assert bundles["kodi19"].jsonrpc.calls == []
    assert default.jsonrpc.calls == []


@pytest.mark.asyncio
async def test_c1_explicit_success_preserves_structured_output_and_short_collisions(
    monkeypatch,
):
    runtime, _, _, _ = _runtime()
    monkeypatch.setenv("KODI19_USERNAME", "set")
    monkeypatch.setenv("KODI19_PASSWORD", "status")
    monkeypatch.setenv("KODI19_TOKEN", "mcp")
    server, _ = build_mcp_server(runtime)

    async with Client(server, mode="auto") as client:
        result = await client.call_tool(
            "kodi_setting_set",
            {"setting_id": SETTING_ID, "value": True, "target": "kodi19"},
        )

    envelope = _envelope(result)
    assert result.is_error is False
    assert result.structured_content == envelope
    assert envelope["tool"] == "kodi_setting_set"
    assert envelope["data"]["setting_id"] == SETTING_ID
    assert envelope["raw"]["target_id"] == "kodi19"


@pytest.mark.asyncio
async def test_c1_explicit_error_redacts_target_location_and_auth(monkeypatch):
    runtime, _, default, bundles = _runtime()
    monkeypatch.setenv("KODI19_TOKEN", "resolved-token")
    leak = (
        "request timeout at https://kodi19.routing-secret.invalid/jsonrpc "
        "using env:KODI19_TOKEN resolved-token"
    )
    bundles["kodi19"].jsonrpc.fail_set = leak
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server,
            {"setting_id": SETTING_ID, "value": True, "target": "kodi19"},
        )
    )
    rendered = json.dumps(result, sort_keys=True)

    assert result["raw"]["target_id"] == "kodi19"
    assert result["tool"] == "kodi_setting_set"
    assert "routing-secret.invalid" not in rendered
    assert "KODI19_TOKEN" not in rendered
    assert "resolved-token" not in rendered
    assert default.jsonrpc.calls == []
