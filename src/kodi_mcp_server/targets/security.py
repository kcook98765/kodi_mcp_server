"""Redaction helpers for explicitly targeted public results."""

from __future__ import annotations

import os
from typing import Any, Mapping
from urllib.parse import urlparse

from .model import Target


def _sensitive_values(
    target: Target, environ: Mapping[str, str]
) -> tuple[str, ...]:
    values: set[str] = set()
    for endpoint in (
        target.endpoints.jsonrpc_url,
        target.endpoints.bridge_url,
        target.endpoints.websocket_url,
    ):
        if not endpoint:
            continue
        values.add(endpoint)
        parsed = urlparse(endpoint)
        if parsed.hostname:
            values.add(parsed.hostname)
        if parsed.netloc:
            values.add(parsed.netloc)
    if target.endpoints.tcp_host:
        values.add(target.endpoints.tcp_host)

    for reference in (
        target.auth.jsonrpc_username,
        target.auth.jsonrpc_password,
        target.auth.bridge_token,
    ):
        if not reference:
            continue
        values.add(reference)
        _, variable = reference.split(":", 1)
        values.add(variable)
        resolved = environ.get(variable, "")
        if resolved:
            values.add(resolved)

    return tuple(sorted(values, key=len, reverse=True))


def redact_target_sensitive(
    value: Any,
    target: Target,
    *,
    environ: Mapping[str, str] | None = None,
) -> Any:
    """Recursively remove one target's endpoint and authentication details."""

    sensitive = _sensitive_values(target, os.environ if environ is None else environ)

    def redact(item: Any) -> Any:
        if isinstance(item, str):
            result = item
            for secret in sensitive:
                result = result.replace(secret, "[redacted]")
            return result
        if isinstance(item, dict):
            return {key: redact(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [redact(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact(nested) for nested in item)
        return item

    return redact(value)
