"""Phase 3 per-call target routing and health contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from mcp.client import Client
from mcp.types import CallToolRequestParams

from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_server.models.messages import ResponseMessage
from kodi_mcp_server.targets.registry import LegacyTargetSettings, TargetRegistry
from kodi_mcp_server.targets.security import redact_target_sensitive


class _JsonRpc:
    def __init__(self, label: str, *, error: str | None = None) -> None:
        self.label = label
        self.error = error
        self.calls: list[str] = []

    async def get_jsonrpc_version(self):
        self.calls.append("get_jsonrpc_version")
        if self.error:
            return ResponseMessage(
                request_id=f"jsonrpc-{self.label}", result=None, error=self.error
            )
        return ResponseMessage(
            request_id=f"jsonrpc-{self.label}",
            result={"version": {"major": 13, "minor": 0, "patch": 0}},
            error=None,
        )

    async def get_application_properties(self):
        self.calls.append("get_application_properties")
        return ResponseMessage(
            request_id=f"application-{self.label}",
            result={
                "name": f"Kodi {self.label}",
                "version": {
                    "major": {"default": 20, "kodi19": 19, "kodi21": 21}.get(
                        self.label, 22
                    ),
                    "minor": 0,
                    "revision": self.label,
                    "tag": "stable",
                },
            },
            error=None,
        )


class _Bridge:
    def __init__(self, label: str, *, error: str | None = None) -> None:
        self.label = label
        self.error = error
        self.calls: list[str] = []

    async def get_bridge_health(self):
        self.calls.append("get_bridge_health")
        return ResponseMessage(
            request_id=f"bridge-health-{self.label}",
            result=None if self.error else {"status": "ok", "label": self.label},
            error=self.error,
        )

    async def get_bridge_status(self):
        self.calls.append("get_bridge_status")
        return ResponseMessage(
            request_id=f"bridge-status-{self.label}",
            result=None if self.error else {"status": "ok", "label": self.label},
            error=self.error,
        )

    async def gui_state(self):
        self.calls.append("gui_state")
        return ResponseMessage(
            request_id=f"gui-{self.label}",
            result=None
            if self.error
            else {
                "current_window": self.label,
                "current_window_id": 10000,
                "current_dialog_id": 0,
                "conditions": {},
                "active_players": [],
            },
            error=self.error,
        )


class _Notifications:
    def __init__(self, *, connected: bool = True, error: str | None = None) -> None:
        self.connected = connected
        self.error = error
        self.calls: list[tuple[int, int]] = []

    async def listen(self, sample_size: int = 3, listen_seconds: int = 5):
        self.calls.append((sample_size, listen_seconds))
        return ResponseMessage(
            request_id="notifications",
            result={"connected": self.connected},
            error=self.error,
        )


class _RaisingBridge(_Bridge):
    async def get_bridge_status(self):
        raise RuntimeError("https://kodi21.routing-secret.invalid/bridge mcp")


class _IdentityBridge(_Bridge):
    async def get_bridge_status(self):
        self.calls.append("get_bridge_status")
        return ResponseMessage(
            request_id=f"bridge-status-{self.label}",
            result={
                "status": "ok",
                "label": self.label,
                "service": "service.kodi_mcp",
                "addon_id": "service.kodi_mcp",
                "addon_version": "0.2.40",
                "build_identity": "service.kodi_mcp/0.2.40",
                "diagnostic": {
                    "endpoint_url": "https://kodi21.routing-secret.invalid/bridge",
                    "username": "kodi",
                    "password": "status",
                    "bridge_token": "mcp",
                },
            },
            error=None,
        )


@dataclass(frozen=True)
class _Bundle:
    jsonrpc: Any
    bridge: Any
    notifications: Any | None


class _Pool:
    def __init__(self, bundles: dict[str, _Bundle]) -> None:
        self.bundles = bundles
        self.calls: list[str] = []

    def get(self, target_id: str = "default") -> _Bundle:
        self.calls.append(target_id)
        return self.bundles[target_id]


def _target(target_id: str, *, websocket: bool = True) -> dict[str, Any]:
    upper = target_id.upper()
    return {
        "id": target_id,
        "name": f"Target {target_id}",
        "endpoints": {
            "jsonrpc_url": f"https://{target_id}.routing-secret.invalid/jsonrpc",
            "bridge_url": f"https://{target_id}.routing-secret.invalid/bridge",
            "websocket_url": (
                f"wss://{target_id}.routing-secret.invalid/jsonrpc" if websocket else ""
            ),
        },
        "auth": {
            "jsonrpc_username": f"env:{upper}_USERNAME_SECRET_REF",
            "jsonrpc_password": f"env:{upper}_PASSWORD_SECRET_REF",
            "bridge_token": f"env:{upper}_TOKEN_SECRET_REF",
        },
        "expected_kodi_version": target_id.removeprefix("kodi"),
    }


def _runtime(targets: list[dict[str, Any]] | None = None):
    registry = TargetRegistry.from_sources(
        legacy=LegacyTargetSettings(
            jsonrpc_url="http://default-proxy:8080/jsonrpc",
            bridge_url="http://default-proxy:8765",
            websocket_url="ws://default-proxy:9090/jsonrpc",
            tcp_host="default-proxy",
        ),
        targets_json=json.dumps(
            targets if targets is not None else [_target("kodi19"), _target("kodi21")]
        ),
    )
    default = _Bundle(_JsonRpc("default"), _Bridge("default"), _Notifications())
    bundles = {
        "default": default,
        "kodi19": _Bundle(_JsonRpc("kodi19"), _Bridge("kodi19"), _Notifications()),
        "kodi21": _Bundle(_JsonRpc("kodi21"), _Bridge("kodi21"), _Notifications()),
    }
    pool = _Pool(bundles)
    runtime = {
        "registry": registry,
        "transport_pool": pool,
        "jsonrpc": default.jsonrpc,
        "bridge": default.bridge,
        "notifications": default.notifications,
    }
    return runtime, pool, bundles


async def _call(server, name: str, arguments: dict[str, Any]):
    return await server.get_request_handler("tools/call").handler(
        None, CallToolRequestParams(name=name, arguments=arguments)
    )


def _envelope(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_kodi_status_explicit_override_is_call_scoped_and_untargeted_is_legacy_exact():
    runtime, pool, bundles = _runtime()
    compatibility = (
        runtime["jsonrpc"],
        runtime["bridge"],
        runtime["notifications"],
    )
    server, _ = build_mcp_server(runtime)

    before = _envelope(await _call(server, "kodi_status", {}))
    targeted = _envelope(await _call(server, "kodi_status", {"target": "kodi21"}))
    after = _envelope(await _call(server, "kodi_status", {}))

    assert before == after
    assert before["data"]["kodi"]["name"] == "Kodi default"
    assert "target_id" not in before["raw"]
    assert targeted["data"]["kodi"]["name"] == "Kodi kodi21"
    assert targeted["raw"]["target_id"] == "kodi21"
    assert targeted["raw"]["target_name"] == "Target kodi21"
    assert pool.calls == ["kodi21"]
    assert bundles["kodi19"].jsonrpc.calls == []
    assert (
        runtime["jsonrpc"],
        runtime["bridge"],
        runtime["notifications"],
    ) == compatibility


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "data_key"),
    [("bridge_status", "label"), ("kodi_gui_state", "current_window")],
)
async def test_bridge_tools_route_a_then_b_then_default_without_cross_target_leakage(
    tool_name: str, data_key: str
):
    runtime, pool, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    target_a = _envelope(await _call(server, tool_name, {"target": "kodi19"}))
    target_b = _envelope(await _call(server, tool_name, {"target": "kodi21"}))
    default = _envelope(await _call(server, tool_name, {}))

    assert target_a["data"][data_key] == "kodi19"
    assert target_b["data"][data_key] == "kodi21"
    assert default["data"][data_key] == "default"
    assert target_a["raw"]["target_id"] == "kodi19"
    assert target_b["raw"]["target_id"] == "kodi21"
    assert "target_id" not in default["raw"]
    assert pool.calls == ["kodi19", "kodi21"]
    method = "get_bridge_status" if tool_name == "bridge_status" else "gui_state"
    assert bundles["kodi19"].bridge.calls == [method]
    assert bundles["kodi21"].bridge.calls == [method]
    assert bundles["default"].bridge.calls == [method]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["kodi_status", "bridge_status", "kodi_gui_state"])
async def test_explicit_unknown_target_is_typed_not_found_without_default_fallback(
    tool_name: str,
):
    runtime, pool, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    result = await _call(server, tool_name, {"target": "missing"})
    envelope = _envelope(result)

    assert result.is_error is True
    assert envelope["error_type"] == "not_found"
    assert envelope["error_code"] == 404
    assert "missing" in envelope["error"]
    assert pool.calls == []
    assert bundles["default"].jsonrpc.calls == []
    assert bundles["default"].bridge.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_id", ["", "Kodi21", "-kodi", "kodi 21", "x" * 65]
)
async def test_malformed_target_id_is_invalid_params_before_registry_or_transport(
    target_id: str,
):
    runtime, pool, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    result = await _call(server, "kodi_status", {"target": target_id})
    envelope = _envelope(result)

    assert result.is_error is True
    assert envelope["error_type"] == "invalid_params"
    assert pool.calls == []
    assert bundles["default"].jsonrpc.calls == []
    assert bundles["default"].bridge.calls == []


@pytest.mark.asyncio
async def test_target_health_reports_each_configured_channel_and_safe_identity():
    runtime, pool, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    result = await _call(server, "target_health", {"target_id": "kodi21"})
    envelope = _envelope(result)
    data = envelope["data"]

    assert result.is_error is False
    assert data["target_id"] == "kodi21"
    assert data["target_name"] == "Target kodi21"
    assert data["expected_kodi_version"] == "21"
    assert data["overall_status"] == "healthy"
    assert set(data["channels"]) == {"jsonrpc", "bridge", "websocket"}
    for name in ("jsonrpc", "bridge", "websocket"):
        assert data["channels"][name]["configured"] is True
        assert data["channels"][name]["status"] == "healthy"
        assert isinstance(data["channels"][name]["latency_ms"], int)
        assert data["channels"][name]["error"] is None
    assert pool.calls == ["kodi21"]
    assert bundles["kodi21"].jsonrpc.calls == ["get_jsonrpc_version"]
    assert bundles["kodi21"].bridge.calls == ["get_bridge_health"]
    notifications = bundles["kodi21"].notifications
    assert notifications is not None
    assert notifications.calls == [(1, 0)]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_channel", ["jsonrpc", "bridge"])
async def test_target_health_reports_partial_failure_without_leaking_route_or_secret(
    failed_channel: str, monkeypatch: pytest.MonkeyPatch
):
    runtime, _, bundles = _runtime()
    leak = (
        "https://kodi21.routing-secret.invalid/jsonrpc "
        "kodi21.routing-secret.invalid KODI21_PASSWORD_SECRET_REF resolved-password"
    )
    monkeypatch.setenv("KODI21_PASSWORD_SECRET_REF", "resolved-password")
    if failed_channel == "jsonrpc":
        bundles["kodi21"] = _Bundle(
            _JsonRpc("kodi21", error=leak),
            bundles["kodi21"].bridge,
            bundles["kodi21"].notifications,
        )
    else:
        bundles["kodi21"] = _Bundle(
            bundles["kodi21"].jsonrpc,
            _Bridge("kodi21", error=leak),
            bundles["kodi21"].notifications,
        )
    server, _ = build_mcp_server(runtime)

    result = await _call(server, "target_health", {"target_id": "kodi21"})
    rendered = result.content[0].text
    channel = _envelope(result)["data"]["channels"][failed_channel]

    assert result.is_error is False
    assert _envelope(result)["data"]["overall_status"] == "degraded"
    assert channel == {
        "configured": True,
        "status": "unhealthy",
        "latency_ms": channel["latency_ms"],
        "error": {
            "type": "operation_failed",
            "code": None,
            "message": f"{failed_channel} probe failed",
        },
    }
    for forbidden in (
        "https://kodi21.routing-secret.invalid/jsonrpc",
        "kodi21.routing-secret.invalid",
        "KODI21_PASSWORD_SECRET_REF",
        "resolved-password",
    ):
        assert forbidden not in rendered


@pytest.mark.asyncio
async def test_target_health_websocket_failure_is_degraded_and_absence_is_not_configured():
    runtime, _, bundles = _runtime()
    bundles["kodi21"] = _Bundle(
        bundles["kodi21"].jsonrpc,
        bundles["kodi21"].bridge,
        _Notifications(connected=False, error="wss://kodi21.routing-secret.invalid/jsonrpc"),
    )
    server, _ = build_mcp_server(runtime)

    failed = _envelope(
        await _call(server, "target_health", {"target_id": "kodi21"})
    )["data"]

    assert failed["overall_status"] == "degraded"
    assert failed["channels"]["websocket"]["status"] == "unhealthy"

    runtime_absent, _, bundles_absent = _runtime([_target("kodi21", websocket=False)])
    server_absent, _ = build_mcp_server(runtime_absent)
    absent = _envelope(
        await _call(server_absent, "target_health", {"target_id": "kodi21"})
    )["data"]

    assert absent["overall_status"] == "healthy"
    assert absent["channels"]["websocket"] == {
        "configured": False,
        "status": "not_configured",
        "latency_ms": None,
        "error": None,
    }
    notifications = bundles_absent["kodi21"].notifications
    assert notifications is not None
    assert notifications.calls == []


@pytest.mark.asyncio
async def test_target_health_unknown_target_is_typed_not_found_without_transport_fallback():
    runtime, pool, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    result = await _call(server, "target_health", {"target_id": "missing"})
    envelope = _envelope(result)

    assert result.is_error is True
    assert envelope["error_type"] == "not_found"
    assert envelope["error_code"] == 404
    assert pool.calls == []
    assert bundles["default"].jsonrpc.calls == []
    assert bundles["default"].bridge.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["kodi_status", "bridge_status", "kodi_gui_state"])
async def test_explicit_targeted_calls_redact_configured_routes_auth_refs_and_credentials(
    tool_name: str, monkeypatch: pytest.MonkeyPatch
):
    runtime, _, bundles = _runtime()
    monkeypatch.setenv("KODI21_USERNAME_SECRET_REF", "resolved-user")
    monkeypatch.setenv("KODI21_PASSWORD_SECRET_REF", "resolved-password")
    monkeypatch.setenv("KODI21_TOKEN_SECRET_REF", "resolved-token")
    leak = (
        "https://kodi21.routing-secret.invalid/jsonrpc "
        "kodi21.routing-secret.invalid KODI21_TOKEN_SECRET_REF "
        "resolved-user resolved-password resolved-token"
    )
    bundles["kodi21"] = _Bundle(
        _JsonRpc("kodi21", error=leak),
        _Bridge("kodi21", error=leak),
        bundles["kodi21"].notifications,
    )
    server, _ = build_mcp_server(runtime)

    result = await _call(server, tool_name, {"target": "kodi21"})
    rendered = result.content[0].text

    assert '"target_id": "kodi21"' in rendered
    for forbidden in (
        "https://kodi21.routing-secret.invalid/jsonrpc",
        "kodi21.routing-secret.invalid",
        "KODI21_TOKEN_SECRET_REF",
        "resolved-user",
        "resolved-password",
        "resolved-token",
    ):
        assert forbidden not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name", ["kodi_status", "bridge_status", "kodi_gui_state"]
)
async def test_short_credentials_do_not_corrupt_client_structured_output_or_public_identity(
    tool_name: str, monkeypatch: pytest.MonkeyPatch
):
    runtime, _, bundles = _runtime()
    monkeypatch.setenv("KODI21_USERNAME_SECRET_REF", "kodi")
    monkeypatch.setenv("KODI21_PASSWORD_SECRET_REF", "status")
    monkeypatch.setenv("KODI21_TOKEN_SECRET_REF", "mcp")
    bundles["kodi21"] = _Bundle(
        bundles["kodi21"].jsonrpc,
        _IdentityBridge("kodi21"),
        bundles["kodi21"].notifications,
    )
    server, _ = build_mcp_server(runtime)

    async with Client(server, mode="auto") as client:
        result = await client.call_tool(tool_name, {"target": "kodi21"})

    envelope = _envelope(result)
    assert result.is_error is False
    assert result.structured_content is not None
    assert envelope["tool"] == tool_name
    assert envelope["raw"]["target_id"] == "kodi21"
    assert envelope["raw"]["target_name"] == "Target kodi21"
    if tool_name == "kodi_status":
        assert envelope["data"]["kodi"]["name"] == "Kodi kodi21"
        assert envelope["data"]["kodi"]["version"]["revision"] == "kodi21"
    elif tool_name == "bridge_status":
        assert envelope["data"]["service"] == "service.kodi_mcp"
        assert envelope["data"]["addon_id"] == "service.kodi_mcp"
        assert envelope["data"]["addon_version"] == "0.2.40"
        assert envelope["data"]["build_identity"] == "service.kodi_mcp/0.2.40"
        assert set(envelope["data"]["diagnostic"].values()) == {"[redacted]"}
    else:
        assert envelope["data"]["current_window"] == "kodi21"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("jsonrpc_error", "bridge_error"),
    [
        (None, None),
        (None, "bridge unavailable"),
        ("jsonrpc unavailable", None),
    ],
)
async def test_kodi_status_stays_successful_when_meaningful_target_status_remains(
    jsonrpc_error: str | None, bridge_error: str | None
):
    runtime, _, bundles = _runtime()
    bundles["kodi21"] = _Bundle(
        _JsonRpc("kodi21", error=jsonrpc_error),
        _Bridge("kodi21", error=bridge_error),
        bundles["kodi21"].notifications,
    )
    server, _ = build_mcp_server(runtime)

    async with Client(server, mode="auto") as client:
        result = await client.call_tool("kodi_status", {"target": "kodi21"})

    envelope = _envelope(result)
    assert result.is_error is False
    assert result.structured_content is not None
    assert envelope["ok"] is True
    assert envelope["tool"] == "kodi_status"


@pytest.mark.asyncio
async def test_kodi_status_all_connectivity_and_identity_unavailable_is_typed_failure():
    runtime, _, bundles = _runtime()
    bundles["kodi21"] = _Bundle(
        _JsonRpc("kodi21", error="jsonrpc unavailable"),
        _Bridge("kodi21", error="bridge unavailable"),
        bundles["kodi21"].notifications,
    )
    server, _ = build_mcp_server(runtime)

    async with Client(server, mode="auto") as client:
        result = await client.call_tool("kodi_status", {"target": "kodi21"})

    envelope = _envelope(result)
    assert result.is_error is True
    assert result.structured_content is not None
    assert envelope["ok"] is False
    assert envelope["tool"] == "kodi_status"
    assert envelope["error_type"] == "server_error"
    assert envelope["error_code"] == 502
    assert envelope["data"]["jsonrpc"]["status"] == "error"
    assert envelope["data"]["kodi"]["status"] == "error"
    assert envelope["data"]["bridge"]["status"] == "error"


@pytest.mark.asyncio
async def test_untargeted_kodi_status_keeps_legacy_success_when_channels_unavailable():
    runtime, _, bundles = _runtime()
    default_jsonrpc = _JsonRpc("default", error="jsonrpc unavailable")
    default_bridge = _Bridge("default", error="bridge unavailable")
    runtime["jsonrpc"] = default_jsonrpc
    runtime["bridge"] = default_bridge
    bundles["default"] = _Bundle(
        default_jsonrpc,
        default_bridge,
        bundles["default"].notifications,
    )
    server, _ = build_mcp_server(runtime)

    async with Client(server, mode="auto") as client:
        result = await client.call_tool("kodi_status", {})

    envelope = _envelope(result)
    assert result.is_error is False
    assert result.structured_content is not None
    assert envelope["ok"] is True
    assert envelope["tool"] == "kodi_status"
    assert envelope["data"]["jsonrpc"]["status"] == "error"
    assert envelope["data"]["kodi"]["status"] == "error"
    assert envelope["data"]["bridge"]["status"] == "error"
    assert "target_id" not in envelope["raw"]


@pytest.mark.asyncio
async def test_explicit_target_exception_is_redacted_and_keeps_safe_routing_identity(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime, _, bundles = _runtime()
    monkeypatch.setenv("KODI21_TOKEN_SECRET_REF", "mcp")
    bundles["kodi21"] = _Bundle(
        bundles["kodi21"].jsonrpc,
        _RaisingBridge("kodi21"),
        bundles["kodi21"].notifications,
    )
    server, _ = build_mcp_server(runtime)

    async with Client(server, mode="auto") as client:
        result = await client.call_tool("bridge_status", {"target": "kodi21"})
    envelope = _envelope(result)

    assert result.is_error is True
    assert result.structured_content is not None
    assert envelope["tool"] == "bridge_status"
    assert envelope["raw"]["target_id"] == "kodi21"
    assert "kodi21.routing-secret.invalid" not in envelope["error"]
    assert "mcp" not in envelope["error"]


def test_target_redaction_never_rewrites_structural_or_identity_fields():
    runtime, _, _ = _runtime()
    target = runtime["registry"].get("kodi21")
    value = {
        "tool": "kodi_status",
        "target_id": "kodi21",
        "target_name": "Target kodi21",
        "error_type": "server_error",
        "error_code": 502,
        "service": "service.kodi_mcp",
        "addon_id": "service.kodi_mcp",
        "version": "0.2.40+kodi_status",
        "password": "server_error",
        "error": "credential server_error was rejected",
        "channel_error": {
            "type": "server_error",
            "code": "server_error",
            "message": "credential server_error was rejected",
        },
    }

    sanitized = redact_target_sensitive(
        value,
        target,
        environ={
            "KODI21_USERNAME_SECRET_REF": "server_error",
            "KODI21_PASSWORD_SECRET_REF": "unused",
            "KODI21_TOKEN_SECRET_REF": "unused-token",
        },
    )

    assert sanitized["tool"] == "kodi_status"
    assert sanitized["target_id"] == "kodi21"
    assert sanitized["target_name"] == "Target kodi21"
    assert sanitized["error_type"] == "server_error"
    assert sanitized["error_code"] == 502
    assert sanitized["service"] == "service.kodi_mcp"
    assert sanitized["addon_id"] == "service.kodi_mcp"
    assert sanitized["version"] == "0.2.40+kodi_status"
    assert sanitized["password"] == "[redacted]"
    assert sanitized["error"] == "credential [redacted] was rejected"
    assert sanitized["channel_error"] == {
        "type": "server_error",
        "code": "server_error",
        "message": "credential [redacted] was rejected",
    }


@pytest.mark.asyncio
async def test_target_arguments_reject_arbitrary_urls_before_transport_use():
    runtime, pool, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    calls = [
        ("kodi_status", {"target": "http://attacker.invalid"}),
        ("bridge_status", {"target": "http://attacker.invalid"}),
        ("kodi_gui_state", {"target": "http://attacker.invalid"}),
        ("target_health", {"target_id": "http://attacker.invalid"}),
    ]
    for tool_name, arguments in calls:
        result = await _call(server, tool_name, arguments)
        assert result.is_error is True
        assert _envelope(result)["error_type"] == "invalid_params"

    assert pool.calls == []
    assert bundles["default"].jsonrpc.calls == []
    assert bundles["default"].bridge.calls == []
