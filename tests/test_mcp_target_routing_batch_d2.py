"""Phase 4A Batch D2 stateless bridge-bootstrap routing tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.types import CallToolRequestParams

import kodi_mcp_mcp.server_core as server_core
import kodi_mcp_mcp.tool_contract as tool_contract
from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_server.targets.registry import LegacyTargetSettings, TargetRegistry


D2_TOOL_NAMES = frozenset({"bridge_bootstrap_status"})
TARGET_PROPERTY = {
    "type": "string",
    "pattern": r"^[a-z0-9](?:[a-z0-9._-]{0,63})$",
}
DEFERRED_TOOL_NAMES = frozenset(
    {
        "bridge_write_log_marker",
        "kodi_gui_screenshot",
        "kodi_notifications_sample",
        "managed_addon_build_publish_and_stage",
        "managed_addon_build_publish_stage_and_apply",
        "managed_addon_validate_state",
        "repo_publish_stage_apply_artifact",
        "repo_stage_and_apply_addon",
        "repo_stage_current_dev_repo",
        "repository_bootstrap_install",
    }
)
ADDON_ID = "service.kodi_mcp"
VERSION = "0.2.40"
GIT_SHA = "b" * 40
FINGERPRINT = "a" * 64


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


class _JsonRpc:
    def __init__(
        self,
        label: str,
        *,
        addon_version: str | None = VERSION,
        version_error: str | None = None,
    ) -> None:
        self.label = label
        self.addon_version = addon_version
        self.version_error = version_error
        self.calls: list[str] = []

    async def get_jsonrpc_version(self):
        self.calls.append("get_jsonrpc_version")
        await asyncio.sleep(0)
        return SimpleNamespace(
            result={"version": {"major": 13}, "observation": self.label},
            error=self.version_error,
            error_code=None,
        )

    async def get_addon_details(self, addonid: str):
        assert addonid == ADDON_ID
        self.calls.append("get_addon_details")
        await asyncio.sleep(0)
        if self.addon_version is None:
            return SimpleNamespace(
                result=None,
                error="jsonrpc error -32602: Invalid params.",
                error_code=-32602,
            )
        return SimpleNamespace(
            result={
                "addon": {
                    "addonid": ADDON_ID,
                    "version": self.addon_version,
                    "enabled": True,
                }
            },
            error=None,
            error_code=None,
        )


class _Bridge:
    def __init__(
        self,
        label: str,
        *,
        health_error: str | None = None,
        status_error: str | None = None,
        bridge_version: str = VERSION,
    ) -> None:
        self.label = label
        self.health_error = health_error
        self.status_error = status_error
        self.bridge_version = bridge_version
        self.calls: list[str] = []

    async def get_bridge_health(self):
        self.calls.append("get_bridge_health")
        await asyncio.sleep(0)
        return SimpleNamespace(
            result={
                "status": "ok",
                "service": ADDON_ID,
                "addon_id": ADDON_ID,
                "health_type": "shallow",
                "endpoint_url": f"https://{self.label}.routing-secret.invalid/health",
            },
            error=self.health_error,
            error_code=None,
        )

    async def get_bridge_status(self):
        self.calls.append("get_bridge_status")
        await asyncio.sleep(0)
        return SimpleNamespace(
            result={
                "addon_id": ADDON_ID,
                "addon_version": self.bridge_version,
                "build": {
                    "source_git_sha": GIT_SHA,
                    "source_fingerprint_sha256": FINGERPRINT,
                },
            },
            error=self.status_error,
            error_code=None,
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


def _runtime(
    *,
    default: _Bundle | None = None,
    bundles: dict[str, _Bundle] | None = None,
):
    source_registry = TargetRegistry.from_sources(
        legacy=LegacyTargetSettings(
            jsonrpc_url="http://default.invalid/jsonrpc",
            bridge_url="http://default.invalid/bridge",
        ),
        targets_json=json.dumps([_target("kodi19"), _target("kodi20")]),
    )
    registry = _CountingRegistry(source_registry)
    default = default or _Bundle(_JsonRpc("default"), _Bridge("default"))
    bundles = bundles or {
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
    return runtime, registry, pool, default, bundles


async def _call(server, arguments: dict[str, Any]):
    return await server.get_request_handler("tools/call").handler(
        None,
        CallToolRequestParams(name="bridge_bootstrap_status", arguments=arguments),
    )


def _envelope(result):
    return json.loads(result.content[0].text)


def _write_bundle(tmp_path: Path) -> Path:
    artifact = tmp_path / f"{ADDON_ID}-{VERSION}.zip"
    with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            f"{ADDON_ID}/addon.xml",
            f'<addon id="{ADDON_ID}" version="{VERSION}" />',
        )
        archive.writestr(
            f"{ADDON_ID}/build_manifest.json",
            json.dumps(
                {
                    "source_git_sha": GIT_SHA,
                    "source_fingerprint_sha256": FINGERPRINT,
                }
            ),
        )
    manifest = {
        "schema_version": 1,
        "addon_id": ADDON_ID,
        "version": VERSION,
        "artifact": artifact.name,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "source_git_sha": GIT_SHA,
        "source_fingerprint_sha256": FINGERPRINT,
    }
    manifest_path = tmp_path / "bridge-bootstrap.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _configure_bundle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server_core, "BRIDGE_BOOTSTRAP_MANIFEST_PATH", _write_bundle(tmp_path))
    monkeypatch.setattr(server_core, "REPO_BASE_URL", "https://canonical.example.test")


@pytest.mark.asyncio
async def test_exact_d2_inventory_and_canonical_optional_target_schema():
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
    assert getattr(tool_contract, "BATCH_D2_TARGET_TOOL_NAMES", None) == D2_TOOL_NAMES
    assert tool_contract.EXPLICIT_TARGET_TOOL_NAMES == (
        tool_contract.BATCH_A_TARGET_TOOL_NAMES
        | tool_contract.BATCH_B_TARGET_TOOL_NAMES
        | tool_contract.BATCH_C1_TARGET_TOOL_NAMES
        | tool_contract.BATCH_C2_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D1_TARGET_TOOL_NAMES
        | D2_TOOL_NAMES
    )
    assert targeted == tool_contract.EXPLICIT_TARGET_TOOL_NAMES
    assert len(targeted) == 33
    assert by_name["bridge_bootstrap_status"].input_schema == {
        "type": "object",
        "properties": {"target": TARGET_PROPERTY},
        "additionalProperties": False,
    }
    assert all(
        "target" not in by_name[name].input_schema["properties"]
        for name in DEFERRED_TOOL_NAMES
    )
    assert by_name["target_health"].input_schema["required"] == ["target_id"]
    assert "target" not in by_name["target_health"].input_schema["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [42, "Kodi19", "https://not-a-target.invalid"])
async def test_malformed_target_fails_before_registry_pool_or_transport_access(target):
    runtime = {
        "registry": _NoAccess(),
        "transport_pool": _NoAccess(),
        "jsonrpc": _NoAccess(),
        "bridge": _NoAccess(),
        "notifications": None,
    }
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": target}))

    assert result["error_type"] == "invalid_params"
    assert result["error_code"] is None


@pytest.mark.asyncio
async def test_credential_url_target_is_sanitized_without_io_or_argument_mutation(monkeypatch):
    runtime = {
        "registry": _NoAccess(),
        "transport_pool": _NoAccess(),
        "jsonrpc": _NoAccess(),
        "bridge": _NoAccess(),
        "notifications": None,
    }
    server, _ = build_mcp_server(runtime)
    target_url = "https://user:secret@example.invalid/path"
    arguments = {"target": target_url, "note": "safe-bootstrap-note"}
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
    assert target_url not in call_result.content[0].text
    assert "user:secret" not in call_result.content[0].text
    assert target_url not in structured
    assert "user:secret" not in structured
    assert result["raw"]["arguments"] == {
        "target": "[redacted]",
        "note": "safe-bootstrap-note",
    }
    assert "safe-bootstrap-note" in call_result.content[0].text
    assert arguments == original


@pytest.mark.asyncio
async def test_unknown_target_is_not_found_without_pool_or_default_fallback():
    runtime, registry, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "missing"}))

    assert result["error_type"] == "not_found"
    assert result["error_code"] == 404
    assert registry.calls == ["missing"]
    assert pool.targets == []
    assert default.jsonrpc.calls == []
    assert default.bridge.calls == []
    assert all(bundle.jsonrpc.calls == [] for bundle in bundles.values())
    assert all(bundle.bridge.calls == [] for bundle in bundles.values())


@pytest.mark.asyncio
async def test_a_b_and_default_bind_exact_same_context_dependencies(monkeypatch):
    runtime, registry, pool, default, bundles = _runtime()
    seen: list[tuple[Any, Any]] = []

    async def inspect(*, jsonrpc_tool, bridge_tool, manifest_path, base_url):
        seen.append((jsonrpc_tool, bridge_tool))
        assert jsonrpc_tool.label == bridge_tool.label
        return {
            "ok": True,
            "state": "already_installed",
            "verified": True,
            "jsonrpc_observation": jsonrpc_tool.label,
            "bridge_observation": bridge_tool.label,
            "canonical_version": VERSION,
        }

    monkeypatch.setattr(server_core, "inspect_bootstrap_state", inspect)
    server, _ = build_mcp_server(runtime)

    results = [
        _envelope(await _call(server, {"target": "kodi19"})),
        _envelope(await _call(server, {"target": "kodi20"})),
        _envelope(await _call(server, {})),
    ]

    assert [(item["data"]["jsonrpc_observation"], item["data"]["bridge_observation"]) for item in results] == [
        ("kodi19", "kodi19"),
        ("kodi20", "kodi20"),
        ("default", "default"),
    ]
    assert [item["raw"].get("target_id") for item in results] == ["kodi19", "kodi20", None]
    assert seen == [
        (bundles["kodi19"].jsonrpc, bundles["kodi19"].bridge),
        (bundles["kodi20"].jsonrpc, bundles["kodi20"].bridge),
        (runtime["jsonrpc"], runtime["bridge"]),
    ]
    assert seen[-1][0] is default.jsonrpc
    assert seen[-1][1] is default.bridge
    assert registry.calls == ["kodi19", "kodi20"]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi20"]


@pytest.mark.asyncio
async def test_concurrent_a_b_calls_never_cross_bind_dependency_pairs(monkeypatch):
    runtime, _, pool, default, bundles = _runtime()

    async def inspect(*, jsonrpc_tool, bridge_tool, manifest_path, base_url):
        await asyncio.sleep(0)
        assert jsonrpc_tool.label == bridge_tool.label
        return {
            "ok": True,
            "state": "already_installed",
            "verified": True,
            "pair": [jsonrpc_tool.label, bridge_tool.label],
        }

    monkeypatch.setattr(server_core, "inspect_bootstrap_state", inspect)
    server, _ = build_mcp_server(runtime)

    first, second = await asyncio.gather(
        _call(server, {"target": "kodi19"}),
        _call(server, {"target": "kodi20"}),
    )
    results = [_envelope(first), _envelope(second)]

    assert [item["data"]["pair"] for item in results] == [
        ["kodi19", "kodi19"],
        ["kodi20", "kodi20"],
    ]
    assert [item["raw"]["target_id"] for item in results] == ["kodi19", "kodi20"]
    assert {target.target_id for target in pool.targets} == {"kodi19", "kodi20"}
    assert default.jsonrpc.calls == []
    assert default.bridge.calls == []
    assert all(bundle.jsonrpc.label == bundle.bridge.label for bundle in bundles.values())


@pytest.mark.asyncio
async def test_server_global_bootstrap_expectations_are_invariant_across_targets(
    monkeypatch, tmp_path
):
    bundles = {
        "kodi19": _Bundle(_JsonRpc("kodi19"), _Bridge("kodi19")),
        "kodi20": _Bundle(
            _JsonRpc("kodi20", addon_version="9.9.9"),
            _Bridge("kodi20", bridge_version="9.9.9"),
        ),
    }
    runtime, _, _, _, _ = _runtime(bundles=bundles)
    _configure_bundle(monkeypatch, tmp_path)
    server, _ = build_mcp_server(runtime)

    result_a = _envelope(await _call(server, {"target": "kodi19"}))["data"]
    result_b = _envelope(await _call(server, {"target": "kodi20"}))["data"]

    assert result_a["expected_identity"] == result_b["expected_identity"] == {
        "addon_id": ADDON_ID,
        "kodi_addon_version": VERSION,
        "bridge_version": VERSION,
        "source_git_sha": GIT_SHA,
        "source_fingerprint_sha256": FINGERPRINT,
    }
    assert result_a["observed_identity"] != result_b["observed_identity"]
    assert result_a["verified"] is True
    assert result_b["verified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("jsonrpc", "bridge", "state", "verified", "jsonrpc_calls", "bridge_calls"),
    [
        (
            _JsonRpc("kodi19", version_error="jsonrpc unavailable"),
            _Bridge("kodi19"),
            "bootstrap_unsupported",
            False,
            ["get_jsonrpc_version"],
            [],
        ),
        (
            _JsonRpc("kodi19"),
            _Bridge("kodi19", health_error="bridge unavailable"),
            "user_action_required",
            False,
            ["get_jsonrpc_version", "get_addon_details"],
            ["get_bridge_health"],
        ),
        (
            _JsonRpc("kodi19", version_error="jsonrpc unavailable"),
            _Bridge("kodi19", health_error="bridge unavailable"),
            "bootstrap_unsupported",
            False,
            ["get_jsonrpc_version"],
            [],
        ),
        (
            _JsonRpc("kodi19", addon_version=None),
            _Bridge("kodi19"),
            "user_action_required",
            False,
            ["get_jsonrpc_version", "get_addon_details"],
            [],
        ),
        (
            _JsonRpc("kodi19", addon_version="9.9.9"),
            _Bridge("kodi19", bridge_version="9.9.9"),
            "already_installed",
            False,
            ["get_jsonrpc_version", "get_addon_details"],
            ["get_bridge_health", "get_bridge_status"],
        ),
    ],
    ids=["jsonrpc-failure", "bridge-failure", "both-fail", "addon-absent", "identity-mismatch"],
)
async def test_failure_and_mismatch_semantics_stay_bound_without_top_level_retry(
    monkeypatch,
    tmp_path,
    jsonrpc,
    bridge,
    state,
    verified,
    jsonrpc_calls,
    bridge_calls,
):
    bundles = {
        "kodi19": _Bundle(jsonrpc, bridge),
        "kodi20": _Bundle(_JsonRpc("kodi20"), _Bridge("kodi20")),
    }
    runtime, registry, pool, default, _ = _runtime(bundles=bundles)
    _configure_bundle(monkeypatch, tmp_path)
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "kodi19"}))

    assert result["ok"] is True
    assert result["data"]["state"] == state
    assert result["data"]["verified"] is verified
    assert result["raw"]["target_id"] == "kodi19"
    assert jsonrpc.calls == jsonrpc_calls
    assert bridge.calls == bridge_calls
    assert registry.calls == ["kodi19"]
    assert [target.target_id for target in pool.targets] == ["kodi19"]
    assert default.jsonrpc.calls == []
    assert default.bridge.calls == []


@pytest.mark.asyncio
async def test_explicit_result_redacts_transport_metadata_and_preserves_safe_fields(monkeypatch):
    runtime, _, _, default, _ = _runtime()

    async def inspect(*, jsonrpc_tool, bridge_tool, manifest_path, base_url):
        return {
            "ok": True,
            "state": "already_installed",
            "verified": True,
            "addon_id": ADDON_ID,
            "bridge_version": VERSION,
            "canonical_build": GIT_SHA,
            "transport": {
                "endpoint_url": "https://user:secret@kodi19.routing-secret.invalid/status",
                "host": "kodi19.routing-secret.invalid",
                "port": 8765,
                "auth_token": "bridge-secret",
            },
        }

    monkeypatch.setattr(server_core, "inspect_bootstrap_state", inspect)
    server, _ = build_mcp_server(runtime)

    call_result = await _call(server, {"target": "kodi19"})
    result = _envelope(call_result)
    rendered = call_result.content[0].text

    assert call_result.structured_content == result
    assert result["raw"]["target_id"] == "kodi19"
    assert result["data"]["addon_id"] == ADDON_ID
    assert result["data"]["bridge_version"] == VERSION
    assert result["data"]["canonical_build"] == GIT_SHA
    assert result["data"]["transport"] == {
        "endpoint_url": "[redacted]",
        "host": "[redacted]",
        "port": "[redacted]",
        "auth_token": "[redacted]",
    }
    for secret in ("user:secret", "routing-secret.invalid", "bridge-secret"):
        assert secret not in rendered
    assert default.jsonrpc.calls == []
    assert default.bridge.calls == []
