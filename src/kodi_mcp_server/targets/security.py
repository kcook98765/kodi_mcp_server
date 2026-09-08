"""Redaction helpers for explicitly targeted public results."""

from __future__ import annotations

import os
from typing import Any, Mapping
from urllib.parse import urlparse

from .model import Target


_CREDENTIAL_FIELDS = frozenset(
    {
        "auth_reference",
        "authorization",
        "bridge_token",
        "credential",
        "credentials",
        "jsonrpc_password",
        "jsonrpc_username",
        "password",
        "secret",
        "token",
        "username",
        "x_kodi_mcp_token",
    }
)
_LOCATION_FIELDS = frozenset(
    {
        "bridge_url",
        "endpoint",
        "endpoint_url",
        "host",
        "hostname",
        "jsonrpc_url",
        "netloc",
        "tcp_host",
        "url",
        "websocket_url",
    }
)
_ERROR_TEXT_FIELDS = frozenset(
    {"description", "detail", "diagnostic", "error", "message", "reason"}
)


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
    """Remove target secrets only from fields that may legitimately carry them.

    Structural and identity strings are deliberately opaque to this sanitizer.
    Short credentials must never be substituted across tool names, target/Kodi
    identity, addon IDs, versions, or other ordinary protocol values.
    """

    sensitive = _sensitive_values(target, os.environ if environ is None else environ)

    def redact_text(item: str) -> str:
        result = item
        for secret in sensitive:
            result = result.replace(secret, "[redacted]")
        return result

    def redact_field(item: Any) -> Any:
        if isinstance(item, str):
            return "[redacted]" if item else item
        if isinstance(item, dict):
            return {key: redact_field(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [redact_field(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact_field(nested) for nested in item)
        return item

    def redact(item: Any, *, in_error: bool = False) -> Any:
        if isinstance(item, str):
            return redact_text(item) if in_error else item
        if isinstance(item, dict):
            sanitized: dict[Any, Any] = {}
            for key, nested in item.items():
                normalized = str(key).lower().replace("-", "_")
                credential_field = normalized in _CREDENTIAL_FIELDS or normalized.endswith(
                    ("_password", "_secret", "_token", "_username")
                )
                location_field = normalized in _LOCATION_FIELDS or normalized.endswith(
                    ("_endpoint", "_host", "_hostname", "_url")
                )
                if credential_field:
                    sanitized[key] = redact_field(nested)
                elif location_field:
                    sanitized[key] = redact(nested, in_error=True)
                else:
                    error_field = normalized in _ERROR_TEXT_FIELDS or (
                        normalized.endswith("_error")
                        and normalized not in {"error_code", "error_type"}
                    )
                    sanitized[key] = redact(nested, in_error=error_field)
            return sanitized
        if isinstance(item, list):
            return [redact(nested, in_error=in_error) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact(nested, in_error=in_error) for nested in item)
        return item

    return redact(value)
