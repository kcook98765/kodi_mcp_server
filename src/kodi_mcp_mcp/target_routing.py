"""Stateless per-call target routing helpers for the MCP adapter."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

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
