"""Core composition helpers for kodi_mcp_server.

This module intentionally contains *no HTTP/web framework imports*.

It builds tool instances and their underlying transports from the configuration
layer so that MCP / CLI / other adapters can call tool logic directly.
"""

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
from kodi_mcp_server.tools.service_ops import ServiceOpsTool
from kodi_mcp_server.transport.http_bridge import HttpBridgeClient
from kodi_mcp_server.transport.http_jsonrpc import HttpJsonRpcTransport


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


def build_service_ops_tool() -> ServiceOpsTool:
    """Build the service-addon operations helper."""
    return ServiceOpsTool(bridge_client=build_bridge_tool())


def build_notification_probe():
    """Build the Kodi WebSocket notification probe.

    NOTE: Imported lazily so core composition can be imported without the
    optional `websockets` dependency unless this probe is actually used.
    """
    from kodi_mcp_server.transport.websocket_notifications import WebSocketNotificationProbe

    return WebSocketNotificationProbe(
        tcp_host=KODI_TCP_HOST,
        tcp_port=KODI_TCP_PORT,
        websocket_url=KODI_WEBSOCKET_URL,
        timeout=KODI_TIMEOUT,
    )
