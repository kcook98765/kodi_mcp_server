"""Core composition helpers for kodi_mcp_server.

This module intentionally contains *no HTTP/web framework imports*.

It builds tool instances and their underlying transports from the configuration
layer so that MCP / CLI / other adapters can call tool logic directly.
"""

from kodi_mcp_server.config import (
    KODI_BRIDGE_BASE_URL,
    KODI_BRIDGE_TOKEN,
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


def build_jsonrpc_tool(
    *,
    url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    timeout: int | None = None,
) -> JsonRpcTool:
    """Build a JSON-RPC tool, defaulting to the legacy global config."""
    return JsonRpcTool(
        transport=HttpJsonRpcTransport(
            url=KODI_JSONRPC_URL if url is None else url,
            username=KODI_JSONRPC_USERNAME if username is None else username,
            password=KODI_JSONRPC_PASSWORD if password is None else password,
            timeout=KODI_TIMEOUT if timeout is None else timeout,
        )
    )


def build_bridge_tool(
    *,
    base_url: str | None = None,
    timeout: int | None = None,
    token: str | None = None,
) -> BridgeTool:
    """Build a bridge tool, defaulting to the legacy global config."""
    client = HttpBridgeClient(
        base_url=KODI_BRIDGE_BASE_URL if base_url is None else base_url,
        timeout=KODI_TIMEOUT if timeout is None else timeout,
        token=KODI_BRIDGE_TOKEN if token is None else token,
    )
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


def build_notification_probe(
    *,
    tcp_host: str | None = None,
    tcp_port: int | None = None,
    websocket_url: str | None = None,
    timeout: int | None = None,
):
    """Build the Kodi WebSocket notification probe.

    NOTE: Imported lazily so core composition can be imported without the
    optional `websockets` dependency unless this probe is actually used.
    """
    from kodi_mcp_server.transport.websocket_notifications import WebSocketNotificationProbe

    return WebSocketNotificationProbe(
        tcp_host=KODI_TCP_HOST if tcp_host is None else tcp_host,
        tcp_port=KODI_TCP_PORT if tcp_port is None else tcp_port,
        websocket_url=KODI_WEBSOCKET_URL if websocket_url is None else websocket_url,
        timeout=KODI_TIMEOUT if timeout is None else timeout,
    )
