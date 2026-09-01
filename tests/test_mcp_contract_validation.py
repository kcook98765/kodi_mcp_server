"""Externally observable MCP input and failure-envelope contracts."""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from mcp.client import Client
from mcp.types import CallToolRequestParams

import kodi_mcp_mcp.server_core as server_core
from kodi_mcp_mcp.server_core import build_mcp_server


class _Tripwire:
    """Records any downstream access; invalid input must never reach it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        async def unexpected_call(*args: Any, **kwargs: Any):
            self.calls.append(name)
            raise AssertionError(f"invalid input reached downstream method {name}")

        return unexpected_call


def _server_with_tripwires():
    bridge = _Tripwire()
    jsonrpc = _Tripwire()
    server, _ = build_mcp_server(
        {"bridge": bridge, "jsonrpc": jsonrpc, "notifications": _Tripwire()}
    )
    return server, bridge, jsonrpc


def _envelope(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_direct_low_level_handler_enforces_the_advertised_schema():
    server, bridge, jsonrpc = _server_with_tripwires()
    handler = server.get_request_handler("tools/call").handler

    result = await handler(
        None,
        CallToolRequestParams(name="kodi_status", arguments={"unexpected": True}),
    )

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["error_type"] == "invalid_params"
    assert "unexpected" in envelope["error"]
    assert bridge.calls == []
    assert jsonrpc.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "field"),
    [
        ("kodi_status", {"unexpected": True}, "unexpected"),
        ("bridge_log_tail", {"lines": 0}, "lines"),
        ("bridge_log_recent_errors", {"lines": 1001}, "lines"),
        ("kodi_gui_screenshot", {"store": "yes"}, "store"),
        ("kodi_player_stop", {"stable_checks": 0}, "stable_checks"),
        ("jsonrpc_introspect", {"summary": "yes"}, "summary"),
        ("repo_stage_current_dev_repo", {"verify": "yes"}, "verify"),
    ],
)
async def test_advertised_schema_constraints_are_enforced_before_dispatch(
    monkeypatch, tool_name, arguments, field
):
    async def unexpected_repo_stage(**kwargs):
        raise AssertionError("invalid input reached repo staging")

    monkeypatch.setattr(
        server_core, "_repo_stage_current_dev_repo", unexpected_repo_stage
    )
    server, bridge, jsonrpc = _server_with_tripwires()

    async with Client(server, mode="auto") as client:
        result = await client.call_tool(tool_name, arguments)

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["ok"] is False
    assert envelope["error_type"] == "invalid_params"
    assert field in envelope["error"]
    assert envelope["raw"]["validation"][0]["message"]
    assert envelope["raw"]["validation"][0]["validator"]
    assert bridge.calls == []
    assert jsonrpc.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({}, "managed_addon_id"),
        ({"managed_addon_id": ""}, "managed_addon_id"),
        ({"managed_addon_id": "   "}, "managed_addon_id"),
        ({"managed_addon_id": "Plugin.Video.Test"}, "managed_addon_id"),
        ({"managed_addon_id": "plugin/video/test"}, "managed_addon_id"),
        ({"managed_addon_id": "..."}, "managed_addon_id"),
    ],
)
async def test_managed_addon_validate_state_rejects_invalid_identifiers_before_bridge(
    arguments, field
):
    server, bridge, jsonrpc = _server_with_tripwires()

    async with Client(server, mode="auto") as client:
        result = await client.call_tool("managed_addon_validate_state", arguments)

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["ok"] is False
    assert envelope["error_type"] == "invalid_params"
    assert field in envelope["error"]
    assert bridge.calls == []
    assert jsonrpc.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("bridge_write_log_marker", {"message": "   "}),
        ("managed_addon_register", {"source_path": "   "}),
        ("artifact_upload_zip", {"zip_base64": "   "}),
        (
            "repo_publish_artifact",
            {
                "artifact_id": "artifact-1",
                "addon_id": "plugin.video.test",
                "addon_name": "   ",
                "addon_version": "0.1.0",
            },
        ),
    ],
)
async def test_advertised_required_strings_reject_whitespace_only(
    tool_name, arguments
):
    server, _, _ = _server_with_tripwires()

    async with Client(server, mode="auto") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    errors = list(
        Draft202012Validator(tools[tool_name].input_schema).iter_errors(arguments)
    )
    assert errors


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addon_id",
    [
        "plugin.video.kodi_mcp_test_lab",
        "service.kodi_mcp",
        "repository.kodi-mcp",
        "resource.language.sr_rs@latin",
    ],
)
async def test_addon_id_schema_accepts_kodi_compatible_identifiers(addon_id):
    server, _, _ = _server_with_tripwires()

    async with Client(server, mode="auto") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    schema = tools["managed_addon_validate_state"].input_schema
    Draft202012Validator(schema).validate({"managed_addon_id": addon_id})


@pytest.mark.asyncio
async def test_addon_execute_alias_is_advertised_and_accepted():
    server, _, _ = _server_with_tripwires()

    async with Client(server, mode="auto") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    schema = tools["addon_execute"].input_schema
    Draft202012Validator(schema).validate({"addon_id": "plugin.video.test"})


@pytest.mark.asyncio
async def test_managed_addon_not_found_has_nonempty_top_level_error():
    server, _, _ = _server_with_tripwires()

    async with Client(server, mode="auto") as client:
        result = await client.call_tool(
            "managed_addon_get", {"managed_addon_id": "plugin.video.does_not_exist"}
        )

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["ok"] is False
    assert isinstance(envelope["error"], str) and envelope["error"].strip()
    assert envelope["error_type"] == "not_found"
    assert "plugin.video.does_not_exist" in envelope["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name", ["repo_publish_stage_apply_artifact", "addon_dev_loop"]
)
async def test_failed_artifact_apply_aliases_have_nonempty_top_level_error(
    monkeypatch, tool_name
):
    async def failed_apply(**kwargs):
        return {
            "ok": False,
            "addon_id": kwargs["addon_id"],
            "apply_status": "initial_install_required",
            "can_retry": False,
            "failure_reason": "initial_install_required",
        }

    monkeypatch.setattr(
        server_core, "_repo_publish_stage_apply_artifact", failed_apply
    )
    server, _, _ = _server_with_tripwires()
    arguments = {
        "artifact_id": "artifact-1",
        "addon_id": "plugin.video.kodi_mcp_test_lab",
        "addon_name": "Kodi MCP Test Lab",
        "addon_version": "0.1.1",
    }

    async with Client(server, mode="auto") as client:
        result = await client.call_tool(tool_name, arguments)

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["ok"] is False
    assert isinstance(envelope["error"], str) and envelope["error"].strip()
    assert envelope["error_type"] == "operation_failed"
    assert "initial_install_required" in envelope["error"]


@pytest.mark.asyncio
async def test_failed_managed_apply_has_nonempty_top_level_error(monkeypatch):
    async def failed_apply(**kwargs):
        return {
            "ok": False,
            "managed_addon_id": kwargs["managed_addon_id"],
            "verification": {
                "apply_status": "repo_not_installed",
                "retry_hint": "Install repository.kodi-mcp once.",
            },
        }

    monkeypatch.setattr(
        server_core, "managed_addon_build_publish_stage_and_apply", failed_apply
    )
    server, _, _ = _server_with_tripwires()

    async with Client(server, mode="auto") as client:
        result = await client.call_tool(
            "managed_addon_build_publish_stage_and_apply",
            {
                "managed_addon_id": "plugin.video.kodi_mcp_test_lab",
                "version_policy": "use_addon_xml",
            },
        )

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["ok"] is False
    assert isinstance(envelope["error"], str) and envelope["error"].strip()
    assert envelope["error_type"] == "operation_failed"
    assert "repo_not_installed" in envelope["error"]
