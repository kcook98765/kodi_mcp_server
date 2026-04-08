"""Remote MCP transport (Streamable HTTP + SSE) mounted into FastAPI.

This module provides:
- a lifespan context manager to run StreamableHTTPSessionManager
- an ASGI app callable to mount at /mcp

Security:
- Optional API key via env var MCP_API_KEY
  - If set, clients must send header: x-mcp-api-key: <key>
"""

from __future__ import annotations

import contextlib
import os
from typing import AsyncIterator, Callable

from fastapi import Response

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from kodi_mcp_mcp.server_core import build_mcp_server, build_runtime


MCP_API_KEY_ENV = "MCP_API_KEY"
MCP_API_KEY_HEADER = "x-mcp-api-key"


def _enforce_api_key(scope) -> Response | None:
    """Return a 401 Response if an API key is configured and missing/invalid."""

    expected = os.getenv(MCP_API_KEY_ENV)
    if not expected:
        return None

    # Be tolerant of accidental trailing whitespace in env var values
    # (common when using `cmd.exe set VAR=value && ...`).
    expected = expected.strip()

    # ASGI headers are (bytes, bytes). Some servers preserve original casing,
    # so we normalize header names to lowercase for case-insensitive lookup.
    headers = {k.lower(): v for (k, v) in (scope.get("headers") or [])}
    provided = headers.get(MCP_API_KEY_HEADER.encode("ascii"), b"").decode("utf-8", "ignore").strip()
    if provided != expected:
        return Response(status_code=401, content="Unauthorized")

    return None


def create_remote_mcp() -> tuple[
    Callable,
    Callable[[], contextlib.AbstractAsyncContextManager[None]],
]:
    """Create the remote MCP ASGI app + lifespan runner.

    Returns:
        (asgi_app, lifespan_cm_factory)

    Notes:
        StreamableHTTPSessionManager.run() must be entered exactly once per
        manager instance; therefore we create it once and expose a lifespan
        context manager factory for FastAPI.
    """

    runtime = build_runtime()
    server, _ = build_mcp_server(runtime)

    session_manager = StreamableHTTPSessionManager(
        app=server,
        event_store=None,
        json_response=False,
        stateless=False,
        security_settings=None,
        retry_interval=None,
        session_idle_timeout=None,
    )

    async def asgi_app(scope, receive, send) -> None:
        # Optional API key enforcement (applies only to this mounted /mcp app).
        unauthorized = _enforce_api_key(scope)
        if unauthorized is not None:
            await unauthorized(scope, receive, send)
            return

        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan() -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    return asgi_app, lifespan
