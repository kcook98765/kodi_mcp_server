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
import os
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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


SERVER_NAME = "kodi-mcp"
SERVER_VERSION = "0.0.0"


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
    - Implement two read-only tools via HTTP GET to the existing FastAPI server
      (kodi_status, bridge_health)
    - All other tools return not-implemented.
    """
    tool_name = request.params.name

    # Implemented subset (read-only first)
    if tool_name in {
        "kodi_status",
        "bridge_health",
        "bridge_status",
        "bridge_runtime_info",
        "bridge_log_tail",
        "bridge_log_markers",
        "bridge_write_log_marker",
        "addon_list",
        "addon_details",
        "jsonrpc_introspect",
        "kodi_notifications_sample",
    }:
        base_url = os.environ.get("KODI_MCP_BASE_URL", "http://localhost:8000").rstrip("/")

        # Tool -> HTTP mapping
        method = "POST" if tool_name == "bridge_write_log_marker" else "GET"
        path = {
            "kodi_status": "/status",
            "bridge_health": "/tools/get_bridge_health",
            "bridge_status": "/tools/get_bridge_status",
            "bridge_runtime_info": "/tools/get_bridge_runtime_info",
            "bridge_log_tail": "/tools/get_bridge_log_tail",
            "bridge_log_markers": "/tools/get_bridge_log_markers",
            "bridge_write_log_marker": "/tools/write_bridge_log_marker",
            "addon_list": "/tools/list_addons",
            "addon_details": "/tools/get_addon_details",
            "jsonrpc_introspect": "/tools/introspect_jsonrpc",
            "kodi_notifications_sample": "/tools/listen_kodi_notifications",
        }[tool_name]

        query: dict[str, Any] | None = None
        body: dict[str, Any] | None = None

        # Argument handling for log tools
        if tool_name in {"bridge_log_tail", "bridge_log_markers"}:
            args = request.params.arguments or {}
            if not isinstance(args, dict):
                args = {}

            default_lines = 50 if tool_name == "bridge_log_tail" else 200
            lines = args.get("lines", default_lines)
            if not isinstance(lines, int):
                lines = default_lines
            if lines < 1:
                lines = 1

            query = {"lines": lines}

        # Argument handling for addon listing
        if tool_name == "addon_list":
            args = request.params.arguments or {}
            if not isinstance(args, dict):
                args = {}

            q: dict[str, Any] = {}
            addon_type = args.get("type")
            if isinstance(addon_type, str) and addon_type:
                q["type"] = addon_type
            enabled = args.get("enabled")
            if isinstance(enabled, bool):
                # FastAPI will parse "true"/"false" strings. urlencode will emit True/False.
                q["enabled"] = str(enabled).lower()

            query = q or None

        # Argument handling for addon details (required addonid)
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

            query = {"addonid": addonid}

        # Argument handling for JSON-RPC introspection
        if tool_name == "jsonrpc_introspect":
            args = request.params.arguments or {}
            if not isinstance(args, dict):
                args = {}

            # Apply MCP defaults defined in tools/list
            summary = args.get("summary", True)
            getdescriptions = args.get("getdescriptions", False)
            getmetadata = args.get("getmetadata", False)
            filterbytransport = args.get("filterbytransport", False)

            def _as_bool(v: Any, default: bool) -> bool:
                return v if isinstance(v, bool) else default

            query = {
                "summary": str(_as_bool(summary, True)).lower(),
                "getdescriptions": str(_as_bool(getdescriptions, False)).lower(),
                "getmetadata": str(_as_bool(getmetadata, False)).lower(),
                "filterbytransport": str(_as_bool(filterbytransport, False)).lower(),
            }

        # Argument handling for Kodi notifications sampling
        if tool_name == "kodi_notifications_sample":
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

            query = {
                "sample_size": sample_size,
                "listen_seconds": listen_seconds,
            }

        # Argument handling for log marker writing (mutating)
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

            body = {"message": message}

        url = f"{base_url}{path}" + (f"?{urlencode(query)}" if query else "")

        start = time.time()

        envelope: dict[str, Any]
        try:
            data = None
            headers: dict[str, str] = {}
            if body is not None:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"

            http_request = Request(url, method=method, data=data, headers=headers)
            with urlopen(http_request, timeout=10) as resp:
                raw_text = resp.read().decode("utf-8")

            try:
                raw_json: Any = json.loads(raw_text) if raw_text else None
            except Exception as exc:  # JSON parse error
                latency_ms = int((time.time() - start) * 1000)
                envelope = {
                    "ok": False,
                    "tool": tool_name,
                    "data": None,
                    "error": f"invalid JSON response: {exc}",
                    "error_type": "parse_error",
                    "error_code": None,
                    "latency_ms": latency_ms,
                    "request_id": None,
                    "raw": {"text": raw_text},
                }
            else:
                latency_ms = int((time.time() - start) * 1000)

                # Normalize: ResponseMessage vs plain dict
                if isinstance(raw_json, dict) and "result" in raw_json and "error" in raw_json:
                    ok = raw_json.get("error") is None
                    envelope = {
                        "ok": ok,
                        "tool": tool_name,
                        "data": raw_json.get("result"),
                        "error": raw_json.get("error"),
                        "error_type": raw_json.get("error_type"),
                        "error_code": raw_json.get("error_code"),
                        "latency_ms": raw_json.get("latency_ms") or latency_ms,
                        "request_id": raw_json.get("request_id"),
                        "raw": raw_json,
                    }
                else:
                    envelope = {
                        "ok": True,
                        "tool": tool_name,
                        "data": raw_json,
                        "error": None,
                        "error_type": None,
                        "error_code": None,
                        "latency_ms": latency_ms,
                        "request_id": None,
                        "raw": raw_json,
                    }

        except HTTPError as exc:
            latency_ms = int((time.time() - start) * 1000)
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8")
            except Exception:
                body_text = ""

            envelope = {
                "ok": False,
                "tool": tool_name,
                "data": None,
                "error": f"http error {exc.code}: {exc.reason}",
                "error_type": "server_error",
                "error_code": exc.code,
                "latency_ms": latency_ms,
                "request_id": None,
                "raw": {"text": body_text},
            }
        except URLError as exc:
            latency_ms = int((time.time() - start) * 1000)
            envelope = {
                "ok": False,
                "tool": tool_name,
                "data": None,
                "error": f"network error: {exc.reason}",
                "error_type": "network_error",
                "error_code": None,
                "latency_ms": latency_ms,
                "request_id": None,
                "raw": None,
            }
        except (TimeoutError, socket.timeout) as exc:
            latency_ms = int((time.time() - start) * 1000)
            envelope = {
                "ok": False,
                "tool": tool_name,
                "data": None,
                "error": f"timeout: {exc}",
                "error_type": "timeout",
                "error_code": None,
                "latency_ms": latency_ms,
                "request_id": None,
                "raw": None,
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
