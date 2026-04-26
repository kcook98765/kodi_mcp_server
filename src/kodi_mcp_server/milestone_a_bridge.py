"""Milestone A bridge integration helpers.

This module is intentionally small and focused:
- Configure/auth to the Kodi addon bridge (service.kodi_mcp)
- POST /mcp/register
- GET /mcp/state
- POST /repo/stage (stage dev repo zip)

It does not redesign tool contracts or introduce new server components.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kodi_mcp_server import __version__
from kodi_mcp_server.config import KODI_BRIDGE_BASE_URL, KODI_BRIDGE_TOKEN, KODI_TIMEOUT, REPO_BASE_URL
from kodi_mcp_server.transport.http_bridge import HttpBridgeClient
from kodi_mcp_server.models.messages import ErrorType


@dataclass
class EnvelopeResult:
    """Helper view of the addon standard envelope."""

    transport_ok: bool
    business_ok: bool
    envelope: dict[str, Any] | None


def _parse_envelope(payload: Any) -> EnvelopeResult:
    if not isinstance(payload, dict):
        return EnvelopeResult(transport_ok=False, business_ok=False, envelope=None)
    transport = payload.get("transport")
    result = payload.get("result")
    transport_ok = bool(isinstance(transport, dict) and transport.get("ok") is True)
    business_ok = bool(isinstance(result, dict) and result.get("ok") is True)
    return EnvelopeResult(transport_ok=transport_ok, business_ok=business_ok, envelope=payload)


def build_bridge_client() -> HttpBridgeClient:
    """Build an HttpBridgeClient with token auth if configured."""

    return HttpBridgeClient(base_url=KODI_BRIDGE_BASE_URL, timeout=KODI_TIMEOUT, token=KODI_BRIDGE_TOKEN)


def build_registration_payload(
    *,
    server_id: str = "kodi-mcp",
    ttl_seconds: int = 60,
    repo_zip_staging: bool = True,
) -> dict[str, Any]:
    """Build the /mcp/register payload as defined by the Milestone A spec."""

    started_at = int(time.time())
    server_base_url = REPO_BASE_URL.rstrip("/")
    return {
        "control_api_version": 1,
        "server_id": server_id,
        "server_instance_id": str(uuid.uuid4()),
        "server_base_url": server_base_url,
        "mcp_endpoint_url": f"{server_base_url}/mcp",
        "server_version": __version__,
        "started_at": started_at,
        "ttl_seconds": ttl_seconds,
        "features": {"repo_zip_staging": bool(repo_zip_staging)},
    }


async def register_with_addon(payload: dict[str, Any]) -> tuple[EnvelopeResult, Any]:
    """Call POST /mcp/register and return (envelope_view, raw_response_message)."""

    client = build_bridge_client()
    resp = await client.mcp_register(payload)
    return _parse_envelope(resp.result), resp


async def read_addon_state() -> tuple[EnvelopeResult, Any]:
    """Call GET /mcp/state and return (envelope_view, raw_response_message)."""

    client = build_bridge_client()
    resp = await client.mcp_state()
    return _parse_envelope(resp.result), resp


def compute_sha256(path: str | Path) -> str:
    """Compute SHA-256 for a file in a streaming manner."""

    p = Path(path)
    sha = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            chunk = f.read(64 * 1024)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


async def stage_dev_repo_zip(
    *,
    zip_path: str,
    repo_version: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Stage a dev repo zip via the addon bridge.

    Input:
        zip_path: already-built zip file path
        repo_version: optional informational version string
        verify: if True, calls GET /mcp/state afterward and returns dev_setup_available
    """

    sha256 = compute_sha256(zip_path)
    client = build_bridge_client()
    upload_resp = await client.repo_stage_upload(
        repo_id="dev-repo",
        zip_path=zip_path,
        mode="overwrite",
        repo_version=repo_version,
        sha256=sha256,
    )

    envelope_view = _parse_envelope(upload_resp.result)
    transport_ok = upload_resp.error is None or upload_resp.error_type not in (
        ErrorType.NETWORK_ERROR,
        ErrorType.TIMEOUT,
    )

    out: dict[str, Any] = {
        "upload": {
            "transport_ok": transport_ok,
            "error": upload_resp.error,
            "envelope": upload_resp.result,
            "envelope_view": envelope_view.__dict__,
        },
        "sha256": sha256,
    }

    if verify:
        state_view, state_resp = await read_addon_state()
        derived = None
        install_hint = None
        if state_resp.error is None and isinstance((state_resp.result or {}).get("result"), dict):
            result = state_resp.result.get("result") or {}
            derived = result.get("derived")
            install_hint = result.get("install_hint")
        out["state"] = {
            "transport_ok": state_resp.error is None,
            "error": state_resp.error,
            "envelope_view": state_view.__dict__,
            "derived": derived,
            "install_hint": install_hint,
            "dev_setup_available": bool(isinstance(derived, dict) and derived.get("dev_setup_available")),
        }

    return out
