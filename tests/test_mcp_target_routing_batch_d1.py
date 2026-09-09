"""Phase 4A Batch D1 stateless repository-readiness routing tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest
from mcp.types import CallToolRequestParams

import kodi_mcp_mcp.tool_contract as tool_contract
from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_server.models.messages import ResponseMessage
from kodi_mcp_server.targets.registry import LegacyTargetSettings, TargetRegistry


D1_TOOL_NAMES = frozenset({"repository_readiness"})
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
    def __init__(self, label: str, *, tripwire: bool = False) -> None:
        self.label = label
        self.tripwire = tripwire
        self.calls = 0

    async def get_repository_readiness(self):
        if self.tripwire:
            raise AssertionError(f"unexpected default bridge access: {self.label}")
        self.calls += 1
        await asyncio.sleep(0)
        return ResponseMessage(
            request_id=f"readiness-{self.label}",
            result={"transport": {"ok": True}, "result": _evidence(self.label)},
            error=None,
        )


def _evidence(label: str) -> dict[str, Any]:
    base_url = "http://repo.test"
    return {
        "ok": True,
        "addon_id": "repository.kodi-mcp",
        "installed": True,
        "enabled": True,
        "installed_version": "1.0.4",
        "configured_identity": {
            "addon_id": "repository.kodi-mcp",
            "version": "1.0.4",
        },
        "urls": {
            "metadata": base_url + "/repo/content/addons.xml",
            "checksum": base_url + "/repo/content/addons.xml.md5",
            "datadir": base_url + "/repo/content/zips/",
        },
        "metadata": {
            "reachable": True,
            "parseable": True,
            "addon_count": 1,
            "observation": label,
            "endpoint_url": f"https://{label}.routing-secret.invalid/readiness",
            "host": f"{label}.routing-secret.invalid",
            "port": 8765,
            "auth_token": f"{label}-bridge-secret",
        },
        "checksum": {"reachable": True, "match": True},
        "package": {"observable": True, "reachable": True},
        "catalog_refresh": {"observable": False, "state": "unknown"},
        "catalog_ingestion": {"observable": False, "state": "unknown"},
    }


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


def _runtime(*, default_tripwire: bool = False):
    source_registry = TargetRegistry.from_sources(
        legacy=LegacyTargetSettings(
            jsonrpc_url="http://default.invalid/jsonrpc",
            bridge_url="http://default.invalid/bridge",
        ),
        targets_json=json.dumps([_target("kodi19"), _target("kodi20")]),
    )
    registry = _CountingRegistry(source_registry)
    default = _Bundle(object(), _Bridge("default", tripwire=default_tripwire))
    bundles = {
        "kodi19": _Bundle(object(), _Bridge("kodi19")),
        "kodi20": _Bundle(object(), _Bridge("kodi20")),
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
        CallToolRequestParams(name="repository_readiness", arguments=arguments),
    )


def _envelope(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_exact_d1_inventory_and_optional_target_schema():
    runtime, _, _, _, _ = _runtime()
    server, _ = build_mcp_server(runtime)
    listed = await server.get_request_handler("tools/list").handler(None, None)
    by_name = {tool.name: tool for tool in listed.tools}
    targeted = {
        name
        for name, tool in by_name.items()
        if "target" in tool.input_schema.get("properties", {})
    }

    assert getattr(tool_contract, "BATCH_D1_TARGET_TOOL_NAMES", None) == D1_TOOL_NAMES
    assert targeted == tool_contract.EXPLICIT_TARGET_TOOL_NAMES
    assert len(targeted) == 33
    readiness_schema = by_name["repository_readiness"].input_schema
    assert readiness_schema == {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "pattern": r"^[a-z0-9](?:[a-z0-9._-]{0,63})$",
            }
        },
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
async def test_malformed_target_fails_before_registry_pool_or_bridge_access(target):
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
async def test_unknown_target_is_not_found_without_pool_or_default_fallback():
    runtime, registry, pool, default, bundles = _runtime(default_tripwire=True)
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "missing"}))

    assert result["error_type"] == "not_found"
    assert result["error_code"] == 404
    assert registry.calls == ["missing"]
    assert pool.targets == []
    assert default.bridge.calls == 0
    assert all(bundle.bridge.calls == 0 for bundle in bundles.values())


@pytest.mark.asyncio
async def test_a_b_default_calls_use_only_their_exact_bridges_without_state(monkeypatch):
    monkeypatch.setattr("kodi_mcp_server.repository_readiness.REPO_BASE_URL", "http://repo.test")
    runtime, registry, pool, default, bundles = _runtime(default_tripwire=True)
    server, _ = build_mcp_server(runtime)

    result_a = _envelope(await _call(server, {"target": "kodi19"}))
    result_b = _envelope(await _call(server, {"target": "kodi20"}))
    default.bridge.tripwire = False
    result_default = _envelope(await _call(server, {}))

    assert [result_a["raw"]["target_id"], result_b["raw"]["target_id"]] == [
        "kodi19",
        "kodi20",
    ]
    assert "target_id" not in result_default["raw"]
    assert registry.calls == ["kodi19", "kodi20"]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi20"]
    assert [bundles["kodi19"].bridge.calls, bundles["kodi20"].bridge.calls] == [1, 1]
    assert default.bridge.calls == 1
    assert runtime["bridge"] is default.bridge


@pytest.mark.asyncio
async def test_server_global_repository_comparison_is_identical_across_targets(monkeypatch):
    monkeypatch.setattr("kodi_mcp_server.repository_readiness.REPO_BASE_URL", "http://repo.test")
    runtime, _, _, _, _ = _runtime(default_tripwire=True)
    server, _ = build_mcp_server(runtime)

    result_a = _envelope(await _call(server, {"target": "kodi19"}))
    result_b = _envelope(await _call(server, {"target": "kodi20"}))

    data_a, data_b = result_a["data"], result_b["data"]
    assert data_a["canonical_version"] == data_b["canonical_version"] == "1.0.4"
    assert data_a["expected_urls"] == data_b["expected_urls"]
    assert data_a["urls_match_server_configuration"] is True
    assert data_b["urls_match_server_configuration"] is True
    assert [data_a["metadata"]["observation"], data_b["metadata"]["observation"]] == [
        "kodi19",
        "kodi20",
    ]


@pytest.mark.asyncio
async def test_explicit_readiness_redacts_target_details_and_keeps_fields(monkeypatch):
    monkeypatch.setattr("kodi_mcp_server.repository_readiness.REPO_BASE_URL", "http://repo.test")
    monkeypatch.setenv("KODI19_USERNAME", "kodi19-user-secret")
    monkeypatch.setenv("KODI19_PASSWORD", "kodi19-password-secret")
    monkeypatch.setenv("KODI19_TOKEN", "kodi19-bridge-secret")
    runtime, _, _, _, _ = _runtime(default_tripwire=True)
    server, _ = build_mcp_server(runtime)

    call_result = await _call(server, {"target": "kodi19"})
    result = _envelope(call_result)
    rendered = call_result.content[0].text

    assert call_result.structured_content == result
    assert result["raw"]["target_id"] == "kodi19"
    assert result["data"]["repository_id"] == "repository.kodi-mcp"
    assert result["data"]["canonical_version"] == "1.0.4"
    assert result["data"]["metadata"]["reachable"] is True
    assert result["data"]["metadata"]["observation"] == "kodi19"
    for field in ("endpoint_url", "host", "port", "auth_token"):
        assert result["data"]["metadata"][field] == "[redacted]"
    for secret in (
        "routing-secret.invalid",
        "kodi19-user-secret",
        "kodi19-password-secret",
        "kodi19-bridge-secret",
    ):
        assert secret not in rendered


@pytest.mark.asyncio
async def test_concurrent_a_b_readiness_calls_keep_bridges_isolated(monkeypatch):
    monkeypatch.setattr("kodi_mcp_server.repository_readiness.REPO_BASE_URL", "http://repo.test")
    runtime, registry, pool, default, bundles = _runtime(default_tripwire=True)
    server, _ = build_mcp_server(runtime)

    results = await asyncio.gather(
        _call(server, {"target": "kodi19"}),
        _call(server, {"target": "kodi20"}),
    )
    envelopes = [_envelope(result) for result in results]

    assert [result["raw"]["target_id"] for result in envelopes] == ["kodi19", "kodi20"]
    assert [result["data"]["metadata"]["observation"] for result in envelopes] == [
        "kodi19",
        "kodi20",
    ]
    assert registry.calls == ["kodi19", "kodi20"]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi20"]
    assert [bundles["kodi19"].bridge.calls, bundles["kodi20"].bridge.calls] == [1, 1]
    assert default.bridge.calls == 0
