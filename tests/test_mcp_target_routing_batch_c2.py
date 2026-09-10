"""Phase 4A Batch C2 stateless addon-execution routing tests."""

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
from kodi_mcp_server.transport.http_jsonrpc import is_safe_to_retry


C2_TOOL_NAMES = frozenset({"addon_execute"})
ADDON_ID = "script.kodi_mcp_execute_fixture"
EXISTING_ADDON_EXECUTE_FIELDS = frozenset(
    {
        "addonid",
        "addon_id",
        "wait",
        "params",
        "expect_player",
        "expect_window",
        "expect_fullscreen",
        "include_gui_state",
        "observe_player_seconds",
        "player_timeout_seconds",
        "poll_interval_ms",
        "window_timeout_seconds",
        "window_poll_interval_ms",
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


class _JsonRpc:
    def __init__(
        self,
        label: str,
        *,
        execute_result: ResponseMessage | Exception | Any = "OK",
        player_results: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        self.label = label
        self.execute_result = execute_result
        self.player_results = list(player_results or [[]])
        self.execute_calls: list[dict[str, Any]] = []
        self.player_calls = 0

    async def execute_addon(self, addonid: str, params=None, wait: bool = False):
        self.execute_calls.append({"addonid": addonid, "params": params, "wait": wait})
        await asyncio.sleep(0)
        if isinstance(self.execute_result, Exception):
            raise self.execute_result
        if isinstance(self.execute_result, ResponseMessage):
            return self.execute_result
        return ResponseMessage(
            request_id=f"execute-{self.label}", result=self.execute_result, error=None
        )

    async def get_active_players(self):
        self.player_calls += 1
        index = min(self.player_calls - 1, len(self.player_results) - 1)
        return ResponseMessage(
            request_id=f"players-{self.label}-{self.player_calls}",
            result=self.player_results[index],
            error=None,
        )


class _Bridge:
    def __init__(self, label: str, windows: list[str] | None = None) -> None:
        self.label = label
        self.windows = list(windows or ["Home"])
        self.gui_calls = 0

    async def gui_state(self):
        self.gui_calls += 1
        index = min(self.gui_calls - 1, len(self.windows) - 1)
        return ResponseMessage(
            request_id=f"gui-{self.label}-{self.gui_calls}",
            result={
                "current_window": self.windows[index],
                "conditions": {"fullscreen_video": False},
            },
            error=None,
        )


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
    default = _Bundle(_JsonRpc("default"), _Bridge("default"))
    bundles = {
        "kodi19": _Bundle(_JsonRpc("kodi19"), _Bridge("kodi19")),
        "kodi20": _Bundle(_JsonRpc("kodi20"), _Bridge("kodi20")),
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
        CallToolRequestParams(name="addon_execute", arguments=arguments),
    )


def _envelope(result):
    return json.loads(result.content[0].text)


def _assert_no_transport_io(pool, default, bundles):
    assert pool.targets == []
    assert default.jsonrpc.execute_calls == []
    assert default.jsonrpc.player_calls == 0
    assert default.bridge.gui_calls == 0
    assert all(bundle.jsonrpc.execute_calls == [] for bundle in bundles.values())
    assert all(bundle.jsonrpc.player_calls == 0 for bundle in bundles.values())
    assert all(bundle.bridge.gui_calls == 0 for bundle in bundles.values())


def _safe_arguments(**overrides):
    return {
        "addonid": ADDON_ID,
        "wait": False,
        "include_gui_state": False,
        "observe_player_seconds": 0,
        **overrides,
    }


@pytest.mark.asyncio
async def test_exact_c2_inventory_schema_output_and_annotations():
    runtime, _, _, _ = _runtime()
    server, _ = build_mcp_server(runtime)
    listed = await server.get_request_handler("tools/list").handler(None, None)
    by_name = {tool.name: tool for tool in listed.tools}
    targeted = {
        name
        for name, tool in by_name.items()
        if "target" in tool.input_schema.get("properties", {})
    }

    assert tool_contract.BATCH_C2_TARGET_TOOL_NAMES == C2_TOOL_NAMES
    assert targeted == (
        tool_contract.BATCH_A_TARGET_TOOL_NAMES
        | tool_contract.BATCH_B_TARGET_TOOL_NAMES
        | tool_contract.BATCH_C1_TARGET_TOOL_NAMES
        | C2_TOOL_NAMES
        | tool_contract.BATCH_D1_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D2_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D3_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D4_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D5_TARGET_TOOL_NAMES
    )
    addon_tool = by_name["addon_execute"]
    assert set(addon_tool.input_schema["properties"]) == EXISTING_ADDON_EXECUTE_FIELDS | {
        "target"
    }
    assert addon_tool.input_schema["properties"]["target"] == {
        "type": "string",
        "pattern": r"^[a-z0-9](?:[a-z0-9._-]{0,63})$",
    }
    assert addon_tool.input_schema["anyOf"] == [
        {"required": ["addonid"]},
        {"required": ["addon_id"]},
    ]
    assert addon_tool.output_schema is not None
    assert addon_tool.output_schema["properties"]["data"] == {}
    assert addon_tool.output_schema["properties"]["raw"] == {}
    assert addon_tool.annotations.read_only_hint is False
    assert addon_tool.annotations.destructive_hint is True
    assert addon_tool.annotations.idempotent_hint is False
    assert addon_tool.annotations.open_world_hint is True
    assert is_safe_to_retry("Addons.ExecuteAddon") is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "error_type", "error_code"),
    [
        (42, "invalid_params", None),
        ("Kodi19", "invalid_params", None),
        ("https://not-a-target.invalid", "invalid_params", None),
        ("missing", "not_found", 404),
    ],
)
async def test_c2_target_errors_fail_before_any_io(target, error_type, error_code):
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, _safe_arguments(target=target)))

    assert result["error_type"] == error_type
    assert result["error_code"] == error_code
    assert pool.targets == []
    assert default.jsonrpc.execute_calls == []
    assert default.jsonrpc.player_calls == 0
    assert default.bridge.gui_calls == 0
    assert all(bundle.jsonrpc.execute_calls == [] for bundle in bundles.values())
    assert all(bundle.jsonrpc.player_calls == 0 for bundle in bundles.values())
    assert all(bundle.bridge.gui_calls == 0 for bundle in bundles.values())


@pytest.mark.asyncio
async def test_c2_early_error_redacts_malformed_url_target_without_io_or_mutation(
    monkeypatch,
):
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)
    target_url = "https://user:supersecret@example.invalid/"
    arguments = {
        "addonid": "script.example",
        "target": target_url,
        "params": {"mode": "probe"},
    }
    original = deepcopy(arguments)

    monkeypatch.setattr(
        server_core,
        "resolve_target_context",
        lambda *_: pytest.fail("early validation must not resolve a target"),
    )
    call_result = await _call(server, arguments)
    result = _envelope(call_result)
    structured = json.dumps(call_result.structured_content, sort_keys=True)

    assert result["error_type"] == "invalid_params"
    assert call_result.structured_content == result
    assert target_url not in call_result.content[0].text
    assert "supersecret" not in call_result.content[0].text
    assert target_url not in structured
    assert "supersecret" not in structured
    assert result["raw"]["arguments"] == {
        "addonid": "script.example",
        "target": "[redacted]",
        "params": {"mode": "probe"},
    }
    assert arguments == original
    _assert_no_transport_io(pool, default, bundles)


@pytest.mark.asyncio
async def test_c2_early_error_redacts_nested_secrets_and_retains_safe_fields(
    monkeypatch,
):
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)
    arguments = {
        "addonid": "script.example",
        "target": "kodi19",
        "wait": "invalid-but-safe",
        "params": {
            "username": "safe-user",
            "password": "secret-one",
            "nested": {
                "token": "secret-two",
                "items": [
                    {
                        "passwd": "secret-passwd",
                        "access_token": "secret-access-token",
                        "api_key": "secret-api-key",
                        "authorization": "secret-authorization",
                        "auth": "secret-auth",
                        "secret": "secret-generic",
                        "credential": "secret-credential",
                        "proxy_auth": "secret-proxy-auth",
                        "client_auth": "secret-client-auth",
                        "upstream_auth": "secret-upstream-auth",
                        "client_credentials": "secret-client-creds",
                        "proxy_credentials": "secret-proxy-creds",
                        "service_credentials": "secret-service-creds",
                        "probe": "list-safe",
                        "enabled": True,
                        "timeout": 5,
                    }
                ],
            },
            "mode": "probe",
        },
    }
    original = deepcopy(arguments)

    monkeypatch.setattr(
        server_core,
        "resolve_target_context",
        lambda *_: pytest.fail("early validation must not resolve a target"),
    )
    call_result = await _call(server, arguments)
    result = _envelope(call_result)
    structured = json.dumps(call_result.structured_content, sort_keys=True)

    assert result["error_type"] == "invalid_params"
    assert call_result.structured_content == result
    sensitive_values = {
        "secret-one",
        "secret-two",
        "secret-passwd",
        "secret-access-token",
        "secret-api-key",
        "secret-authorization",
        "secret-auth",
        "secret-generic",
        "secret-credential",
        "secret-proxy-auth",
        "secret-client-auth",
        "secret-upstream-auth",
        "secret-client-creds",
        "secret-proxy-creds",
        "secret-service-creds",
    }
    assert all(value not in call_result.content[0].text for value in sensitive_values)
    assert all(value not in structured for value in sensitive_values)
    assert result["raw"]["arguments"] == {
        "addonid": "script.example",
        "target": "kodi19",
        "wait": "invalid-but-safe",
        "params": {
            "username": "safe-user",
            "password": "[redacted]",
            "nested": {
                "token": "[redacted]",
                "items": [
                    {
                        "passwd": "[redacted]",
                        "access_token": "[redacted]",
                        "api_key": "[redacted]",
                        "authorization": "[redacted]",
                        "auth": "[redacted]",
                        "secret": "[redacted]",
                        "credential": "[redacted]",
                        "proxy_auth": "[redacted]",
                        "client_auth": "[redacted]",
                        "upstream_auth": "[redacted]",
                        "client_credentials": "[redacted]",
                        "proxy_credentials": "[redacted]",
                        "service_credentials": "[redacted]",
                        "probe": "list-safe",
                        "enabled": True,
                        "timeout": 5,
                    }
                ],
            },
            "mode": "probe",
        },
    }
    assert arguments == original
    _assert_no_transport_io(pool, default, bundles)


@pytest.mark.asyncio
async def test_c2_early_missing_addon_redacts_secrets_without_io_or_mutation(monkeypatch):
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)
    arguments = {
        "target": "kodi19-lab",
        "params": {
            "password": "secret-three",
            "token": "secret-four",
            "mode": "probe",
        },
    }
    original = deepcopy(arguments)

    monkeypatch.setattr(
        server_core,
        "resolve_target_context",
        lambda *_: pytest.fail("missing addon ID must not resolve a target"),
    )
    call_result = await _call(server, arguments)
    result = _envelope(call_result)
    structured = json.dumps(call_result.structured_content, sort_keys=True)

    assert result["error"] == "missing required argument: addonid"
    assert result["error_type"] == "invalid_params"
    assert result["error_code"] is None
    assert call_result.structured_content == result
    assert "secret-three" not in call_result.content[0].text
    assert "secret-four" not in call_result.content[0].text
    assert "secret-three" not in structured
    assert "secret-four" not in structured
    assert result["raw"]["arguments"] == {
        "target": "kodi19-lab",
        "params": {
            "password": "[redacted]",
            "token": "[redacted]",
            "mode": "probe",
        },
    }
    assert arguments == original
    _assert_no_transport_io(pool, default, bundles)


@pytest.mark.asyncio
async def test_c2_a_b_default_calls_use_only_their_exact_legacy_or_routed_objects():
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    results = [
        _envelope(await _call(server, _safe_arguments(target="kodi19"))),
        _envelope(await _call(server, _safe_arguments(target="kodi20"))),
        _envelope(await _call(server, _safe_arguments())),
    ]

    assert [result["raw"].get("target_id") for result in results] == [
        "kodi19",
        "kodi20",
        None,
    ]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi20"]
    assert len(bundles["kodi19"].jsonrpc.execute_calls) == 1
    assert len(bundles["kodi20"].jsonrpc.execute_calls) == 1
    assert len(default.jsonrpc.execute_calls) == 1
    assert bundles["kodi19"].jsonrpc.player_calls == 1
    assert bundles["kodi20"].jsonrpc.player_calls == 1
    assert default.jsonrpc.player_calls == 1


@pytest.mark.asyncio
async def test_c2_execution_player_gui_polling_and_snapshot_share_one_context():
    runtime, pool, default, bundles = _runtime()
    routed = bundles["kodi19"]
    routed.jsonrpc.player_results = [[], [{"playerid": 1, "type": "video"}]]
    routed.bridge.windows = ["Home", "Fixture Window"]
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server,
            _safe_arguments(
                target="kodi19",
                include_gui_state=True,
                expect_player=True,
                expect_window="Fixture",
                player_timeout_seconds=1,
                window_timeout_seconds=1,
                poll_interval_ms=100,
                window_poll_interval_ms=100,
            ),
        )
    )

    assert result["ok"] is True
    assert result["data"]["dispatch_ok"] is True
    assert result["data"]["verified"] is True
    assert result["data"]["gui_state"]["source"] == "gui_verification"
    assert result["raw"]["target_id"] == "kodi19"
    assert [target.target_id for target in pool.targets] == ["kodi19"]
    assert len(routed.jsonrpc.execute_calls) == 1
    assert routed.jsonrpc.player_calls == 2
    assert routed.bridge.gui_calls == 2
    assert default.jsonrpc.execute_calls == []
    assert default.jsonrpc.player_calls == 0
    assert default.bridge.gui_calls == 0
    assert bundles["kodi20"].jsonrpc.execute_calls == []


@pytest.mark.asyncio
async def test_c2_ambiguous_execute_exception_is_an_error_and_is_never_retried():
    runtime, _, default, bundles = _runtime()
    routed = bundles["kodi19"].jsonrpc
    routed.execute_result = TimeoutError("ambiguous transport timeout")
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, _safe_arguments(target="kodi19")))

    assert result["ok"] is False
    assert result["error_type"] == "unknown_error"
    assert "ambiguous transport timeout" in result["error"]
    assert len(routed.execute_calls) == 1
    assert routed.player_calls == 0
    assert default.jsonrpc.execute_calls == []


@pytest.mark.asyncio
async def test_c2_failed_dispatch_stays_failed_even_if_observation_sees_a_player():
    runtime, _, default, bundles = _runtime()
    routed = bundles["kodi19"].jsonrpc
    routed.execute_result = ResponseMessage(
        request_id="timeout",
        result=None,
        error="request timeout after ambiguous dispatch",
        error_type=ErrorType.TIMEOUT,
    )
    routed.player_results = [[{"playerid": 1, "type": "video"}]]
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server,
            _safe_arguments(target="kodi19", expect_player=True),
        )
    )

    assert result["ok"] is False
    assert result["data"]["dispatch_ok"] is False
    assert result["data"]["player_observation"]["player_started"] is True
    assert result["error_type"] == "timeout"
    assert len(routed.execute_calls) == 1
    assert routed.player_calls == 1
    assert default.jsonrpc.execute_calls == []


@pytest.mark.asyncio
async def test_c2_verification_mismatch_never_reexecutes():
    runtime, _, default, bundles = _runtime()
    routed = bundles["kodi19"].jsonrpc
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server,
            _safe_arguments(
                target="kodi19",
                expect_player=True,
                player_timeout_seconds=1,
            ),
        )
    )

    assert result["ok"] is False
    assert result["error_type"] == "verification_failed"
    assert result["data"]["dispatch_ok"] is True
    assert len(routed.execute_calls) == 1
    assert routed.player_calls > 1
    assert default.jsonrpc.execute_calls == []


@pytest.mark.asyncio
async def test_c2_kodi_invalid_params_normalization_remains_compatible():
    runtime, _, _, bundles = _runtime()
    routed = bundles["kodi19"].jsonrpc
    routed.execute_result = ResponseMessage(
        request_id="invalid",
        result=None,
        error=(
            "Kodi rejected the supplied parameters for Addons.ExecuteAddon; verify the "
            "addon ID with addon_list and check addon-specific params"
        ),
        error_type=ErrorType.INVALID_PARAMS,
        error_code=-32602,
    )
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, _safe_arguments(target="kodi19")))

    assert result["ok"] is False
    assert result["error_type"] == "invalid_params"
    assert result["error_code"] == -32602
    assert len(routed.execute_calls) == 1


@pytest.mark.asyncio
async def test_c2_explicit_output_redacts_target_and_param_secrets_only(monkeypatch):
    runtime, _, default, bundles = _runtime()
    monkeypatch.setenv("KODI19_USERNAME", "addon")
    monkeypatch.setenv("KODI19_PASSWORD", "execute")
    monkeypatch.setenv("KODI19_TOKEN", "mcp")
    original_params = {
        "password": "payload-secret",
        "token": "payload-token",
        "mode": "addon",
        "label": "execute",
        "scope": "mcp",
    }
    server, _ = build_mcp_server(runtime)

    call_result = await _call(
        server,
        _safe_arguments(target="kodi19", params=original_params),
    )
    result = _envelope(call_result)
    rendered = json.dumps(result, sort_keys=True)

    assert call_result.structured_content == result
    assert result["tool"] == "addon_execute"
    assert result["data"]["addonid"] == ADDON_ID
    assert result["raw"]["target_id"] == "kodi19"
    assert result["data"]["params"] == {
        "password": "[redacted]",
        "token": "[redacted]",
        "mode": "addon",
        "label": "execute",
        "scope": "mcp",
    }
    assert bundles["kodi19"].jsonrpc.execute_calls[0]["params"] == original_params
    assert "payload-secret" not in rendered
    assert "payload-token" not in rendered
    assert default.jsonrpc.execute_calls == []


@pytest.mark.asyncio
async def test_c2_explicit_error_redacts_endpoint_and_auth_details(monkeypatch):
    runtime, _, default, bundles = _runtime()
    monkeypatch.setenv("KODI19_TOKEN", "resolved-token")
    routed = bundles["kodi19"].jsonrpc
    routed.execute_result = ResponseMessage(
        request_id="failed",
        result=None,
        error=(
            "failure at https://kodi19.routing-secret.invalid/jsonrpc "
            "using env:KODI19_TOKEN resolved-token"
        ),
        error_type=ErrorType.NETWORK_ERROR,
    )
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, _safe_arguments(target="kodi19")))
    rendered = json.dumps(result, sort_keys=True)

    assert result["ok"] is False
    assert result["raw"]["target_id"] == "kodi19"
    assert "routing-secret.invalid" not in rendered
    assert "KODI19_TOKEN" not in rendered
    assert "resolved-token" not in rendered
    assert len(routed.execute_calls) == 1
    assert default.jsonrpc.execute_calls == []


@pytest.mark.asyncio
async def test_c2_concurrent_target_calls_are_isolated_and_execute_once_each():
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    first, second = await asyncio.gather(
        _call(server, _safe_arguments(target="kodi19")),
        _call(server, _safe_arguments(target="kodi20")),
    )

    assert [_envelope(first)["raw"]["target_id"], _envelope(second)["raw"]["target_id"]] == [
        "kodi19",
        "kodi20",
    ]
    assert {target.target_id for target in pool.targets} == {"kodi19", "kodi20"}
    assert len(bundles["kodi19"].jsonrpc.execute_calls) == 1
    assert len(bundles["kodi20"].jsonrpc.execute_calls) == 1
    assert default.jsonrpc.execute_calls == []
    assert default.jsonrpc.player_calls == 0
    assert default.bridge.gui_calls == 0
