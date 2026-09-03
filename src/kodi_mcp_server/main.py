"""Kodi MCP Server composition layer."""

import os

from kodi_mcp_server.bootstrap_app import configure_bootstrap_app
from kodi_mcp_server.http_app import create_base_app
from kodi_mcp_server.config import ConfigError, validate_config
from kodi_mcp_server.mcp_app import configure_mcp_app
from kodi_mcp_server.remote_mcp_app import (
    DEFAULT_MCP_BIND_HOST,
    DEFAULT_MCP_PORT,
    MCP_ALLOW_INSECURE_REMOTE_ENV,
    MCP_API_KEY_ENV,
    MCP_BIND_HOST_ENV,
    MCP_PORT_ENV,
    validate_remote_deployment,
)
from kodi_mcp_server.repo_app import configure_repo_app

app = create_base_app()
configure_repo_app(app)
configure_bootstrap_app(app)
configure_mcp_app(app)


def _configured_port() -> int:
    raw_port = os.getenv(MCP_PORT_ENV, str(DEFAULT_MCP_PORT))
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ConfigError(f"{MCP_PORT_ENV} must be an integer from 1 to 65535") from exc
    if not 1 <= port <= 65535:
        raise ConfigError(f"{MCP_PORT_ENV} must be an integer from 1 to 65535")
    return port


def main():
    """Run the supported HTTP entry point with pre-bind security validation."""
    validate_config()
    bind_host = os.getenv(MCP_BIND_HOST_ENV, DEFAULT_MCP_BIND_HOST)
    port = _configured_port()
    validate_remote_deployment(
        bind_host=bind_host,
        api_key=os.getenv(MCP_API_KEY_ENV),
        allow_insecure_remote=os.getenv(MCP_ALLOW_INSECURE_REMOTE_ENV),
    )
    app.state.remote_deployment_validated = True
    import uvicorn
    uvicorn.run(app, host=bind_host, port=port)


if __name__ == "__main__":
    main()
