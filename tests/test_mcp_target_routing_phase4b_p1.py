"""Phase 4B-P1 deployment-orchestration targeting tests."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolRequestParams

import kodi_mcp_mcp.server_core as server_core
import kodi_mcp_mcp.tool_contract as tool_contract
from kodi_mcp_mcp.output_contracts import output_schema_for
from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_server.models.messages import ResponseMessage
from kodi_mcp_server.targets.registry import LegacyTargetSettings, TargetRegistry


P1_TOOL_NAMES = frozenset({"managed_addon_validate_state"})
TARGET_PROPERTY = {
    "type": "string",
    "pattern": r"^[a-z0-9](?:[a-z0-9._-]{0,63})$",
}
MANAGED_ADDON_ID = "plugin.example"


@dataclass(frozen=True)
class _Bundle:
    jsonrpc: Any
    bridge: Any
    notifications: Any | None = None


class _NoAccess:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected access: {name}")


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
        self.targets: list[Any] = []

    def get_for_target(self, target: Any) -> _Bundle:
        self.targets.append(target)
        return self.bundles[target.target_id]


class _Bridge:
    def __init__(
        self,
        label: str,
        *,
        health_error: str | None = None,
        state_error: Exception | None = None,
        special_path: str = "special://home/addons/packages/dev-repo.zip",
    ) -> None:
        self.label = label
        self.health_error = health_error
        self.state_error = state_error
        self.special_path = special_path
        self.calls: list[str] = []
        self.health_response = ResponseMessage(
            request_id=f"health-{label}",
            result={"status": "ok", "label": label},
            error=health_error,
        )
        self.state_response = ResponseMessage(
            request_id=f"state-{label}",
            result={
                "transport": {"ok": True},
                "result": {
                    "ok": True,
                    "derived": {
                        "registration_present": True,
                        "registration_stale": False,
                        "repo_zip_file_exists": True,
                        "dev_setup_available": True,
                    },
                    "repo_zip": {"special_path": special_path},
                },
            },
            error=None,
        )

    async def get_bridge_health(self) -> ResponseMessage:
        self.calls.append("health")
        await asyncio.sleep(0)
        return self.health_response

    async def get_mcp_state(self) -> ResponseMessage:
        self.calls.append("state")
        await asyncio.sleep(0)
        if self.state_error is not None:
            raise self.state_error
        return self.state_response


class _StateClient:
    def __init__(self, label: str = "legacy-state") -> None:
        self.label = label
        self.calls = 0

    async def mcp_state(self) -> ResponseMessage:
        self.calls += 1
        return _Bridge(self.label).state_response


def _target(target_id: str) -> dict[str, Any]:
    upper = target_id.upper()
    return {
        "id": target_id,
        "name": f"Target {target_id}",
        "endpoints": {
            "jsonrpc_url": f"https://{target_id}.routing-secret.invalid/jsonrpc",
            "bridge_url": f"https://{target_id}.routing-secret.invalid/bridge",
            "websocket_url": f"wss://{target_id}.routing-secret.invalid/ws",
        },
        "auth": {
            "jsonrpc_username": f"env:{upper}_USERNAME",
            "jsonrpc_password": f"env:{upper}_PASSWORD",
            "bridge_token": f"env:{upper}_TOKEN",
        },
    }


def _runtime(
    *,
    default_bridge: Any | None = None,
    target_bridges: dict[str, Any] | None = None,
):
    source = TargetRegistry.from_sources(
        legacy=LegacyTargetSettings(
            jsonrpc_url="http://default.invalid/jsonrpc",
            bridge_url="http://default.invalid/bridge",
        ),
        targets_json=json.dumps([_target("kodi19"), _target("kodi22")]),
    )
    registry = _CountingRegistry(source)
    default_bridge = default_bridge or _Bridge("default")
    target_bridges = target_bridges or {
        "kodi19": _Bridge("kodi19"),
        "kodi22": _Bridge("kodi22"),
    }
    bundles = {
        target_id: _Bundle(_NoAccess(), bridge)
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


def _validation_result(managed_addon_id: str = MANAGED_ADDON_ID) -> dict[str, Any]:
    return {
        "ok": True,
        "managed_addon_id": managed_addon_id,
        "registry": {
            "exists": True,
            "enabled": True,
            "addon_id": managed_addon_id,
            "source_path": "",
            "last_observed_version": "1.2.3",
            "last_build": None,
        },
        "artifacts": {
            "last_build_zip_exists": False,
            "published_repo_zip_exists": False,
            "dev_repo_exists": False,
            "addons_xml_exists": False,
            "addons_xml_md5_exists": False,
        },
        "kodi_bridge": {
            "reachable": True,
            "mcp_state_read_ok": True,
            "error": None,
            "registration_present": True,
            "registration_stale": False,
            "repo_zip_file_exists": True,
            "repo_zip_special_path": None,
            "dev_setup_available": True,
        },
        "summary": {
            "ready_for_build": False,
            "ready_for_publish": False,
            "ready_for_stage": False,
            "ready_for_kodi_install": False,
        },
    }


async def _call(server: Any, arguments: dict[str, Any]):
    return await server.get_request_handler("tools/call").handler(
        None,
        CallToolRequestParams(
            name="managed_addon_validate_state",
            arguments=arguments,
        ),
    )


def _envelope(result: Any) -> dict[str, Any]:
    envelope = json.loads(result.content[0].text)
    assert result.structured_content == envelope
    return envelope


@pytest.mark.asyncio
async def test_exact_p1_inventory_schema_output_and_annotations() -> None:
    server, _ = build_mcp_server(
        {"bridge": _NoAccess(), "jsonrpc": _NoAccess(), "notifications": _NoAccess()}
    )
    listed = await server.get_request_handler("tools/list").handler(None, None)
    by_name = {tool.name: tool for tool in listed.tools}
    targeted = {
        name
        for name, tool in by_name.items()
        if "target" in tool.input_schema.get("properties", {})
    }
    tool = by_name["managed_addon_validate_state"]

    assert len(by_name) == 56
    assert tool_contract.BATCH_P1_TARGET_TOOL_NAMES == P1_TOOL_NAMES
    assert tool_contract.BATCH_D3_TARGET_TOOL_NAMES == frozenset(
        {"kodi_notifications_sample"}
    )
    assert tool_contract.BATCH_D4_TARGET_TOOL_NAMES == frozenset(
        {"bridge_write_log_marker"}
    )
    assert tool_contract.BATCH_D5_TARGET_TOOL_NAMES == frozenset(
        {"kodi_gui_screenshot"}
    )
    assert not hasattr(tool_contract, "BATCH_D6_TARGET_TOOL_NAMES")
    assert targeted == tool_contract.EXPLICIT_TARGET_TOOL_NAMES
    assert len(targeted) == 37
    assert targeted - (
        tool_contract.BATCH_A_TARGET_TOOL_NAMES
        | tool_contract.BATCH_B_TARGET_TOOL_NAMES
        | tool_contract.BATCH_C1_TARGET_TOOL_NAMES
        | tool_contract.BATCH_C2_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D1_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D2_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D3_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D4_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D5_TARGET_TOOL_NAMES
    ) == P1_TOOL_NAMES
    assert tool.input_schema == {
        "type": "object",
        "properties": {
            "managed_addon_id": {
                "type": "string",
                "minLength": 1,
                "pattern": r"^(?=.*[a-z0-9])[a-z0-9._@-]+$",
            },
            "target": TARGET_PROPERTY,
        },
        "required": ["managed_addon_id"],
        "additionalProperties": False,
    }
    assert tool.output_schema == output_schema_for("managed_addon_validate_state")
    assert tool.annotations.model_dump(by_alias=True, exclude_none=False) == {
        "title": None,
        "readOnlyHint": True,
        "destructiveHint": None,
        "idempotentHint": None,
        "openWorldHint": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [42, "Kodi19", "bad target"])
async def test_malformed_target_fails_before_all_io(
    target: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def tripwire(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected access: {args!r} {kwargs!r}")

    monkeypatch.setattr(server_core, "managed_addon_get", tripwire)
    monkeypatch.setattr(server_core, "validate_managed_addon_state", tripwire)
    monkeypatch.setattr(server_core, "build_bridge_client", tripwire)
    monkeypatch.setattr(server_core, "resolve_target_context", tripwire)
    server, _ = build_mcp_server(
        {
            "registry": _NoAccess(),
            "transport_pool": _NoAccess(),
            "bridge": _NoAccess(),
            "jsonrpc": _NoAccess(),
            "notifications": _NoAccess(),
        }
    )

    result = _envelope(
        await _call(server, {"managed_addon_id": "Plugin/Bad", "target": target})
    )

    assert result["error_type"] == "invalid_params"


@pytest.mark.asyncio
async def test_credential_target_and_other_invalid_field_are_sanitized_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rejected = "https://user:secret@example.invalid/private"
    arguments = {"managed_addon_id": "Plugin/Bad", "target": rejected}
    original = deepcopy(arguments)
    monkeypatch.setattr(
        server_core,
        "resolve_target_context",
        lambda *_: pytest.fail("invalid arguments must not resolve a target"),
    )
    monkeypatch.setattr(
        server_core,
        "managed_addon_get",
        lambda **_: pytest.fail("invalid arguments must not read managed registry"),
    )
    server, _ = build_mcp_server(
        {
            "registry": _NoAccess(),
            "transport_pool": _NoAccess(),
            "bridge": _NoAccess(),
            "jsonrpc": _NoAccess(),
            "notifications": _NoAccess(),
        }
    )

    call_result = await _call(server, arguments)
    result = _envelope(call_result)
    rendered = json.dumps(call_result.structured_content, sort_keys=True)

    assert result["error_type"] == "invalid_params"
    assert result["raw"]["arguments"] == {
        "managed_addon_id": "Plugin/Bad",
        "target": "[redacted]",
    }
    assert all(item["message"] == "does not satisfy the advertised schema" for item in result["raw"]["validation"])
    assert rejected not in call_result.content[0].text
    assert rejected not in rendered
    assert "user:secret" not in call_result.content[0].text
    assert arguments == original


@pytest.mark.asyncio
async def test_omitted_target_missing_id_keeps_exact_advertised_schema_error() -> None:
    server, _ = build_mcp_server(
        {"bridge": _NoAccess(), "jsonrpc": _NoAccess(), "notifications": _NoAccess()}
    )

    result = _envelope(await _call(server, {}))

    assert result == {
        "ok": False,
        "tool": "managed_addon_validate_state",
        "data": None,
        "error": "invalid arguments: 'managed_addon_id' is a required property",
        "error_type": "invalid_params",
        "error_code": None,
        "latency_ms": 0,
        "request_id": None,
        "raw": {
            "arguments": {},
            "validation": [
                {
                    "field": None,
                    "message": "'managed_addon_id' is a required property",
                    "validator": "required",
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_unknown_target_precedes_managed_registry_helper_filesystem_and_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, registry, pool, default, bundles = _runtime()
    monkeypatch.setattr(
        server_core,
        "managed_addon_get",
        lambda **_: pytest.fail("unknown target must precede managed registry read"),
    )
    monkeypatch.setattr(
        server_core,
        "validate_managed_addon_state",
        lambda **_: pytest.fail("unknown target must precede helper/filesystem work"),
    )
    monkeypatch.setattr(
        server_core,
        "build_bridge_client",
        lambda: pytest.fail("unknown target must not construct legacy bridge"),
    )
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(server, {"managed_addon_id": MANAGED_ADDON_ID, "target": "missing"})
    )

    assert result["error_type"] == "not_found"
    assert result["error_code"] == 404
    assert registry.calls == ["missing"]
    assert pool.targets == []
    assert default.calls == []
    assert all(bundle.bridge.calls == [] for bundle in bundles.values())


@pytest.mark.asyncio
async def test_explicit_a_b_then_default_bind_exact_bridges_and_registry_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, registry, pool, default, bundles = _runtime()
    entry = {"managed_addon_id": MANAGED_ADDON_ID, "enabled": True}
    original_entry = deepcopy(entry)
    managed_calls: list[str] = []
    helper_calls: list[dict[str, Any]] = []
    state_clients: list[_StateClient] = []

    def registry_get(*, managed_addon_id: str) -> dict[str, Any]:
        managed_calls.append(managed_addon_id)
        return {"ok": True, "managed_addon": entry}

    async def helper(**kwargs: Any) -> dict[str, Any]:
        helper_calls.append(kwargs)
        return _validation_result(kwargs["managed_addon_id"])

    def build_legacy_client() -> _StateClient:
        client = _StateClient()
        state_clients.append(client)
        return client

    monkeypatch.setattr(server_core, "managed_addon_get", registry_get)
    monkeypatch.setattr(server_core, "validate_managed_addon_state", helper)
    monkeypatch.setattr(server_core, "build_bridge_client", build_legacy_client)
    server, _ = build_mcp_server(runtime)

    results = [
        _envelope(await _call(server, {"managed_addon_id": MANAGED_ADDON_ID, "target": "kodi19"})),
        _envelope(await _call(server, {"managed_addon_id": MANAGED_ADDON_ID, "target": "kodi22"})),
        _envelope(await _call(server, {"managed_addon_id": MANAGED_ADDON_ID})),
    ]

    assert registry.calls == ["kodi19", "kodi22"]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi22"]
    assert managed_calls == [MANAGED_ADDON_ID] * 3
    assert len(state_clients) == 1
    assert helper_calls[0]["health_bridge_tool"] is bundles["kodi19"].bridge
    assert helper_calls[0]["state_bridge_tool"] is bundles["kodi19"].bridge
    assert helper_calls[1]["health_bridge_tool"] is bundles["kodi22"].bridge
    assert helper_calls[1]["state_bridge_tool"] is bundles["kodi22"].bridge
    assert helper_calls[2]["health_bridge_tool"] is default
    assert helper_calls[2]["state_bridge_tool"].client is state_clients[0]
    assert helper_calls[2]["state_bridge_tool"] is not default
    assert [item["raw"].get("target_id") for item in results] == [
        "kodi19",
        "kodi22",
        None,
    ]
    assert "target_name" not in results[2]["raw"]
    assert entry == original_entry


@pytest.mark.asyncio
async def test_concurrent_a_b_calls_keep_target_bridges_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, registry, pool, default, bundles = _runtime()
    managed_calls = 0
    seen: list[tuple[Any, Any]] = []

    def registry_get(*, managed_addon_id: str) -> dict[str, Any]:
        nonlocal managed_calls
        managed_calls += 1
        return {"ok": False, "managed_addon_id": managed_addon_id}

    async def helper(**kwargs: Any) -> dict[str, Any]:
        seen.append((kwargs["health_bridge_tool"], kwargs["state_bridge_tool"]))
        await asyncio.sleep(0)
        return _validation_result(kwargs["managed_addon_id"])

    monkeypatch.setattr(server_core, "managed_addon_get", registry_get)
    monkeypatch.setattr(server_core, "validate_managed_addon_state", helper)
    monkeypatch.setattr(
        server_core,
        "build_bridge_client",
        lambda: pytest.fail("explicit call reconstructed legacy bridge"),
    )
    server, _ = build_mcp_server(runtime)

    first, second = await asyncio.gather(
        _call(server, {"managed_addon_id": MANAGED_ADDON_ID, "target": "kodi19"}),
        _call(server, {"managed_addon_id": MANAGED_ADDON_ID, "target": "kodi22"}),
    )
    results = [_envelope(first), _envelope(second)]

    assert managed_calls == 2
    assert registry.calls == ["kodi19", "kodi22"]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi22"]
    assert seen == [
        (bundles["kodi19"].bridge, bundles["kodi19"].bridge),
        (bundles["kodi22"].bridge, bundles["kodi22"].bridge),
    ]
    assert [item["raw"]["target_id"] for item in results] == ["kodi19", "kodi22"]
    assert default.calls == []


@pytest.mark.asyncio
async def test_explicit_finalizer_redacts_orchestration_provenance_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _, _, _, _ = _runtime()
    helper_result = _validation_result()
    helper_result["registry"].update(
        {
            "source_path": "/srv/private/source/plugin.example",
            "last_build": {
                "version": "1.2.3",
                "zip_path": "/srv/private/artifacts/plugin.example-1.2.3.zip",
                "repo_zip_path": "/srv/private/repo/plugin.example-1.2.3.zip",
                "artifact_path": "C:\\private\\artifact.zip",
                "filename": "plugin.example-1.2.3.zip",
                "sha256": "a" * 64,
                "size": 1234,
                "timestamp": "2026-09-10T12:34:56Z",
            },
        }
    )
    helper_result["kodi_bridge"].update(
        {
            "repo_zip_special_path": "special://home/addons/packages/dev-repo.zip",
            "translated_path": "/target/home/addons/packages/dev-repo.zip",
            "staged_zip_path": "/target/profile/addon_data/dev-repo.zip",
            "addon_profile_path": "special://profile/addon_data/plugin.example",
            "bridge_endpoint": "https://bridge.target.invalid:8765/mcp/state",
            "jsonrpc_url": "http://user:secret@target.invalid:8080/jsonrpc",
            "websocket_url": "ws://target.invalid:9090/jsonrpc",
            "host": "target.invalid",
            "port": 8765,
            "diagnostic": "failed at /target/private/state from target.invalid:8765",
        }
    )
    helper_original = deepcopy(helper_result)
    entry = {
        "source_path": "/srv/private/source/plugin.example",
        "last_build": deepcopy(helper_result["registry"]["last_build"]),
    }
    entry_original = deepcopy(entry)

    monkeypatch.setattr(
        server_core,
        "managed_addon_get",
        lambda **_: {"ok": True, "managed_addon": entry},
    )
    monkeypatch.setattr(
        server_core,
        "validate_managed_addon_state",
        lambda **_: asyncio.sleep(0, result=helper_result),
    )
    monkeypatch.setattr(
        server_core,
        "build_bridge_client",
        lambda: pytest.fail("explicit call reconstructed legacy bridge"),
    )
    server, _ = build_mcp_server(runtime)

    call_result = await _call(
        server, {"managed_addon_id": MANAGED_ADDON_ID, "target": "kodi19"}
    )
    result = _envelope(call_result)
    rendered = json.dumps(result, sort_keys=True)

    for unsafe in (
        "/srv/private",
        "C:\\private",
        "special://",
        "/target/",
        "bridge.target.invalid",
        "target.invalid:8765",
        "user:secret",
        "ws://",
    ):
        assert unsafe not in rendered
    last_build = result["data"]["registry"]["last_build"]
    assert result["data"]["managed_addon_id"] == MANAGED_ADDON_ID
    assert result["data"]["registry"]["addon_id"] == MANAGED_ADDON_ID
    assert result["data"]["registry"]["last_observed_version"] == "1.2.3"
    assert last_build["version"] == "1.2.3"
    assert last_build["filename"] == "plugin.example-1.2.3.zip"
    assert last_build["sha256"] == "a" * 64
    assert last_build["size"] == 1234
    assert last_build["timestamp"] == "2026-09-10T12:34:56Z"
    assert result["data"]["summary"] == helper_result["summary"]
    assert result["raw"]["target_id"] == "kodi19"
    assert result["raw"]["target_name"] == "Target kodi19"
    assert call_result.structured_content == result
    assert helper_result == helper_original
    assert entry == entry_original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bridge", "expected_calls"),
    [
        (_Bridge("health-fail", health_error="health failed at https://private.invalid:8765/health"), ["health"]),
        (_Bridge("state-fail", state_error=RuntimeError("state failed at special://profile/private")), ["health", "state"]),
    ],
)
async def test_explicit_bridge_diagnostics_are_sanitized_and_attributed(
    bridge: _Bridge,
    expected_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _, pool, _, _ = _runtime(target_bridges={"kodi19": bridge, "kodi22": _Bridge("kodi22")})
    monkeypatch.setattr(
        server_core,
        "managed_addon_get",
        lambda **_: {"ok": False, "managed_addon_id": MANAGED_ADDON_ID},
    )
    monkeypatch.setattr(server_core, "AUTHORITATIVE_REPO_ROOT", Path("/missing"))
    monkeypatch.setattr(
        server_core,
        "build_bridge_client",
        lambda: pytest.fail("explicit call reconstructed legacy bridge"),
    )
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(server, {"managed_addon_id": MANAGED_ADDON_ID, "target": "kodi19"})
    )
    rendered = json.dumps(result, sort_keys=True)

    assert result["ok"] is True
    assert result["raw"]["target_id"] == "kodi19"
    assert result["raw"]["target_name"] == "Target kodi19"
    assert bridge.calls == expected_calls
    assert [target.target_id for target in pool.targets] == ["kodi19"]
    assert "private.invalid" not in rendered
    assert "special://" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "diagnostic",
    [
        "state failure {/srv/private/state}",
        "state failure (/srv/private/state)",
        "state failure [/srv/private/state]",
        "state failure =/srv/private/state",
        "state failure: /srv/private/state",
        "state failure '/srv/private/state'",
        "/srv/private/state",
    ],
)
async def test_explicit_state_error_redacts_absolute_path_after_safe_boundary(
    diagnostic: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _Bridge("path-fail", state_error=RuntimeError(diagnostic))
    runtime, _, _, _, _ = _runtime(
        target_bridges={"kodi19": bridge, "kodi22": _Bridge("kodi22")}
    )
    monkeypatch.setattr(
        server_core,
        "managed_addon_get",
        lambda **_: {"ok": False, "managed_addon_id": MANAGED_ADDON_ID},
    )
    monkeypatch.setattr(server_core, "AUTHORITATIVE_REPO_ROOT", Path("/missing"))
    monkeypatch.setattr(
        server_core,
        "build_bridge_client",
        lambda: pytest.fail("explicit call reconstructed legacy bridge"),
    )
    server, _ = build_mcp_server(runtime)

    call_result = await _call(
        server, {"managed_addon_id": MANAGED_ADDON_ID, "target": "kodi19"}
    )
    result = _envelope(call_result)
    rendered = json.dumps(call_result.structured_content, sort_keys=True)

    assert result["data"]["kodi_bridge"]["error"] == "[redacted]"
    assert result["raw"]["kodi_bridge"]["error"] == "[redacted]"
    assert "/srv/private/state" not in call_result.content[0].text
    assert "/srv/private/state" not in rendered
    assert result["raw"]["target_id"] == "kodi19"
    assert result["raw"]["target_name"] == "Target kodi19"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "kodi-box:8765",
        "kodi19:8080",
        "claw:9119",
        "localhost:8765",
        "[::1]:8765",
        "[2001:db8::1]:9090",
        "[fe80::1234]:8080",
    ],
)
async def test_explicit_state_error_redacts_internal_host_port_endpoint(
    endpoint: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic = f"state failed at {endpoint}"
    bridge = _Bridge("endpoint-fail", state_error=RuntimeError(diagnostic))
    runtime, _, _, _, _ = _runtime(
        target_bridges={"kodi19": bridge, "kodi22": _Bridge("kodi22")}
    )
    monkeypatch.setattr(
        server_core,
        "managed_addon_get",
        lambda **_: {"ok": False, "managed_addon_id": MANAGED_ADDON_ID},
    )
    monkeypatch.setattr(server_core, "AUTHORITATIVE_REPO_ROOT", Path("/missing"))
    monkeypatch.setattr(
        server_core,
        "build_bridge_client",
        lambda: pytest.fail("explicit call reconstructed legacy bridge"),
    )
    server, _ = build_mcp_server(runtime)

    call_result = await _call(
        server, {"managed_addon_id": MANAGED_ADDON_ID, "target": "kodi19"}
    )
    result = _envelope(call_result)
    rendered = json.dumps(call_result.structured_content, sort_keys=True)

    assert result["data"]["kodi_bridge"]["error"] == "[redacted]"
    assert result["raw"]["kodi_bridge"]["error"] == "[redacted]"
    assert endpoint not in call_result.content[0].text
    assert endpoint not in rendered
    assert result["raw"]["target_id"] == "kodi19"
    assert result["raw"]["target_name"] == "Target kodi19"


@pytest.mark.asyncio
async def test_nested_diagnostic_context_is_inherited_only_for_explicit_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_text = [
        "docs/api/v1",
        "plugin.video.example/path",
        "relative/path/file.png",
        "logical key:value",
        "version 1:2",
        "time 12:34",
        "label kodi-box",
        "text [section]: note",
    ]
    unsafe_text = [
        "state failure {/srv/private/state}",
        "state failed at [::1]:8765",
        "state failed at kodi-box:8765",
    ]
    helper_result = _validation_result()
    helper_result["kodi_bridge"]["diagnostic"] = {
        "details": {
            "where": unsafe_text[0],
            "observations": [
                {"where": unsafe_text[1]},
                unsafe_text[2],
            ],
            "safe_text": safe_text,
        }
    }
    original_helper_result = deepcopy(helper_result)
    runtime, _, _, _, _ = _runtime()
    monkeypatch.setattr(
        server_core,
        "managed_addon_get",
        lambda **_: {"ok": False, "managed_addon_id": MANAGED_ADDON_ID},
    )
    monkeypatch.setattr(
        server_core,
        "validate_managed_addon_state",
        lambda **_: asyncio.sleep(0, result=helper_result),
    )
    monkeypatch.setattr(server_core, "build_bridge_client", _StateClient)
    server, _ = build_mcp_server(runtime)
    explicit_arguments = {
        "managed_addon_id": MANAGED_ADDON_ID,
        "target": "kodi19",
    }
    omitted_arguments = {"managed_addon_id": MANAGED_ADDON_ID}
    original_arguments = deepcopy((explicit_arguments, omitted_arguments))

    explicit_call = await _call(server, explicit_arguments)
    explicit = _envelope(explicit_call)
    omitted_call = await _call(server, omitted_arguments)
    omitted = _envelope(omitted_call)
    explicit_rendered = json.dumps(explicit_call.structured_content, sort_keys=True)
    omitted_rendered = json.dumps(omitted_call.structured_content, sort_keys=True)

    for unsafe in unsafe_text:
        assert unsafe not in explicit_call.content[0].text
        assert unsafe not in explicit_rendered
        assert unsafe in omitted_call.content[0].text
        assert unsafe in omitted_rendered
    for safe in safe_text:
        assert safe in explicit_call.content[0].text
        assert safe in explicit_rendered
    expected_diagnostic = {
        "details": {
            "where": "[redacted]",
            "observations": [
                {"where": "[redacted]"},
                "[redacted]",
            ],
            "safe_text": safe_text,
        }
    }
    assert explicit["data"]["kodi_bridge"]["diagnostic"] == expected_diagnostic
    assert explicit["raw"]["kodi_bridge"]["diagnostic"] == expected_diagnostic
    assert explicit["raw"]["target_id"] == "kodi19"
    assert explicit["raw"]["target_name"] == "Target kodi19"
    assert "target_id" not in omitted["raw"]
    assert "target_name" not in omitted["raw"]
    assert helper_result == original_helper_result
    assert (explicit_arguments, omitted_arguments) == original_arguments


@pytest.mark.asyncio
async def test_unexpected_failure_after_context_is_sanitized_and_attributed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, registry, pool, default, _ = _runtime()

    def fail_registry(**_: Any) -> Any:
        raise RuntimeError("registry failed at /srv/private and https://user:secret@host.invalid")

    monkeypatch.setattr(server_core, "managed_addon_get", fail_registry)
    monkeypatch.setattr(
        server_core,
        "build_bridge_client",
        lambda: pytest.fail("explicit call reconstructed legacy bridge"),
    )
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(server, {"managed_addon_id": MANAGED_ADDON_ID, "target": "kodi22"})
    )
    rendered = json.dumps(result, sort_keys=True)

    assert result["ok"] is False
    assert result["error_type"] == "unknown_error"
    assert result["raw"]["target_id"] == "kodi22"
    assert result["raw"]["target_name"] == "Target kodi22"
    assert "/srv/private" not in rendered
    assert "user:secret" not in rendered
    assert registry.calls == ["kodi22"]
    assert [target.target_id for target in pool.targets] == ["kodi22"]
    assert default.calls == []


@pytest.mark.asyncio
async def test_actual_helper_and_finalization_do_not_mutate_inputs_or_bridge_responses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = _Bridge("kodi19")
    runtime, _, _, _, _ = _runtime(
        target_bridges={"kodi19": bridge, "kodi22": _Bridge("kodi22")}
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "addon.xml").write_text("<addon/>", encoding="utf-8")
    entry = {
        "managed_addon_id": MANAGED_ADDON_ID,
        "addon_id": MANAGED_ADDON_ID,
        "enabled": True,
        "source_path": str(source),
        "last_observed_version": "1.2.3",
        "last_build": None,
    }
    arguments = {"managed_addon_id": MANAGED_ADDON_ID, "target": "kodi19"}
    originals = (
        deepcopy(arguments),
        deepcopy(entry),
        deepcopy(bridge.health_response.to_dict()),
        deepcopy(bridge.state_response.to_dict()),
    )
    monkeypatch.setattr(
        server_core,
        "managed_addon_get",
        lambda **_: {"ok": True, "managed_addon": entry},
    )
    monkeypatch.setattr(server_core, "AUTHORITATIVE_REPO_ROOT", tmp_path / "repo")
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, arguments))

    assert result["raw"]["target_id"] == "kodi19"
    assert bridge.calls == ["health", "state"]
    assert arguments == originals[0]
    assert entry == originals[1]
    assert bridge.health_response.to_dict() == originals[2]
    assert bridge.state_response.to_dict() == originals[3]
