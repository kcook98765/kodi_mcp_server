"""Build the registry-aware runtime while preserving legacy runtime keys."""

from __future__ import annotations

from typing import Any

from kodi_mcp_server import config

from .registry import LegacyTargetSettings, TargetRegistry
from .transport_pool import ResolvedTargetAuth, TargetTransportPool


def build_target_runtime() -> dict[str, Any]:
    """Build target foundations and expose the legacy default transport view."""

    registry = TargetRegistry.from_sources(
        legacy=LegacyTargetSettings(
            jsonrpc_url=config.KODI_JSONRPC_URL,
            bridge_url=config.KODI_BRIDGE_BASE_URL,
            websocket_url=config.KODI_WEBSOCKET_URL,
            tcp_host=config.KODI_TCP_HOST,
            tcp_port=config.KODI_TCP_PORT,
            timeout_seconds=config.KODI_TIMEOUT,
        ),
        targets_file=config.KODI_MCP_TARGETS_FILE,
        targets_json=config.KODI_MCP_TARGETS,
    )
    transport_pool = TargetTransportPool(
        registry,
        legacy_default_auth=ResolvedTargetAuth(
            jsonrpc_username=config.KODI_JSONRPC_USERNAME,
            jsonrpc_password=config.KODI_JSONRPC_PASSWORD,
            bridge_token=config.KODI_BRIDGE_TOKEN,
        ),
    )
    default = transport_pool.get("default")
    return {
        "registry": registry,
        "transport_pool": transport_pool,
        "bridge": default.bridge,
        "jsonrpc": default.jsonrpc,
        "notifications": default.notifications,
    }
