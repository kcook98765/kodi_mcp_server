"""Phase 4A Batch B stateless mutation-routing tests."""

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
    BATCH_C1_TARGET_TOOL_NAMES,
    BATCH_D1_TARGET_TOOL_NAMES,
)
from kodi_mcp_server.models.messages import ErrorType, ResponseMessage
from kodi_mcp_server.targets.registry import LegacyTargetSettings, TargetRegistry


BATCH_B_TOOL_NAMES = frozenset(
    {
        "kodi_gui_action",
        "kodi_player_open",
        "kodi_player_pause",
        "kodi_player_seek",
        "kodi_player_stop",
    }
)

VALID_ARGUMENTS = {
    "kodi_gui_action": {"action": "home"},
    "kodi_player_open": {"media_type": "movie", "item_id": 7},
    "kodi_player_pause": {"playerid": 1},
    "kodi_player_seek": {"playerid": 1, "seconds": 12.5},
    "kodi_player_stop": {"playerid": 1, "verify": False},
}

EXCLUDED_TOOL_NAMES = frozenset(
    {
        "bridge_write_log_marker",
        "kodi_gui_screenshot",
        "kodi_notifications_sample",
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


class _Bridge:
    def __init__(self, label: str, *, sensitive: bool = False) -> None:
        self.label = label
        self.sensitive = sensitive
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def gui_action(self, action: str):
        self.calls.append(("gui_action", {"action": action}))
        result = {"action": action, "window": "Home", "label": self.label}
        if self.sensitive:
            result.update(
                {
                    "host": f"{self.label}.routing-secret.invalid",
                    "port": 19765,
                    "endpoint_url": f"https://{self.label}.routing-secret.invalid/gui/action",
                    "auth_reference": f"env:{self.label.upper()}_TOKEN",
                }
            )
        return ResponseMessage(
            request_id=f"gui-{self.label}", result=result, error=None
        )


class _JsonRpc:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.active_players: list[dict[str, Any]] = []
        self.sticky_stop_checks = 0

    def _response(self, operation: str, result: Any) -> ResponseMessage:
        return ResponseMessage(
            request_id=f"{operation}-{self.label}", result=result, error=None
        )

    async def execute_input_action(self, action: str):
        self.calls.append(("execute_input_action", {"action": action}))
        return self._response(
            "execute_input_action",
            {"action": action, "method": "Input.ExecuteAction", "label": self.label},
        )

    async def open_library_item(self, media_type: str, item_id: int):
        self.calls.append(
            ("open_library_item", {"media_type": media_type, "item_id": item_id})
        )
        self.active_players = [{"playerid": 1, "label": self.label}]
        return self._response("open_library_item", {"opened": True, "label": self.label})

    async def pause_player(self, playerid: int = 1):
        self.calls.append(("pause_player", {"playerid": playerid}))
        return self._response(
            "pause_player", {"playerid": playerid, "paused": True, "label": self.label}
        )

    async def seek_player_to_seconds(self, playerid: int = 1, seconds: float = 0):
        self.calls.append(
            ("seek_player_to_seconds", {"playerid": playerid, "seconds": seconds})
        )
        return self._response(
            "seek_player_to_seconds", {"position": seconds, "label": self.label}
        )

    async def stop_player(self, playerid: int = 1):
        self.calls.append(("stop_player", {"playerid": playerid}))
        if self.sticky_stop_checks <= 0:
            self.active_players = [
                player
                for player in self.active_players
                if player.get("playerid") != playerid
            ]
        return self._response("stop_player", "OK")

    async def get_active_players(self):
        self.calls.append(("get_active_players", {}))
        players = list(self.active_players)
        if self.sticky_stop_checks > 0:
            self.sticky_stop_checks -= 1
        return self._response("get_active_players", players)


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


async def _call(server, name: str, arguments: dict[str, Any]):
    return await server.get_request_handler("tools/call").handler(
        None, CallToolRequestParams(name=name, arguments=arguments)
    )


def _envelope(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_exact_schema_inventory_through_batch_c2_and_exclusions():
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
        | BATCH_B_TOOL_NAMES
        | BATCH_C1_TARGET_TOOL_NAMES
        | {"addon_execute"}
        | BATCH_D1_TARGET_TOOL_NAMES
    )
    by_name = {tool.name: tool for tool in listed.tools}
    for name in BATCH_B_TOOL_NAMES:
        assert by_name[name].input_schema["properties"]["target"] == {
            "type": "string",
            "pattern": r"^[a-z0-9](?:[a-z0-9._-]{0,63})$",
        }
        assert "target" not in by_name[name].input_schema.get("required", [])
    for name in EXCLUDED_TOOL_NAMES:
        assert "target" not in by_name[name].input_schema["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", sorted(BATCH_B_TOOL_NAMES))
@pytest.mark.parametrize(
    ("target", "error_type", "error_code"),
    [
        ("https://not-a-target.invalid", "invalid_params", None),
        ("missing", "not_found", 404),
    ],
)
async def test_batch_b_target_errors_fail_before_mutation(
    tool_name, target, error_type, error_code
):
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server, tool_name, {**VALID_ARGUMENTS[tool_name], "target": target}
        )
    )

    assert result["ok"] is False
    assert result["error_type"] == error_type
    assert result["error_code"] == error_code
    assert pool.targets == []
    assert default.jsonrpc.calls == []
    assert default.bridge.calls == []
    assert bundles["kodi19"].jsonrpc.calls == []
    assert bundles["kodi20"].bridge.calls == []


@pytest.mark.asyncio
async def test_gui_and_playback_mutations_interleave_a_b_default_without_leakage():
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    results = [
        _envelope(
            await _call(
                server,
                "kodi_gui_action",
                {"action": "home", "target": "kodi19"},
            )
        ),
        _envelope(
            await _call(
                server,
                "kodi_player_open",
                {"media_type": "movie", "item_id": 7, "target": "kodi20"},
            )
        ),
        _envelope(
            await _call(server, "kodi_player_pause", {"playerid": 1})
        ),
    ]

    assert [result["data"]["label"] for result in results] == [
        "kodi19",
        "kodi20",
        "default",
    ]
    assert [result.get("raw", {}).get("target_id") for result in results] == [
        "kodi19",
        "kodi20",
        None,
    ]
    assert [target.target_id for target in pool.targets] == ["kodi19", "kodi20"]
    assert bundles["kodi19"].bridge.calls == [
        ("gui_action", {"action": "home"})
    ]
    assert bundles["kodi20"].jsonrpc.calls == [
        ("open_library_item", {"media_type": "movie", "item_id": 7})
    ]
    assert default.jsonrpc.calls == [("pause_player", {"playerid": 1})]


@pytest.mark.asyncio
async def test_gui_stop_action_uses_target_jsonrpc_not_target_bridge_or_default():
    runtime, pool, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server,
            "kodi_gui_action",
            {"action": "stop", "target": "kodi20"},
        )
    )

    assert result["ok"] is True
    assert result["raw"]["target_id"] == "kodi20"
    assert result["data"]["jsonrpc"]["result"]["label"] == "kodi20"
    assert [target.target_id for target in pool.targets] == ["kodi20"]
    assert bundles["kodi20"].jsonrpc.calls == [
        ("execute_input_action", {"action": "stop"})
    ]
    assert bundles["kodi20"].bridge.calls == []
    assert default.jsonrpc.calls == []
    assert default.bridge.calls == []


@pytest.mark.asyncio
async def test_stop_retries_and_verification_reads_stay_on_resolved_target():
    runtime, pool, default, bundles = _runtime()
    routed = bundles["kodi19"].jsonrpc
    routed.active_players = [{"playerid": 1, "label": "kodi19"}]
    routed.sticky_stop_checks = 1
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server,
            "kodi_player_stop",
            {
                "playerid": 1,
                "target": "kodi19",
                "verify_attempts": 3,
                "verify_delay_ms": 0,
                "stable_checks": 1,
            },
        )
    )

    assert result["ok"] is True
    assert result["raw"]["target_id"] == "kodi19"
    assert result["data"]["stop_attempts"] == 2
    assert routed.calls == [
        ("stop_player", {"playerid": 1}),
        ("get_active_players", {}),
        ("stop_player", {"playerid": 1}),
        ("get_active_players", {}),
    ]
    assert [target.target_id for target in pool.targets] == ["kodi19"]
    assert default.jsonrpc.calls == []
    assert bundles["kodi20"].jsonrpc.calls == []


@pytest.mark.asyncio
async def test_concurrent_mutations_keep_target_transports_isolated():
    runtime, pool, default, bundles = _runtime()
    bundles["kodi20"].jsonrpc.active_players = [{"playerid": 1, "label": "kodi20"}]
    server, _ = build_mcp_server(runtime)

    gui, stop = await asyncio.gather(
        _call(
            server,
            "kodi_gui_action",
            {"action": "back", "target": "kodi19"},
        ),
        _call(
            server,
            "kodi_player_stop",
            {
                "playerid": 1,
                "target": "kodi20",
                "verify_attempts": 1,
                "verify_delay_ms": 0,
                "stable_checks": 1,
            },
        ),
    )

    assert _envelope(gui)["raw"]["target_id"] == "kodi19"
    assert _envelope(stop)["raw"]["target_id"] == "kodi20"
    assert bundles["kodi19"].bridge.calls == [
        ("gui_action", {"action": "back"})
    ]
    assert bundles["kodi20"].jsonrpc.calls == [
        ("stop_player", {"playerid": 1}),
        ("get_active_players", {}),
    ]
    assert {target.target_id for target in pool.targets} == {"kodi19", "kodi20"}
    assert default.jsonrpc.calls == []
    assert default.bridge.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name", sorted(BATCH_B_TOOL_NAMES - {"kodi_player_stop"})
)
async def test_mutation_transport_error_does_not_trigger_automatic_dispatch_retry(
    tool_name,
):
    runtime, _, default, bundles = _runtime()
    server, _ = build_mcp_server(runtime)
    target = bundles["kodi19"]

    calls = 0

    async def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return ResponseMessage(
            request_id="failed-mutation",
            result=None,
            error="temporary target failure",
            error_type=ErrorType.NETWORK_ERROR,
        )

    if tool_name == "kodi_gui_action":
        target.bridge.gui_action = fail
    else:
        method = {
            "kodi_player_open": "open_library_item",
            "kodi_player_pause": "pause_player",
            "kodi_player_seek": "seek_player_to_seconds",
        }[tool_name]
        setattr(target.jsonrpc, method, fail)

    result = _envelope(
        await _call(
            server, tool_name, {**VALID_ARGUMENTS[tool_name], "target": "kodi19"}
        )
    )

    assert result["ok"] is False
    assert result["raw"]["target_id"] == "kodi19"
    assert calls == 1
    assert target.bridge.calls == []
    assert target.jsonrpc.calls == []
    assert default.bridge.calls == []
    assert default.jsonrpc.calls == []


@pytest.mark.asyncio
async def test_explicit_mutation_redacts_target_fields_and_keeps_structured_output():
    runtime, _, _, bundles = _runtime()
    bundles["kodi19"] = _Bundle(
        bundles["kodi19"].jsonrpc, _Bridge("kodi19", sensitive=True)
    )
    server, _ = build_mcp_server(runtime)

    async with Client(server, mode="auto") as client:
        result = await client.call_tool(
            "kodi_gui_action", {"action": "home", "target": "kodi19"}
        )

    envelope = _envelope(result)
    rendered = json.dumps(envelope, sort_keys=True)
    assert result.is_error is False
    assert result.structured_content is not None
    assert envelope["tool"] == "kodi_gui_action"
    assert envelope["raw"]["target_id"] == "kodi19"
    assert envelope["data"]["window"] == "Home"
    assert envelope["data"]["label"] == "kodi19"
    assert envelope["data"]["host"] == "[redacted]"
    assert envelope["data"]["port"] == "[redacted]"
    assert envelope["data"]["endpoint_url"] == "[redacted]"
    assert envelope["data"]["auth_reference"] == "[redacted]"
    for forbidden in (
        "routing-secret.invalid",
        "19765",
        "KODI19_TOKEN",
    ):
        assert forbidden not in rendered


@pytest.mark.asyncio
async def test_explicit_mutation_error_redacts_endpoint_and_auth_reference():
    runtime, _, default, bundles = _runtime()
    calls = 0

    async def fail_gui_action(action: str):
        nonlocal calls
        calls += 1
        return ResponseMessage(
            request_id="failed-gui",
            result=None,
            error=(
                "POST https://kodi19.routing-secret.invalid/bridge failed "
                "using env:KODI19_TOKEN"
            ),
            error_type=ErrorType.NETWORK_ERROR,
        )

    bundles["kodi19"].bridge.gui_action = fail_gui_action
    server, _ = build_mcp_server(runtime)

    result = _envelope(
        await _call(
            server,
            "kodi_gui_action",
            {"action": "home", "target": "kodi19"},
        )
    )
    rendered = json.dumps(result, sort_keys=True)

    assert result["ok"] is False
    assert result["raw"]["target_id"] == "kodi19"
    assert result["tool"] == "kodi_gui_action"
    assert calls == 1
    assert "routing-secret.invalid" not in rendered
    assert "KODI19_TOKEN" not in rendered
    assert default.bridge.calls == []
    assert default.jsonrpc.calls == []
