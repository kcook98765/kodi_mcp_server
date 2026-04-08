"""FastAPI app factory for kodi_mcp_server.

This module is the HTTP adapter boundary and is allowed to import FastAPI.
"""

import contextlib
from typing import AsyncIterator

from fastapi import FastAPI

from kodi_mcp_server.remote_mcp_app import create_remote_mcp


def create_base_app() -> FastAPI:
    """Create the shared FastAPI app shell."""

    remote_asgi_app, remote_lifespan = create_remote_mcp()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with remote_lifespan():
            yield

    app = FastAPI(title="Kodi MCP Server", version="0.1.0", lifespan=lifespan)
    # Mount remote MCP at /mcp
    app.mount("/mcp", remote_asgi_app)
    return app
