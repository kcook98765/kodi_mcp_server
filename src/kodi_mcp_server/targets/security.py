"""Redaction helpers for explicitly targeted public results."""

from __future__ import annotations

import os
import re
from typing import Any, Mapping
from urllib.parse import unquote_plus, urlparse, urlsplit, urlunsplit

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
        "address",
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
_PORT_FIELDS = frozenset({"bind_port", "port"})
_ERROR_TEXT_FIELDS = frozenset(
    {"description", "detail", "diagnostic", "error", "message", "reason"}
)
_ARGUMENT_SECRET_FIELDS = (
    _CREDENTIAL_FIELDS
    | frozenset({"access_token", "api_key", "auth", "passwd"})
) - frozenset({"jsonrpc_username", "username"})
_NOTIFICATION_CREDENTIAL_FIELDS = frozenset(
    {
        "access_token",
        "access_tokens",
        "api_key",
        "api_keys",
        "auth",
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "passwd",
        "password",
        "passwords",
        "secret",
        "secrets",
        "set_cookie",
        "token",
        "tokens",
    }
)
_NOTIFICATION_CREDENTIAL_SUFFIXES = (
    "_access_token",
    "_access_tokens",
    "_api_key",
    "_api_keys",
    "_auth",
    "_authorization",
    "_cookie",
    "_cookies",
    "_credential",
    "_credentials",
    "_passwd",
    "_password",
    "_passwords",
    "_secret",
    "_secrets",
    "_set_cookie",
    "_token",
    "_tokens",
)
_REDACTED_QUERY_VALUE = "%5Bredacted%5D"
_ACRONYM_WORD_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_LOWER_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")
_URI_WITH_SLASHES = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")


def _normalized_security_key(key: Any) -> str:
    value = _ACRONYM_WORD_BOUNDARY.sub("_", str(key))
    value = _LOWER_WORD_BOUNDARY.sub("_", value)
    return _NON_WORD_SEPARATOR.sub("_", value).strip("_").lower()


def _is_notification_credential_key(key: Any) -> bool:
    normalized = _normalized_security_key(key)
    return normalized in _NOTIFICATION_CREDENTIAL_FIELDS or normalized.endswith(
        _NOTIFICATION_CREDENTIAL_SUFFIXES
    )


def _is_notification_url(value: str) -> bool:
    match = _URI_WITH_SLASHES.match(value)
    return match is not None and len(match.group(1)) > 1


def _sanitize_notification_url(value: str) -> str:
    """Redact credential query values and remove all URL fragments."""
    if not _is_notification_url(value):
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value

    netloc = parsed.netloc.rsplit("@", 1)[-1]
    query_parts = []
    for part in parsed.query.split("&") if parsed.query else []:
        key, separator, nested = part.partition("=")
        if _is_notification_credential_key(unquote_plus(key)):
            query_parts.append(f"{key}={_REDACTED_QUERY_VALUE}")
        else:
            query_parts.append(f"{key}{separator}{nested}")
    sanitized_query = "&".join(query_parts)
    if not parsed.netloc:
        prefix = _URI_WITH_SLASHES.match(value)
        assert prefix is not None
        return (
            f"{prefix.group(0)}{parsed.path}"
            f"{'?' + sanitized_query if sanitized_query else ''}"
        )
    return urlunsplit(
        (parsed.scheme, netloc, parsed.path, sanitized_query, "")
    )


def sanitize_notification_event_payload(value: Any) -> Any:
    """Return a credential-sanitized copy of a notification event payload.

    Location and provenance fields remain visible. Credential fields and URL
    credential components are redacted recursively without global substitution.
    """

    def redact_credential(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: redact_credential(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [redact_credential(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact_credential(nested) for nested in item)
        if item is None or item == "":
            return item
        return "[redacted]"

    def sanitize(item: Any) -> Any:
        if isinstance(item, str):
            return _sanitize_notification_url(item)
        if isinstance(item, dict):
            return {
                key: (
                    redact_credential(nested)
                    if _is_notification_credential_key(key)
                    else sanitize(nested)
                )
                for key, nested in item.items()
            }
        if isinstance(item, list):
            return [sanitize(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(sanitize(nested) for nested in item)
        return item

    return sanitize(value)


def redact_unresolved_sensitive_arguments(value: Any) -> Any:
    """Return a field-sanitized copy without requiring target resolution."""

    def redact_field(item: Any) -> Any:
        if isinstance(item, str):
            return "[redacted]" if item else item
        if isinstance(item, dict):
            return {key: redact_field(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [redact_field(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact_field(nested) for nested in item)
        return "[redacted]" if item is not None else item

    def is_url_shaped(item: Any) -> bool:
        if not isinstance(item, str):
            return False
        parsed = urlparse(item)
        return bool(parsed.scheme and parsed.netloc)

    def redact(item: Any) -> Any:
        if isinstance(item, dict):
            sanitized: dict[Any, Any] = {}
            for key, nested in item.items():
                normalized = str(key).lower().replace("-", "_")
                secret_field = normalized in _ARGUMENT_SECRET_FIELDS or normalized.endswith(
                    (
                        "_auth",
                        "_api_key",
                        "_authorization",
                        "_credential",
                        "_credentials",
                        "_passwd",
                        "_password",
                        "_secret",
                        "_token",
                    )
                )
                location_field = normalized in _LOCATION_FIELDS or normalized.endswith(
                    ("_address", "_endpoint", "_host", "_hostname", "_url")
                )
                port_field = normalized in _PORT_FIELDS or normalized.endswith("_port")
                malformed_target = normalized == "target" and is_url_shaped(nested)
                if secret_field or location_field or port_field or malformed_target:
                    sanitized[key] = redact_field(nested)
                else:
                    sanitized[key] = redact(nested)
            return sanitized
        if isinstance(item, list):
            return [redact(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact(nested) for nested in item)
        return item

    return redact(value)


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
        if item is not None:
            return "[redacted]"
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
                    ("_address", "_endpoint", "_host", "_hostname", "_url")
                )
                port_field = normalized in _PORT_FIELDS or normalized.endswith("_port")
                if credential_field:
                    sanitized[key] = redact_field(nested)
                elif location_field or port_field:
                    sanitized[key] = redact_field(nested)
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
