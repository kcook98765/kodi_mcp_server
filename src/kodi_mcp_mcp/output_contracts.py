"""Canonical MCP output contracts and conservative tool behavior hints.

The schemas in this module describe the normalized application envelope emitted
by ``server_core``. Stable result families add required data fields; inherently
heterogeneous tools are explicitly left schema-less. Runtime projection and
validation use the same registry that feeds ``tools/list`` so advertised and
emitted structured output cannot drift silently.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator


SCHEMALESS_TOOLS = frozenset({"addon_execute", "jsonrpc_introspect"})
LOG_TOOLS = frozenset(
    {"bridge_log_tail", "bridge_log_markers", "bridge_log_recent_errors"}
)


_OBJECT: dict[str, Any] = {"type": "object"}
_ARRAY: dict[str, Any] = {"type": "array"}
_ANY: dict[str, Any] = {}
_NULLABLE_STRING = {"type": ["string", "null"]}

_STATUS_DATA = {
    "type": "object",
    "properties": {
        "server": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
        },
        "config": {
            "type": "object",
            "properties": {"loaded": {"type": "boolean"}},
            "required": ["loaded"],
        },
        "jsonrpc": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
        },
        "bridge": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
        },
        "vision": {"type": "object"},
    },
    "required": ["server", "config", "jsonrpc", "bridge", "vision"],
}

_GUI_STATE_DATA = {
    "type": "object",
    "properties": {
        "current_window": {"type": "string"},
        "current_window_id": {"type": "integer"},
        "current_dialog_id": {"type": "integer"},
        "conditions": {"type": "object"},
        "active_players": {"type": "array"},
    },
    "required": [
        "current_window",
        "current_window_id",
        "current_dialog_id",
        "conditions",
        "active_players",
    ],
}

_LOG_METADATA_DATA = {
    "type": "object",
    "properties": {
        "truncated": {"type": "boolean"},
        "truncation_direction": _NULLABLE_STRING,
        "max_bytes": {"type": "integer", "minimum": 1},
        "available_lines": {"type": "integer", "minimum": 0},
        "available_bytes": {"type": "integer", "minimum": 0},
        "returned_lines": {"type": "integer", "minimum": 0},
        "returned_bytes": {"type": "integer", "minimum": 0},
    },
    "required": [
        "truncated",
        "truncation_direction",
        "max_bytes",
        "available_lines",
        "available_bytes",
        "returned_lines",
        "returned_bytes",
    ],
    "not": {
        "anyOf": [
            {"required": ["lines"]},
            {"required": ["matching_lines"]},
            {"required": ["log"]},
            {"required": ["text"]},
            {"required": ["tail"]},
        ]
    },
}

_SCREENSHOT_DATA = {
    "type": "object",
    "properties": {
        "content_type": {"const": "image/png"},
        "size_bytes": {"type": "integer", "minimum": 0},
        "server_screenshot": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "size_bytes": {"type": "integer", "minimum": 0},
                "sha256": {"type": "string"},
            },
            "required": ["url", "size_bytes"],
        },
        "inline_image": {
            "type": "object",
            "properties": {"included": {"type": "boolean"}},
            "required": ["included"],
        },
    },
    "required": ["content_type", "size_bytes"],
    "not": {"required": ["image_base64"]},
}

_MANAGED_VALIDATE_DATA = {
    "type": "object",
    "properties": {
        "ok": {"const": True},
        "managed_addon_id": {"type": "string", "minLength": 1},
        "registry": {"type": "object"},
        "artifacts": {"type": "object"},
        "kodi_bridge": {"type": "object"},
        "summary": {
            "type": "object",
            "properties": {
                "ready_for_build": {"type": "boolean"},
                "ready_for_publish": {"type": "boolean"},
                "ready_for_stage": {"type": "boolean"},
                "ready_for_kodi_install": {"type": "boolean"},
            },
            "required": [
                "ready_for_build",
                "ready_for_publish",
                "ready_for_stage",
                "ready_for_kodi_install",
            ],
        },
    },
    "required": [
        "ok",
        "managed_addon_id",
        "registry",
        "artifacts",
        "kodi_bridge",
        "summary",
    ],
}

_SOURCE_INSPECT_DATA = {
    "type": "object",
    "properties": {
        "ok": {"const": True},
        "addon_id": {"type": "string"},
        "name": {"type": "string"},
        "version": {"type": "string"},
        "extensions": {"type": "array"},
        "entrypoints": {"type": "array"},
        "tests": {"type": "array"},
        "project_map": {"type": "object"},
    },
    "required": [
        "ok",
        "addon_id",
        "name",
        "version",
        "extensions",
        "entrypoints",
        "tests",
        "project_map",
    ],
}

_SOURCE_TREE_DATA = {
    "type": "object",
    "properties": {
        "ok": {"const": True},
        "entries": {"type": "array"},
        "truncated": {"type": "boolean"},
    },
    "required": ["ok", "entries", "truncated"],
}

_DATA_SCHEMAS: dict[str, dict[str, Any]] = {
    "kodi_status": _STATUS_DATA,
    "bridge_health": _OBJECT,
    "bridge_status": _OBJECT,
    "bridge_runtime_info": _OBJECT,
    "bridge_bootstrap_status": _OBJECT,
    "bridge_log_tail": _LOG_METADATA_DATA,
    "bridge_log_markers": _LOG_METADATA_DATA,
    "bridge_log_recent_errors": _LOG_METADATA_DATA,
    "bridge_write_log_marker": _OBJECT,
    "kodi_gui_action": _OBJECT,
    "kodi_gui_screenshot": _SCREENSHOT_DATA,
    "kodi_gui_state": _GUI_STATE_DATA,
    "addon_list": _OBJECT,
    "addon_details": _OBJECT,
    "addon_source_inspect": _SOURCE_INSPECT_DATA,
    "addon_project_map_status": _OBJECT,
    "addon_source_tree": _SOURCE_TREE_DATA,
    "kodi_player_active": _ARRAY,
    "kodi_player_open": _ANY,
    "kodi_player_item": _OBJECT,
    "kodi_player_seek": _ANY,
    "kodi_player_pause": _ANY,
    "kodi_player_stop": _ANY,
    "kodi_notifications_sample": _OBJECT,
    "managed_addon_register": _OBJECT,
    "managed_addon_list": _OBJECT,
    "managed_addon_get": _OBJECT,
    "managed_addon_build_publish_and_stage": _OBJECT,
    "managed_addon_build_publish_stage_and_apply": _OBJECT,
    "managed_addon_validate_state": _MANAGED_VALIDATE_DATA,
    "artifact_upload_zip": _OBJECT,
    "repo_publish_artifact": _OBJECT,
    "repo_stage_current_dev_repo": _OBJECT,
    "repo_stage_and_apply_addon": _OBJECT,
    "repo_publish_stage_apply_artifact": _OBJECT,
    "addon_dev_loop": _OBJECT,
}

_READ_ONLY = frozenset(
    {
        "kodi_status",
        "bridge_health",
        "bridge_status",
        "bridge_runtime_info",
        "bridge_bootstrap_status",
        "bridge_log_tail",
        "bridge_log_markers",
        "bridge_log_recent_errors",
        "kodi_gui_state",
        "addon_list",
        "addon_details",
        "addon_source_inspect",
        "addon_project_map_status",
        "addon_source_tree",
        "kodi_player_active",
        "kodi_player_item",
        "jsonrpc_introspect",
        "kodi_notifications_sample",
        "managed_addon_list",
        "managed_addon_get",
        "managed_addon_validate_state",
    }
)
_NONDESTRUCTIVE_MUTATIONS = frozenset(
    {
        "bridge_write_log_marker",
        "kodi_gui_screenshot",
        "kodi_player_open",
        "kodi_player_seek",
        "kodi_player_pause",
        "kodi_player_stop",
        "artifact_upload_zip",
    }
)
_DESTRUCTIVE_MUTATIONS = frozenset(
    {
        "kodi_gui_action",
        "addon_execute",
        "managed_addon_register",
        "managed_addon_build_publish_and_stage",
        "managed_addon_build_publish_stage_and_apply",
        "repo_publish_artifact",
        "repo_stage_current_dev_repo",
        "repo_stage_and_apply_addon",
        "repo_publish_stage_apply_artifact",
        "addon_dev_loop",
    }
)
_ALL_ANNOTATED = _READ_ONLY | _NONDESTRUCTIVE_MUTATIONS | _DESTRUCTIVE_MUTATIONS


def _envelope_schema(tool_name: str, data_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "tool": {"const": tool_name},
            "data": {},
            "error": {"type": ["string", "null"]},
            "error_type": {"type": ["string", "null"]},
            "error_code": {"type": ["integer", "string", "null"]},
            "latency_ms": {"type": "integer", "minimum": 0},
            "request_id": {"type": ["string", "integer", "null"]},
            "raw": {},
        },
        "required": [
            "ok",
            "tool",
            "data",
            "error",
            "error_type",
            "error_code",
            "latency_ms",
            "request_id",
            "raw",
        ],
        "additionalProperties": False,
        "oneOf": [
            {
                "properties": {
                    "ok": {"const": True},
                    "tool": {"const": tool_name},
                    "data": data_schema,
                    "error": {"type": "null"},
                },
                "required": ["ok", "tool", "data", "error"],
            },
            {
                "properties": {
                    "ok": {"const": False},
                    "tool": {"const": tool_name},
                    "error": {"type": "string", "minLength": 1},
                },
                "required": ["ok", "tool", "error"],
            },
        ],
    }


OUTPUT_SCHEMAS = {
    name: _envelope_schema(name, schema) for name, schema in _DATA_SCHEMAS.items()
}


def output_schema_for(tool_name: str) -> dict[str, Any] | None:
    """Return an isolated copy of the exact advertised schema, if supported."""

    schema = OUTPUT_SCHEMAS.get(tool_name)
    return deepcopy(schema) if schema is not None else None


def annotation_values_for(tool_name: str) -> dict[str, bool]:
    """Return only standardized MCP annotation hints for a reviewed tool."""

    if tool_name not in _ALL_ANNOTATED:
        raise KeyError(f"tool annotations have not been reviewed: {tool_name}")
    if tool_name in _READ_ONLY:
        return {
            "read_only_hint": True,
            "open_world_hint": False,
        }
    return {
        "read_only_hint": False,
        "destructive_hint": tool_name in _DESTRUCTIVE_MUTATIONS,
        "idempotent_hint": False,
        "open_world_hint": tool_name == "addon_execute",
    }


def _project_log_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    projected = deepcopy(envelope)
    data = projected.get("data")
    if isinstance(data, dict):
        for key in ("lines", "matching_lines", "log", "text", "tail"):
            data.pop(key, None)
    raw = projected.get("raw")
    if isinstance(raw, dict):
        raw.pop("result", None)
        raw["content_omitted"] = "see TextContent"
    return projected


def apply_output_contract(
    envelope: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Project and validate structured output using the advertised registry.

    A schema mismatch is converted into a schema-valid, model-visible tool error
    rather than emitting a successful result that violates ``tools/list``.
    """

    tool_name = str(envelope.get("tool") or "")
    schema = OUTPUT_SCHEMAS.get(tool_name)
    if schema is None:
        return envelope, None

    structured = (
        _project_log_envelope(envelope) if tool_name in LOG_TOOLS else deepcopy(envelope)
    )
    errors = sorted(
        Draft202012Validator(schema).iter_errors(structured),
        key=lambda error: (list(error.absolute_path), list(error.absolute_schema_path)),
    )
    if not errors:
        return envelope, structured

    first = errors[0]
    field = ".".join(str(part) for part in first.absolute_path) or "result"
    contract_error = {
        "ok": False,
        "tool": tool_name,
        "data": None,
        "error": f"output contract violation at {field}: {first.message}",
        "error_type": "output_contract_error",
        "error_code": None,
        "latency_ms": max(0, int(envelope.get("latency_ms") or 0)),
        "request_id": envelope.get("request_id"),
        "raw": {
            "validation": {
                "field": field,
                "message": first.message,
                "validator": first.validator,
            }
        },
    }
    Draft202012Validator(schema).validate(contract_error)
    return contract_error, contract_error
