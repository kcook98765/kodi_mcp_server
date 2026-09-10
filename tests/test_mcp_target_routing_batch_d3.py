"""Phase 4A Batch D3 stateless notification-routing tests."""

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


D3_TOOL_NAMES = frozenset({"kodi_notifications_sample"})
TARGET_PROPERTY = {
    "type": "string",
    "pattern": r"^[a-z0-9](?:[a-z0-9._-]{0,63})$",
}
STILL_UNTARGETED = frozenset(
    {
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


class _Notification:
    def __init__(
        self,
        label: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        error_type: ErrorType | None = None,
        tripwire_legacy: bool = False,
        tripwire_strict: bool = False,
    ) -> None:
        self.label = label
        self.result = result or {
            "connected": True,
            "websocket_url": f"ws://{label}.example.invalid:9090/jsonrpc",
            "messages": [{"method": "Player.OnPlay", "label": label}],
            "message_count": 1,
            "listen_seconds": 5,
        }
        self.error = error
        self.error_type = error_type
        self.tripwire_legacy = tripwire_legacy
        self.tripwire_strict = tripwire_strict
        self.legacy_calls: list[dict[str, int]] = []
        self.strict_calls: list[dict[str, int]] = []

    def _response(self) -> ResponseMessage:
        return ResponseMessage(
            request_id=f"notifications-{self.label}",
            result=self.result,
            error=self.error,
            error_type=self.error_type,
        )

    async def listen(self, *, sample_size: int, listen_seconds: int):
        if self.tripwire_legacy:
            raise AssertionError(f"explicit call used default/legacy listener: {self.label}")
        self.legacy_calls.append(
            {"sample_size": sample_size, "listen_seconds": listen_seconds}
        )
        await asyncio.sleep(0)
        return self._response()

    async def listen_target_bound(self, *, sample_size: int, listen_seconds: int):
        if self.tripwire_strict:
            raise AssertionError(f"legacy call used strict listener: {self.label}")
        self.strict_calls.append(
            {"sample_size": sample_size, "listen_seconds": listen_seconds}
        )
        await asyncio.sleep(0)
        return self._response()


def _target(target_id: str) -> dict[str, Any]:
    return {
        "id": target_id,
        "name": f"Target {target_id}",
        "endpoints": {
            "jsonrpc_url": f"https://{target_id}.routing-secret.invalid/jsonrpc",
            "bridge_url": f"https://{target_id}.routing-secret.invalid/bridge",
            "websocket_url": f"ws://{target_id}.routing-secret.invalid:9090/jsonrpc",
        },
    }


def _runtime(
    *,
    default_notification: Any | None = None,
    target_notifications: dict[str, Any | None] | None = None,
):
    source_registry = TargetRegistry.from_sources(
        legacy=LegacyTargetSettings(
            jsonrpc_url="http://default.invalid/jsonrpc",
            bridge_url="http://default.invalid/bridge",
        ),
        targets_json=json.dumps([_target("kodi19"), _target("kodi22")]),
    )
    registry = _CountingRegistry(source_registry)
    default_notification = default_notification or _Notification("default")
    target_notifications = target_notifications or {
        "kodi19": _Notification("kodi19"),
        "kodi22": _Notification("kodi22"),
    }
    bundles = {
        target_id: _Bundle(object(), object(), notification)
        for target_id, notification in target_notifications.items()
    }
    pool = _Pool(bundles)
    runtime = {
        "registry": registry,
        "transport_pool": pool,
        "jsonrpc": object(),
        "bridge": object(),
        "notifications": default_notification,
    }
    return runtime, registry, pool, default_notification, bundles


async def _call(server, arguments: dict[str, Any]):
    return await server.get_request_handler("tools/call").handler(
        None,
        CallToolRequestParams(name="kodi_notifications_sample", arguments=arguments),
    )


def _envelope(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_exact_d3_inventory_and_canonical_schema():
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
    assert getattr(tool_contract, "BATCH_D3_TARGET_TOOL_NAMES", None) == D3_TOOL_NAMES
    assert tool_contract.EXPLICIT_TARGET_TOOL_NAMES == (
        tool_contract.BATCH_A_TARGET_TOOL_NAMES
        | tool_contract.BATCH_B_TARGET_TOOL_NAMES
        | tool_contract.BATCH_C1_TARGET_TOOL_NAMES
        | tool_contract.BATCH_C2_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D1_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D2_TARGET_TOOL_NAMES
        | D3_TOOL_NAMES
        | tool_contract.BATCH_D4_TARGET_TOOL_NAMES
        | tool_contract.BATCH_D5_TARGET_TOOL_NAMES
    )
    assert targeted == tool_contract.EXPLICIT_TARGET_TOOL_NAMES
    assert len(targeted) == 36
    assert by_name["kodi_notifications_sample"].input_schema == {
        "type": "object",
        "properties": {
            "sample_size": {
                "type": "integer",
                "description": "Number of notifications to capture before returning.",
                "minimum": 1,
                "default": 3,
            },
            "listen_seconds": {
                "type": "integer",
                "description": "Maximum time to listen before returning.",
                "minimum": 1,
                "default": 5,
            },
            "target": TARGET_PROPERTY,
        },
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
async def test_malformed_target_fails_before_registry_pool_default_or_socket_access(target):
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
    arguments = {"target": target_url}
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
    assert result["raw"]["arguments"] == {"target": "[redacted]"}
    for rendered in (call_result.content[0].text, structured):
        assert target_url not in rendered
        assert "user:secret" not in rendered
        assert "secret" not in rendered
    assert arguments == original


@pytest.mark.asyncio
async def test_unknown_target_is_not_found_without_pool_or_default_fallback():
    default = _Notification("default", tripwire_legacy=True)
    runtime, registry, pool, _, bundles = _runtime(default_notification=default)
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "missing"}))

    assert result["error_type"] == "not_found"
    assert result["error_code"] == 404
    assert registry.calls == ["missing"]
    assert pool.targets == []
    assert default.legacy_calls == default.strict_calls == []
    assert all(
        bundle.notifications.legacy_calls == bundle.notifications.strict_calls == []
        for bundle in bundles.values()
    )


@pytest.mark.asyncio
async def test_explicit_a_b_then_default_use_strict_target_bound_and_exact_legacy_paths():
    default = _Notification("default", tripwire_strict=True)
    target_notifications = {
        "kodi19": _Notification("kodi19", tripwire_legacy=True),
        "kodi22": _Notification("kodi22", tripwire_legacy=True),
    }
    runtime, registry, pool, _, bundles = _runtime(
        default_notification=default,
        target_notifications=target_notifications,
    )
    server, _ = build_mcp_server(runtime)

    calls = [
        await _call(server, {"target": "kodi19", "sample_size": 1, "listen_seconds": 7}),
        await _call(server, {"target": "kodi22", "sample_size": 2, "listen_seconds": 8}),
        await _call(server, {"sample_size": 3, "listen_seconds": 9}),
    ]
    results = [_envelope(call) for call in calls]

    assert [result["data"]["messages"][0]["label"] for result in results] == [
        "kodi19",
        "kodi22",
        "default",
    ]
    assert [result["raw"].get("target_id") for result in results] == [
        "kodi19",
        "kodi22",
        None,
    ]
    assert bundles["kodi19"].notifications.strict_calls == [
        {"sample_size": 1, "listen_seconds": 7}
    ]
    assert bundles["kodi22"].notifications.strict_calls == [
        {"sample_size": 2, "listen_seconds": 8}
    ]
    assert all(
        bundle.notifications.legacy_calls == [] for bundle in bundles.values()
    )
    assert default.legacy_calls == [{"sample_size": 3, "listen_seconds": 9}]
    assert default.strict_calls == []
    assert registry.calls == ["kodi19", "kodi22"]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi22"]


@pytest.mark.asyncio
async def test_concurrent_a_b_calls_keep_notification_objects_and_events_isolated():
    default = _Notification("default", tripwire_legacy=True, tripwire_strict=True)
    runtime, _, pool, _, bundles = _runtime(default_notification=default)
    server, _ = build_mcp_server(runtime)

    first, second = await asyncio.gather(
        _call(server, {"target": "kodi19", "sample_size": 1, "listen_seconds": 4}),
        _call(server, {"target": "kodi22", "sample_size": 1, "listen_seconds": 6}),
    )
    results = [_envelope(first), _envelope(second)]

    assert [result["data"]["messages"][0]["label"] for result in results] == [
        "kodi19",
        "kodi22",
    ]
    assert [result["raw"]["target_id"] for result in results] == ["kodi19", "kodi22"]
    assert bundles["kodi19"].notifications.strict_calls == [
        {"sample_size": 1, "listen_seconds": 4}
    ]
    assert bundles["kodi22"].notifications.strict_calls == [
        {"sample_size": 1, "listen_seconds": 6}
    ]
    assert {target.target_id for target in pool.targets} == {"kodi19", "kodi22"}
    assert default.legacy_calls == default.strict_calls == []


@pytest.mark.asyncio
async def test_selected_notification_transport_unavailable_does_not_fallback_to_default():
    default = _Notification("default", tripwire_legacy=True, tripwire_strict=True)
    runtime, registry, pool, _, _ = _runtime(
        default_notification=default,
        target_notifications={"kodi19": None, "kodi22": _Notification("kodi22")},
    )
    server, _ = build_mcp_server(runtime)

    result = _envelope(await _call(server, {"target": "kodi19"}))

    assert result["ok"] is False
    assert result["raw"]["target_id"] == "kodi19"
    assert "notifications probe unavailable" in result["error"]
    assert registry.calls == ["kodi19"]
    assert [target.target_id for target in pool.targets] == ["kodi19"]
    assert default.legacy_calls == default.strict_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "error", "error_type", "expected_ok"),
    [
        (
            {
                "connected": True,
                "websocket_url": "ws://silent.invalid:9090/jsonrpc",
                "messages": [],
                "message_count": 0,
                "listen_seconds": 11,
            },
            None,
            None,
            True,
        ),
        (
            {
                "connected": False,
                "websocket_url": "ws://refused.invalid:9090/jsonrpc",
                "messages": [],
                "message_count": 0,
                "listen_seconds": 11,
                "diagnostic_code": "connection_refused",
            },
            "connection refused",
            ErrorType.NETWORK_ERROR,
            False,
        ),
    ],
    ids=["silent-empty-success", "connection-failure"],
)
async def test_observation_arguments_and_existing_success_failure_semantics_are_preserved(
    result, error, error_type, expected_ok
):
    notification = _Notification(
        "kodi19", result=result, error=error, error_type=error_type
    )
    runtime, _, _, _, bundles = _runtime(
        target_notifications={"kodi19": notification, "kodi22": _Notification("kodi22")}
    )
    server, _ = build_mcp_server(runtime)

    envelope = _envelope(
        await _call(
            server,
            {"target": "kodi19", "sample_size": 7, "listen_seconds": 11},
        )
    )

    assert envelope["ok"] is expected_ok
    assert envelope["data"]["message_count"] == 0
    assert envelope["data"]["listen_seconds"] == 11
    assert bundles["kodi19"].notifications.strict_calls == [
        {"sample_size": 7, "listen_seconds": 11}
    ]
    if error_type is not None:
        assert envelope["error_type"] == error_type.value
        assert envelope["data"]["diagnostic_code"] == "connection_refused"


@pytest.mark.asyncio
async def test_explicit_output_redacts_transport_but_preserves_sanitized_event_provenance():
    response_result = {
        "connected": True,
        "websocket_url": "ws://user:transport-secret@kodi19.routing-secret.invalid:9090/jsonrpc?token=transport-token",
        "endpoint": "kodi19.routing-secret.invalid",
        "host": "kodi19.routing-secret.invalid",
        "port": 9090,
        "proxy": "http://user:secret@proxy.example:8080/path?token=abc",
        "transport": {
            "redirect": {
                "location": "https://name:password@target.example/private?api_key=xyz"
            },
            "diagnostics": {
                "proxy": {
                    "url": "http://nested-user:nested-secret@proxy.example/path"
                }
            },
        },
        "messages": [
            {
                "method": "Player.OnPlay",
                "sender": "xbmc",
                "host": "media-box.local",
                "hostname": "kodi-living-room",
                "port": 9090,
                "url": "plugin://plugin.video.example/?action=play&token=event-token#event-fragment",
                "uri": "library://video/movies/titles.xml/7",
                "path": "/media/Movies/Example.mkv",
                "file": "smb://media.example/Movies/Example.mkv",
                "endpoint": "library://video/movies/titles.xml/",
                "params": {
                    "addon": "plugin.video.example",
                    "password": "event-password",
                    "clientSecret": "event-client-secret",
                },
            },
            {
                "method": "Example.Event",
                "params": {
                    "proxy": "proxy-character-name",
                    "location": "Seattle",
                    "host": "media-box.local",
                    "port": 8080,
                    "url": "plugin://example/item/1",
                },
            },
            {
                "method": "Example.CredentialEvent",
                "params": {
                    "proxy": (
                        "http://event-user:event-secret@event-proxy.example/path"
                        "?token=event-proxy-token#private"
                    ),
                    "location": "Seattle",
                },
            },
        ],
        "message_count": 3,
        "listen_seconds": 5,
    }
    original = deepcopy(response_result)
    notification = _Notification("kodi19", result=response_result)
    runtime, _, _, _, _ = _runtime(
        target_notifications={"kodi19": notification, "kodi22": _Notification("kodi22")}
    )
    server, _ = build_mcp_server(runtime)

    call_result = await _call(server, {"target": "kodi19"})
    envelope = _envelope(call_result)
    event = envelope["data"]["messages"][0]
    rendered = call_result.content[0].text
    structured = json.dumps(call_result.structured_content, sort_keys=True)

    assert call_result.structured_content == envelope
    assert envelope["raw"]["target_id"] == "kodi19"
    assert envelope["raw"]["target_name"] == "Target kodi19"
    for key in ("websocket_url", "endpoint", "host", "port"):
        assert envelope["data"][key] == "[redacted]"
    assert envelope["data"]["proxy"] == "[redacted]"
    assert envelope["data"]["transport"] == {
        "redirect": {"location": "[redacted]"},
        "diagnostics": {"proxy": {"url": "[redacted]"}},
    }
    assert envelope["raw"]["result"]["proxy"] == "[redacted]"
    assert envelope["raw"]["result"]["transport"] == envelope["data"]["transport"]
    assert event == {
        "method": "Player.OnPlay",
        "sender": "xbmc",
        "host": "media-box.local",
        "hostname": "kodi-living-room",
        "port": 9090,
        "url": "plugin://plugin.video.example/?action=play&token=%5Bredacted%5D",
        "uri": "library://video/movies/titles.xml/7",
        "path": "/media/Movies/Example.mkv",
        "file": "smb://media.example/Movies/Example.mkv",
        "endpoint": "library://video/movies/titles.xml/",
        "params": {
            "addon": "plugin.video.example",
            "password": "[redacted]",
            "clientSecret": "[redacted]",
        },
    }
    assert envelope["raw"]["result"]["messages"] == envelope["data"]["messages"]
    assert envelope["data"]["messages"][1] == {
        "method": "Example.Event",
        "params": {
            "proxy": "proxy-character-name",
            "location": "Seattle",
            "host": "media-box.local",
            "port": 8080,
            "url": "plugin://example/item/1",
        },
    }
    assert envelope["data"]["messages"][2] == {
        "method": "Example.CredentialEvent",
        "params": {
            "proxy": (
                "http://event-proxy.example/path?token=%5Bredacted%5D"
            ),
            "location": "Seattle",
        },
    }
    for secret in (
        "transport-secret",
        "transport-token",
        "routing-secret.invalid",
        "event-token",
        "event-fragment",
        "event-password",
        "event-client-secret",
        "user:secret",
        "token=abc",
        "name:password",
        "api_key=xyz",
        "nested-user:nested-secret",
        "event-user:event-secret",
        "event-proxy-token",
        "#private",
    ):
        for representation in (rendered, structured):
            assert secret not in representation
    assert all(
        "notification-transport-mask" not in representation
        for representation in (rendered, structured)
    )
    assert response_result == original
