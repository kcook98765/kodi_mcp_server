"""Shared MCP server core for Kodi.

This module contains the transport-agnostic MCP server implementation:
- runtime construction (composition)
- tool schema definitions
- tool dispatch logic

It is used by:
- stdio MCP entrypoint: `kodi_mcp_mcp.server`
- remote StreamableHTTP/SSE transport mounted in the FastAPI app

NOTE: This file is intentionally a refactor/extraction from
`kodi_mcp_mcp.server` so stdio behavior remains unchanged.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Tuple
from xml.etree import ElementTree

from jsonschema import Draft202012Validator
from mcp.server import Server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ErrorData,
    ImageContent,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)

from kodi_mcp_mcp.output_contracts import (
    annotation_values_for,
    apply_output_contract,
    output_schema_for,
)

from kodi_mcp_server import __version__
from kodi_mcp_server.bridge_bootstrap import inspect_bootstrap_state
from kodi_mcp_server.composition import (
    build_bridge_tool,
    build_jsonrpc_tool,
    build_notification_probe,
)
from kodi_mcp_server.config import (
    BRIDGE_BOOTSTRAP_MANIFEST_PATH,
    KODI_BRIDGE_BASE_URL,
    KODI_JSONRPC_URL,
    REPO_BASE_URL,
    VISION_ENABLED,
)
from kodi_mcp_server.managed_addons import (
    managed_addon_build_publish_and_stage,
    managed_addon_get,
    managed_addon_list,
    managed_addon_register,
)
from kodi_mcp_server.dev_loop_artifacts import (
    artifact_upload_zip as _artifact_upload_zip,
    repo_publish_artifact as _repo_publish_artifact,
    repo_publish_stage_apply_artifact as _repo_publish_stage_apply_artifact,
    repo_stage_current_dev_repo as _repo_stage_current_dev_repo,
    repo_stage_and_apply_addon as _repo_stage_and_apply_addon,
)
from kodi_mcp_server.kodi_apply import managed_addon_build_publish_stage_and_apply
from kodi_mcp_server.milestone_a_bridge import read_addon_state
from kodi_mcp_server.paths import AUTHORITATIVE_REPO_ROOT, PROJECT_ROOT
from kodi_mcp_server.screenshot_store import (
    inspect_screenshot_from_base64,
    store_screenshot_from_base64,
)


SERVER_NAME = "kodi-mcp"
SERVER_VERSION = __version__
SCREENSHOT_CAPTURE_MAX_ATTEMPTS = 5
SCREENSHOT_RETRY_TIMEOUT_SECONDS = 2.0
SCREENSHOT_RETRY_CONDITION = "effectively_uniform_black_home_without_active_media_or_dialog"

# Kodi documents lowercase letters, numbers, periods, underscores, and dashes
# for addon IDs. Kodi 20 also ships a language-resource ID containing ``@``
# (resource.language.sr_rs@latin), so preserve that observed compatibility.
# Require at least one alphanumeric character so separator-only values cannot
# be interpreted as registry keys.
ADDON_ID_PATTERN = r"^(?=.*[a-z0-9])[a-z0-9._@-]+$"


def _result_from_envelope(
    envelope: dict[str, Any], extra_content: list[Any] | None = None
) -> CallToolResult:
    """Return compatible text plus schema-validated structured content."""

    rendered_envelope, structured_content = apply_output_contract(envelope)
    content: list[Any] = [
        TextContent(
            type="text",
            text=json.dumps(rendered_envelope, indent=2, sort_keys=True),
        )
    ]
    if extra_content and rendered_envelope.get("ok"):
        content.extend(extra_content)
    return CallToolResult(
        is_error=not rendered_envelope.get("ok", False),
        content=content,
        structured_content=structured_content,
    )


def _addon_id_schema(description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "string",
        "minLength": 1,
        "pattern": ADDON_ID_PATTERN,
    }
    if description:
        schema["description"] = description
    return schema


def _invalid_params_result(
    tool_name: str,
    arguments: Any,
    detail: str,
    validation: list[dict[str, Any]] | None = None,
) -> CallToolResult:
    raw: dict[str, Any] = {"arguments": arguments}
    if validation:
        raw["validation"] = validation
    envelope = {
        "ok": False,
        "tool": tool_name,
        "data": None,
        "error": detail,
        "error_type": "invalid_params",
        "error_code": None,
        "latency_ms": 0,
        "request_id": None,
        "raw": raw,
    }
    return _result_from_envelope(envelope)


def _validate_tool_arguments(
    tool_name: str, arguments: Any, input_schema: dict[str, Any]
) -> CallToolResult | None:
    """Validate actual call arguments against the exact advertised schema."""

    candidate = {} if arguments is None else arguments
    errors = sorted(
        Draft202012Validator(input_schema).iter_errors(candidate),
        key=lambda error: (list(error.absolute_path), list(error.absolute_schema_path)),
    )
    if not errors:
        return None

    error = errors[0]
    field = ".".join(str(part) for part in error.absolute_path)
    detail = (
        f"invalid argument {field}: {error.message}"
        if field
        else f"invalid arguments: {error.message}"
    )
    validation = [
        {
            "field": ".".join(str(part) for part in item.absolute_path) or None,
            "message": item.message,
            "validator": item.validator,
        }
        for item in errors
    ]
    return _invalid_params_result(tool_name, candidate, detail, validation)


def _failure_fields(raw_value: dict[str, Any], tool_name: str) -> tuple[Any, Any]:
    """Return nonempty error/error_type values for a failed plain-dict result."""

    error = raw_value.get("error")
    error_type = raw_value.get("error_type")
    if error not in (None, ""):
        return error, error_type or "operation_failed"

    verification = raw_value.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    reason = (
        raw_value.get("message")
        or raw_value.get("failure_reason")
        or raw_value.get("apply_status")
        or verification.get("apply_status")
        or raw_value.get("error_code")
        or "operation_failed"
    )
    identifiers = []
    for key in ("managed_addon_id", "addon_id", "addonid", "artifact_id"):
        value = raw_value.get(key)
        if isinstance(value, str) and value:
            identifiers.append(f"{key}={value!r}")
    context = f" ({', '.join(identifiers)})" if identifiers else ""
    hint = verification.get("retry_hint")
    hint_text = f"; {hint}" if isinstance(hint, str) and hint.strip() else ""
    normalized_error = f"{tool_name} failed: {reason}{context}{hint_text}"

    code = raw_value.get("error_code")
    if error_type in (None, "") and isinstance(code, str) and code:
        error_type = code.lower()
    return normalized_error, error_type or "operation_failed"


async def _observe_active_players(
    jsonrpc_tool: Any,
    timeout_seconds: int,
    poll_interval_ms: int,
) -> dict[str, Any]:
    """Poll Kodi for an active player and return structured observation details."""
    timeout_seconds = max(0, min(60, timeout_seconds))
    poll_interval_ms = max(100, min(5000, poll_interval_ms))

    active_value: Any = None
    active_players: list[Any] = []
    checks = 0
    deadline = time.monotonic() + timeout_seconds

    while True:
        checks += 1
        active_result = await jsonrpc_tool.get_active_players()
        active_value = _as_dict(active_result)
        if isinstance(active_value, dict):
            result_value = active_value.get("result")
            active_players = result_value if isinstance(result_value, list) else []
        else:
            active_players = active_value if isinstance(active_value, list) else []

        if active_players or time.monotonic() >= deadline:
            break
        await asyncio.sleep(poll_interval_ms / 1000)

    return {
        "player_started": bool(active_players),
        "timeout_seconds": timeout_seconds,
        "poll_interval_ms": poll_interval_ms,
        "checks": checks,
        "active_players": active_players,
        "last_active_response": active_value,
    }


async def _observe_gui_state(
    bridge_tool: Any,
    *,
    expect_window: str | None,
    expect_fullscreen: bool | None,
    timeout_seconds: int,
    poll_interval_ms: int,
) -> dict[str, Any]:
    """Poll Kodi GUI state until all requested expectations match or timeout."""
    timeout_seconds = max(1, min(60, timeout_seconds))
    poll_interval_ms = max(100, min(5000, poll_interval_ms))
    expected_window = (expect_window or "").strip()

    last_gui_state: Any = None
    checks = 0
    deadline = time.monotonic() + timeout_seconds
    window_matched = expected_window == ""
    fullscreen_matched = expect_fullscreen is None

    while True:
        checks += 1
        gui_result = await bridge_tool.gui_state()
        gui_value = _as_dict(gui_result)
        last_gui_state = gui_value.get("result") if isinstance(gui_value, dict) and "result" in gui_value else gui_value

        current_window = ""
        fullscreen_video = None
        if isinstance(last_gui_state, dict):
            current_window = str(last_gui_state.get("current_window") or "")
            conditions = last_gui_state.get("conditions") or {}
            if isinstance(conditions, dict):
                fullscreen_video = conditions.get("fullscreen_video")

        window_matched = (
            True
            if not expected_window
            else expected_window.casefold() in current_window.casefold()
        )
        fullscreen_matched = (
            True
            if expect_fullscreen is None
            else bool(fullscreen_video) is expect_fullscreen
        )

        if (window_matched and fullscreen_matched) or time.monotonic() >= deadline:
            break
        await asyncio.sleep(poll_interval_ms / 1000)

    return {
        "expected_window": expected_window or None,
        "expected_fullscreen": expect_fullscreen,
        "matched": bool(window_matched and fullscreen_matched),
        "window_matched": bool(window_matched),
        "fullscreen_matched": bool(fullscreen_matched),
        "timeout_seconds": timeout_seconds,
        "poll_interval_ms": poll_interval_ms,
        "checks": checks,
        "last_gui_state": last_gui_state,
    }


async def _read_gui_state(bridge_tool: Any) -> dict[str, Any]:
    """Read Kodi GUI state once without making addon execution depend on it."""
    gui_state = getattr(bridge_tool, "gui_state", None)
    if not callable(gui_state):
        return {
            "captured": False,
            "source": "kodi_gui_state",
            "state": None,
            "error": "bridge tool does not expose gui_state",
            "request_id": None,
        }

    try:
        gui_result = await gui_state()
    except Exception as exc:  # pragma: no cover - defensive around live bridge transport
        return {
            "captured": False,
            "source": "kodi_gui_state",
            "state": None,
            "error": str(exc),
            "request_id": None,
        }

    gui_value = _as_dict(gui_result)
    state = gui_value.get("result") if isinstance(gui_value, dict) and "result" in gui_value else gui_value
    error = gui_value.get("error") if isinstance(gui_value, dict) else None
    return {
        "captured": error is None,
        "source": "kodi_gui_state",
        "state": state if error is None else None,
        "error": error,
        "request_id": getattr(gui_result, "request_id", None),
    }


Runtime = dict[str, Any]

SOURCE_TREE_EXCLUDES = {".git", "__pycache__", ".pytest_cache", "venv", ".venv", "node_modules"}
LOG_ERROR_RE = re.compile(r"(error|exception|traceback|failed|failure|warning|timeout|refused)", re.IGNORECASE)
LOG_DEFAULT_MAX_BYTES = 128 * 1024
LOG_MAX_BYTES = 128 * 1024
INLINE_SCREENSHOT_MAX_RAW_BYTES = 512 * 1024
LOG_TOOL_NAMES = {"bridge_log_tail", "bridge_log_markers", "bridge_log_recent_errors"}


def _utf8_tail(text: str, max_bytes: int) -> str:
    """Return at most ``max_bytes`` from the end without splitting UTF-8."""

    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    tail = encoded[-max_bytes:]
    while tail and tail[0] & 0xC0 == 0x80:
        tail = tail[1:]
    return tail.decode("utf-8")


def _bound_log_lines(lines: list[Any], max_bytes: int) -> dict[str, Any]:
    """Keep newest log content under a joined UTF-8 byte budget."""

    normalized = [str(line) for line in lines]
    available_bytes = len("\n".join(normalized).encode("utf-8"))
    kept_reversed: list[str] = []
    used = 0

    for line in reversed(normalized):
        separator = 1 if kept_reversed else 0
        encoded_size = len(line.encode("utf-8"))
        if used + separator + encoded_size <= max_bytes:
            kept_reversed.append(line)
            used += separator + encoded_size
            continue

        remaining = max_bytes - used - separator
        if remaining > 0:
            fragment = _utf8_tail(line, remaining)
            if fragment:
                kept_reversed.append(fragment)
                used += separator + len(fragment.encode("utf-8"))
        break

    kept = list(reversed(kept_reversed))
    truncated = kept != normalized
    return {
        "lines": kept,
        "truncated": truncated,
        "truncation_direction": "start" if truncated else None,
        "max_bytes": max_bytes,
        "available_lines": len(normalized),
        "available_bytes": available_bytes,
        "returned_lines": len(kept),
        "returned_bytes": len("\n".join(kept).encode("utf-8")),
    }


def _bound_log_response(raw_result: Any, max_bytes: int) -> Any:
    """Bound a bridge ResponseMessage-shaped log result."""

    raw_value = _as_dict(raw_result)
    if not isinstance(raw_value, dict):
        return raw_value
    result = raw_value.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("lines"), list):
        return raw_value
    bounded = _bound_log_lines(result["lines"], max_bytes)
    result = {**result, **bounded}
    raw_value["result"] = result
    return raw_value


def _compact_log_raw(raw_value: Any, data: Any) -> dict[str, Any]:
    """Keep transport metadata without duplicating bounded log content."""

    metadata_keys = (
        "truncated",
        "truncation_direction",
        "max_bytes",
        "available_lines",
        "available_bytes",
        "returned_lines",
        "returned_bytes",
    )
    metadata = {key: data.get(key) for key in metadata_keys if isinstance(data, dict) and key in data}
    compact = {"content_omitted": "see data", "result_metadata": metadata}
    if isinstance(raw_value, dict):
        for key in ("request_id", "error", "error_type", "error_code", "latency_ms"):
            if key in raw_value:
                compact[key] = raw_value.get(key)
    return compact


def _as_dict(value: Any) -> Any:
    """Best-effort conversion for tool results.

    - ResponseMessage exposes `to_dict()`.
    - Plain dict results are passed through.
    """

    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        return value.to_dict()
    return value


def _black_home_capture_is_retryable(gui_state: Any) -> bool:
    """Return true only when a uniform black capture contradicts visible Home state."""

    raw = _as_dict(gui_state)
    state = raw.get("result") if isinstance(raw, dict) else None
    if not isinstance(state, dict) or state.get("ok") is not True:
        return False
    conditions_value = state.get("conditions")
    conditions = conditions_value if isinstance(conditions_value, dict) else {}
    active_players = state.get("active_players")
    dialog_id = state.get("current_dialog_id")
    return (
        state.get("current_window_id") == 10000
        and isinstance(active_players, list)
        and not active_players
        and not any(
            bool(conditions.get(key))
            for key in (
                "fullscreen_video",
                "player_has_media",
                "player_has_video",
                "player_playing",
                "player_paused",
            )
        )
        and dialog_id in (0, 9999)
    )


async def _capture_screenshot_with_retry(bridge: Any, *, include_image: bool) -> Any:
    """Capture until a Home frame is non-black, subject to strict attempt/time bounds."""

    attempts: list[dict[str, Any]] = []
    retry_deadline: float | None = None
    loop = asyncio.get_running_loop()

    def failure(reason: str) -> dict[str, Any]:
        return {
            "ok": False,
            "error": reason,
            "error_type": "screenshot_not_ready",
            "error_code": "BLACK_FRAME",
            "capture_validation": {
                "status": "failed_black_frame",
                "attempts": len(attempts),
                "retries": max(0, len(attempts) - 1),
                "max_attempts": SCREENSHOT_CAPTURE_MAX_ATTEMPTS,
                "retry_timeout_seconds": SCREENSHOT_RETRY_TIMEOUT_SECONDS,
                "retry_condition": SCREENSHOT_RETRY_CONDITION,
                "attempt_diagnostics": attempts,
            },
        }

    while len(attempts) < SCREENSHOT_CAPTURE_MAX_ATTEMPTS:
        try:
            if retry_deadline is None:
                raw_result = await bridge.gui_screenshot(include_image=include_image)
            else:
                remaining = retry_deadline - loop.time()
                if remaining <= 0:
                    return failure("screenshot capture remained black until the retry timeout expired")
                raw_result = await asyncio.wait_for(
                    bridge.gui_screenshot(include_image=include_image), timeout=remaining
                )
        except TimeoutError:
            return failure("screenshot capture remained black until the retry timeout expired")

        raw_value = _as_dict(raw_result)
        bridge_result = raw_value.get("result") if isinstance(raw_value, dict) else None
        if not isinstance(bridge_result, dict):
            return raw_result
        image_base64 = bridge_result.get("image_base64")
        if not isinstance(image_base64, str) or not image_base64:
            return raw_result

        inspection = inspect_screenshot_from_base64(image_base64)
        attempts.append(
            {
                key: inspection.get(key)
                for key in (
                    "size_bytes",
                    "width",
                    "height",
                    "sha256",
                    "pixel_analysis_supported",
                    "effectively_uniform_black",
                    "max_rgb_channel",
                    "black_channel_max",
                )
                if key in inspection
            }
        )
        if not inspection["effectively_uniform_black"]:
            bridge_result["capture_validation"] = {
                "status": "valid_after_retry" if len(attempts) > 1 else "valid",
                "attempts": len(attempts),
                "retries": len(attempts) - 1,
                "max_attempts": SCREENSHOT_CAPTURE_MAX_ATTEMPTS,
                "retry_timeout_seconds": SCREENSHOT_RETRY_TIMEOUT_SECONDS,
                "retry_condition": SCREENSHOT_RETRY_CONDITION,
            }
            raw_value["result"] = bridge_result
            return raw_value

        if retry_deadline is None:
            retry_deadline = loop.time() + SCREENSHOT_RETRY_TIMEOUT_SECONDS

        gui_state_method = getattr(bridge, "gui_state", None)
        if not callable(gui_state_method):
            bridge_result["capture_validation"] = {
                "status": "accepted_uniform_black_without_gui_context",
                "attempts": len(attempts),
                "retries": len(attempts) - 1,
                "max_attempts": SCREENSHOT_CAPTURE_MAX_ATTEMPTS,
                "retry_timeout_seconds": SCREENSHOT_RETRY_TIMEOUT_SECONDS,
                "retry_condition": SCREENSHOT_RETRY_CONDITION,
            }
            raw_value["result"] = bridge_result
            return raw_value

        try:
            remaining = retry_deadline - loop.time()
            if remaining <= 0:
                return failure("screenshot capture remained black until the retry timeout expired")
            gui_state = await asyncio.wait_for(bridge.gui_state(), timeout=remaining)
        except TimeoutError:
            return failure("screenshot capture remained black until the retry timeout expired")

        if not _black_home_capture_is_retryable(gui_state):
            bridge_result["capture_validation"] = {
                "status": "accepted_uniform_black_context_may_be_legitimate",
                "attempts": len(attempts),
                "retries": len(attempts) - 1,
                "max_attempts": SCREENSHOT_CAPTURE_MAX_ATTEMPTS,
                "retry_timeout_seconds": SCREENSHOT_RETRY_TIMEOUT_SECONDS,
                "retry_condition": SCREENSHOT_RETRY_CONDITION,
            }
            raw_value["result"] = bridge_result
            return raw_value

    return failure(
        "screenshot capture remained effectively black after %d attempts"
        % SCREENSHOT_CAPTURE_MAX_ATTEMPTS
    )


def _source_roots() -> list[Path]:
    configured = os.environ.get("KODI_MCP_SOURCE_ROOTS")
    values = configured.split(":") if configured else [
        "/home/kyle/workspace",
        "/srv/agent-work",
        str(PROJECT_ROOT.parent),
        "/tmp",
    ]
    roots = []
    for value in values:
        if value.strip():
            roots.append(Path(value).expanduser().resolve())
    return roots


def _translate_agent_source_path(source_path: str) -> str:
    """Translate known agent-container mounts to MCP-server host paths."""
    path = source_path.strip()
    mappings = (
        ("/srv/workspaces/kodi_3d_pov/", "/home/kyle/workspace/kodi_3d_pov/"),
        ("/srv/workspaces/", f"{PROJECT_ROOT.parent}/"),
        ("/srv/knowledge/kodi_3d_pov/", "/home/kyle/workspace/kodi_3d_pov/"),
    )
    for prefix, replacement in mappings:
        if path.startswith(prefix):
            return replacement + path[len(prefix):]
    return path


def _resolve_allowed_source_path(source_path: str) -> Path:
    if not source_path or not source_path.strip():
        raise ValueError("source_path is required")
    path = Path(_translate_agent_source_path(source_path)).expanduser().resolve()
    roots = _source_roots()
    if roots and not any(path == root or root in path.parents for root in roots):
        raise ValueError(f"source_path is outside allowed roots: {path}")
    return path


def _addon_xml_path(source_path: str) -> Path:
    path = _resolve_allowed_source_path(source_path)
    if path.is_file() and path.name == "addon.xml":
        return path
    direct = path / "addon.xml"
    if direct.exists():
        return direct
    matches = sorted(path.glob("*/addon.xml"))
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError(f"could not find a single addon.xml under {path}")


def _addon_source_inspect(source_path: str) -> dict[str, Any]:
    xml_path = _addon_xml_path(source_path)
    root = ElementTree.parse(xml_path).getroot()
    addon_dir = xml_path.parent
    extensions = [
        {
            "point": extension.attrib.get("point"),
            "library": extension.attrib.get("library"),
            "provides": extension.attrib.get("provides"),
        }
        for extension in root.findall("extension")
    ]
    entrypoints = sorted(
        str(path.relative_to(addon_dir))
        for path in addon_dir.rglob("*.py")
        if not any(part in SOURCE_TREE_EXCLUDES for part in path.parts)
    )[:100]
    tests = sorted(
        str(path.relative_to(addon_dir))
        for path in addon_dir.rglob("test*.py")
        if not any(part in SOURCE_TREE_EXCLUDES for part in path.parts)
    )[:100]
    project_map = addon_dir / "PROJECT_MAP.md"
    return {
        "ok": True,
        "addon_id": root.attrib.get("id", ""),
        "name": root.attrib.get("name", ""),
        "version": root.attrib.get("version", ""),
        "provider_name": root.attrib.get("provider-name", ""),
        "addon_dir": str(addon_dir),
        "addon_xml": str(xml_path),
        "extensions": extensions,
        "entrypoints": entrypoints,
        "tests": tests,
        "project_map": {
            "exists": project_map.exists(),
            "path": str(project_map),
            "size_bytes": project_map.stat().st_size if project_map.exists() else None,
        },
    }


def _addon_project_map_status(source_path: str) -> dict[str, Any]:
    xml_path = _addon_xml_path(source_path)
    project_map = xml_path.parent / "PROJECT_MAP.md"
    return {
        "ok": True,
        "addon_dir": str(xml_path.parent),
        "project_map_exists": project_map.exists(),
        "project_map_path": str(project_map),
        "size_bytes": project_map.stat().st_size if project_map.exists() else None,
    }


def _addon_source_tree(source_path: str, max_entries: int = 200) -> dict[str, Any]:
    xml_path = _addon_xml_path(source_path)
    addon_dir = xml_path.parent
    max_entries = max(1, min(1000, max_entries))
    entries: list[dict[str, Any]] = []
    truncated = False
    for path in sorted(addon_dir.rglob("*")):
        rel = path.relative_to(addon_dir)
        if any(part in SOURCE_TREE_EXCLUDES for part in rel.parts):
            if path.is_dir():
                continue
            continue
        if len(entries) >= max_entries:
            truncated = True
            break
        entries.append(
            {
                "path": rel.as_posix(),
                "type": "dir" if path.is_dir() else "file",
                "size_bytes": None if path.is_dir() else path.stat().st_size,
            }
        )
    return {
        "ok": True,
        "addon_dir": str(addon_dir),
        "max_entries": max_entries,
        "truncated": truncated,
        "entries": entries,
    }


async def _bridge_log_recent_errors(
    bridge_tool: Any,
    lines: int = 300,
    pattern: str | None = None,
    max_bytes: int = LOG_DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    lines = max(1, min(1000, lines if isinstance(lines, int) else 300))
    raw_result = await bridge_tool.get_bridge_log_tail(lines=lines)
    raw_value = _as_dict(raw_result)
    result_value = raw_value.get("result") if isinstance(raw_value, dict) else raw_value
    if isinstance(result_value, str):
        source_lines = result_value.splitlines()
    elif isinstance(result_value, dict):
        source_lines = []
        for key in ("log", "text", "tail", "lines"):
            value = result_value.get(key)
            if isinstance(value, str):
                source_lines = value.splitlines()
                break
            if isinstance(value, list):
                source_lines = [str(item) for item in value]
                break
    elif isinstance(result_value, list):
        source_lines = [str(item) for item in result_value]
    else:
        source_lines = []

    pattern_re = re.compile(pattern, re.IGNORECASE) if isinstance(pattern, str) and pattern.strip() else None
    matches = []
    for line in source_lines:
        if not LOG_ERROR_RE.search(line):
            continue
        if pattern_re and not pattern_re.search(line):
            continue
        matches.append(line)

    bounded = _bound_log_lines(matches[-100:], max_bytes)
    matching_lines = bounded.pop("lines")
    return {
        "ok": True,
        "lines_requested": lines,
        "lines_scanned": len(source_lines),
        "pattern": pattern if pattern_re else None,
        "count": len(matches),
        "matching_lines": matching_lines,
        **bounded,
        "request_id": raw_value.get("request_id") if isinstance(raw_value, dict) else None,
    }


async def _kodi_status(runtime: Runtime) -> dict[str, Any]:
    """Direct-call implementation for `kodi_status`.

    Returns a dict matching the FastAPI `/status` endpoint shape.
    """

    result: dict[str, Any] = {
        "server": {"status": "running"},
        "config": {"loaded": bool(KODI_JSONRPC_URL and KODI_BRIDGE_BASE_URL)},
        "jsonrpc": {"status": "unknown", "url": KODI_JSONRPC_URL},
        "bridge": {"status": "unknown", "url": KODI_BRIDGE_BASE_URL},
        "vision": {
            "enabled": VISION_ENABLED,
            "tools_available": [],
            "note": "Screenshot capture is available; vision analysis tools require explicit vision model configuration.",
        },
    }

    # Test JSON-RPC connectivity (simple ping)
    if KODI_JSONRPC_URL:
        try:
            jsonrpc_response = await runtime["jsonrpc"].get_jsonrpc_version()
            if getattr(jsonrpc_response, "error", None):
                result["jsonrpc"]["status"] = "error"
                result["jsonrpc"]["error"] = getattr(jsonrpc_response, "error", None)
            else:
                result["jsonrpc"]["status"] = "ok"
        except Exception as exc:
            result["jsonrpc"]["status"] = "error"
            result["jsonrpc"]["error"] = str(exc)

    # Test bridge connectivity
    if KODI_BRIDGE_BASE_URL:
        try:
            bridge_response = await runtime["bridge"].get_bridge_health()
            if getattr(bridge_response, "error", None):
                result["bridge"]["status"] = "error"
                result["bridge"]["error"] = getattr(bridge_response, "error", None)
            else:
                result["bridge"]["status"] = "ok"
        except Exception as exc:
            result["bridge"]["status"] = "error"
            result["bridge"]["error"] = str(exc)

    return result


def build_runtime() -> Runtime:
    """Build the shared runtime once at startup."""

    notifications = None
    try:
        # Optional dependency (`websockets`) may not be installed in all environments.
        notifications = build_notification_probe()
    except Exception:
        notifications = None

    return {
        "bridge": build_bridge_tool(),
        "jsonrpc": build_jsonrpc_tool(),
        "notifications": notifications,
    }


def build_mcp_server(runtime: Runtime) -> Tuple[Server, Any]:
    """Build a configured MCP server and its InitializationOptions.

    The returned Server is transport-agnostic; callers are responsible for
    running it over stdio or a remote HTTP transport.

    MCP 2.x: request handlers are registered via the public
    ``add_request_handler(method, params_type, handler)`` API and
    ``initialize`` is answered by the SDK runner from
    ``server.create_initialization_options()`` (server name/version/instructions
    come from the ``Server`` constructor; capabilities are derived from the
    registered handlers). The second return value is that
    ``InitializationOptions`` so transports keep a single source of truth.
    """

    async def _handle_list_tools(ctx, params: PaginatedRequestParams | None) -> ListToolsResult:
        """Return the tool list."""

        tools: list[Tool] = [
            Tool(
                name="kodi_status",
                description=(
                    "Get end-to-end server status, including config loaded state "
                    "and connectivity to Kodi JSON-RPC + bridge."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_health",
                description="Check whether the Kodi bridge addon HTTP service is reachable.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_status",
                description="Get bridge addon status payload (bridge-provided runtime summary).",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_runtime_info",
                description="Get bridge runtime info (paths/config) useful for debugging addon deployment.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_bootstrap_status",
                description=(
                    "Classify the secure first-install state for service.kodi_mcp using stock "
                    "Kodi JSON-RPC and the configured authoritative bridge bundle. This tool is "
                    "read-only and returns explicit user action when Kodi requires it."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_log_tail",
                description="Read the last N lines of the Kodi log via the bridge (primary dev-loop debugging signal).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "lines": {
                            "type": "integer",
                            "description": "Number of log lines to return.",
                            "minimum": 1,
                            "maximum": 1000,
                            "default": 50,
                        },
                        "max_bytes": {
                            "type": "integer",
                            "description": "Maximum UTF-8 bytes of returned log content; truncation is reported explicitly.",
                            "minimum": 1,
                            "maximum": LOG_MAX_BYTES,
                            "default": LOG_DEFAULT_MAX_BYTES,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_log_markers",
                description="Retrieve recent log markers written by the bridge/service (helps correlate dev-loop actions).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "lines": {
                            "type": "integer",
                            "description": "Number of log lines to scan for markers.",
                            "minimum": 1,
                            "maximum": 1000,
                            "default": 200,
                        },
                        "max_bytes": {
                            "type": "integer",
                            "description": "Maximum UTF-8 bytes of returned marker content; truncation is reported explicitly.",
                            "minimum": 1,
                            "maximum": LOG_MAX_BYTES,
                            "default": LOG_DEFAULT_MAX_BYTES,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_log_recent_errors",
                description="Return only recent error-like bridge/Kodi log lines, optionally filtered by a pattern.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "lines": {
                            "type": "integer",
                            "description": "Number of recent log lines to scan.",
                            "minimum": 1,
                            "maximum": 1000,
                            "default": 300,
                        },
                        "pattern": {
                            "type": "string",
                            "description": "Optional case-insensitive regex that matching error lines must also satisfy.",
                        },
                        "max_bytes": {
                            "type": "integer",
                            "description": "Maximum UTF-8 bytes of returned matching content; filtering occurs before truncation.",
                            "minimum": 1,
                            "maximum": LOG_MAX_BYTES,
                            "default": LOG_DEFAULT_MAX_BYTES,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="bridge_write_log_marker",
                description="Write a unique marker into the Kodi log to bracket experiments and verify an action occurred.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "minLength": 1,
                            "pattern": r"\S",
                            "description": "Marker text to write into the log. Use a unique token for traceability.",
                        }
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="kodi_gui_action",
                description="Send a basic GUI navigation action to Kodi through the bridge addon.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "up",
                                "down",
                                "left",
                                "right",
                                "select",
                                "back",
                                "home",
                                "context",
                                "info",
                                "stop",
                            ],
                        }
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="kodi_gui_screenshot",
                description="Capture a Kodi GUI screenshot through the bridge addon and store it on the MCP server for remote clients.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "include_image": {
                            "type": "boolean",
                            "default": False,
                            "description": "If true and the PNG is at most 524288 bytes, also return canonical MCP ImageContent. Larger images use stored-artifact mode or fail explicitly when store is false.",
                        },
                        "store": {
                            "type": "boolean",
                            "default": True,
                            "description": "If true, persist the screenshot on the MCP server and return a served URL.",
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="kodi_gui_state",
                description="Return compact Kodi GUI/window/player state through the bridge addon for UI verification.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="addon_list",
                description="List addons (optionally filtered by type and/or enabled) to confirm install/enable state during dev.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": "Addon type filter (Kodi taxonomy), e.g. 'xbmc.python.script', 'kodi.gameclient', etc.",
                        },
                        "enabled": {
                            "type": "boolean",
                            "description": "If provided, filter by enabled state.",
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="addon_details",
                description="Fetch addon metadata (version, enabled status, etc.) for a specific addon id.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "addonid": _addon_id_schema(
                            "Kodi addon id (e.g. 'service.kodi_mcp')."
                        )
                    },
                    "required": ["addonid"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="addon_execute",
                description="Execute a Kodi addon through JSON-RPC and return a compact launch report.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "addonid": _addon_id_schema("Kodi addon id to execute."),
                        "addon_id": _addon_id_schema(
                            "Alias for addonid. Prefer addonid when possible."
                        ),
                        "wait": {
                            "type": "boolean",
                            "default": False,
                            "description": "If true, ask Kodi to wait for addon execution to complete.",
                        },
                        "params": {
                            "type": "object",
                            "default": {},
                            "description": "Optional Addons.ExecuteAddon params object.",
                        },
                        "expect_player": {
                            "type": "boolean",
                            "default": False,
                            "description": "Use only when the requested addon behavior is media playback. If true, fail the tool call unless an active Kodi player appears after launch. For UI/navigation addons, leave false and inspect gui_state or use expect_window / expect_fullscreen instead.",
                        },
                        "expect_window": {
                            "type": "string",
                            "description": "If provided, fail unless the current Kodi window contains this text after launch.",
                        },
                        "expect_fullscreen": {
                            "type": "boolean",
                            "description": "If provided, fail unless Kodi fullscreen-video state matches this value after launch.",
                        },
                        "include_gui_state": {
                            "type": "boolean",
                            "default": True,
                            "description": "Include one post-launch kodi_gui_state snapshot in the response. Enabled by default so agents can see window/control/player context without an extra tool call.",
                        },
                        "observe_player_seconds": {
                            "type": "integer",
                            "default": 2,
                            "minimum": 0,
                            "maximum": 60,
                            "description": "Seconds to observe active-player state after launch even when expect_player is false. This makes dispatch-only success visible without requiring playback verification.",
                        },
                        "player_timeout_seconds": {
                            "type": "integer",
                            "default": 8,
                            "minimum": 1,
                            "maximum": 60,
                            "description": "Seconds to wait for an active player when expect_player is true.",
                        },
                        "poll_interval_ms": {
                            "type": "integer",
                            "default": 500,
                            "minimum": 100,
                            "maximum": 5000,
                            "description": "Delay between active-player checks when expect_player is true.",
                        },
                        "window_timeout_seconds": {
                            "type": "integer",
                            "default": 8,
                            "minimum": 1,
                            "maximum": 60,
                            "description": "Seconds to wait for GUI state expectations.",
                        },
                        "window_poll_interval_ms": {
                            "type": "integer",
                            "default": 500,
                            "minimum": 100,
                            "maximum": 5000,
                            "description": "Delay between GUI state checks.",
                        },
                    },
                    "anyOf": [
                        {"required": ["addonid"]},
                        {"required": ["addon_id"]},
                    ],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="addon_source_inspect",
                description=(
                    "Inspect a server-local or known agent-mounted addon source tree containing addon.xml. "
                    "Use this for agent preflight instead of shelling out for addon identity."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_path": {
                            "type": "string",
                            "minLength": 1,
                            "pattern": r"\S",
                            "description": "Server-local path under KODI_MCP_SOURCE_ROOTS, or a known agent mount such as /srv/workspaces/...; must contain addon.xml.",
                        }
                    },
                    "required": ["source_path"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="addon_project_map_status",
                description="Report whether PROJECT_MAP.md exists for a server-local or known agent-mounted addon source tree.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_path": {
                            "type": "string",
                            "minLength": 1,
                            "pattern": r"\S",
                            "description": "Server-local path under KODI_MCP_SOURCE_ROOTS, or a known agent mount such as /srv/workspaces/...; must contain addon.xml.",
                        }
                    },
                    "required": ["source_path"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="addon_source_tree",
                description="Return a compact file tree for a server-local or known agent-mounted addon source tree.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_path": {
                            "type": "string",
                            "minLength": 1,
                            "pattern": r"\S",
                            "description": "Server-local path under KODI_MCP_SOURCE_ROOTS, or a known agent mount such as /srv/workspaces/...; must contain addon.xml.",
                        },
                        "max_entries": {
                            "type": "integer",
                            "default": 200,
                            "minimum": 1,
                            "maximum": 1000,
                        },
                    },
                    "required": ["source_path"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="kodi_player_active",
                description="Return active Kodi players through MCP. Agents should use this instead of raw Player.GetActivePlayers calls.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="kodi_player_open",
                description="Start playback of a known Kodi library movie or episode by its library id.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "media_type": {
                            "type": "string",
                            "enum": ["movie", "episode"],
                            "description": "Kodi library item type.",
                        },
                        "item_id": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Existing Kodi movieid or episodeid matching media_type.",
                        },
                    },
                    "required": ["media_type", "item_id"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="kodi_player_item",
                description="Return the current item for a Kodi player through MCP.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "playerid": {
                            "type": "integer",
                            "default": 1,
                            "minimum": 0,
                            "description": "Kodi player id, usually 1 for video.",
                        }
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="kodi_player_seek",
                description="Seek a Kodi player to an absolute timestamp in seconds through MCP.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "playerid": {
                            "type": "integer",
                            "default": 1,
                            "minimum": 0,
                            "description": "Kodi player id, usually 1 for video.",
                        },
                        "seconds": {
                            "type": "number",
                            "minimum": 0,
                            "description": "Absolute playback timestamp in seconds.",
                        },
                    },
                    "required": ["seconds"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="kodi_player_pause",
                description="Pause a Kodi player through MCP without toggling it back to playing.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "playerid": {
                            "type": "integer",
                            "default": 1,
                            "minimum": 0,
                            "description": "Kodi player id, usually 1 for video.",
                        }
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="kodi_player_stop",
                description=(
                    "Stop a Kodi player through MCP and optionally verify the player is no longer active. "
                    "Agents should use this for playback cleanup instead of direct JSON-RPC or host-control fallbacks."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "playerid": {
                            "type": "integer",
                            "default": 1,
                            "minimum": 0,
                            "description": "Kodi player id, usually 1 for video.",
                        },
                        "verify": {
                            "type": "boolean",
                            "default": True,
                            "description": "If true, query active players after stop and fail if this player remains active.",
                        },
                        "verify_attempts": {
                            "type": "integer",
                            "default": 20,
                            "minimum": 1,
                            "maximum": 60,
                            "description": "Number of active-player checks to make after stop.",
                        },
                        "verify_delay_ms": {
                            "type": "integer",
                            "default": 250,
                            "minimum": 0,
                            "maximum": 5000,
                            "description": "Delay between stop verification checks in milliseconds.",
                        },
                        "stable_checks": {
                            "type": "integer",
                            "default": 5,
                            "minimum": 1,
                            "maximum": 20,
                            "description": "Consecutive inactive checks required before stop is considered stable.",
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="jsonrpc_introspect",
                description="Introspect the Kodi JSON-RPC API; useful for discovering methods and validating parameter shapes.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "boolean",
                            "description": "If true, return a summarized view instead of the full introspection payload.",
                            "default": True,
                        },
                        "getdescriptions": {
                            "type": "boolean",
                            "description": "Include human-readable method descriptions.",
                            "default": False,
                        },
                        "getmetadata": {
                            "type": "boolean",
                            "description": "Include extra metadata.",
                            "default": False,
                        },
                        "filterbytransport": {
                            "type": "boolean",
                            "description": "If true, filter by transport-supported methods.",
                            "default": False,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="kodi_notifications_sample",
                description=(
                    "Collect a short sample of Kodi WebSocket notifications. "
                    "This is an OPTIONAL capability (requires Kodi WebSocket at ws://<host>:9090/jsonrpc). "
                    "Core repo/publish/update workflows do NOT require WebSocket notifications; "
                    "treat failures as advisory/informational."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sample_size": {
                            "type": "integer",
                            "description": "Number of notifications to capture before returning.",
                            "minimum": 1,
                            "default": 3,
                        },
                        "listen_seconds": {
                            "type": "integer",
                            "description": "Maximum time to listen before returning.",
                            "minimum": 1,
                            "default": 5,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="managed_addon_register",
                description=(
                    "Register/update a managed addon backed by a SERVER-LOCAL source directory (must contain addon.xml). "
                    "This workflow is intended for local dev on the same host as the MCP server; remote agents should prefer "
                    "artifact_upload_zip + repo_publish_artifact + repo_stage_* tools."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_path": {
                            "type": "string",
                            "minLength": 1,
                            "pattern": r"\S",
                            "description": "Local filesystem path to an addon root containing addon.xml.",
                        }
                    },
                    "required": ["source_path"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="managed_addon_list",
                description="List all managed addons registered with this MCP server.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="managed_addon_get",
                description="Get a single managed addon registry entry by managed_addon_id (usually addon id).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "managed_addon_id": _addon_id_schema(
                            "Managed addon id (key). Defaults to addon id."
                        )
                    },
                    "required": ["managed_addon_id"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="managed_addon_build_publish_and_stage",
                description=(
                    "SERVER-LOCAL workflow: build+publish a managed addon from its registered source_path, then build dev repo zip "
                    "and stage it to Kodi via the bridge. For remote agent workflows, use artifact_upload_zip + repo_publish_artifact."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "managed_addon_id": _addon_id_schema(),
                        "version_policy": {
                            "type": "string",
                            "enum": ["use_addon_xml", "bump_patch", "set_explicit"],
                        },
                        "explicit_version": {"type": "string"},
                        "repo_version": {"type": "string"},
                        "verify": {"type": "boolean", "default": True},
                    },
                    "required": ["managed_addon_id", "version_policy"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="managed_addon_build_publish_stage_and_apply",
                description=(
                    "SERVER-LOCAL workflow: build+publish from source_path, stage repo zip to Kodi, then refresh and install/update "
                    "the addon (best-effort; assumes repo already installed once). For remote agent workflows, use repo_stage_and_apply_addon."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "managed_addon_id": _addon_id_schema(),
                        "version_policy": {
                            "type": "string",
                            "enum": ["use_addon_xml", "bump_patch", "set_explicit"],
                        },
                        "explicit_version": {"type": "string"},
                        "repo_version": {"type": "string"},
                        "verify": {"type": "boolean", "default": True},
                    },
                    "required": ["managed_addon_id", "version_policy"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="managed_addon_validate_state",
                description="Read-only validation report for managed addon readiness (registry/artifacts/bridge).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "managed_addon_id": _addon_id_schema(),
                    },
                    "required": ["managed_addon_id"],
                    "additionalProperties": False,
                },
            ),

            # Agent-safe, pathless dev-loop tools (artifact-based)
            Tool(
                name="artifact_upload_zip",
                description=(
                    "Upload a zip artifact to the server-owned artifact store (agent-safe; no server filesystem paths required). "
                    "Input is base64-encoded zip bytes."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "zip_base64": {"type": "string", "minLength": 1, "pattern": r"\S", "description": "Base64-encoded zip bytes (optionally data: URI)."},
                        "filename": {"type": "string", "default": "upload.zip"},
                        "addon_id": _addon_id_schema(),
                        "version": {"type": "string"},
                    },
                    "required": ["zip_base64"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="repo_publish_artifact",
                description=(
                    "Publish a previously uploaded artifact (by artifact_id) into the dev repo. "
                    "Returns repo-relative paths/URLs and does not expose internal server filesystem paths."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "artifact_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
                        "addon_id": _addon_id_schema(),
                        "addon_name": {"type": "string", "minLength": 1, "pattern": r"\S"},
                        "addon_version": {"type": "string", "minLength": 1, "pattern": r"\S"},
                        "provider_name": {"type": "string", "default": "kodi_mcp"},
                    },
                    "required": ["artifact_id", "addon_id", "addon_name", "addon_version"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="repo_stage_current_dev_repo",
                description=(
                    "Build a dev-repo zip from the current server repo state (repo/dev-repo) and stage it to Kodi via the bridge."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_version": {"type": "string"},
                        "verify": {"type": "boolean", "default": True},
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="repo_stage_and_apply_addon",
                description=(
                    "Agent-safe dev loop: stage current dev repo zip to Kodi, then refresh/install/update an addon from the repo "
                    "(best-effort; assumes repo already installed at least once)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "addonid": _addon_id_schema(),
                        "repo_version": {"type": "string"},
                        "target_version": {
                            "type": "string",
                            "description": "Optional installed version to require after apply; defaults to the repo/update result.",
                        },
                        "verify": {"type": "boolean", "default": True},
                        "timeout_seconds": {"type": "integer", "default": 45, "minimum": 1},
                        "poll_interval_seconds": {"type": "integer", "default": 4, "minimum": 1},
                    },
                    "required": ["addonid"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="repo_publish_stage_apply_artifact",
                description=(
                    "One-shot agent-safe dev loop: publish an uploaded artifact into the dev repo, stage repo content to Kodi, "
                    "apply the addon, and verify the installed version equals addon_version."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "artifact_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
                        "addon_id": _addon_id_schema(),
                        "addon_name": {"type": "string", "minLength": 1, "pattern": r"\S"},
                        "addon_version": {"type": "string", "minLength": 1, "pattern": r"\S"},
                        "provider_name": {"type": "string", "default": "kodi_mcp"},
                        "repo_version": {"type": "string"},
                        "verify": {"type": "boolean", "default": True},
                        "timeout_seconds": {"type": "integer", "default": 45, "minimum": 1},
                        "poll_interval_seconds": {"type": "integer", "default": 4, "minimum": 1},
                    },
                    "required": ["artifact_id", "addon_id", "addon_name", "addon_version"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="addon_dev_loop",
                description=(
                    "Alias for the one-shot artifact dev loop: publish an uploaded addon zip artifact, stage the repo, "
                    "apply the addon, and verify the installed version."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "artifact_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
                        "addon_id": _addon_id_schema(),
                        "addon_name": {"type": "string", "minLength": 1, "pattern": r"\S"},
                        "addon_version": {"type": "string", "minLength": 1, "pattern": r"\S"},
                        "provider_name": {"type": "string", "default": "kodi_mcp"},
                        "repo_version": {"type": "string"},
                        "verify": {"type": "boolean", "default": True},
                        "timeout_seconds": {"type": "integer", "default": 45, "minimum": 1},
                        "poll_interval_seconds": {"type": "integer", "default": 4, "minimum": 1},
                    },
                    "required": ["artifact_id", "addon_id", "addon_name", "addon_version"],
                    "additionalProperties": False,
                },
            ),
        ]

        tools = [
            tool.model_copy(
                update={
                    "output_schema": output_schema_for(tool.name),
                    "annotations": ToolAnnotations(**annotation_values_for(tool.name)),
                }
            )
            for tool in tools
        ]
        return ListToolsResult(tools=tools)

    async def _handle_call_tool(ctx, params: CallToolRequestParams) -> CallToolResult:
        """Dispatch a tool call."""

        tool_name = params.name

        if tool_name in {
            "kodi_status",
            "bridge_health",
            "bridge_status",
            "bridge_runtime_info",
            "bridge_bootstrap_status",
            "bridge_log_tail",
            "bridge_log_markers",
            "bridge_log_recent_errors",
            "addon_list",
            "addon_details",
            "addon_execute",
            "addon_source_inspect",
            "addon_project_map_status",
            "addon_source_tree",
            "kodi_player_active",
            "kodi_player_open",
            "kodi_player_item",
            "kodi_player_seek",
            "kodi_player_pause",
            "kodi_player_stop",
            "jsonrpc_introspect",
            "kodi_notifications_sample",
            "bridge_write_log_marker",
            "kodi_gui_action",
            "kodi_gui_screenshot",
            "kodi_gui_state",
            "managed_addon_register",
            "managed_addon_list",
            "managed_addon_get",
            "managed_addon_build_publish_and_stage",
            "managed_addon_build_publish_stage_and_apply",
            "managed_addon_validate_state",
            "artifact_upload_zip",
            "repo_publish_artifact",
            "repo_stage_current_dev_repo",
            "repo_stage_and_apply_addon",
            "repo_publish_stage_apply_artifact",
            "addon_dev_loop",
        }:
            # Preserve exact normalized missing-arg behavior for addon_details.
            if tool_name in {"addon_details", "addon_execute"}:
                args = params.arguments or {}
                if not isinstance(args, dict):
                    args = {}

                addonid = args.get("addonid") or args.get("addon_id")
                if not isinstance(addonid, str) or not addonid:
                    envelope = {
                        "ok": False,
                        "tool": tool_name,
                        "data": None,
                        "error": "missing required argument: addonid",
                        "error_type": "invalid_params",
                        "error_code": None,
                        "latency_ms": 0,
                        "request_id": None,
                        "raw": {"arguments": args, "accepted_aliases": ["addonid", "addon_id"]},
                    }
                    return _result_from_envelope(envelope)

            # Preserve exact normalized missing-arg behavior for bridge_write_log_marker.
            if tool_name == "bridge_write_log_marker":
                args = params.arguments or {}
                if not isinstance(args, dict):
                    args = {}

                message = args.get("message")
                if not isinstance(message, str) or not message.strip():
                    envelope = {
                        "ok": False,
                        "tool": tool_name,
                        "data": None,
                        "error": "missing required argument: message",
                        "error_type": "invalid_params",
                        "error_code": None,
                        "latency_ms": 0,
                        "request_id": None,
                        "raw": {"arguments": args},
                    }
                    return _result_from_envelope(envelope)

            if tool_name == "kodi_gui_action":
                args = params.arguments or {}
                if not isinstance(args, dict):
                    args = {}

                action = args.get("action")
                allowed_actions = {"up", "down", "left", "right", "select", "back", "home", "context", "info", "stop"}
                if not isinstance(action, str) or action not in allowed_actions:
                    envelope = {
                        "ok": False,
                        "tool": tool_name,
                        "data": None,
                        "error": "missing or invalid required argument: action",
                        "error_type": "invalid_params",
                        "error_code": None,
                        "latency_ms": 0,
                        "request_id": None,
                        "raw": {"arguments": args, "allowed": sorted(allowed_actions)},
                    }
                    return _result_from_envelope(envelope)

            if tool_name in {"addon_source_inspect", "addon_project_map_status", "addon_source_tree"}:
                args = params.arguments or {}
                if not isinstance(args, dict):
                    args = {}

                source_path = args.get("source_path")
                if not isinstance(source_path, str) or not source_path.strip():
                    envelope = {
                        "ok": False,
                        "tool": tool_name,
                        "data": None,
                        "error": "missing required argument: source_path",
                        "error_type": "invalid_params",
                        "error_code": None,
                        "latency_ms": 0,
                        "request_id": None,
                        "raw": {"arguments": args},
                    }
                    return _result_from_envelope(envelope)

            if tool_name == "kodi_player_open":
                args = params.arguments or {}
                if not isinstance(args, dict):
                    args = {}
                media_type = args.get("media_type")
                item_id = args.get("item_id")
                if media_type not in {"movie", "episode"}:
                    error = "missing or invalid required argument: media_type"
                elif not isinstance(item_id, int) or isinstance(item_id, bool) or item_id < 0:
                    error = "missing or invalid required argument: item_id"
                else:
                    error = None
                if error is not None:
                    envelope = {
                        "ok": False,
                        "tool": tool_name,
                        "data": None,
                        "error": error,
                        "error_type": "invalid_params",
                        "error_code": None,
                        "latency_ms": 0,
                        "request_id": None,
                        "raw": {"arguments": args},
                    }
                    return _result_from_envelope(envelope)

            if tool_name in {"kodi_player_item", "kodi_player_seek", "kodi_player_pause", "kodi_player_stop"}:
                args = params.arguments or {}
                if not isinstance(args, dict):
                    args = {}

                playerid = args.get("playerid", 1)
                if not isinstance(playerid, int) or isinstance(playerid, bool) or playerid < 0:
                    envelope = {
                        "ok": False,
                        "tool": tool_name,
                        "data": None,
                        "error": "missing or invalid argument: playerid",
                        "error_type": "invalid_params",
                        "error_code": None,
                        "latency_ms": 0,
                        "request_id": None,
                        "raw": {"arguments": args},
                    }
                    return _result_from_envelope(envelope)

                if tool_name == "kodi_player_seek":
                    seconds = args.get("seconds")
                    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing or invalid required argument: seconds",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        return _result_from_envelope(envelope)

            # Managed addon required-arg checks.
            if tool_name in {
                "managed_addon_register",
                "managed_addon_get",
                "managed_addon_build_publish_and_stage",
                "managed_addon_build_publish_stage_and_apply",
                "managed_addon_validate_state",
            }:
                args = params.arguments or {}
                if not isinstance(args, dict):
                    args = {}

                if tool_name == "managed_addon_register":
                    source_path = args.get("source_path")
                    if not isinstance(source_path, str) or not source_path.strip():
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing required argument: source_path",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        return _result_from_envelope(envelope)

            # Agent-safe artifact tools required-arg checks.
            if tool_name in {
                "artifact_upload_zip",
                "repo_publish_artifact",
                "repo_stage_and_apply_addon",
                "repo_publish_stage_apply_artifact",
                "addon_dev_loop",
            }:
                args = params.arguments or {}
                if not isinstance(args, dict):
                    args = {}

                if tool_name == "artifact_upload_zip":
                    zip_base64 = args.get("zip_base64")
                    if not isinstance(zip_base64, str) or not zip_base64.strip():
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing required argument: zip_base64",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        return _result_from_envelope(envelope)

                if tool_name == "repo_publish_artifact":
                    for k in ("artifact_id", "addon_id", "addon_name", "addon_version"):
                        v = args.get(k)
                        if not isinstance(v, str) or not v.strip():
                            envelope = {
                                "ok": False,
                                "tool": tool_name,
                                "data": None,
                                "error": f"missing required argument: {k}",
                                "error_type": "invalid_params",
                                "error_code": None,
                                "latency_ms": 0,
                                "request_id": None,
                                "raw": {"arguments": args},
                            }
                            return _result_from_envelope(envelope)

                if tool_name == "repo_stage_and_apply_addon":
                    addonid = args.get("addonid")
                    if not isinstance(addonid, str) or not addonid.strip():
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing required argument: addonid",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        return _result_from_envelope(envelope)

                if tool_name in {"repo_publish_stage_apply_artifact", "addon_dev_loop"}:
                    for k in ("artifact_id", "addon_id", "addon_name", "addon_version"):
                        v = args.get(k)
                        if not isinstance(v, str) or not v.strip():
                            envelope = {
                                "ok": False,
                                "tool": tool_name,
                                "data": None,
                                "error": f"missing required argument: {k}",
                                "error_type": "invalid_params",
                                "error_code": None,
                                "latency_ms": 0,
                                "request_id": None,
                                "raw": {"arguments": args},
                            }
                            return _result_from_envelope(envelope)

                if tool_name == "managed_addon_get":
                    managed_addon_id = args.get("managed_addon_id")
                    if not isinstance(managed_addon_id, str) or not managed_addon_id.strip():
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing required argument: managed_addon_id",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        return _result_from_envelope(envelope)

                if tool_name == "managed_addon_validate_state":
                    managed_addon_id = args.get("managed_addon_id")
                    if not isinstance(managed_addon_id, str) or not managed_addon_id.strip():
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing required argument: managed_addon_id",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        return _result_from_envelope(envelope)

                if tool_name == "managed_addon_build_publish_and_stage":
                    managed_addon_id = args.get("managed_addon_id")
                    version_policy = args.get("version_policy")
                    if not isinstance(managed_addon_id, str) or not managed_addon_id.strip():
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing required argument: managed_addon_id",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        return _result_from_envelope(envelope)
                    if version_policy not in {"use_addon_xml", "bump_patch", "set_explicit"}:
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "invalid argument: version_policy",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args, "allowed": ["use_addon_xml", "bump_patch", "set_explicit"]},
                        }
                        return _result_from_envelope(envelope)

                if tool_name == "managed_addon_build_publish_stage_and_apply":
                    managed_addon_id = args.get("managed_addon_id")
                    version_policy = args.get("version_policy")
                    if not isinstance(managed_addon_id, str) or not managed_addon_id.strip():
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "missing required argument: managed_addon_id",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {"arguments": args},
                        }
                        return _result_from_envelope(envelope)
                    if version_policy not in {"use_addon_xml", "bump_patch", "set_explicit"}:
                        envelope = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": "invalid argument: version_policy",
                            "error_type": "invalid_params",
                            "error_code": None,
                            "latency_ms": 0,
                            "request_id": None,
                            "raw": {
                                "arguments": args,
                                "allowed": ["use_addon_xml", "bump_patch", "set_explicit"],
                            },
                        }
                        return _result_from_envelope(envelope)

            # The MCP SDK validates only CallToolRequestParams itself. The
            # custom low-level handler must enforce each listed tool schema.
            listed_tools = await _handle_list_tools(ctx, None)
            tool_schema = next(
                tool.input_schema for tool in listed_tools.tools if tool.name == tool_name
            )
            validation_error = _validate_tool_arguments(
                tool_name, params.arguments, tool_schema
            )
            if validation_error is not None:
                return validation_error

            pending_image_content: ImageContent | None = None
            start = time.time()
            envelope: dict[str, Any]
            try:
                if tool_name == "bridge_health":
                    raw_result = await runtime["bridge"].get_bridge_health()
                elif tool_name == "bridge_status":
                    raw_result = await runtime["bridge"].get_bridge_status()
                elif tool_name == "bridge_runtime_info":
                    raw_result = await runtime["bridge"].get_bridge_runtime_info()
                elif tool_name == "bridge_bootstrap_status":
                    raw_result = await inspect_bootstrap_state(
                        jsonrpc_tool=runtime["jsonrpc"],
                        bridge_tool=runtime["bridge"],
                        manifest_path=BRIDGE_BOOTSTRAP_MANIFEST_PATH,
                        base_url=REPO_BASE_URL,
                    )
                elif tool_name in {"bridge_log_tail", "bridge_log_markers"}:
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}

                    default_lines = 50 if tool_name == "bridge_log_tail" else 200
                    lines = args.get("lines", default_lines)
                    if not isinstance(lines, int):
                        lines = default_lines
                    if lines < 1:
                        lines = 1
                    max_bytes = args.get("max_bytes", LOG_DEFAULT_MAX_BYTES)
                    if not isinstance(max_bytes, int):
                        max_bytes = LOG_DEFAULT_MAX_BYTES

                    if tool_name == "bridge_log_tail":
                        raw_result = await runtime["bridge"].get_bridge_log_tail(lines=lines)
                    else:
                        raw_result = await runtime["bridge"].get_bridge_log_markers(lines=lines)
                    raw_result = _bound_log_response(raw_result, max_bytes)
                elif tool_name == "bridge_log_recent_errors":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    lines = args.get("lines", 300)
                    lines = lines if isinstance(lines, int) else 300
                    max_bytes = args.get("max_bytes", LOG_DEFAULT_MAX_BYTES)
                    max_bytes = max_bytes if isinstance(max_bytes, int) else LOG_DEFAULT_MAX_BYTES
                    pattern = args.get("pattern")
                    raw_result = await _bridge_log_recent_errors(
                        runtime["bridge"],
                        lines=lines,
                        pattern=pattern if isinstance(pattern, str) and pattern.strip() else None,
                        max_bytes=max_bytes,
                    )
                elif tool_name == "addon_list":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}

                    addon_type = args.get("type")
                    enabled = args.get("enabled")

                    raw_result = await runtime["jsonrpc"].list_addons(
                        type=addon_type if isinstance(addon_type, str) and addon_type else None,
                        enabled=enabled if isinstance(enabled, bool) else None,
                    )
                elif tool_name == "addon_details":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    addonid = args.get("addonid")
                    raw_result = await runtime["jsonrpc"].get_addon_details(addonid=addonid)
                elif tool_name == "addon_execute":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    addonid = str(args.get("addonid") or args.get("addon_id") or "").strip()
                    wait = args.get("wait", False)
                    wait = wait if isinstance(wait, bool) else False
                    params = args.get("params")
                    params = params if isinstance(params, dict) else {}
                    execute_result = await runtime["jsonrpc"].execute_addon(addonid=addonid, params=params, wait=wait)

                    expect_player = args.get("expect_player", False)
                    expect_player = expect_player if isinstance(expect_player, bool) else False
                    expect_window = args.get("expect_window")
                    expect_window = expect_window.strip() if isinstance(expect_window, str) and expect_window.strip() else None
                    expect_fullscreen = args.get("expect_fullscreen")
                    expect_fullscreen = expect_fullscreen if isinstance(expect_fullscreen, bool) else None
                    include_gui_state = args.get("include_gui_state", True)
                    include_gui_state = include_gui_state if isinstance(include_gui_state, bool) else True
                    timeout_seconds = args.get("player_timeout_seconds", 8)
                    timeout_seconds = timeout_seconds if isinstance(timeout_seconds, int) else 8
                    timeout_seconds = max(1, min(60, timeout_seconds))
                    observe_seconds = args.get("observe_player_seconds", 2)
                    observe_seconds = observe_seconds if isinstance(observe_seconds, int) else 2
                    observe_seconds = max(0, min(60, observe_seconds))
                    poll_interval_ms = args.get("poll_interval_ms", 500)
                    poll_interval_ms = poll_interval_ms if isinstance(poll_interval_ms, int) else 500
                    poll_interval_ms = max(100, min(5000, poll_interval_ms))
                    window_timeout_seconds = args.get("window_timeout_seconds", 8)
                    window_timeout_seconds = window_timeout_seconds if isinstance(window_timeout_seconds, int) else 8
                    window_timeout_seconds = max(1, min(60, window_timeout_seconds))
                    window_poll_interval_ms = args.get("window_poll_interval_ms", 500)
                    window_poll_interval_ms = window_poll_interval_ms if isinstance(window_poll_interval_ms, int) else 500
                    window_poll_interval_ms = max(100, min(5000, window_poll_interval_ms))

                    execute_value = _as_dict(execute_result)
                    execute_ok = not (isinstance(execute_value, dict) and execute_value.get("error") is not None)
                    observation_timeout = timeout_seconds if expect_player else observe_seconds
                    player_observation = await _observe_active_players(
                        runtime["jsonrpc"],
                        timeout_seconds=observation_timeout,
                        poll_interval_ms=poll_interval_ms,
                    )
                    player_started = bool(player_observation["player_started"])
                    gui_verification_required = expect_window is not None or expect_fullscreen is not None
                    gui_verification = None
                    if gui_verification_required:
                        gui_verification = await _observe_gui_state(
                            runtime["bridge"],
                            expect_window=expect_window,
                            expect_fullscreen=expect_fullscreen,
                            timeout_seconds=window_timeout_seconds,
                            poll_interval_ms=window_poll_interval_ms,
                        )
                    if gui_verification is not None:
                        gui_state = {
                            "captured": True,
                            "source": "gui_verification",
                            "state": gui_verification.get("last_gui_state"),
                            "error": None,
                            "request_id": None,
                        }
                    elif include_gui_state:
                        gui_state = await _read_gui_state(runtime["bridge"])
                    else:
                        gui_state = {
                            "captured": False,
                            "source": "disabled",
                            "state": None,
                            "error": None,
                            "request_id": None,
                        }
                    gui_matched = bool((gui_verification or {}).get("matched", False)) if gui_verification_required else True
                    verification_required = bool(expect_player or gui_verification_required)
                    verified = (
                        (player_started if expect_player else True) and gui_matched
                        if verification_required
                        else None
                    )
                    ok = execute_ok and (player_started if expect_player else True) and gui_matched

                    verification_errors = []
                    verification_guidance = []
                    if expect_player and not player_started:
                        verification_errors.append("expected active player did not appear after addon_execute")
                        verification_guidance.append(
                            "expect_player is a playback-only assertion. This is a failed verification, not a successful UI-addon launch check. For UI/navigation addons, rerun without expect_player and use gui_state, expect_window, expect_fullscreen, or screenshot evidence."
                        )
                    if gui_verification_required and not gui_matched:
                        verification_errors.append("expected GUI state did not appear after addon_execute")
                        verification_guidance.append(
                            "GUI verification failed. Compare gui_verification.last_gui_state/current_window with the expected window, then adjust the expectation or investigate the addon UI."
                        )
                    raw_result = {
                        "ok": ok,
                        "addonid": addonid,
                        "wait": wait,
                        "params": params,
                        "execute": execute_value,
                        "dispatch_ok": execute_ok,
                        "verified": verified,
                        "verification_required": verification_required,
                        "player_observation": player_observation,
                        "player_verification": {
                            "expected": expect_player,
                            **player_observation,
                        },
                        "gui_state": gui_state,
                        "gui_verification": gui_verification,
                        "verification_guidance": verification_guidance,
                        "note": (
                            "addon_execute dispatch succeeded; no active player was observed in the post-launch window"
                            if execute_ok and not player_started and not gui_verification_required
                            else None
                        ),
                        "error": None if ok else "; ".join(verification_errors) or "addon_execute verification failed",
                        "error_type": None if ok else "verification_failed",
                        "error_code": None,
                        "request_id": getattr(execute_result, "request_id", None),
                    }
                elif tool_name == "addon_source_inspect":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    raw_result = _addon_source_inspect(str(args.get("source_path") or "").strip())
                elif tool_name == "addon_project_map_status":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    raw_result = _addon_project_map_status(str(args.get("source_path") or "").strip())
                elif tool_name == "addon_source_tree":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    max_entries = args.get("max_entries", 200)
                    max_entries = max_entries if isinstance(max_entries, int) else 200
                    raw_result = _addon_source_tree(str(args.get("source_path") or "").strip(), max_entries=max_entries)
                elif tool_name == "kodi_player_active":
                    raw_result = await runtime["jsonrpc"].get_active_players()
                elif tool_name == "kodi_player_open":
                    args = params.arguments or {}
                    raw_result = await runtime["jsonrpc"].open_library_item(
                        media_type=args["media_type"],
                        item_id=args["item_id"],
                    )
                elif tool_name == "kodi_player_item":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    playerid = args.get("playerid", 1)
                    raw_result = await runtime["jsonrpc"].get_player_item(playerid=playerid)
                elif tool_name == "kodi_player_seek":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    playerid = args.get("playerid", 1)
                    seconds = args.get("seconds")
                    raw_result = await runtime["jsonrpc"].seek_player_to_seconds(playerid=playerid, seconds=float(seconds))
                elif tool_name == "kodi_player_pause":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    playerid = args.get("playerid", 1)
                    raw_result = await runtime["jsonrpc"].pause_player(playerid=playerid)
                elif tool_name == "kodi_player_stop":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    playerid = args.get("playerid", 1)
                    verify = args.get("verify", True)
                    verify = verify if isinstance(verify, bool) else True
                    verify_attempts = args.get("verify_attempts", 20)
                    verify_attempts = verify_attempts if isinstance(verify_attempts, int) else 20
                    verify_attempts = max(1, min(60, verify_attempts))
                    verify_delay_ms = args.get("verify_delay_ms", 250)
                    verify_delay_ms = verify_delay_ms if isinstance(verify_delay_ms, int) else 250
                    verify_delay_ms = max(0, min(5000, verify_delay_ms))
                    stable_checks = args.get("stable_checks", 5)
                    stable_checks = stable_checks if isinstance(stable_checks, int) else 5
                    stable_checks = max(1, min(20, stable_checks))
                    stop_result = await runtime["jsonrpc"].stop_player(playerid=playerid)
                    stop_value = _as_dict(stop_result)
                    if not verify or getattr(stop_result, "error", None) is not None:
                        raw_result = stop_result
                    else:
                        active_result = None
                        active_players = []
                        still_active = True
                        stable_inactive_checks = 0
                        attempts_made = 0
                        stop_attempts = 1
                        for attempt in range(verify_attempts):
                            attempts_made = attempt + 1
                            active_result = await runtime["jsonrpc"].get_active_players()
                            active_value = _as_dict(active_result)
                            active_players = active_value.get("result") if isinstance(active_value, dict) else None
                            if not isinstance(active_players, list):
                                active_players = []
                            still_active = any(
                                isinstance(player, dict) and player.get("playerid") == playerid
                                for player in active_players
                            )
                            if getattr(active_result, "error", None) is not None:
                                break
                            if still_active:
                                stable_inactive_checks = 0
                                if attempt < verify_attempts - 1:
                                    retry_stop = await runtime["jsonrpc"].stop_player(playerid=playerid)
                                    stop_attempts += 1
                                    stop_value = _as_dict(retry_stop)
                                    if getattr(retry_stop, "error", None) is not None:
                                        stop_result = retry_stop
                                        break
                            else:
                                stable_inactive_checks += 1
                                if stable_inactive_checks >= stable_checks:
                                    break
                            if attempt < verify_attempts - 1 and verify_delay_ms > 0:
                                await asyncio.sleep(verify_delay_ms / 1000)
                        stopped = stable_inactive_checks >= stable_checks and getattr(active_result, "error", None) is None
                        raw_result = {
                            "ok": stopped,
                            "playerid": playerid,
                            "stopped": stopped,
                            "stop": stop_value,
                            "stop_attempts": stop_attempts,
                            "active_players": active_players,
                            "verification_attempts": attempts_made,
                            "verification_delay_ms": verify_delay_ms,
                            "stable_checks_required": stable_checks,
                            "stable_inactive_checks": stable_inactive_checks,
                            "error": (
                                "player did not remain inactive after stop"
                                if not stopped and getattr(active_result, "error", None) is None
                                else getattr(active_result, "error", None)
                            ),
                            "error_type": "player_still_active" if not stopped else None,
                            "error_code": None,
                            "request_id": getattr(stop_result, "request_id", None),
                        }
                elif tool_name == "jsonrpc_introspect":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}

                    summary = args.get("summary", True)
                    getdescriptions = args.get("getdescriptions", False)
                    getmetadata = args.get("getmetadata", False)
                    filterbytransport = args.get("filterbytransport", False)

                    def _as_bool(v: Any, default: bool) -> bool:
                        return v if isinstance(v, bool) else default

                    raw_result = await runtime["jsonrpc"].introspect_jsonrpc(
                        summary=_as_bool(summary, True),
                        getdescriptions=_as_bool(getdescriptions, False),
                        getmetadata=_as_bool(getmetadata, False),
                        filterbytransport=_as_bool(filterbytransport, False),
                    )
                elif tool_name == "kodi_notifications_sample":
                    if runtime.get("notifications") is None:
                        raise RuntimeError("notifications probe unavailable (missing optional dependency: websockets)")

                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}

                    sample_size = args.get("sample_size", 3)
                    if not isinstance(sample_size, int):
                        sample_size = 3
                    if sample_size < 1:
                        sample_size = 1

                    listen_seconds = args.get("listen_seconds", 5)
                    if not isinstance(listen_seconds, int):
                        listen_seconds = 5
                    if listen_seconds < 1:
                        listen_seconds = 1

                    raw_result = await runtime["notifications"].listen(
                        sample_size=sample_size,
                        listen_seconds=listen_seconds,
                    )
                elif tool_name == "bridge_write_log_marker":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    message = args.get("message")
                    raw_result = await runtime["bridge"].write_bridge_log_marker(message=message)
                elif tool_name == "kodi_gui_action":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    action = str(args.get("action") or "").strip()
                    if action == "stop":
                        action_result = await runtime["jsonrpc"].execute_input_action(action=action)
                        action_value = _as_dict(action_result)
                        action_error = getattr(action_result, "error", None)
                        raw_result = {
                            "ok": action_error is None,
                            "action": action,
                            "method": "Input.ExecuteAction",
                            "jsonrpc": action_value,
                            "error": action_error,
                            "error_type": getattr(action_result, "error_type", None),
                            "error_code": getattr(action_result, "error_code", None),
                            "request_id": getattr(action_result, "request_id", None),
                        }
                    else:
                        raw_result = await runtime["bridge"].gui_action(action=action)
                elif tool_name == "kodi_gui_screenshot":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    include_image = args.get("include_image", False)
                    store = args.get("store", True)
                    include_image = include_image if isinstance(include_image, bool) else False
                    store = store if isinstance(store, bool) else True
                    raw_result = await _capture_screenshot_with_retry(
                        runtime["bridge"], include_image=(include_image or store)
                    )
                    raw_value = _as_dict(raw_result)
                    bridge_result = raw_value.get("result") if isinstance(raw_value, dict) else None
                    image_base64 = bridge_result.get("image_base64") if isinstance(bridge_result, dict) else None
                    payload_error = None
                    if isinstance(image_base64, str) and image_base64:
                        stored = None
                        if store:
                            stored = store_screenshot_from_base64(image_base64)
                            bridge_result["server_screenshot"] = stored
                            raw_size = int(stored["size_bytes"])
                        else:
                            raw_size = len(base64.b64decode(image_base64, validate=True))

                        bridge_result.pop("image_base64", None)
                        if include_image and raw_size <= INLINE_SCREENSHOT_MAX_RAW_BYTES:
                            pending_image_content = ImageContent(
                                type="image",
                                data=image_base64,
                                mimeType="image/png",
                            )
                            bridge_result["inline_image"] = {
                                "included": True,
                                "raw_size_bytes": raw_size,
                                "max_raw_size_bytes": INLINE_SCREENSHOT_MAX_RAW_BYTES,
                            }
                        elif include_image and store:
                            bridge_result["inline_image"] = {
                                "included": False,
                                "raw_size_bytes": raw_size,
                                "max_raw_size_bytes": INLINE_SCREENSHOT_MAX_RAW_BYTES,
                                "reason": "image exceeds inline MCP payload limit; use server_screenshot.url",
                            }
                        elif include_image:
                            payload_error = {
                                "ok": False,
                                "error": "screenshot exceeds inline MCP payload limit; retry with store=true",
                                "error_type": "payload_too_large",
                                "error_code": None,
                                "raw_size_bytes": raw_size,
                                "max_raw_size_bytes": INLINE_SCREENSHOT_MAX_RAW_BYTES,
                                "retry": {"store": True, "include_image": False},
                            }
                    if payload_error is not None:
                        raw_result = payload_error
                    elif isinstance(raw_value, dict) and isinstance(bridge_result, dict):
                        raw_value["result"] = bridge_result
                        raw_result = raw_value
                elif tool_name == "kodi_gui_state":
                    raw_result = await runtime["bridge"].gui_state()
                elif tool_name == "managed_addon_register":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    raw_result = managed_addon_register(source_path=str(args.get("source_path") or "").strip())
                elif tool_name == "managed_addon_list":
                    raw_result = managed_addon_list()
                elif tool_name == "managed_addon_get":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    raw_result = managed_addon_get(managed_addon_id=str(args.get("managed_addon_id") or "").strip())
                elif tool_name == "managed_addon_build_publish_and_stage":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    verify = args.get("verify", True)
                    if not isinstance(verify, bool):
                        verify = True
                    raw_result = await managed_addon_build_publish_and_stage(
                        managed_addon_id=str(args.get("managed_addon_id") or "").strip(),
                        version_policy=str(args.get("version_policy") or "").strip(),
                        explicit_version=(
                            str(args.get("explicit_version") or "").strip()
                            if args.get("explicit_version") is not None
                            else None
                        ),
                        repo_version=(
                            str(args.get("repo_version") or "").strip()
                            if args.get("repo_version") is not None
                            else None
                        ),
                        verify=verify,
                    )
                elif tool_name == "managed_addon_build_publish_stage_and_apply":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    verify = args.get("verify", True)
                    if not isinstance(verify, bool):
                        verify = True
                    raw_result = await managed_addon_build_publish_stage_and_apply(
                        managed_addon_id=str(args.get("managed_addon_id") or "").strip(),
                        version_policy=str(args.get("version_policy") or "").strip(),
                        explicit_version=(
                            str(args.get("explicit_version") or "").strip()
                            if args.get("explicit_version") is not None
                            else None
                        ),
                        repo_version=(
                            str(args.get("repo_version") or "").strip()
                            if args.get("repo_version") is not None
                            else None
                        ),
                        verify=verify,
                        bridge_tool=runtime["bridge"],
                        jsonrpc_tool=runtime["jsonrpc"],
                    )
                elif tool_name == "managed_addon_validate_state":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    managed_addon_id = str(args.get("managed_addon_id") or "").strip()
                    registry_result = managed_addon_get(managed_addon_id=managed_addon_id)

                    entry = (registry_result.get("managed_addon") if registry_result.get("ok") else None)
                    last_build = entry.get("last_build") if isinstance(entry, dict) else None

                    registry_exists = bool(registry_result.get("ok") and isinstance(entry, dict))
                    enabled = bool(entry.get("enabled", False)) if isinstance(entry, dict) else False
                    source_path = str(entry.get("source_path") or "") if isinstance(entry, dict) else ""
                    addon_id = str(entry.get("addon_id") or "") if isinstance(entry, dict) else ""
                    last_observed_version = str(entry.get("last_observed_version") or "") if isinstance(entry, dict) else ""

                    # Artifact presence
                    def _exists(p: str) -> bool:
                        try:
                            from pathlib import Path as _Path

                            return bool(p and _Path(p).exists())
                        except Exception:
                            return False

                    last_build_zip_exists = _exists(str((last_build or {}).get("zip_path") or "")) if isinstance(last_build, dict) else False
                    published_repo_zip_exists = _exists(str((last_build or {}).get("repo_zip_path") or "")) if isinstance(last_build, dict) else False

                    dev_repo_dir = AUTHORITATIVE_REPO_ROOT / "dev-repo"
                    dev_repo_exists = dev_repo_dir.exists()
                    addons_xml_exists = (dev_repo_dir / "addons.xml").exists()
                    addons_xml_md5_exists = (dev_repo_dir / "addons.xml.md5").exists()

                    # Best-effort bridge checks
                    reachable = False
                    bridge_error = None
                    mcp_state_read_ok = False
                    registration_present = None
                    registration_stale = None
                    repo_zip_file_exists = None
                    repo_zip_special_path = None
                    dev_setup_available = None

                    try:
                        health = await runtime["bridge"].get_bridge_health()
                        bridge_error = getattr(health, "error", None)
                        reachable = bool(bridge_error is None)
                    except Exception as exc:
                        reachable = False
                        bridge_error = str(exc)

                    if reachable:
                        try:
                            view, resp = await read_addon_state()
                            mcp_state_read_ok = bool(view.transport_ok)
                            derived = None
                            repo_zip = None
                            if resp.error is None and isinstance((resp.result or {}).get("result"), dict):
                                result_obj = (resp.result.get("result") or {})
                                derived = result_obj.get("derived")
                                repo_zip = result_obj.get("repo_zip")
                            if isinstance(derived, dict):
                                registration_present = derived.get("registration_present")
                                registration_stale = derived.get("registration_stale")
                                repo_zip_file_exists = derived.get("repo_zip_file_exists")
                                dev_setup_available = derived.get("dev_setup_available")
                            if isinstance(repo_zip, dict):
                                special_path = repo_zip.get("special_path")
                                if isinstance(special_path, str) and special_path.strip():
                                    repo_zip_special_path = special_path.strip()
                        except Exception as exc:
                            mcp_state_read_ok = False
                            bridge_error = str(exc)

                    # Overall readiness
                    ready_for_build = bool(registry_exists and enabled and source_path and _exists(source_path) and _exists(str(Path(source_path) / "addon.xml")))
                    ready_for_publish = bool(ready_for_build and last_build_zip_exists and dev_repo_exists)
                    ready_for_stage = bool(ready_for_publish and reachable and mcp_state_read_ok and bool(registration_present) and not bool(registration_stale))
                    ready_for_kodi_install = bool(ready_for_stage and bool(repo_zip_file_exists) and bool(dev_setup_available))

                    raw_result = {
                        "ok": True,
                        "managed_addon_id": managed_addon_id,
                        "registry": {
                            "exists": registry_exists,
                            "enabled": enabled,
                            "addon_id": addon_id,
                            "source_path": source_path,
                            "last_observed_version": last_observed_version,
                            "last_build": last_build if isinstance(last_build, dict) else None,
                        },
                        "artifacts": {
                            "last_build_zip_exists": last_build_zip_exists,
                            "published_repo_zip_exists": published_repo_zip_exists,
                            "dev_repo_exists": dev_repo_exists,
                            "addons_xml_exists": addons_xml_exists,
                            "addons_xml_md5_exists": addons_xml_md5_exists,
                        },
                        "kodi_bridge": {
                            "reachable": reachable,
                            "mcp_state_read_ok": mcp_state_read_ok,
                            "error": bridge_error,
                            "registration_present": registration_present,
                            "registration_stale": registration_stale,
                            "repo_zip_file_exists": repo_zip_file_exists,
                            "repo_zip_special_path": repo_zip_special_path,
                            "dev_setup_available": dev_setup_available,
                        },
                        "summary": {
                            "ready_for_build": ready_for_build,
                            "ready_for_publish": ready_for_publish,
                            "ready_for_stage": ready_for_stage,
                            "ready_for_kodi_install": ready_for_kodi_install,
                        },
                    }
                elif tool_name == "artifact_upload_zip":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    raw_result = _artifact_upload_zip(
                        zip_base64=str(args.get("zip_base64") or ""),
                        filename=str(args.get("filename") or "upload.zip"),
                        addon_id=(str(args.get("addon_id") or "").strip() if args.get("addon_id") is not None else None),
                        version=(str(args.get("version") or "").strip() if args.get("version") is not None else None),
                    )
                elif tool_name == "repo_publish_artifact":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    raw_result = _repo_publish_artifact(
                        artifact_id=str(args.get("artifact_id") or "").strip(),
                        addon_id=str(args.get("addon_id") or "").strip(),
                        addon_name=str(args.get("addon_name") or "").strip(),
                        addon_version=str(args.get("addon_version") or "").strip(),
                        provider_name=(
                            str(args.get("provider_name") or "kodi_mcp").strip() or "kodi_mcp"
                        ),
                    )
                elif tool_name == "repo_stage_current_dev_repo":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    verify = args.get("verify", True)
                    if not isinstance(verify, bool):
                        verify = True
                    repo_version = args.get("repo_version")
                    raw_result = await _repo_stage_current_dev_repo(
                        repo_version=(str(repo_version).strip() if isinstance(repo_version, str) and repo_version.strip() else None),
                        verify=verify,
                    )
                elif tool_name == "repo_stage_and_apply_addon":
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    verify = args.get("verify", True)
                    if not isinstance(verify, bool):
                        verify = True
                    timeout_seconds = args.get("timeout_seconds", 45)
                    if not isinstance(timeout_seconds, int) or timeout_seconds < 1:
                        timeout_seconds = 45
                    poll_interval_seconds = args.get("poll_interval_seconds", 4)
                    if not isinstance(poll_interval_seconds, int) or poll_interval_seconds < 1:
                        poll_interval_seconds = 4
                    repo_version = args.get("repo_version")
                    raw_result = await _repo_stage_and_apply_addon(
                        addonid=str(args.get("addonid") or "").strip(),
                        runtime_bridge_tool=runtime["bridge"],
                        runtime_jsonrpc_tool=runtime["jsonrpc"],
                        repo_version=(str(repo_version).strip() if isinstance(repo_version, str) and repo_version.strip() else None),
                        verify=verify,
                        timeout_seconds=timeout_seconds,
                        poll_interval_seconds=poll_interval_seconds,
                        target_version=(
                            str(args.get("target_version")).strip()
                            if isinstance(args.get("target_version"), str) and str(args.get("target_version")).strip()
                            else None
                        ),
                    )
                elif tool_name in {"repo_publish_stage_apply_artifact", "addon_dev_loop"}:
                    args = params.arguments or {}
                    if not isinstance(args, dict):
                        args = {}
                    verify = args.get("verify", True)
                    if not isinstance(verify, bool):
                        verify = True
                    timeout_seconds = args.get("timeout_seconds", 45)
                    if not isinstance(timeout_seconds, int) or timeout_seconds < 1:
                        timeout_seconds = 45
                    poll_interval_seconds = args.get("poll_interval_seconds", 4)
                    if not isinstance(poll_interval_seconds, int) or poll_interval_seconds < 1:
                        poll_interval_seconds = 4
                    repo_version = args.get("repo_version")
                    raw_result = await _repo_publish_stage_apply_artifact(
                        artifact_id=str(args.get("artifact_id") or "").strip(),
                        addon_id=str(args.get("addon_id") or "").strip(),
                        addon_name=str(args.get("addon_name") or "").strip(),
                        addon_version=str(args.get("addon_version") or "").strip(),
                        provider_name=(
                            str(args.get("provider_name") or "kodi_mcp").strip() or "kodi_mcp"
                        ),
                        repo_version=(str(repo_version).strip() if isinstance(repo_version, str) and repo_version.strip() else None),
                        verify=verify,
                        timeout_seconds=timeout_seconds,
                        poll_interval_seconds=poll_interval_seconds,
                        runtime_bridge_tool=runtime["bridge"],
                        runtime_jsonrpc_tool=runtime["jsonrpc"],
                    )
                elif tool_name == "kodi_status":
                    raw_result = await _kodi_status(runtime)
                else:
                    raise NotImplementedError(f"no dispatch implementation for whitelisted tool {tool_name!r}")

                raw_value = _as_dict(raw_result)
                latency_ms = int((time.time() - start) * 1000)

                # Normalize: ResponseMessage dict vs plain dict
                if isinstance(raw_value, dict) and "result" in raw_value and "error" in raw_value:
                    ok = raw_value.get("error") is None
                    envelope = {
                        "ok": ok,
                        "tool": tool_name,
                        "data": raw_value.get("result"),
                        "error": raw_value.get("error"),
                        "error_type": raw_value.get("error_type"),
                        "error_code": raw_value.get("error_code"),
                        "latency_ms": raw_value.get("latency_ms") or latency_ms,
                        "request_id": raw_value.get("request_id"),
                        "raw": raw_value,
                    }
                else:
                    ok = raw_value.get("ok", True) if isinstance(raw_value, dict) else True
                    error = raw_value.get("error") if isinstance(raw_value, dict) else None
                    error_type = raw_value.get("error_type") if isinstance(raw_value, dict) else None
                    error_code = raw_value.get("error_code") if isinstance(raw_value, dict) else None
                    if isinstance(raw_value, dict) and not bool(ok):
                        error, error_type = _failure_fields(raw_value, tool_name)
                    envelope = {
                        "ok": bool(ok),
                        "tool": tool_name,
                        "data": raw_value,
                        "error": error,
                        "error_type": error_type,
                        "error_code": error_code,
                        "latency_ms": latency_ms,
                        "request_id": raw_value.get("request_id") if isinstance(raw_value, dict) else None,
                        "raw": raw_value,
                    }
                if tool_name in LOG_TOOL_NAMES and envelope.get("ok"):
                    envelope["raw"] = _compact_log_raw(raw_value, envelope.get("data"))
            except Exception as exc:
                latency_ms = int((time.time() - start) * 1000)
                envelope = {
                    "ok": False,
                    "tool": tool_name,
                    "data": None,
                    "error": f"request failed: {exc}",
                    "error_type": "unknown_error",
                    "error_code": None,
                    "latency_ms": latency_ms,
                    "request_id": None,
                    "raw": None,
                }

            extra_content = (
                [pending_image_content]
                if pending_image_content is not None and envelope.get("ok")
                else None
            )
            return _result_from_envelope(envelope, extra_content=extra_content)

        payload = ErrorData(code=0, message=f"Tool not implemented: {tool_name}", data=None)
        return             CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text=payload.model_dump_json(indent=2))],
            )

    server = Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=(
            "Kodi MCP server for Kodi status, bridge diagnostics, GUI navigation, "
            "screenshots, and managed addon workflows."
        ),
    )
    # MCP 2.x: register low-level request handlers through the public API.
    # ``initialize`` is answered by the SDK runner from the initialization
    # options below; the dual-era connection loop serves both legacy
    # handshake-era clients and the modern 2026-07-28 protocol.
    server.add_request_handler("tools/list", PaginatedRequestParams, _handle_list_tools)
    server.add_request_handler("tools/call", CallToolRequestParams, _handle_call_tool)

    init_options = server.create_initialization_options()

    return server, init_options
