"""Kodi MCP Server composition layer."""

from kodi_mcp_server.app_shared import create_base_app
from kodi_mcp_server.config import validate_config
from kodi_mcp_server.mcp_app import configure_mcp_app
from kodi_mcp_server.repo_app import configure_repo_app

app = create_base_app()
configure_repo_app(app)
configure_mcp_app(app)


def main():
    """Entry point."""
    validate_config()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
