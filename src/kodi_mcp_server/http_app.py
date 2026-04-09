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

    # Repo zip staging retry policy (first-time onboarding convenience).
    stage_retry_seconds = 60
    stage_retry_max_seconds = 300
    next_stage_attempt_at = 0.0

    while not stop_event.is_set():
        healthy = False
        ttl_seconds = 60
        derived = None
        repo_needs_stage = False

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

                    # If registration is healthy but repo zip isn't ready, attempt to stage it.
                    # This is best-effort and bounded to avoid tight loops.
                    if healthy and isinstance(derived, dict):
                        repo_ready = bool(derived.get("dev_setup_available") is True)
                        repo_present = bool(derived.get("repo_zip_file_exists") is True)
                        repo_needs_stage = (not repo_ready) and (not repo_present)

                        now_mono = asyncio.get_running_loop().time()
                        if repo_needs_stage and now_mono >= next_stage_attempt_at:
                            try:
                                from kodi_mcp_server.managed_addons import build_and_stage_dev_repo_zip
                                from kodi_mcp_server.paths import AUTHORITATIVE_REPO_ROOT

                                # Ensure dev-repo directory exists for first-time onboarding.
                                # (build_dev_repo_zip requires repo/dev-repo to exist.)
                                dev_repo_dir = AUTHORITATIVE_REPO_ROOT / "dev-repo"
                                if not dev_repo_dir.exists():
                                    dev_repo_dir.mkdir(parents=True, exist_ok=True)
                                addons_xml_path = dev_repo_dir / "addons.xml"
                                if not addons_xml_path.exists():
                                    addons_xml_path.write_text(
                                        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                                        '<addons>\n'
                                        '</addons>\n',
                                        encoding="utf-8",
                                    )

                                print("[kodi_mcp_server] repo zip staging: starting")
                                stage_out = await build_and_stage_dev_repo_zip(verify=True)
                                stage_result = (stage_out or {}).get("addon_stage_result") or {}
                                state_view = ((stage_result.get("state") or {}) if isinstance(stage_result, dict) else {})
                                dev_ready_after = bool(isinstance(state_view, dict) and state_view.get("dev_setup_available"))

                                if dev_ready_after:
                                    print("[kodi_mcp_server] repo zip staging: ok")
                                    stage_retry_seconds = 60
                                    next_stage_attempt_at = now_mono + stage_retry_seconds
                                else:
                                    print("[kodi_mcp_server] repo zip staging: attempted but not ready yet")
                                    stage_retry_seconds = min(stage_retry_max_seconds, stage_retry_seconds * 2)
                                    next_stage_attempt_at = now_mono + stage_retry_seconds
                            except Exception as exc:
                                print(f"[kodi_mcp_server] repo zip staging: failed: {exc}")
                                stage_retry_seconds = min(stage_retry_max_seconds, stage_retry_seconds * 2)
                                next_stage_attempt_at = now_mono + stage_retry_seconds

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

        # If registration is healthy but we still need staging, wake up sooner so
        # onboarding converges quickly (but still bounded by retry/backoff).
        if healthy and repo_needs_stage:
            interval = min(interval, 10)

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
