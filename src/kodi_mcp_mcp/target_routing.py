"""Stateless per-call target routing helpers for the MCP adapter."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from kodi_mcp_server.targets.model import Target
from kodi_mcp_server.targets.resolver import resolve_target
from kodi_mcp_server.targets.security import (
    redact_target_sensitive,
    sanitize_notification_event_payload,
)
from kodi_mcp_server.targets.transport_pool import TargetTransports


TARGET_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9._-]{0,63})$"
TARGET_INPUT_PROPERTY: dict[str, Any] = {
    "type": "string",
    "pattern": TARGET_ID_PATTERN,
}


@dataclass(frozen=True)
class TargetContext:
    """Immutable transport snapshot for one MCP tool call."""

    explicit: bool
    target: Target | None
    transports: TargetTransports

    @property
    def jsonrpc(self) -> Any:
        return self.transports.jsonrpc

    @property
    def bridge(self) -> Any:
        return self.transports.bridge

    @property
    def notifications(self) -> Any | None:
        return self.transports.notifications


def schema_with_optional_target(schema: dict[str, Any]) -> dict[str, Any]:
    """Return an isolated object schema with the canonical target property."""

    routed = deepcopy(schema)
    if routed.get("type") != "object":
        raise ValueError("target-aware tool schema must have object type")
    if routed.get("additionalProperties") is not False:
        raise ValueError("target-aware tool schema must reject additional properties")
    properties = routed.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("target-aware tool schema must define object properties")
    existing = properties.get("target")
    if existing is not None and existing != TARGET_INPUT_PROPERTY:
        raise ValueError("target-aware tool has a conflicting target schema")
    properties["target"] = deepcopy(TARGET_INPUT_PROPERTY)
    return routed


def resolve_target_context(
    runtime: Mapping[str, Any], arguments: Mapping[str, Any]
) -> TargetContext:
    """Resolve one explicit target or preserve the exact legacy transports."""

    if "target" not in arguments:
        return TargetContext(
            explicit=False,
            target=None,
            transports=TargetTransports(
                jsonrpc=runtime["jsonrpc"],
                bridge=runtime["bridge"],
                notifications=runtime.get("notifications"),
            ),
        )

    target_id = arguments["target"]
    if not isinstance(target_id, str):
        raise ValueError("target must be a string")
    target = resolve_target(runtime["registry"], target_id)
    transports = runtime["transport_pool"].get_for_target(target)
    return TargetContext(explicit=True, target=target, transports=transports)


def finalize_target_envelope(
    envelope: dict[str, Any], context: TargetContext
) -> dict[str, Any]:
    """Redact and attribute an explicitly routed result without schema drift."""

    if not context.explicit or context.target is None:
        return envelope
    routed = redact_target_sensitive(envelope, context.target)
    raw = routed.get("raw")
    raw = dict(raw) if isinstance(raw, dict) else {"response": raw}
    raw["target_id"] = context.target.target_id
    raw["target_name"] = context.target.name
    routed["raw"] = raw
    return routed


_ORCHESTRATION_LOCATION_TEXT = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]+://|[A-Za-z]:[\\/]|(?<![A-Za-z0-9._~/-])/(?=[A-Za-z0-9._~-])|(?:^|[\s:=('\"\[,])(?:\\\\|~/))"
)
_ORCHESTRATION_HOST_PORT_TEXT = re.compile(
    r"""
    (?<![A-Za-z0-9._-])
    (?:
        \[(?=[0-9A-Fa-f:]*:)[0-9A-Fa-f:]+\]
        |
        (?=[A-Za-z0-9-]{1,63}:)(?=[A-Za-z0-9-]*[A-Za-z])
        [A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?
        |
        [A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?
        (?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+
    )
    :[0-9]{1,5}(?![A-Za-z0-9])
    """,
    re.IGNORECASE | re.VERBOSE,
)
_ORCHESTRATION_LOCATION_FIELDS = frozenset(
    {
        "address",
        "directory",
        "endpoint",
        "host",
        "hostname",
        "location",
        "netloc",
        "path",
        "port",
        "repo_root",
        "url",
    }
)
_ORCHESTRATION_CREDENTIAL_FIELDS = frozenset(
    {
        "access_token",
        "api_key",
        "auth",
        "authorization",
        "credential",
        "credentials",
        "passwd",
        "password",
        "secret",
        "token",
        "username",
    }
)
_ORCHESTRATION_DIAGNOSTIC_FIELDS = frozenset(
    {"description", "detail", "diagnostic", "error", "message", "reason"}
)


def finalize_orchestration_target_envelope(
    envelope: dict[str, Any], context: TargetContext
) -> dict[str, Any]:
    """Redact explicit orchestration locations, then add safe target identity."""

    if not context.explicit or context.target is None:
        return envelope

    def redact_field(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: redact_field(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [redact_field(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact_field(nested) for nested in item)
        return item if item in (None, "") else "[redacted]"

    def redact_provenance(item: Any, *, diagnostic: bool = False) -> Any:
        if isinstance(item, str):
            return (
                "[redacted]"
                if _ORCHESTRATION_LOCATION_TEXT.search(item)
                or (diagnostic and _ORCHESTRATION_HOST_PORT_TEXT.search(item))
                else item
            )
        if isinstance(item, dict):
            sanitized: dict[Any, Any] = {}
            for key, nested in item.items():
                normalized = str(key).lower().replace("-", "_")
                location_field = normalized in _ORCHESTRATION_LOCATION_FIELDS or normalized.endswith(
                    (
                        "_address",
                        "_directory",
                        "_dir",
                        "_endpoint",
                        "_host",
                        "_hostname",
                        "_location",
                        "_path",
                        "_port",
                        "_root",
                        "_url",
                    )
                )
                credential_field = normalized in _ORCHESTRATION_CREDENTIAL_FIELDS or normalized.endswith(
                    (
                        "_api_key",
                        "_auth",
                        "_authorization",
                        "_credential",
                        "_credentials",
                        "_passwd",
                        "_password",
                        "_secret",
                        "_token",
                        "_username",
                    )
                )
                diagnostic_field = normalized in _ORCHESTRATION_DIAGNOSTIC_FIELDS or (
                    normalized.endswith("_error")
                    and normalized not in {"error_code", "error_type"}
                )
                sanitized[key] = (
                    redact_field(nested)
                    if location_field or credential_field
                    else redact_provenance(
                        nested, diagnostic=diagnostic or diagnostic_field
                    )
                )
            return sanitized
        if isinstance(item, list):
            return [redact_provenance(nested, diagnostic=diagnostic) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact_provenance(nested, diagnostic=diagnostic) for nested in item)
        return item

    prepared = redact_provenance(deepcopy(envelope))
    return finalize_target_envelope(prepared, context)


def finalize_notification_target_envelope(
    envelope: dict[str, Any], context: TargetContext
) -> dict[str, Any]:
    """Strictly redact transport metadata while preserving safe event provenance."""

    if not context.explicit or context.target is None:
        return envelope

    prepared = deepcopy(envelope)
    data = prepared.get("data")
    raw = prepared.get("raw")
    raw_result = raw.get("result") if isinstance(raw, dict) else None
    data_has_messages = isinstance(data, dict) and "messages" in data
    raw_has_messages = isinstance(raw_result, dict) and "messages" in raw_result
    event_messages = (
        data.get("messages")
        if data_has_messages
        else raw_result.get("messages") if raw_has_messages else None
    )

    if data_has_messages:
        data["messages"] = []
    if raw_has_messages:
        raw_result["messages"] = []

    def redact_transport_value(item: Any) -> Any:
        if isinstance(item, str):
            return "[redacted]" if item else item
        if isinstance(item, dict):
            return {key: redact_transport_value(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [redact_transport_value(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact_transport_value(nested) for nested in item)
        return "[redacted]" if item is not None else item

    def redact_transport_locations(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                key: (
                    redact_transport_value(nested)
                    if str(key).lower().replace("-", "_") in {"proxy", "location"}
                    else redact_transport_locations(nested)
                )
                for key, nested in item.items()
            }
        if isinstance(item, list):
            return [redact_transport_locations(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact_transport_locations(nested) for nested in item)
        return item

    prepared = redact_transport_locations(prepared)
    routed = finalize_target_envelope(prepared, context)
    sanitized_messages = sanitize_notification_event_payload(event_messages)
    routed_data = routed.get("data")
    routed_raw = routed.get("raw")
    routed_raw_result = (
        routed_raw.get("result") if isinstance(routed_raw, dict) else None
    )
    if data_has_messages and isinstance(routed_data, dict):
        routed_data["messages"] = deepcopy(sanitized_messages)
    if raw_has_messages and isinstance(routed_raw_result, dict):
        routed_raw_result["messages"] = deepcopy(sanitized_messages)
    return routed


_SCREENSHOT_LOCATION_TEXT = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]+://|[A-Za-z]:[\\/]|(?:^|[\s:=('\"])(?:/|\\\\|~/))"
)
_SCREENSHOT_ENCODED_PATH_TEXT = re.compile(
    r"(?:%2e%2e|%2f|%5c)", re.IGNORECASE
)
_SERVER_SCREENSHOT_FILENAME = re.compile(
    r"^(?:0|[1-9][0-9]*)-[0-9a-f]{12}\.png$"
)


def _trusted_server_screenshot_route(metadata: Any) -> str | None:
    """Accept only the server-store route bound to its validated PNG filename."""

    if not isinstance(metadata, dict):
        return None
    filename = metadata.get("filename")
    route = metadata.get("url")
    if (
        not isinstance(filename, str)
        or _SERVER_SCREENSHOT_FILENAME.fullmatch(filename) is None
        or not isinstance(route, str)
        or not route
    ):
        return None

    parsed = urlsplit(route)
    expected_path = f"/screenshots/{filename}"
    if parsed.query or parsed.fragment or parsed.username is not None or parsed.password is not None:
        return None
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
    elif route != expected_path:
        return None
    if parsed.path != expected_path:
        return None
    return route


def finalize_screenshot_target_envelope(
    envelope: dict[str, Any],
    context: TargetContext,
    *,
    trusted_server_screenshot: Any = None,
) -> dict[str, Any]:
    """Redact screenshot provenance and restore only a server-generated route.

    Bridge-returned paths, locations, and URLs are never trusted. The one URL
    that may survive is produced by the local screenshot store and accepted
    only when it is a credential-free route for that store's validated PNG
    filename.
    """

    if not context.explicit or context.target is None:
        return envelope

    def redact_field(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: redact_field(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [redact_field(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact_field(nested) for nested in item)
        return item if item in (None, "") else "[redacted]"

    def redact_provenance(item: Any) -> Any:
        if isinstance(item, str):
            text = item.strip()
            return (
                "[redacted]"
                if _SCREENSHOT_LOCATION_TEXT.search(text)
                or _SCREENSHOT_ENCODED_PATH_TEXT.search(text)
                else item
            )
        if isinstance(item, dict):
            sanitized: dict[Any, Any] = {}
            for key, nested in item.items():
                normalized = str(key).lower().replace("-", "_")
                location_field = normalized in {
                    "filename",
                    "location",
                    "path",
                    "url",
                } or normalized.endswith(
                    ("_filename", "_location", "_path", "_url")
                )
                sanitized[key] = (
                    redact_field(nested)
                    if location_field
                    else redact_provenance(nested)
                )
            return sanitized
        if isinstance(item, list):
            return [redact_provenance(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact_provenance(nested) for nested in item)
        return item

    trusted_route = _trusted_server_screenshot_route(trusted_server_screenshot)
    prepared = redact_provenance(deepcopy(envelope))
    routed = finalize_target_envelope(prepared, context)
    if trusted_route is None or not routed.get("ok"):
        return routed

    candidates = []
    data = routed.get("data")
    if isinstance(data, dict):
        candidates.append(data.get("server_screenshot"))
    raw = routed.get("raw")
    raw_result = raw.get("result") if isinstance(raw, dict) else None
    if isinstance(raw_result, dict):
        candidates.append(raw_result.get("server_screenshot"))
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate["url"] = trusted_route
    return routed
