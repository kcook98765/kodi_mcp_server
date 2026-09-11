"""Read-only MCP target discovery contracts."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.types import CallToolRequestParams

from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_server.targets.registry import (
    LegacyTargetSettings,
    TargetConfigWarning,
    TargetRegistry,
)


class _Tripwire:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        def unexpected_call(*args: Any, **kwargs: Any):
            self.calls.append(name)
            raise AssertionError(f"target discovery reached {name}")

        return unexpected_call


class _TransportPoolTripwire:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, target_id: str = "default"):
        self.calls.append(target_id)
        raise AssertionError(f"target discovery constructed transports for {target_id}")


def _configured(
    target_id: str,
    *,
    name: str | None = None,
    groups: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    expected_kodi_version: str | None = None,
    websocket: bool = False,
    tcp: bool = False,
) -> dict[str, Any]:
    auth_prefix = target_id.upper().replace("-", "_")
    return {
        "id": target_id,
        "name": name or target_id,
        "endpoints": {
            "jsonrpc_url": f"https://{target_id}.example.invalid/jsonrpc",
            "bridge_url": f"http://{target_id}.example.invalid:8765",
            "websocket_url": (
                f"wss://{target_id}.example.invalid/jsonrpc" if websocket else ""
            ),
            "tcp_host": f"{target_id}.example.invalid" if tcp else "",
        },
        "auth": {
            "jsonrpc_username": f"env:{auth_prefix}_AUTH_REF_USERNAME",
            "jsonrpc_password": f"env:{auth_prefix}_AUTH_REF_PASSWORD",
            "bridge_token": f"env:{auth_prefix}_AUTH_REF_TOKEN",
        },
        "groups": list(groups),
        "tags": list(tags),
        "expected_kodi_version": expected_kodi_version,
        "timeout_seconds": 14,
    }


def _runtime(targets: list[dict[str, Any]] | None = None):
    registry = TargetRegistry.from_sources(
        legacy=LegacyTargetSettings(
            jsonrpc_url="http://selected-proxy:8080/jsonrpc",
            bridge_url="http://selected-proxy:8765",
            websocket_url="ws://selected-proxy:9090/jsonrpc",
            tcp_host="selected-proxy",
            timeout_seconds=19,
        ),
        targets_json=json.dumps(targets or []),
    )
    transports = _TransportPoolTripwire()
    io = _Tripwire()
    return {
        "registry": registry,
        "transport_pool": transports,
        "jsonrpc": io,
        "bridge": io,
        "notifications": io,
    }, transports, io


async def _call(server, name: str, arguments: dict[str, Any]):
    handler = server.get_request_handler("tools/call").handler
    return await handler(None, CallToolRequestParams(name=name, arguments=arguments))


def _envelope(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_target_list_returns_single_implicit_default():
    runtime, transports, io = _runtime()
    server, _ = build_mcp_server(runtime)

    result = await _call(server, "target_list", {})

    assert result.is_error is False
    assert _envelope(result)["data"] == {
        "count": 1,
        "filters": {"group": None, "tag": None},
        "targets": [
            {
                "id": "default",
                "name": "Default Kodi",
                "groups": [],
                "tags": [],
                "expected_kodi_version": None,
                "is_default": True,
                "endpoint_types": ["jsonrpc", "bridge", "websocket", "tcp"],
            }
        ],
    }
    assert transports.calls == []
    assert io.calls == []


@pytest.mark.asyncio
async def test_target_list_is_sorted_and_filters_by_group_and_tag():
    runtime, transports, io = _runtime(
        [
            _configured("zeta", groups=("living",), tags=("stable",)),
            _configured(
                "alpha",
                groups=("dev", "living"),
                tags=("omega", "stable"),
                expected_kodi_version="21.3",
                websocket=True,
            ),
        ]
    )
    server, _ = build_mcp_server(runtime)

    unfiltered = _envelope(await _call(server, "target_list", {}))["data"]
    by_group = _envelope(
        await _call(server, "target_list", {"group": "living"})
    )["data"]
    by_tag = _envelope(await _call(server, "target_list", {"tag": "omega"}))[
        "data"
    ]

    assert [target["id"] for target in unfiltered["targets"]] == [
        "alpha",
        "default",
        "zeta",
    ]
    assert [target["id"] for target in by_group["targets"]] == ["alpha", "zeta"]
    assert by_group["filters"] == {"group": "living", "tag": None}
    assert [target["id"] for target in by_tag["targets"]] == ["alpha"]
    assert by_tag["filters"] == {"group": None, "tag": "omega"}
    assert transports.calls == []
    assert io.calls == []


@pytest.mark.asyncio
async def test_target_info_returns_safe_detail_without_auth_or_endpoint_locations():
    runtime, transports, io = _runtime(
        [
            _configured(
                "alpha",
                name="Alpha Kodi",
                groups=("dev",),
                tags=("omega",),
                expected_kodi_version="21.3",
                websocket=True,
                tcp=True,
            )
        ]
    )
    server, _ = build_mcp_server(runtime)
    registry_before = [target.to_dict() for target in runtime["registry"].list()]
    default_before = runtime["registry"].get("default")

    list_result = await _call(server, "target_list", {})
    info_result = await _call(server, "target_info", {"target_id": "alpha"})
    rendered = json.dumps(
        {"list": _envelope(list_result), "info": _envelope(info_result)},
        sort_keys=True,
    )

    assert info_result.is_error is False
    assert _envelope(info_result)["data"] == {
        "id": "alpha",
        "name": "Alpha Kodi",
        "groups": ["dev"],
        "tags": ["omega"],
        "expected_kodi_version": "21.3",
        "is_default": False,
        "timeout_seconds": 14,
        "transports": {
            "jsonrpc": {"configured": True, "scheme": "https"},
            "bridge": {"configured": True, "scheme": "http"},
            "websocket": {"configured": True, "scheme": "wss"},
            "tcp": {"configured": True},
        },
    }
    for forbidden in (
        "ALPHA_AUTH_REF_USERNAME",
        "ALPHA_AUTH_REF_PASSWORD",
        "ALPHA_AUTH_REF_TOKEN",
        "alpha.example.invalid",
        "jsonrpc_url",
        "bridge_url",
        "websocket_url",
        "tcp_host",
        "mutation_domain",
        '"auth"',
    ):
        assert forbidden not in rendered
    assert transports.calls == []
    assert io.calls == []
    assert [target.to_dict() for target in runtime["registry"].list()] == registry_before
    assert runtime["registry"].get("default") is default_before
    assert runtime["jsonrpc"] is io
    assert runtime["bridge"] is io
    assert runtime["notifications"] is io


@pytest.mark.asyncio
async def test_target_info_unknown_target_is_typed_not_found_without_io():
    runtime, transports, io = _runtime()
    server, _ = build_mcp_server(runtime)

    result = await _call(server, "target_info", {"target_id": "missing"})
    envelope = _envelope(result)

    assert result.is_error is True
    assert envelope["ok"] is False
    assert envelope["data"] is None
    assert envelope["error_type"] == "not_found"
    assert envelope["error_code"] == 404
    assert "missing" in envelope["error"]
    assert transports.calls == []
    assert io.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [{"group": ""}, {"tag": "   "}, {"group": ["living"]}],
)
async def test_target_list_invalid_filters_are_typed_invalid_params_without_io(arguments):
    runtime, transports, io = _runtime()
    server, _ = build_mcp_server(runtime)

    result = await _call(server, "target_list", arguments)
    envelope = _envelope(result)

    assert result.is_error is True
    assert envelope["error_type"] == "invalid_params"
    assert transports.calls == []
    assert io.calls == []


@pytest.mark.asyncio
async def test_target_list_preserves_warn_and_skip_for_malformed_optional_target():
    valid = _configured("valid")
    malformed = _configured("malformed")
    malformed["auth"]["bridge_token"] = "inline-secret-must-not-leak"

    with pytest.warns(TargetConfigWarning) as recorded:
        runtime, transports, io = _runtime([malformed, valid])
    server, _ = build_mcp_server(runtime)

    result = await _call(server, "target_list", {})
    rendered = result.content[0].text

    assert [target["id"] for target in _envelope(result)["data"]["targets"]] == [
        "default",
        "valid",
    ]
    assert "inline-secret-must-not-leak" not in rendered
    assert all("inline-secret-must-not-leak" not in str(item.message) for item in recorded)
    assert transports.calls == []
    assert io.calls == []
