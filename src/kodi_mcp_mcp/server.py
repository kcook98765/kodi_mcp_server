"""Minimal MCP stdio wrapper server (skeleton).

This module intentionally does NOT implement any HTTP forwarding yet.

It exists to prove the MCP lifecycle wiring for VS Code/Cline:
- initialize
- tools/list (currently empty)
- tools/call (currently returns not-implemented)

Next steps (future modules): tool definitions, HTTP dispatcher, and response
normalization.
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any

import anyio

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ErrorData,
    Tool,
    Implementation,
    InitializeRequest,
    InitializeResult,
    ListToolsRequest,
    ListToolsResult,
    ServerCapabilities,
    ServerResult,
    TextContent,
    ToolsCapability,
)

from kodi_mcp_server.composition import build_bridge_tool, build_jsonrpc_tool, build_notification_probe
from kodi_mcp_server.config import KODI_BRIDGE_BASE_URL, KODI_JSONRPC_URL


SERVER_NAME = "kodi-mcp"
SERVER_VERSION = "0.0.0"


_RUNTIME: dict[str, Any] | None = None


def _as_dict(value: Any) -> Any:
    """Best-effort conversion for tool results.

    - ResponseMessage exposes `to_dict()`.
    - Plain dict results are passed through.
    """
    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        return value.to_dict()
    return value


async def _kodi_status(runtime: dict[str, Any]) -> dict[str, Any]:
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


async def _handle_initialize(_: InitializeRequest) -> ServerResult:
    """MCP initialize handler.

    The low-level ServerSession already uses InitializationOptions for
    handshake enforcement; this handler provides the official protocol
    response payload.
    """
    result = InitializeResult(
        protocolVersion="2025-11-25",
        capabilities=ServerCapabilities(tools=ToolsCapability()),
        serverInfo=Implementation(name=SERVER_NAME, version=SERVER_VERSION),
        instructions="Kodi MCP wrapper (skeleton): tools not implemented yet.",
    )
    return ServerResult(result)


async def _handle_list_tools(_: ListToolsRequest) -> ServerResult:
    """Return the tool list.

    Curated surface only (read-only/safe-first). Tool execution is NOT
    implemented yet; this handler only advertises tool metadata.
    """
    tools: list[Tool] = [
        Tool(
            name="kodi_status",
            description="Get end-to-end server status, including config loaded state and connectivity to Kodi JSON-RPC + bridge.",
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
            description="Collect a short sample of Kodi WebSocket notifications (helps validate events your addon should emit/observe).",
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
    ]

    return ServerResult(ListToolsResult(tools=tools))


async def _handle_call_tool(request: CallToolRequest) -> ServerResult:
    """Dispatch a tool call.

    Minimal behavior:
    - Implement two tools via direct core calls (kodi_status, bridge_health)
    - All other tools return not-implemented.
    """
    tool_name = request.params.name

    # Phase 2 (direct-call slices): direct calls (no HTTP)
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
    }:
        global _RUNTIME
        runtime = _RUNTIME or {}

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
                # Keep argument handling for `lines` exactly aligned with the
                # existing HTTP-forwarding logic.
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
                # Keep argument handling aligned with the existing HTTP-forwarding logic:
                # optional `type` and optional boolean `enabled`.
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
                # `addonid` validation is handled above to preserve the exact
                # normalized error envelope.
                args = request.params.arguments or {}
                if not isinstance(args, dict):
                    args = {}
                addonid = args.get("addonid")
                raw_result = await runtime["jsonrpc"].get_addon_details(addonid=addonid)
            elif tool_name == "jsonrpc_introspect":
                # Keep boolean defaults and parsing behavior aligned with the
                # existing HTTP-forwarding logic.
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
                # Keep argument handling exactly aligned with the previous HTTP-forwarding logic.
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

    # Default: not implemented
    payload = ErrorData(code=0, message=f"Tool not implemented: {tool_name}", data=None)
    return ServerResult(
        CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=payload.model_dump_json(indent=2))],
        )
    )


async def run_server() -> None:
    """Run the MCP server over stdio."""

    # Build shared runtime once at startup.
    global _RUNTIME
    _RUNTIME = {
        "bridge": build_bridge_tool(),
        "jsonrpc": build_jsonrpc_tool(),
        "notifications": build_notification_probe(),
    }

    server = Server(SERVER_NAME, version=SERVER_VERSION)
    server.request_handlers[InitializeRequest] = _handle_initialize
    server.request_handlers[ListToolsRequest] = _handle_list_tools
    server.request_handlers[CallToolRequest] = _handle_call_tool

    init_options = InitializationOptions(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        capabilities=ServerCapabilities(tools=ToolsCapability()),
    )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def main() -> None:
    """Console script entrypoint."""
    anyio.run(run_server)


if __name__ == "__main__":
    main()
