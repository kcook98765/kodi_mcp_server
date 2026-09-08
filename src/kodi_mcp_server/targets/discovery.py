"""Stable, secret-free public target discovery representations."""

from __future__ import annotations

from urllib.parse import urlparse

from .model import Target
from .registry import TargetRegistry
from .resolver import resolve_target

_ENDPOINT_ORDER = ("jsonrpc", "bridge", "websocket", "tcp")


def _configured_endpoint_types(target: Target) -> list[str]:
    configured = {
        "jsonrpc": bool(target.endpoints.jsonrpc_url),
        "bridge": bool(target.endpoints.bridge_url),
        "websocket": bool(target.endpoints.websocket_url),
        "tcp": bool(target.endpoints.tcp_host),
    }
    return [name for name in _ENDPOINT_ORDER if configured[name]]


def _public_summary(target: Target) -> dict[str, object]:
    """Project one internal target into the compact public inventory shape."""

    return {
        "id": target.target_id,
        "name": target.name,
        "groups": list(target.groups),
        "tags": list(target.tags),
        "expected_kodi_version": target.expected_kodi_version,
        "is_default": target.target_id == "default",
        "endpoint_types": _configured_endpoint_types(target),
    }


def _scheme(url: str) -> str | None:
    return urlparse(url).scheme if url else None


def list_public_targets(
    registry: TargetRegistry,
    *,
    group: str | None = None,
    tag: str | None = None,
) -> dict[str, object]:
    """Return the deterministic configured inventory without resolving transports."""

    targets = [
        target
        for target in registry.list()
        if (group is None or group in target.groups)
        and (tag is None or tag in target.tags)
    ]
    return {
        "count": len(targets),
        "filters": {"group": group, "tag": tag},
        "targets": [_public_summary(target) for target in targets],
    }


def get_public_target_info(
    registry: TargetRegistry, target_id: str
) -> dict[str, object]:
    """Return safe detailed configuration metadata for one target."""

    target = resolve_target(registry, target_id)
    return {
        "id": target.target_id,
        "name": target.name,
        "groups": list(target.groups),
        "tags": list(target.tags),
        "expected_kodi_version": target.expected_kodi_version,
        "is_default": target.target_id == "default",
        "timeout_seconds": target.timeout_seconds,
        "transports": {
            "jsonrpc": {
                "configured": bool(target.endpoints.jsonrpc_url),
                "scheme": _scheme(target.endpoints.jsonrpc_url),
            },
            "bridge": {
                "configured": bool(target.endpoints.bridge_url),
                "scheme": _scheme(target.endpoints.bridge_url),
            },
            "websocket": {
                "configured": bool(target.endpoints.websocket_url),
                "scheme": _scheme(target.endpoints.websocket_url),
            },
            "tcp": {"configured": bool(target.endpoints.tcp_host)},
        },
    }
