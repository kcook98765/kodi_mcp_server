"""FastAPI app factory for kodi_mcp_server.

This module is the HTTP adapter boundary and is allowed to import FastAPI.
"""

from fastapi import FastAPI


def create_base_app() -> FastAPI:
    """Create the shared FastAPI app shell."""
    return FastAPI(title="Kodi MCP Server", version="0.1.0")
