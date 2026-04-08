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
            # Milestone A: best-effort addon registration on server startup.
            # This is intentionally non-fatal so the server can still run even if
            # Kodi is offline or the bridge token is not yet configured.
            try:
                from kodi_mcp_server.milestone_a_bridge import build_registration_payload, register_with_addon

                payload = build_registration_payload()
                envelope_view, resp = await register_with_addon(payload)
                if not envelope_view.transport_ok:
                    # likely network error or response not parseable
                    print(f"[kodi_mcp_server] addon registration transport failed: {resp.error}")
                elif not envelope_view.business_ok:
                    # transport ok, but addon rejected or unauthorized
                    result = (envelope_view.envelope or {}).get("result") if envelope_view.envelope else None
                    print(f"[kodi_mcp_server] addon registration rejected: {result}")
                else:
                    state_rev = ((envelope_view.envelope or {}).get("result") or {}).get("state_rev")
                    print(f"[kodi_mcp_server] addon registration ok (state_rev={state_rev})")
            except Exception as exc:
                print(f"[kodi_mcp_server] addon registration skipped due to error: {exc}")
            yield

    app = FastAPI(title="Kodi MCP Server", version="0.1.0", lifespan=lifespan)
    # Mount remote MCP at /mcp
    app.mount("/mcp", remote_asgi_app)
    return app
