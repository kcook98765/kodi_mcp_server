"""Phase 4A Batch D5 stateless screenshot-routing tests."""

from __future__ import annotations

import asyncio
import base64
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest
from mcp.types import CallToolRequestParams

import kodi_mcp_mcp.server_core as server_core
import kodi_mcp_mcp.tool_contract as tool_contract
from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_mcp.target_routing import _trusted_server_screenshot_route
from kodi_mcp_server.models.messages import ErrorType, ResponseMessage
from kodi_mcp_server.targets.registry import LegacyTargetSettings, TargetRegistry
from tests.png_fixtures import png_rgba


D5_TOOL_NAMES = frozenset({"kodi_gui_screenshot"})
TARGET_PROPERTY = {
    "type": "string",
    "pattern": r"^[a-z0-9](?:[a-z0-9._-]{0,63})$",
}
PNG_A = png_rgba([[(24, 48, 72, 255), (1, 2, 3, 255)]])
PNG_B = png_rgba([[(96, 72, 48, 255), (4, 5, 6, 255)]])
BLACK_PNG = png_rgba([[(0, 0, 0, 255), (0, 0, 0, 255)]])


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
        captures: list[bytes | ResponseMessage | Exception] | None = None,
        *,
        tripwire: bool = False,
        gui_states: list[ResponseMessage | Exception] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.label = label
        self.captures = list(captures or [PNG_A])
        self.tripwire = tripwire
        self.extra = deepcopy(extra or {})
        self.capture_calls: list[bool] = []
        self.state_calls = 0
        self.returned: list[ResponseMessage] = []
        self.gui_states = list(gui_states or [])

    async def gui_screenshot(self, include_image: bool = False):
        if self.tripwire:
            raise AssertionError(f"unexpected capture through {self.label}")
        index = min(len(self.capture_calls), len(self.captures) - 1)
        capture = self.captures[index]
        self.capture_calls.append(include_image)
        await asyncio.sleep(0)
        if isinstance(capture, Exception):
            raise capture
        if isinstance(capture, ResponseMessage):
            self.returned.append(capture)
            return capture
        result = {
            "ok": True,
            "path": f"/target/{self.label}/screenshot-{len(self.capture_calls)}.png",
            "filename": f"{self.label}-{len(self.capture_calls)}.png",
            "content_type": "image/png",
            "size_bytes": len(capture),
            **deepcopy(self.extra),
        }
        if include_image:
            result["image_base64"] = base64.b64encode(capture).decode("ascii")
        response = ResponseMessage(
            request_id=f"capture-{self.label}-{len(self.capture_calls)}",
            result=result,
            error=None,
        )
        self.returned.append(response)
        return response

    async def gui_state(self):
        self.state_calls += 1
        await asyncio.sleep(0)
        if self.gui_states:
            value = self.gui_states[min(self.state_calls - 1, len(self.gui_states) - 1)]
            if isinstance(value, Exception):
                raise value
            return value
        return ResponseMessage(
            request_id=f"state-{self.label}-{self.state_calls}",
            result={
                "ok": True,
                "current_window": "Home",
                "current_window_id": 10000,
                "current_dialog_id": 9999,
                "conditions": {
                    "fullscreen_video": False,
                    "player_has_media": False,
                    "player_has_video": False,
                    "player_playing": False,
                    "player_paused": False,
                },
                "active_players": [],
            },
            error=None,
        )


class _Store:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[bytes] = []
        self.results: list[dict[str, Any]] = []

    def __call__(self, image_base64: str):
        image = base64.b64decode(image_base64, validate=True)
        self.calls.append(image)
        if self.error is not None:
            raise self.error
        label = "a" if image == PNG_A else "b" if image == PNG_B else "other"
        filename = {
            "a": "1700000000001-aaaaaaaaaaaa.png",
            "b": "1700000000002-bbbbbbbbbbbb.png",
            "other": "1700000000003-cccccccccccc.png",
        }[label]
        result = {
            "screenshot_id": filename.removesuffix(".png"),
            "filename": filename,
            "path": f"/workspaces/internal/screenshots/{filename}",
            "url": f"https://mcp.example.test/screenshots/{filename}",
            "content_type": "image/png",
            "format": "png",
            "size_bytes": len(image),
            "width": 2,
            "height": 1,
            "sha256": f"sha-{label}",
        }
        self.results.append(result)
        return result


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
    source = TargetRegistry.from_sources(
        legacy=LegacyTargetSettings(
            jsonrpc_url="http://default.invalid/jsonrpc",
            bridge_url="http://default.invalid/bridge",
        ),
        targets_json=json.dumps([_target("kodi19"), _target("kodi22")]),
    )
    registry = _CountingRegistry(source)
    default_bridge = default_bridge or _Bridge("default", [PNG_A])
    target_bridges = target_bridges or {
        "kodi19": _Bridge("kodi19", [PNG_A]),
        "kodi22": _Bridge("kodi22", [PNG_B]),
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
        CallToolRequestParams(name="kodi_gui_screenshot", arguments=arguments),
    )


def _envelope(result):
    value = json.loads(result.content[0].text)
    assert result.structured_content == value
    return value


@pytest.mark.asyncio
async def test_exact_d5_inventory_and_screenshot_schema():
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
    assert tool_contract.BATCH_D4_TARGET_TOOL_NAMES == frozenset(
        {"bridge_write_log_marker"}
    )
    assert getattr(tool_contract, "BATCH_D5_TARGET_TOOL_NAMES", None) == D5_TOOL_NAMES
    assert targeted == tool_contract.EXPLICIT_TARGET_TOOL_NAMES
    assert len(targeted) == 36
    assert by_name["kodi_gui_screenshot"].input_schema == {
        "type": "object",
        "properties": {
            "include_image": {
                "type": "boolean",
                "default": False,
                "description": "If true and the PNG is at most 524288 bytes, also return canonical MCP ImageContent. Larger images use stored-artifact mode or fail explicitly when store is false.",
            },
            "store": {
                "type": "boolean",
                "default": True,
                "description": "If true, persist the screenshot on the MCP server and return a served URL.",
            },
            "target": TARGET_PROPERTY,
        },
        "additionalProperties": False,
    }
    assert "managed_addon_validate_state" not in targeted
    assert not ({
        "managed_addon_build_publish_and_stage",
        "managed_addon_build_publish_stage_and_apply",
        "repo_publish_stage_apply_artifact",
        "repo_stage_and_apply_addon",
        "repo_stage_current_dev_repo",
        "repository_bootstrap_install",
    } & targeted)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [42, "Kodi19", "bad target", "https://not-a-target.invalid"])
async def test_malformed_target_fails_before_registry_pool_bridge_state_or_store(
    target, monkeypatch
):
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    runtime = {
        "registry": _NoAccess(),
        "transport_pool": _NoAccess(),
        "jsonrpc": _NoAccess(),
        "bridge": _NoAccess(),
        "notifications": _NoAccess(),
    }
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": target}))

    assert result["error_type"] == "invalid_params"
    assert store.calls == []


@pytest.mark.asyncio
async def test_credential_target_and_other_schema_error_are_sanitized_without_mutation(
    monkeypatch,
):
    target_url = "https://user:secret@example.invalid/private"
    arguments = {"target": target_url, "store": "yes"}
    original = deepcopy(arguments)
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    monkeypatch.setattr(
        server_core,
        "resolve_target_context",
        lambda *_: pytest.fail("malformed target must not be resolved"),
    )
    runtime = {
        "registry": _NoAccess(),
        "transport_pool": _NoAccess(),
        "jsonrpc": _NoAccess(),
        "bridge": _NoAccess(),
        "notifications": _NoAccess(),
    }
    server, _ = build_mcp_server(runtime)

    call_result = await _call(server, arguments)
    result = _envelope(call_result)
    rendered = json.dumps(call_result.structured_content, sort_keys=True)

    assert result["error_type"] == "invalid_params"
    assert result["raw"]["arguments"]["target"] == "[redacted]"
    assert target_url not in call_result.content[0].text
    assert target_url not in rendered
    assert "user:secret" not in call_result.content[0].text
    assert store.calls == []
    assert arguments == original


@pytest.mark.asyncio
async def test_rejected_schemeless_credential_target_is_always_redacted_without_io(
    monkeypatch,
):
    rejected_target = "user:synthetic-secret@example.invalid"
    arguments = {"target": rejected_target, "store": "yes"}
    original = deepcopy(arguments)
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    monkeypatch.setattr(
        server_core,
        "resolve_target_context",
        lambda *_: pytest.fail("rejected target must not be resolved"),
    )
    runtime = {
        "registry": _NoAccess(),
        "transport_pool": _NoAccess(),
        "jsonrpc": _NoAccess(),
        "bridge": _NoAccess(),
        "notifications": _NoAccess(),
    }
    server, _ = build_mcp_server(runtime)

    call_result = await _call(server, arguments)
    result = _envelope(call_result)
    rendered_structured = json.dumps(call_result.structured_content, sort_keys=True)

    assert result["error_type"] == "invalid_params"
    assert result["raw"]["arguments"] == {
        "target": "[redacted]",
        "store": "yes",
    }
    assert rejected_target not in call_result.content[0].text
    assert rejected_target not in rendered_structured
    assert arguments == original
    assert store.calls == []


@pytest.mark.asyncio
async def test_unknown_target_is_not_found_without_pool_default_or_store(monkeypatch):
    default = _Bridge("default", tripwire=True)
    runtime, registry, pool, _, bundles = _runtime(default_bridge=default)
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "missing"}))

    assert result["error_type"] == "not_found"
    assert registry.calls == ["missing"]
    assert pool.targets == []
    assert default.capture_calls == []
    assert all(bundle.bridge.capture_calls == [] for bundle in bundles.values())
    assert store.calls == []


@pytest.mark.asyncio
async def test_explicit_a_b_then_default_keep_exact_bridge_and_artifact_assignment(monkeypatch):
    default = _Bridge("default", [PNG_A], tripwire=True)
    bridges = {
        "kodi19": _Bridge("kodi19", [PNG_A]),
        "kodi22": _Bridge("kodi22", [PNG_B]),
    }
    runtime, registry, pool, _, bundles = _runtime(
        default_bridge=default, target_bridges=bridges
    )
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    result_a = _envelope(await _call(server, {"target": "kodi19"}))
    result_b = _envelope(await _call(server, {"target": "kodi22"}))
    default.tripwire = False
    result_default = _envelope(await _call(server, {}))

    assert [result_a["raw"]["target_id"], result_b["raw"]["target_id"]] == [
        "kodi19",
        "kodi22",
    ]
    assert "target_id" not in result_default["raw"]
    assert bridges["kodi19"].capture_calls == [True]
    assert bridges["kodi22"].capture_calls == [True]
    assert default.capture_calls == [True]
    assert store.calls == [PNG_A, PNG_B, PNG_A]
    assert result_a["data"]["server_screenshot"]["url"].endswith(
        "1700000000001-aaaaaaaaaaaa.png"
    )
    assert result_b["data"]["server_screenshot"]["url"].endswith(
        "1700000000002-bbbbbbbbbbbb.png"
    )
    assert registry.calls == ["kodi19", "kodi22"]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi22"]
    assert runtime["bridge"] is default
    assert bundles["kodi19"].bridge is bridges["kodi19"]


@pytest.mark.asyncio
async def test_black_frame_retries_and_gui_reads_remain_on_one_selected_bridge(monkeypatch):
    selected = _Bridge("kodi19", [BLACK_PNG, BLACK_PNG, PNG_A])
    default = _Bridge("default", tripwire=True)
    runtime, registry, pool, _, _ = _runtime(
        default_bridge=default,
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "kodi19"}))

    assert result["ok"] is True
    assert result["data"]["capture_validation"]["attempts"] == 3
    assert selected.capture_calls == [True, True, True]
    assert selected.state_calls == 2
    assert default.capture_calls == []
    assert registry.calls == ["kodi19"]
    assert [target.target_id for target in pool.targets] == ["kodi19"]
    assert store.calls == [PNG_A]


@pytest.mark.asyncio
async def test_concurrent_a_b_captures_keep_bridges_images_targets_and_stores_isolated(
    monkeypatch,
):
    default = _Bridge("default", tripwire=True)
    bridges = {
        "kodi19": _Bridge("kodi19", [PNG_A]),
        "kodi22": _Bridge("kodi22", [PNG_B]),
    }
    runtime, registry, pool, _, _ = _runtime(
        default_bridge=default, target_bridges=bridges
    )
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    call_a, call_b = await asyncio.gather(
        _call(server, {"target": "kodi19", "include_image": True}),
        _call(server, {"target": "kodi22", "include_image": True}),
    )
    result_a, result_b = _envelope(call_a), _envelope(call_b)

    assert [result_a["raw"]["target_id"], result_b["raw"]["target_id"]] == [
        "kodi19",
        "kodi22",
    ]
    assert [call_a.content[1].data, call_b.content[1].data] == [
        base64.b64encode(PNG_A).decode("ascii"),
        base64.b64encode(PNG_B).decode("ascii"),
    ]
    assert store.calls == [PNG_A, PNG_B]
    assert bridges["kodi19"].capture_calls == [True]
    assert bridges["kodi22"].capture_calls == [True]
    assert default.capture_calls == []
    assert registry.calls == ["kodi19", "kodi22"]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi22"]


@pytest.mark.asyncio
async def test_omitted_target_uses_exact_legacy_bridge_without_lookup_or_attribution(
    monkeypatch,
):
    default = _Bridge("default", [PNG_A])
    runtime = {
        "registry": _NoAccess(),
        "transport_pool": _NoAccess(),
        "jsonrpc": _NoAccess(),
        "bridge": default,
        "notifications": _NoAccess(),
    }
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {}))

    assert result["ok"] is True
    assert runtime["bridge"] is default
    assert default.capture_calls == [True]
    assert store.calls == [PNG_A]
    assert result["data"]["path"] == "/target/default/screenshot-1.png"
    assert "target_id" not in result["raw"]
    assert "target_name" not in result["raw"]


@pytest.mark.asyncio
async def test_explicit_provenance_redacts_all_paths_and_bridge_locations_but_keeps_trusted_route(
    monkeypatch,
):
    target_path = "/target/private/profile/screenshots/capture.png"
    server_path = "/workspaces/private/screenshots/capture.png"
    bridge_url = "https://bridge-user:bridge-secret@bridge.invalid/private.png"
    bridge_location = "file:///target/private/location.png"
    selected = _Bridge(
        "kodi19",
        [PNG_A],
        extra={
            "target_path": target_path,
            "server_path": server_path,
            "url": bridge_url,
            "location": bridge_location,
            "width": 2,
            "height": 1,
            "format": "png",
        },
    )
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    call_result = await _call(server, {"target": "kodi19"})
    result = _envelope(call_result)
    rendered = json.dumps(result, sort_keys=True)

    assert result["data"]["path"] == "[redacted]"
    assert result["data"]["target_path"] == "[redacted]"
    assert result["data"]["server_path"] == "[redacted]"
    assert result["data"]["url"] == "[redacted]"
    assert result["data"]["location"] == "[redacted]"
    assert result["data"]["server_screenshot"]["path"] == "[redacted]"
    assert result["data"]["server_screenshot"]["url"] == (
        "https://mcp.example.test/screenshots/1700000000001-aaaaaaaaaaaa.png"
    )
    assert result["data"]["width"] == 2
    assert result["data"]["height"] == 1
    assert result["data"]["format"] == "png"
    for secret in (target_path, server_path, bridge_url, bridge_location, "bridge-secret"):
        assert secret not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("include_image", [False, True])
async def test_image_content_contract_has_no_json_base64_duplication(
    include_image, monkeypatch
):
    selected = _Bridge("kodi19", [PNG_A])
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    call_result = await _call(
        server, {"target": "kodi19", "include_image": include_image}
    )
    result = _envelope(call_result)
    encoded = base64.b64encode(PNG_A).decode("ascii")

    assert "image_base64" not in json.dumps(result)
    assert encoded not in call_result.content[0].text
    assert [item.type for item in call_result.content] == (
        ["text", "image"] if include_image else ["text"]
    )
    if include_image:
        assert call_result.content[1].data == encoded
        assert call_result.content[1].mime_type == "image/png"
    assert result["raw"]["target_id"] == "kodi19"


@pytest.mark.asyncio
async def test_explicit_finalization_does_not_mutate_bridge_gui_store_or_arguments(monkeypatch):
    capture = ResponseMessage(
        request_id="capture-original",
        result={
            "ok": True,
            "path": "/target/original/black.png",
            "content_type": "image/png",
            "size_bytes": len(BLACK_PNG),
            "image_base64": base64.b64encode(BLACK_PNG).decode("ascii"),
        },
        error=None,
    )
    valid = ResponseMessage(
        request_id="capture-valid",
        result={
            "ok": True,
            "path": "/target/original/valid.png",
            "content_type": "image/png",
            "size_bytes": len(PNG_A),
            "image_base64": base64.b64encode(PNG_A).decode("ascii"),
        },
        error=None,
    )
    gui = ResponseMessage(
        request_id="gui-original",
        result={
            "ok": True,
            "current_window_id": 10000,
            "current_dialog_id": 9999,
            "conditions": {
                "fullscreen_video": False,
                "player_has_media": False,
                "player_has_video": False,
                "player_playing": False,
                "player_paused": False,
            },
            "active_players": [],
        },
        error=None,
    )
    selected = _Bridge("kodi19", [capture, valid], gui_states=[gui])
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    store_result = {
        "screenshot_id": "1700000000004-dddddddddddd",
        "filename": "1700000000004-dddddddddddd.png",
        "path": "/workspaces/internal/1700000000004-dddddddddddd.png",
        "url": "https://mcp.example.test/screenshots/1700000000004-dddddddddddd.png",
        "content_type": "image/png",
        "format": "png",
        "size_bytes": len(PNG_A),
        "width": 2,
        "height": 1,
        "sha256": "original",
    }
    store_calls = []

    def store(image_base64):
        store_calls.append(image_base64)
        return store_result

    originals = deepcopy((capture, valid, gui, store_result))
    arguments = {"target": "kodi19", "include_image": True, "store": True}
    arguments_original = deepcopy(arguments)
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, arguments))

    assert result["ok"] is True
    assert selected.capture_calls == [True, True]
    assert selected.state_calls == 1
    assert (capture, valid, gui, store_result) == originals
    assert arguments == arguments_original
    assert len(store_calls) == 1


@pytest.mark.asyncio
async def test_selected_bridge_unavailable_fails_without_default_or_storage(monkeypatch):
    default = _Bridge("default", tripwire=True)
    runtime, registry, pool, _, _ = _runtime(
        default_bridge=default,
        target_bridges={"kodi19": None, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "kodi19"}))

    assert result["ok"] is False
    assert result["raw"]["target_id"] == "kodi19"
    assert default.capture_calls == []
    assert store.calls == []
    assert registry.calls == ["kodi19"]
    assert [target.target_id for target in pool.targets] == ["kodi19"]


@pytest.mark.asyncio
async def test_invalid_png_failure_is_target_attributed_and_not_stored(monkeypatch):
    invalid = ResponseMessage(
        request_id="invalid-png",
        result={
            "ok": True,
            "path": "/target/private/not-a-png.png",
            "content_type": "image/png",
            "size_bytes": 9,
            "image_base64": base64.b64encode(b"not a png").decode("ascii"),
        },
        error=None,
    )
    selected = _Bridge("kodi19", [invalid])
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "kodi19"}))

    assert result["ok"] is False
    assert result["raw"]["target_id"] == "kodi19"
    assert store.calls == []
    assert base64.b64encode(b"not a png").decode("ascii") not in json.dumps(result)


@pytest.mark.asyncio
async def test_black_retry_exhaustion_remains_selected_target_only_and_unstored(monkeypatch):
    selected = _Bridge("kodi19", [BLACK_PNG])
    runtime, registry, pool, default, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "kodi19"}))

    assert result["error_type"] == "screenshot_not_ready"
    assert result["raw"]["target_id"] == "kodi19"
    assert len(selected.capture_calls) == 5
    assert selected.state_calls == 5
    assert default.capture_calls == []
    assert registry.calls == ["kodi19"]
    assert [target.target_id for target in pool.targets] == ["kodi19"]
    assert store.calls == []


@pytest.mark.asyncio
async def test_gui_state_failure_stays_target_attributed_and_redacted(monkeypatch):
    gui_failure = _Bridge(
        "kodi19", [BLACK_PNG], gui_states=[RuntimeError("gui failed at /target/private/gui")]
    )
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": gui_failure, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", _Store())
    server, _ = build_mcp_server(runtime)
    result = _envelope(await _call(server, {"target": "kodi19"}))


    assert result["raw"]["target_id"] == "kodi19"
    assert "/target/private/gui" not in json.dumps(result)


@pytest.mark.asyncio
async def test_storage_failure_stays_target_attributed_and_redacted(monkeypatch):
    storage_failure = _Bridge("kodi19", [PNG_A])
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": storage_failure, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    monkeypatch.setattr(
        server_core,
        "store_screenshot_from_base64",
        _Store(error=RuntimeError("write failed at /workspaces/private/screenshots")),
    )
    server, _ = build_mcp_server(runtime)
    result = _envelope(await _call(server, {"target": "kodi19"}))

    assert result["raw"]["target_id"] == "kodi19"
    assert "/workspaces/private/screenshots" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quoted_path",
    [
        "'/workspaces/private/capture.png'",
        '"/workspaces/private/capture.png"',
        r"'\\server\share\capture.png'",
        r'"\\server\share\capture.png"',
    ],
    ids=[
        "single-quoted-posix",
        "double-quoted-posix",
        "single-quoted-unc",
        "double-quoted-unc",
    ],
)
async def test_quoted_filesystem_paths_are_redacted_from_data_and_raw_without_mutation(
    quoted_path, monkeypatch
):
    diagnostic = f"request failed: [Errno 2] No such file: {quoted_path}"
    response = ResponseMessage(
        request_id="quoted-path",
        result={
            "content_type": "image/png",
            "size_bytes": 0,
            "diagnostic": diagnostic,
        },
        error=diagnostic,
        error_type=ErrorType.SERVER_ERROR,
    )
    original = deepcopy(response)
    selected = _Bridge("kodi19", [response])
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    call_result = await _call(server, {"target": "kodi19"})
    result = _envelope(call_result)

    assert result["data"]["diagnostic"] == "[redacted]"
    assert result["raw"]["result"]["diagnostic"] == "[redacted]"
    assert result["error"] == "[redacted]"
    assert response == original
    assert store.calls == []


@pytest.mark.asyncio
async def test_quoted_relative_slash_text_is_not_mistaken_for_filesystem_provenance(
    monkeypatch,
):
    ordinary_text = "operator note: 'docs/api/v1' is a logical label"
    response = ResponseMessage(
        request_id="ordinary-slashes",
        result={
            "content_type": "image/png",
            "size_bytes": 0,
            "diagnostic": ordinary_text,
        },
        error=None,
    )
    original = deepcopy(response)
    selected = _Bridge("kodi19", [response])
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", _Store())
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "kodi19"}))

    assert result["data"]["diagnostic"] == ordinary_text
    assert result["raw"]["result"]["diagnostic"] == ordinary_text
    assert response == original


@pytest.mark.asyncio
async def test_bridge_capture_failure_stays_target_attributed_and_unstored(monkeypatch):
    failure = ResponseMessage(
        request_id="capture-failed",
        result=None,
        error="capture failed at /target/private/screenshots",
        error_type=ErrorType.NETWORK_ERROR,
    )
    selected = _Bridge("kodi19", [failure])
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    store = _Store()
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "kodi19"}))

    assert result["error_type"] == "network_error"
    assert result["raw"]["target_id"] == "kodi19"
    assert "/target/private/screenshots" not in json.dumps(result)
    assert store.calls == []


@pytest.mark.asyncio
async def test_credentialed_store_url_is_not_treated_as_a_trusted_server_route(monkeypatch):
    selected = _Bridge("kodi19", [PNG_A])
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    stored = {
        "filename": "1700000000005-eeeeeeeeeeee.png",
        "path": "/workspaces/private/1700000000005-eeeeeeeeeeee.png",
        "url": "https://server-user:server-secret@mcp.example.test/screenshots/1700000000005-eeeeeeeeeeee.png",
        "size_bytes": len(PNG_A),
    }
    original = deepcopy(stored)

    def store(_image_base64):
        return stored

    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "kodi19"}))

    assert result["data"]["server_screenshot"]["url"] == "[redacted]"
    assert result["data"]["server_screenshot"]["path"] == "[redacted]"
    assert "server-secret" not in json.dumps(result)
    assert stored == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "route"),
    [
        ("%2e%2e%2fsecret.png", "/screenshots/%2e%2e%2fsecret.png"),
        ("dir%5csecret.png", "/screenshots/dir%5csecret.png"),
    ],
)
async def test_untrusted_encoded_store_filename_is_absent_from_explicit_output(
    filename, route, monkeypatch
):
    selected = _Bridge("kodi19", [PNG_A])
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    stored = {
        "filename": filename,
        "path": f"/workspaces/private/{filename}",
        "url": route,
        "size_bytes": len(PNG_A),
    }
    original = deepcopy(stored)

    def store(_image_base64):
        return stored

    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    call_result = await _call(server, {"target": "kodi19"})
    result = _envelope(call_result)
    rendered_structured = json.dumps(call_result.structured_content, sort_keys=True)

    assert result["data"]["server_screenshot"]["filename"] == "[redacted]"
    assert result["data"]["server_screenshot"]["url"] == "[redacted]"
    assert filename not in call_result.content[0].text
    assert filename not in rendered_structured
    assert stored == original


@pytest.mark.parametrize(
    ("filename", "route"),
    [
        ("%2e%2e%2fsecret.png", "/screenshots/%2e%2e%2fsecret.png"),
        ("dir%5csecret.png", "/screenshots/dir%5csecret.png"),
        ("..%2fsecret.png", "/screenshots/..%2fsecret.png"),
        ("%2E%2E", "/screenshots/%2E%2E/secret.png"),
        ("foo%2fbar.png", "/screenshots/foo%2fbar.png"),
        ("../secret.png", "/screenshots/../secret.png"),
        (r"dir\secret.png", r"/screenshots/dir\secret.png"),
        ("dir/secret.png", "/screenshots/dir/secret.png"),
        (
            "1700000000006-ffffffffffff.png",
            "/screenshots/1700000000007-ffffffffffff.png",
        ),
        (
            "1700000000006-ffffffffffff.png",
            "/screenshots/1700000000006-ffffffffffff.png?download=1",
        ),
        (
            "1700000000006-ffffffffffff.png",
            "/screenshots/1700000000006-ffffffffffff.png#fragment",
        ),
        (
            "1700000000006-ffffffffffff.png",
            "https://user:secret@mcp.example.test/screenshots/1700000000006-ffffffffffff.png",
        ),
    ],
)
def test_trusted_server_route_rejects_encoded_traversal_and_ambiguous_filenames(
    filename, route
):
    metadata = {"filename": filename, "url": route}
    original = deepcopy(metadata)

    assert _trusted_server_screenshot_route(metadata) is None
    assert metadata == original


@pytest.mark.parametrize(
    "route",
    [
        "/screenshots/1700000000006-ffffffffffff.png",
        "https://mcp.example.test/screenshots/1700000000006-ffffffffffff.png",
    ],
)
def test_trusted_server_route_accepts_real_generated_filename_grammar(route):
    metadata = {
        "filename": "1700000000006-ffffffffffff.png",
        "url": route,
    }
    original = deepcopy(metadata)

    assert _trusted_server_screenshot_route(metadata) == route
    assert metadata == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoded_token",
    ["%2e%2e%2f", "%2E%2E%2F", "%2f", "%2F", "%5c", "%5C"],
)
async def test_encoded_path_tokens_in_diagnostics_are_redacted_case_insensitively(
    encoded_token, monkeypatch
):
    diagnostic = f"capture rejected filename {encoded_token}secret.png"
    response = ResponseMessage(
        request_id="encoded-diagnostic",
        result={
            "content_type": "image/png",
            "size_bytes": 0,
            "diagnostic": diagnostic,
        },
        error=None,
    )
    original = deepcopy(response)
    selected = _Bridge("kodi19", [response])
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", _Store())
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "kodi19"}))

    assert result["data"]["diagnostic"] == "[redacted]"
    assert result["raw"]["result"]["diagnostic"] == "[redacted]"
    assert response == original


@pytest.mark.asyncio
async def test_encoded_diagnostics_are_removed_everywhere_but_safe_text_and_trusted_route_survive(
    monkeypatch,
):
    malicious = [
        "capture rejected filename %2e%2e%2fsecret.png",
        "capture rejected filename dir%5csecret.png",
        "capture rejected filename foo%2fbar.png",
        "capture rejected filename %2E%2E%5Csecret.png",
    ]
    safe_relative = "operator note: 'docs/api/v1' is a logical label"
    safe_percent = ["progress=50%25", "label%20with%20spaces"]
    failure = ResponseMessage(
        request_id="encoded-failure",
        result={
            "content_type": "image/png",
            "size_bytes": 0,
            "diagnostic": malicious[1],
            "nested": {
                "separator": malicious[2],
                "metadata": {"mixed_case": malicious[3]},
                "safe_relative": safe_relative,
                "safe_percent": safe_percent,
            },
        },
        error=malicious[0],
        error_type=ErrorType.SERVER_ERROR,
    )
    success = ResponseMessage(
        request_id="encoded-success",
        result={
            "ok": True,
            "path": "/target/private/capture.png",
            "content_type": "image/png",
            "size_bytes": len(PNG_A),
            "image_base64": base64.b64encode(PNG_A).decode("ascii"),
            "diagnostic": malicious[0],
            "nested": {
                "separator": malicious[1],
                "metadata": {
                    "encoded_slash": malicious[2],
                    "mixed_case": malicious[3],
                },
                "safe_relative": safe_relative,
                "safe_percent": safe_percent,
            },
        },
        error=None,
    )
    selected = _Bridge("kodi19", [failure, success])
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    trusted_store = {
        "filename": "1700000000006-ffffffffffff.png",
        "path": "/workspaces/internal/1700000000006-ffffffffffff.png",
        "url": "/screenshots/1700000000006-ffffffffffff.png",
        "size_bytes": len(PNG_A),
    }
    originals = deepcopy((failure, success, trusted_store))
    store_calls = []

    def store(image_base64):
        store_calls.append(image_base64)
        return trusted_store

    arguments = {"target": "kodi19"}
    arguments_original = deepcopy(arguments)
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", store)
    server, _ = build_mcp_server(runtime)

    failure_call = await _call(server, arguments)
    success_call = await _call(server, arguments)
    failure_result = _envelope(failure_call)
    success_result = _envelope(success_call)

    for call_result in (failure_call, success_call):
        rendered_structured = json.dumps(call_result.structured_content, sort_keys=True)
        for value in malicious:
            assert value not in call_result.content[0].text
            assert value not in rendered_structured
    assert failure_result["error"] == "[redacted]"
    assert failure_result["data"]["diagnostic"] == "[redacted]"
    assert failure_result["data"]["nested"]["separator"] == "[redacted]"
    assert failure_result["raw"]["result"]["nested"]["metadata"]["mixed_case"] == "[redacted]"
    assert success_result["data"]["diagnostic"] == "[redacted]"
    assert success_result["data"]["nested"]["separator"] == "[redacted]"
    assert success_result["raw"]["result"]["nested"]["metadata"] == {
        "encoded_slash": "[redacted]",
        "mixed_case": "[redacted]",
    }
    for result in (failure_result, success_result):
        assert result["data"]["nested"]["safe_relative"] == safe_relative
        assert result["data"]["nested"]["safe_percent"] == safe_percent
    assert success_result["data"]["server_screenshot"]["url"] == (
        "/screenshots/1700000000006-ffffffffffff.png"
    )
    assert (failure, success, trusted_store) == originals
    assert arguments == arguments_original
    assert len(store_calls) == 1


@pytest.mark.asyncio
async def test_inline_oversize_failure_preserves_contract_without_image_or_base64(
    monkeypatch,
):
    selected = _Bridge("kodi19", [PNG_A])
    runtime, _, _, _, _ = _runtime(
        default_bridge=_Bridge("default", tripwire=True),
        target_bridges={"kodi19": selected, "kodi22": _Bridge("kodi22", [PNG_B])},
    )
    monkeypatch.setattr(server_core, "INLINE_SCREENSHOT_MAX_RAW_BYTES", 1)
    monkeypatch.setattr(server_core, "store_screenshot_from_base64", _Store())
    server, _ = build_mcp_server(runtime)

    call_result = await _call(
        server,
        {"target": "kodi19", "include_image": True, "store": False},
    )
    result = _envelope(call_result)

    assert result["error_type"] == "payload_too_large"
    assert result["raw"]["target_id"] == "kodi19"
    assert [item.type for item in call_result.content] == ["text"]
    assert "image_base64" not in json.dumps(result)
    assert base64.b64encode(PNG_A).decode("ascii") not in call_result.content[0].text
