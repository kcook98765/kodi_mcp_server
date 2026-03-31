"""Shared app initialization and tool-builder helpers for kodi_mcp_server."""

from fastapi import FastAPI

from kodi_mcp_server.config import (
    KODI_BRIDGE_BASE_URL,
    KODI_JSONRPC_PASSWORD,
    KODI_JSONRPC_URL,
    KODI_JSONRPC_USERNAME,
    KODI_TIMEOUT,
    KODI_TCP_HOST,
    KODI_TCP_PORT,
    KODI_WEBSOCKET_URL,
)
from kodi_mcp_server.tools.addon_ops import AddonOpsTool
from kodi_mcp_server.tools.bridge import BridgeTool
from kodi_mcp_server.tools.jsonrpc import JsonRpcTool
from kodi_mcp_server.tools.repo import RepoTool
from kodi_mcp_server.transport.http_bridge import HttpBridgeClient
from kodi_mcp_server.transport.http_jsonrpc import HttpJsonRpcTransport
from kodi_mcp_server.transport.websocket_notifications import WebSocketNotificationProbe


def create_base_app() -> FastAPI:
    """Create the shared FastAPI app shell."""
    return FastAPI(title="Kodi MCP Server", version="0.1.0")


def build_jsonrpc_tool() -> JsonRpcTool:
    """Build a JSON-RPC tool with real transport."""
    return JsonRpcTool(
        transport=HttpJsonRpcTransport(
            url=KODI_JSONRPC_URL,
            username=KODI_JSONRPC_USERNAME,
            password=KODI_JSONRPC_PASSWORD,
            timeout=KODI_TIMEOUT,
        )
    )


def build_bridge_tool() -> BridgeTool:
    """Build a tool client for the minimal Kodi addon bridge."""
    client = HttpBridgeClient(base_url=KODI_BRIDGE_BASE_URL, timeout=KODI_TIMEOUT)
    return BridgeTool(client=client)


def build_repo_tool() -> RepoTool:
    """Build the repo-management tool wrapper."""
    return RepoTool()


def build_addon_ops_tool() -> AddonOpsTool:
    """Build the high-level addon orchestration helper."""
    return AddonOpsTool(
        bridge_tool=build_bridge_tool(),
        jsonrpc_tool=build_jsonrpc_tool(),
    )


def build_notification_probe() -> WebSocketNotificationProbe:
    """Build the Kodi WebSocket notification probe."""
    return WebSocketNotificationProbe(
        tcp_host=KODI_TCP_HOST,
        tcp_port=KODI_TCP_PORT,
        websocket_url=KODI_WEBSOCKET_URL,
        timeout=KODI_TIMEOUT,
    )
