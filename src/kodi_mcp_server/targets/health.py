"""Read-only, per-target transport health probing."""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from .model import Target
from .transport_pool import TargetTransports


def _response_value(response: Any, field: str) -> Any:
    if isinstance(response, dict):
        return response.get(field)
    return getattr(response, field, None)


def _error_type(response: Any) -> str:
    value = _response_value(response, "error_type")
    value = getattr(value, "value", value)
    return value if isinstance(value, str) and value else "operation_failed"


async def _probe(
    channel: str,
    operation: Callable[[], Awaitable[Any]],
    *,
    healthy: Callable[[Any], bool] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = await operation()
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        response_latency = _response_value(response, "latency_ms")
        if isinstance(response_latency, int) and not isinstance(response_latency, bool):
            latency_ms = max(0, response_latency)
        error = _response_value(response, "error")
        is_healthy = error is None and (healthy(response) if healthy else True)
        if is_healthy:
            return {
                "configured": True,
                "status": "healthy",
                "latency_ms": latency_ms,
                "error": None,
            }
        return {
            "configured": True,
            "status": "unhealthy",
            "latency_ms": latency_ms,
            "error": {
                "type": _error_type(response),
                "code": _response_value(response, "error_code"),
                "message": f"{channel} probe failed",
            },
        }
    except Exception:
        return {
            "configured": True,
            "status": "unhealthy",
            "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
            "error": {
                "type": "operation_failed",
                "code": None,
                "message": f"{channel} probe failed",
            },
        }


def _not_configured() -> dict[str, Any]:
    return {
        "configured": False,
        "status": "not_configured",
        "latency_ms": None,
        "error": None,
    }


def _websocket_connected(response: Any) -> bool:
    result = _response_value(response, "result")
    return isinstance(result, dict) and result.get("connected") is True


async def probe_target_health(
    target: Target, transports: TargetTransports
) -> dict[str, Any]:
    """Probe one registered target without changing routing or registry state."""

    channels = {
        "jsonrpc": (
            await _probe("jsonrpc", transports.jsonrpc.get_jsonrpc_version)
            if target.endpoints.jsonrpc_url
            else _not_configured()
        ),
        "bridge": (
            await _probe("bridge", transports.bridge.get_bridge_health)
            if target.endpoints.bridge_url
            else _not_configured()
        ),
    }
    websocket_configured = bool(
        target.endpoints.websocket_url or target.endpoints.tcp_host
    )
    if not websocket_configured:
        channels["websocket"] = _not_configured()
    elif transports.notifications is None:
        channels["websocket"] = {
            "configured": True,
            "status": "unhealthy",
            "latency_ms": 0,
            "error": {
                "type": "config_error",
                "code": None,
                "message": "websocket probe failed",
            },
        }
    else:
        notifications = transports.notifications
        channels["websocket"] = await _probe(
            "websocket",
            lambda: notifications.listen(sample_size=1, listen_seconds=0),
            healthy=_websocket_connected,
        )

    configured_statuses = [
        channel["status"]
        for channel in channels.values()
        if channel["configured"]
    ]
    if configured_statuses and all(status == "healthy" for status in configured_statuses):
        overall_status = "healthy"
    elif any(status == "healthy" for status in configured_statuses):
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    return {
        "target_id": target.target_id,
        "target_name": target.name,
        "expected_kodi_version": target.expected_kodi_version,
        "overall_status": overall_status,
        "channels": channels,
    }
