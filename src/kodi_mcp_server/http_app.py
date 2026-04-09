"""FastAPI app factory for kodi_mcp_server.

This module is the HTTP adapter boundary and is allowed to import FastAPI.
"""

import contextlib
import asyncio
from typing import AsyncIterator

from fastapi import FastAPI

from kodi_mcp_server.remote_mcp_app import create_remote_mcp


async def _addon_registration_loop(*, stop_event: asyncio.Event) -> None:
    """Best-effort background loop to converge and maintain addon registration.

    Goals:
    - converge after startup even if Kodi/token/bridge become ready later
    - keep registration fresh given the addon TTL (default 60s)
    """

    # Import lazily to keep FastAPI adapter light and avoid import-order surprises.
    from kodi_mcp_server.milestone_a_bridge import build_registration_payload, read_addon_state, register_with_addon

    payload = build_registration_payload()

    unhealthy_retry_seconds = 5
    log_cooldown_seconds = 30
    last_log_at = 0.0
    last_health = None

    while not stop_event.is_set():
        healthy = False
        ttl_seconds = 60

        try:
            reg_view, reg_resp = await register_with_addon(payload)

            # transport vs business result separation
            if not reg_view.transport_ok:
                status = f"transport_failed: {reg_resp.error}"
            elif not reg_view.business_ok:
                result = (reg_view.envelope or {}).get("result") if reg_view.envelope else None
                status = f"rejected: {result}"
            else:
                # Verify registration health from addon-side state.
                state_view, state_resp = await read_addon_state()
                if not state_view.transport_ok:
                    status = f"state_transport_failed: {state_resp.error}"
                elif not state_view.business_ok:
                    result = (state_view.envelope or {}).get("result") if state_view.envelope else None
                    status = f"state_rejected: {result}"
                else:
                    result = (state_view.envelope or {}).get("result") if state_view.envelope else {}
                    derived = result.get("derived") if isinstance(result, dict) else None
                    state_obj = result.get("state") if isinstance(result, dict) else None
                    reg_obj = (state_obj or {}).get("registration") if isinstance(state_obj, dict) else None
                    if isinstance(reg_obj, dict):
                        try:
                            ttl_seconds = int(reg_obj.get("applied_ttl_seconds") or ttl_seconds)
                        except Exception:
                            ttl_seconds = ttl_seconds

                    healthy = bool(
                        isinstance(derived, dict)
                        and derived.get("registration_present") is True
                        and derived.get("registration_stale") is False
                    )
                    status = "healthy" if healthy else f"unhealthy: derived={derived}"

        except Exception as exc:
            status = f"error: {exc}"

        now = asyncio.get_running_loop().time()
        if (healthy != last_health) or (now - last_log_at >= log_cooldown_seconds):
            print(f"[kodi_mcp_server] addon registration loop: {status}")
            last_log_at = now
            last_health = healthy

        # Sleep policy:
        # - unhealthy: retry quickly to converge after user fixes token/Kodi state
        # - healthy: refresh before TTL expiry (simple: half TTL, capped at 30s)
        if healthy:
            interval = max(10, min(30, int(ttl_seconds / 2)))
        else:
            interval = unhealthy_retry_seconds

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


def create_base_app() -> FastAPI:
    """Create the shared FastAPI app shell."""

    remote_asgi_app, remote_lifespan = create_remote_mcp()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with remote_lifespan():
            stop_event = asyncio.Event()
            task = asyncio.create_task(_addon_registration_loop(stop_event=stop_event))
            try:
                yield
            finally:
                stop_event.set()
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Kodi MCP Server", version="0.1.0", lifespan=lifespan)
    # Mount remote MCP at /mcp
    app.mount("/mcp", remote_asgi_app)
    return app
