"""Shared MCP server core for Kodi.

This module contains the transport-agnostic MCP server implementation:
- runtime construction (composition)
- tool schema definitions
- tool dispatch logic

It is used by:
- stdio MCP entrypoint: `kodi_mcp_mcp.server`
- remote StreamableHTTP/SSE transport mounted in the FastAPI app

NOTE: This file is intentionally a refactor/extraction from
`kodi_mcp_mcp.server` so stdio behavior remains unchanged.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Tuple

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ErrorData,
    Implementation,
    InitializeRequest,
    InitializeResult,
    ListToolsRequest,
    ListToolsResult,
    ServerCapabilities,
    ServerResult,
    TextContent,
    Tool,
    ToolsCapability,
)

from kodi_mcp_server.composition import (
    build_bridge_tool,
    build_jsonrpc_tool,
    build_notification_probe,
)
from kodi_mcp_server.config import KODI_BRIDGE_BASE_URL, KODI_JSONRPC_URL
from kodi_mcp_server.managed_addons import (
    managed_addon_build_publish_and_stage,
    managed_addon_get,
    managed_addon_list,
    managed_addon_register,
)
from kodi_mcp_server.kodi_apply import managed_addon_build_publish_stage_and_apply
from kodi_mcp_server.milestone_a_bridge import read_addon_state
from kodi_mcp_server.paths import AUTHORITATIVE_REPO_ROOT


SERVER_NAME = "kodi-mcp"
SERVER_VERSION = "0.0.0"


Runtime = dict[str, Any]


def _as_dict(value: Any) -> Any:
    """Best-effort conversion for tool results.

    - ResponseMessage exposes `to_dict()`.
    - Plain dict results are passed through.
    """

    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        return value.to_dict()
    return value


async def _kodi_status(runtime: Runtime) -> dict[str, Any]:
    """Direct-call implementation for `kodi_status`.

    Returns a dict matching the FastAPI `/status` endpoint shape.
    """

    result: dict[str, Any] = {
        "server": {"status": "running"},
        "config": {"loaded": bool(KODI_JSONRPC_URL and KODI_BRIDGE_BASE_URL)},
        "jsonrpc": {"status": "unknown", "url": KODI_JSONRPC_URL},
        "bridge": {"status": "unknown", "url": KODI_BRIDGE_BASE_URL},
    }

    # Test JSON-RPC connectivity (simple ping)
    if KODI_JSONRPC_URL:
        try:
            jsonrpc_response = await runtime["jsonrpc"].get_jsonrpc_version()
            if getattr(jsonrpc_response, "error", None):
                result["jsonrpc"]["status"] = "error"
                result["jsonrpc"]["error"] = getattr(jsonrpc_response, "error", None)
            else:
                result["jsonrpc"]["status"] = "ok"
        except Exception as exc:
            result["jsonrpc"]["status"] = "error"
            result["jsonrpc"]["error"] = str(exc)

    # Test bridge connectivity
    if KODI_BRIDGE_BASE_URL:
        try:
            bridge_response = await runtime["bridge"].get_bridge_health()
            if getattr(bridge_response, "error", None):
                result["bridge"]["status"] = "error"
                result["bridge"]["error"] = getattr(bridge_response, "error", None)
            else:
                result["bridge"]["status"] = "ok"
        except Exception as exc:
            result["bridge"]["status"] = "error"
            result["bridge"]["error"] = str(exc)

    return result


def build_runtime() -> Runtime:
    """Build the shared runtime once at startup."""

    notifications = None
    try:
        # Optional dependency (`websockets`) may not be installed in all environments.
        notifications = build_notification_probe()
    except Exception:
        notifications = None

    return {
        "bridge": build_bridge_tool(),
        "jsonrpc": build_jsonrpc_tool(),
        "notifications": notifications,
    }


def build_mcp_server(runtime: Runtime) -> Tuple[Server, InitializationOptions]:
    """Build a configured MCP server and its InitializationOptions.

    The returned Server is transport-agnostic; callers are responsible for
    running it over stdio or a remote HTTP transport.
    """

    async def _handle_initialize(_: InitializeRequest) -> ServerResult:
        """MCP initialize handler."""

        result = InitializeResult(
            protocolVersion="2025-11-25",
            capabilities=ServerCapabilities(tools=ToolsCapability()),
            serverInfo=Implementation(name=SERVER_NAME, version=SERVER_VERSION),
            instructions="Kodi MCP wrapper (skeleton): tools not implemented yet.",
        )
        return ServerResult(result)

    async def _handle_list_tools(_: ListToolsRequest) -> ServerResult:
        """Return the tool list."""

        tools: list[Tool] = [
            Tool(
                name="kodi_status",
                description=(
                    "Get end-to-end server status, including config loaded state "
                    "and connectivity to Kodi JSON-RPC + bridge."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_health",
                description="Check whether the Kodi bridge addon HTTP service is reachable.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_status",
                description="Get bridge addon status payload (bridge-provided runtime summary).",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_runtime_info",
                description="Get bridge runtime info (paths/config) useful for debugging addon deployment.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_log_tail",
                description="Read the last N lines of the Kodi log via the bridge (primary dev-loop debugging signal).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "lines": {
                            "type": "integer",
                            "description": "Number of log lines to return.",
                            "minimum": 1,
                            "default": 50,
                        }
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_log_markers",
                description="Retrieve recent log markers written by the bridge/service (helps correlate dev-loop actions).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "lines": {
                            "type": "integer",
                            "description": "Number of log lines to scan for markers.",
                            "minimum": 1,
                            "default": 200,
                        }
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_write_log_marker",
                description="Write a unique marker into the Kodi log to bracket experiments and verify an action occurred.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Marker text to write into the log. Use a unique token for traceability.",
                        }
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="addon_list",
                description="List addons (optionally filtered by type and/or enabled) to confirm install/enable state during dev.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": "Addon type filter (Kodi taxonomy), e.g. 'xbmc.python.script', 'kodi.gameclient', etc.",
                        },
                        "enabled": {
                            "type": "boolean",
                            "description": "If provided, filter by enabled state.",
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="addon_details",
                description="Fetch addon metadata (version, enabled status, etc.) for a specific addon id.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "addonid": {
                            "type": "string",
                            "description": "Kodi addon id (e.g. 'service.kodi_mcp').",
                        }
                    },
                    "required": ["addonid"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="jsonrpc_introspect",
                description="Introspect the Kodi JSON-RPC API; useful for discovering methods and validating parameter shapes.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "boolean",
                            "description": "If true, return a summarized view instead of the full introspection payload.",
                            "default": True,
                        },
                        "getdescriptions": {
                            "type": "boolean",
                            "description": "Include human-readable method descriptions.",
                            "default": False,
                        },
                        "getmetadata": {
                            "type": "boolean",
                            "description": "Include extra metadata.",
                            "default": False,
                        },
                        "filterbytransport": {
                            "type": "boolean",
                            "description": "If true, filter by transport-supported methods.",
                            "default": False,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="kodi_notifications_sample",
                description=(
                    "Collect a short sample of Kodi WebSocket notifications. "
                    "This is an OPTIONAL capability (requires Kodi WebSocket at ws://<host>:9090/jsonrpc). "
                    "Core repo/publish/update workflows do NOT require WebSocket notifications; "
                    "treat failures as advisory/informational."
                ),
                inputSchema={
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
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="managed_addon_register",
                description="Register/update a managed local addon source path (no GitHub).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_path": {
                            "type": "string",
                            "description": "Local filesystem path to an addon root containing addon.xml.",
                        }
                    },
                    "required": ["source_path"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="managed_addon_list",
                description="List all managed addons registered with this MCP server.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="managed_addon_get",
                description="Get a single managed addon registry entry by managed_addon_id (usually addon id).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "managed_addon_id": {
                            "type": "string",
                            "description": "Managed addon id (key). Defaults to addon id.",
                        }
                    },
                    "required": ["managed_addon_id"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="managed_addon_build_publish_and_stage",
                description="Build+publish a managed addon, build dev repo zip, then stage it to Kodi via the bridge.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "managed_addon_id": {"type": "string"},
                        "version_policy": {
                            "type": "string",
                            "enum": ["use_addon_xml", "bump_patch", "set_explicit"],
                        },
                        "explicit_version": {"type": "string"},
                        "repo_version": {"type": "string"},
                        "verify": {"type": "boolean", "default": True},
                    },
                    "required": ["managed_addon_id", "version_policy"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="managed_addon_build_publish_stage_and_apply",
                description=(
                    "Build+publish a managed addon, stage the dev repo zip to Kodi, then refresh and "
                    "install/update the addon from Kodi (best-effort; assumes repo already installed once)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "managed_addon_id": {"type": "string"},
                        "version_policy": {
                            "type": "string",
                            "enum": ["use_addon_xml", "bump_patch", "set_explicit"],
                        },
                        "explicit_version": {"type": "string"},
                        "repo_version": {"type": "string"},
                        "verify": {"type": "boolean", "default": True},
                    },
                    "required": ["managed_addon_id", "version_policy"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="managed_addon_validate_state",
                description="Read-only validation report for managed addon readiness (registry/artifacts/bridge).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "managed_addon_id": {"type": "string"},
                    },
                    "required": ["managed_addon_id"],
                    "additionalProperties": False,
                },
            ),
        ]

        return ServerResult(ListToolsResult(tools=tools))

    async def _handle_call_tool(request: CallToolRequest) -> ServerResult:
        """Dispatch a tool call."""

        tool_name = request.params.name

        if tool_name in {
            "kodi_status",
            "bridge_health",
            "bridge_status",
            "bridge_runtime_info",
            "bridge_log_tail",
            "bridge_log_markers",
            "addon_list",
            "addon_details",
            "jsonrpc_introspect",
            "kodi_notifications_sample",
            "bridge_write_log_marker",
            "managed_addon_register",
            "managed_addon_list",
            "managed_addon_get",
            "managed_addon_build_publish_and_stage",
            "managed_addon_build_publish_stage_and_apply",
            "managed_addon_validate_state",
        }:
            # Preserve exact normalized missing-arg behavior for addon_details.
            if tool_name == "addon_details":
                args = request.params.arguments or {}
                if not isinstance(args, dict):
                    args = {}

                addonid = args.get("addonid")
                if not isinstance(addonid, str) or not addonid:
                    envelope = {
                        "ok": False,
                        "tool": tool_name,
                        "data": None,
                        "error": "missing required argument: addonid",
                        "error_type": "invalid_params",
                        "error_code": None,
                        "latency_ms": 0,
                        "request_id": None,
                        "raw": {"arguments": args},
                    }
                    text = json.dumps(envelope, indent=2, sort_keys=True)
                    return ServerResult(
                        CallToolResult(
                            isError=True,
                            content=[TextContent(type="text", text=text)],
                        )
                    )

            # Preserve exact normalized missing-arg behavior for bridge_write_log_marker.
            if tool_name == "bridge_write_log_marker":
                args = request.params.arguments or {}
                if not isinstance(args, dict):
                    args = {}

                message = args.get("message")
                if not isinstance(message, str) or not message.strip():
                    envelope = {
                        "ok": False,
                        "tool": tool_name,
                        "data": None,
                        "error": "missing required argument: message",
                        "error_type": "invalid_params",
                        "error_code": None,
                        "latency_ms": 0,
                        "request_id": None,
                        "raw": {"arguments": args},
                    }
                    text = json.dumps(envelope, indent=2, sort_keys=True)
                    return ServerResult(
                        CallToolResult(
                            isError=True,
                            content=[TextContent(type="text", text=text)],
                        )
                    )

            # Managed addon required-arg checks.
            if tool_name in {
                "managed_addon_register",
                "managed_addon_get",
                "managed_addon_build_publish_and_stage",
                "managed_addon_build_publish_stage_and_apply",
                "managed_addon_validate_state",
            }:
                args = request.params.arguments or {}
                if not isinstance(args, dict):
                    args = {}

                if tool_name == "managed_addon_register":
                    source_path = args.get("source_path")
                    if not isinstance(source_path, str) or not source_path.strip():
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing required argument: source_path",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        text = json.dumps(envelope, indent=2, sort_keys=True)
                        return ServerResult(
                            CallToolResult(
                                isError=True,
                                content=[TextContent(type="text", text=text)],
                            )
                        )

                if tool_name == "managed_addon_get":
                    managed_addon_id = args.get("managed_addon_id")
                    if not isinstance(managed_addon_id, str) or not managed_addon_id.strip():
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing required argument: managed_addon_id",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        text = json.dumps(envelope, indent=2, sort_keys=True)
                        return ServerResult(
                            CallToolResult(
                                isError=True,
                                content=[TextContent(type="text", text=text)],
                            )
                        )

                if tool_name == "managed_addon_validate_state":
                    managed_addon_id = args.get("managed_addon_id")
                    if not isinstance(managed_addon_id, str) or not managed_addon_id.strip():
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing required argument: managed_addon_id",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        text = json.dumps(envelope, indent=2, sort_keys=True)
                        return ServerResult(
                            CallToolResult(
                                isError=True,
                                content=[TextContent(type="text", text=text)],
                            )
                        )

                if tool_name == "managed_addon_build_publish_and_stage":
                    managed_addon_id = args.get("managed_addon_id")
                    version_policy = args.get("version_policy")
                    if not isinstance(managed_addon_id, str) or not managed_addon_id.strip():
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing required argument: managed_addon_id",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        text = json.dumps(envelope, indent=2, sort_keys=True)
                        return ServerResult(
                            CallToolResult(
                                isError=True,
                                content=[TextContent(type="text", text=text)],
                            )
                        )
                    if version_policy not in {"use_addon_xml", "bump_patch", "set_explicit"}:
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "invalid argument: version_policy",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args, "allowed": ["use_addon_xml", "bump_patch", "set_explicit"]},
                        }
                        text = json.dumps(envelope, indent=2, sort_keys=True)
                        return ServerResult(
                            CallToolResult(
                                isError=True,
                                content=[TextContent(type="text", text=text)],
                            )
                        )

                if tool_name == "managed_addon_build_publish_stage_and_apply":
                    managed_addon_id = args.get("managed_addon_id")
                    version_policy = args.get("version_policy")
                    if not isinstance(managed_addon_id, str) or not managed_addon_id.strip():
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing required argument: managed_addon_id",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        text = json.dumps(envelope, indent=2, sort_keys=True)
                        return ServerResult(
                            CallToolResult(
                                isError=True,
                                content=[TextContent(type="text", text=text)],
                            )
                        )
                    if version_policy not in {"use_addon_xml", "bump_patch", "set_explicit"}:
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "invalid argument: version_policy",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {
                                "arguments": args,
                                "allowed": ["use_addon_xml", "bump_patch", "set_explicit"],
                            },
                        }
                        text = json.dumps(envelope, indent=2, sort_keys=True)
                        return ServerResult(
                            CallToolResult(
                                isError=True,
                                content=[TextContent(type="text", text=text)],
                            )
                        )

            start = time.time()
            envelope: dict[str, Any]
            try:
                if tool_name == "bridge_health":
                    raw_result = await runtime["bridge"].get_bridge_health()
                elif tool_name == "bridge_status":
                    raw_result = await runtime["bridge"].get_bridge_status()
                elif tool_name == "bridge_runtime_info":
                    raw_result = await runtime["bridge"].get_bridge_runtime_info()
                elif tool_name in {"bridge_log_tail", "bridge_log_markers"}:
                    args = request.params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}

                    default_lines = 50 if tool_name == "bridge_log_tail" else 200
                    lines = args.get("lines", default_lines)
                    if not isinstance(lines, int):
                        lines = default_lines
                    if lines < 1:
                        lines = 1

                    if tool_name == "bridge_log_tail":
                        raw_result = await runtime["bridge"].get_bridge_log_tail(lines=lines)
                    else:
                        raw_result = await runtime["bridge"].get_bridge_log_markers(lines=lines)
                elif tool_name == "addon_list":
                    args = request.params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}

                    addon_type = args.get("type")
                    enabled = args.get("enabled")

                    raw_result = await runtime["jsonrpc"].list_addons(
                        type=addon_type if isinstance(addon_type, str) and addon_type else None,
                        enabled=enabled if isinstance(enabled, bool) else None,
                    )
                elif tool_name == "addon_details":
                    args = request.params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    addonid = args.get("addonid")
                    raw_result = await runtime["jsonrpc"].get_addon_details(addonid=addonid)
                elif tool_name == "jsonrpc_introspect":
                    args = request.params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}

                    summary = args.get("summary", True)
                    getdescriptions = args.get("getdescriptions", False)
                    getmetadata = args.get("getmetadata", False)
                    filterbytransport = args.get("filterbytransport", False)

                    def _as_bool(v: Any, default: bool) -> bool:
                        return v if isinstance(v, bool) else default

                    raw_result = await runtime["jsonrpc"].introspect_jsonrpc(
                        summary=_as_bool(summary, True),
                        getdescriptions=_as_bool(getdescriptions, False),
                        getmetadata=_as_bool(getmetadata, False),
                        filterbytransport=_as_bool(filterbytransport, False),
                    )
                elif tool_name == "kodi_notifications_sample":
                    if runtime.get("notifications") is None:
                        raise RuntimeError("notifications probe unavailable (missing optional dependency: websockets)")

                    args = request.params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}

                    sample_size = args.get("sample_size", 3)
                    if not isinstance(sample_size, int):
                        sample_size = 3
                    if sample_size < 1:
                        sample_size = 1

                    listen_seconds = args.get("listen_seconds", 5)
                    if not isinstance(listen_seconds, int):
                        listen_seconds = 5
                    if listen_seconds < 1:
                        listen_seconds = 1

                    raw_result = await runtime["notifications"].listen(
                        sample_size=sample_size,
                        listen_seconds=listen_seconds,
                    )
                elif tool_name == "bridge_write_log_marker":
                    args = request.params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    message = args.get("message")
                    raw_result = await runtime["bridge"].write_bridge_log_marker(message=message)
                elif tool_name == "managed_addon_register":
                    args = request.params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    raw_result = managed_addon_register(source_path=str(args.get("source_path") or "").strip())
                elif tool_name == "managed_addon_list":
                    raw_result = managed_addon_list()
                elif tool_name == "managed_addon_get":
                    args = request.params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    raw_result = managed_addon_get(managed_addon_id=str(args.get("managed_addon_id") or "").strip())
                elif tool_name == "managed_addon_build_publish_and_stage":
                    args = request.params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    verify = args.get("verify", True)
                    if not isinstance(verify, bool):
                        verify = True
                    raw_result = await managed_addon_build_publish_and_stage(
                        managed_addon_id=str(args.get("managed_addon_id") or "").strip(),
                        version_policy=str(args.get("version_policy") or "").strip(),
                        explicit_version=(
                            str(args.get("explicit_version") or "").strip()
                            if args.get("explicit_version") is not None
                            else None
                        ),
                        repo_version=(
                            str(args.get("repo_version") or "").strip()
                            if args.get("repo_version") is not None
                            else None
                        ),
                        verify=verify,
                    )
                elif tool_name == "managed_addon_build_publish_stage_and_apply":
                    args = request.params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    verify = args.get("verify", True)
                    if not isinstance(verify, bool):
                        verify = True
                    raw_result = await managed_addon_build_publish_stage_and_apply(
                        managed_addon_id=str(args.get("managed_addon_id") or "").strip(),
                        version_policy=str(args.get("version_policy") or "").strip(),
                        explicit_version=(
                            str(args.get("explicit_version") or "").strip()
                            if args.get("explicit_version") is not None
                            else None
                        ),
                        repo_version=(
                            str(args.get("repo_version") or "").strip()
                            if args.get("repo_version") is not None
                            else None
                        ),
                        verify=verify,
                        bridge_tool=runtime["bridge"],
                        jsonrpc_tool=runtime["jsonrpc"],
                    )
                elif tool_name == "managed_addon_validate_state":
                    args = request.params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    managed_addon_id = str(args.get("managed_addon_id") or "").strip()
                    registry_result = managed_addon_get(managed_addon_id=managed_addon_id)

                    entry = (registry_result.get("managed_addon") if registry_result.get("ok") else None)
                    last_build = entry.get("last_build") if isinstance(entry, dict) else None

                    registry_exists = bool(registry_result.get("ok") and isinstance(entry, dict))
                    enabled = bool(entry.get("enabled", False)) if isinstance(entry, dict) else False
                    source_path = str(entry.get("source_path") or "") if isinstance(entry, dict) else ""
                    addon_id = str(entry.get("addon_id") or "") if isinstance(entry, dict) else ""
                    last_observed_version = str(entry.get("last_observed_version") or "") if isinstance(entry, dict) else ""

                    # Artifact presence
                    def _exists(p: str) -> bool:
                        try:
                            from pathlib import Path as _Path

                            return bool(p and _Path(p).exists())
                        except Exception:
                            return False

                    last_build_zip_exists = _exists(str((last_build or {}).get("zip_path") or "")) if isinstance(last_build, dict) else False
                    published_repo_zip_exists = _exists(str((last_build or {}).get("repo_zip_path") or "")) if isinstance(last_build, dict) else False

                    dev_repo_dir = AUTHORITATIVE_REPO_ROOT / "dev-repo"
                    dev_repo_exists = dev_repo_dir.exists()
                    addons_xml_exists = (dev_repo_dir / "addons.xml").exists()
                    addons_xml_md5_exists = (dev_repo_dir / "addons.xml.md5").exists()

                    # Best-effort bridge checks
                    reachable = False
                    bridge_error = None
                    mcp_state_read_ok = False
                    registration_present = None
                    registration_stale = None
                    repo_zip_file_exists = None
                    repo_zip_special_path = None
                    dev_setup_available = None

                    try:
                        health = await runtime["bridge"].get_bridge_health()
                        bridge_error = getattr(health, "error", None)
                        reachable = bool(bridge_error is None)
                    except Exception as exc:
                        reachable = False
                        bridge_error = str(exc)

                    if reachable:
                        try:
                            view, resp = await read_addon_state()
                            mcp_state_read_ok = bool(view.transport_ok)
                            derived = None
                            repo_zip = None
                            if resp.error is None and isinstance((resp.result or {}).get("result"), dict):
                                result_obj = (resp.result.get("result") or {})
                                derived = result_obj.get("derived")
                                repo_zip = result_obj.get("repo_zip")
                            if isinstance(derived, dict):
                                registration_present = derived.get("registration_present")
                                registration_stale = derived.get("registration_stale")
                                repo_zip_file_exists = derived.get("repo_zip_file_exists")
                                dev_setup_available = derived.get("dev_setup_available")
                            if isinstance(repo_zip, dict):
                                special_path = repo_zip.get("special_path")
                                if isinstance(special_path, str) and special_path.strip():
                                    repo_zip_special_path = special_path.strip()
                        except Exception as exc:
                            mcp_state_read_ok = False
                            bridge_error = str(exc)

                    # Overall readiness
                    ready_for_build = bool(registry_exists and enabled and source_path and _exists(source_path) and _exists(str(Path(source_path) / "addon.xml")))
                    ready_for_publish = bool(ready_for_build and last_build_zip_exists and dev_repo_exists)
                    ready_for_stage = bool(ready_for_publish and reachable and mcp_state_read_ok and bool(registration_present) and not bool(registration_stale))
                    ready_for_kodi_install = bool(ready_for_stage and bool(repo_zip_file_exists) and bool(dev_setup_available))

                    raw_result = {
                        "ok": True,
                        "managed_addon_id": managed_addon_id,
                        "registry": {
                            "exists": registry_exists,
                            "enabled": enabled,
                            "addon_id": addon_id,
                            "source_path": source_path,
                            "last_observed_version": last_observed_version,
                            "last_build": last_build if isinstance(last_build, dict) else None,
                        },
                        "artifacts": {
                            "last_build_zip_exists": last_build_zip_exists,
                            "published_repo_zip_exists": published_repo_zip_exists,
                            "dev_repo_exists": dev_repo_exists,
                            "addons_xml_exists": addons_xml_exists,
                            "addons_xml_md5_exists": addons_xml_md5_exists,
                        },
                        "kodi_bridge": {
                            "reachable": reachable,
                            "mcp_state_read_ok": mcp_state_read_ok,
                            "error": bridge_error,
                            "registration_present": registration_present,
                            "registration_stale": registration_stale,
                            "repo_zip_file_exists": repo_zip_file_exists,
                            "repo_zip_special_path": repo_zip_special_path,
                            "dev_setup_available": dev_setup_available,
                        },
                        "summary": {
                            "ready_for_build": ready_for_build,
                            "ready_for_publish": ready_for_publish,
                            "ready_for_stage": ready_for_stage,
                            "ready_for_kodi_install": ready_for_kodi_install,
                        },
                    }
                else:
                    raw_result = await _kodi_status(runtime)

                raw_value = _as_dict(raw_result)
                latency_ms = int((time.time() - start) * 1000)

                # Normalize: ResponseMessage dict vs plain dict
                if isinstance(raw_value, dict) and "result" in raw_value and "error" in raw_value:
                    ok = raw_value.get("error") is None
                    envelope = {
                        "ok": ok,
                        "tool": tool_name,
                        "data": raw_value.get("result"),
                        "error": raw_value.get("error"),
                        "error_type": raw_value.get("error_type"),
                        "error_code": raw_value.get("error_code"),
                        "latency_ms": raw_value.get("latency_ms") or latency_ms,
                        "request_id": raw_value.get("request_id"),
                        "raw": raw_value,
                    }
                else:
                    envelope = {
                        "ok": True,
                        "tool": tool_name,
                        "data": raw_value,
                        "error": None,
                        "error_type": None,
                        "error_code": None,
                        "latency_ms": latency_ms,
                        "request_id": None,
                        "raw": raw_value,
                    }
            except Exception as exc:
                latency_ms = int((time.time() - start) * 1000)
                envelope = {
                    "ok": False,
                    "tool": tool_name,
                    "data": None,
                    "error": f"request failed: {exc}",
                    "error_type": "unknown_error",
                    "error_code": None,
                    "latency_ms": latency_ms,
                    "request_id": None,
                    "raw": None,
                }

            text = json.dumps(envelope, indent=2, sort_keys=True)
            return ServerResult(
                CallToolResult(
                    isError=not envelope.get("ok", False),
                    content=[TextContent(type="text", text=text)],
                )
            )

        payload = ErrorData(code=0, message=f"Tool not implemented: {tool_name}", data=None)
        return ServerResult(
            CallToolResult(
                isError=True,
                content=[TextContent(type="text", text=payload.model_dump_json(indent=2))],
            )
        )

    server = Server(SERVER_NAME, version=SERVER_VERSION)
    server.request_handlers[InitializeRequest] = _handle_initialize
    server.request_handlers[ListToolsRequest] = _handle_list_tools
    server.request_handlers[CallToolRequest] = _handle_call_tool

    init_options = InitializationOptions(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        capabilities=ServerCapabilities(tools=ToolsCapability()),
    )

    return server, init_options
