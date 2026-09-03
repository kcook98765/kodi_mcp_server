"""Remote MCP transport (Streamable HTTP + SSE) mounted into FastAPI.

This module provides:
- a lifespan context manager to run StreamableHTTPSessionManager
- an ASGI app callable to mount at /mcp

Security:
- loopback bind is the default and may run without an API key
- remote-capable binds require MCP_API_KEY unless explicitly overridden
- authenticated requests send x-mcp-api-key; query credentials are unsupported
"""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import os
import secrets
import sys
from typing import AsyncIterator, Callable

from fastapi import Response

from kodi_mcp_server.config import ConfigError

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from kodi_mcp_mcp.server_core import build_mcp_server, build_runtime


MCP_API_KEY_ENV = "MCP_API_KEY"
MCP_API_KEY_HEADER = "x-mcp-api-key"
MCP_BIND_HOST_ENV = "MCP_BIND_HOST"
MCP_PORT_ENV = "MCP_PORT"
MCP_ALLOW_INSECURE_REMOTE_ENV = "MCP_ALLOW_INSECURE_REMOTE"
DEFAULT_MCP_BIND_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8010
MAX_MCP_API_KEY_BYTES = 4096

_LOGGER = logging.getLogger("kodi_mcp_server.remote_security")


def _normalized_api_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ConfigError("MCP_API_KEY must be valid UTF-8") from exc
    if len(encoded) > MAX_MCP_API_KEY_BYTES:
        raise ConfigError(
            f"MCP_API_KEY exceeds the {MAX_MCP_API_KEY_BYTES}-byte limit"
        )
    return normalized


def runtime_bind_host(argv: list[str] | None = None) -> str:
    """Resolve configured host, including legacy direct Uvicorn CLI binds."""

    configured = os.getenv(MCP_BIND_HOST_ENV)
    arguments = list(sys.argv if argv is None else argv)
    uvicorn_host = None
    if arguments and "uvicorn" in arguments[0].casefold():
        uvicorn_host = DEFAULT_MCP_BIND_HOST
        for index, argument in enumerate(arguments[1:], start=1):
            if argument.startswith("--host="):
                uvicorn_host = argument.split("=", 1)[1]
                break
            if argument == "--host":
                if index + 1 >= len(arguments):
                    raise ConfigError("Uvicorn --host requires a value")
                uvicorn_host = arguments[index + 1]
                break
    if configured is not None:
        if (
            uvicorn_host is not None
            and configured.strip().casefold() != uvicorn_host.strip().casefold()
        ):
            raise ConfigError(
                f"{MCP_BIND_HOST_ENV} disagrees with Uvicorn --host; "
                "use kodi-mcp-server or make both bind settings identical"
            )
        return configured
    return uvicorn_host or DEFAULT_MCP_BIND_HOST


def is_loopback_bind_host(host: str) -> bool:
    """Classify bind intent without making firewall-reachability claims."""

    normalized = host.strip()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if normalized.rstrip(".").casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _parse_insecure_remote_override(value: str | None) -> bool:
    if value is None or value == "":
        return False
    lowered = value.casefold()
    if lowered == "true":
        return True
    if lowered in {"false", "0"}:
        return False
    raise ConfigError(
        f"{MCP_ALLOW_INSECURE_REMOTE_ENV} must be exactly true or false"
    )


def validate_remote_deployment(
    *,
    bind_host: str,
    api_key: str | None,
    allow_insecure_remote: str | None,
) -> None:
    """Reject unsafe remote-capable configuration before server startup."""

    normalized_key = _normalized_api_key(api_key)
    insecure_override = _parse_insecure_remote_override(allow_insecure_remote)
    if is_loopback_bind_host(bind_host):
        return
    if normalized_key:
        return
    if insecure_override:
        _LOGGER.warning(
            "Insecure remote MCP mode enabled; use only on an explicitly trusted network"
        )
        return
    raise ConfigError(
        f"Remote-capable MCP bind {bind_host!r} requires {MCP_API_KEY_ENV}; "
        f"set a key, bind to loopback, or explicitly set "
        f"{MCP_ALLOW_INSECURE_REMOTE_ENV}=true for a trusted network"
    )


def _unauthorized_response() -> Response:
    return Response(
        status_code=401,
        content="Unauthorized",
        headers={
            "Cache-Control": "no-store",
            "WWW-Authenticate": 'ApiKey realm="mcp"',
        },
    )


def _enforce_api_key(scope) -> Response | None:
    """Return a non-reflective 401 when configured header authentication fails."""

    try:
        expected = _normalized_api_key(os.getenv(MCP_API_KEY_ENV))
    except ConfigError:
        return Response(status_code=503, content="Service unavailable")
    if expected is None:
        return None

    expected_header = MCP_API_KEY_HEADER.encode("ascii")
    values = [
        value
        for name, value in (scope.get("headers") or [])
        if name.lower() == expected_header
    ]
    if len(values) != 1 or len(values[0]) > MAX_MCP_API_KEY_BYTES:
        return _unauthorized_response()
    try:
        provided = values[0].decode("utf-8", "strict").strip().encode("utf-8")
    except UnicodeDecodeError:
        return _unauthorized_response()
    if not secrets.compare_digest(provided, expected.encode("utf-8")):
        return _unauthorized_response()
    return None


def _is_protected_http_path(path: str) -> bool:
    return (
        path == "/mcp"
        or path.startswith("/mcp/")
        or path == "/tools"
        or path.startswith("/tools/")
        or path in {"/status", "/repo-health", "/repo/health"}
    )


class RemoteApiKeyMiddleware:
    """Apply the MCP API-key contract to all sensitive HTTP surfaces."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and _is_protected_http_path(
            scope.get("path", "")
        ):
            unauthorized = _enforce_api_key(scope)
            if unauthorized is not None:
                await unauthorized(scope, receive, send)
                return
        await self.app(scope, receive, send)


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
