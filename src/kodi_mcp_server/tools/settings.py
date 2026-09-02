"""Typed, explicitly allowlisted Kodi core-setting administration."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any

from kodi_mcp_server.models.messages import ErrorType, ResponseMessage


@dataclass(frozen=True)
class SettingPolicy:
    id: str
    label: str
    help: str
    section: str
    category: str
    value_type: str
    writable: bool
    mutation_reason: str | None
    supported_kodi_major: tuple[int, ...]
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float | None = None
    allowed_values: tuple[bool | int | float | str, ...] = ()
    max_length: int | None = None

    def public_metadata(self) -> dict[str, Any]:
        constraints: dict[str, Any] = {}
        if self.minimum is not None:
            constraints["minimum"] = self.minimum
        if self.maximum is not None:
            constraints["maximum"] = self.maximum
        if self.step is not None:
            constraints["step"] = self.step
        if self.allowed_values:
            constraints["allowed_values"] = list(self.allowed_values)
        if self.max_length is not None:
            constraints["max_length"] = self.max_length
        return {
            "id": self.id,
            "label": self.label,
            "help": self.help,
            "section": self.section,
            "category": self.category,
            "type": self.value_type,
            "readable": True,
            "writable": self.writable,
            "mutation_unavailable_reason": self.mutation_reason,
            "policy": "explicit_allowlist",
            "supported_kodi_major": list(self.supported_kodi_major),
            "constraints": constraints,
        }


_POLICIES = (
    SettingPolicy(
        id="filelists.showextensions",
        label="Show file extensions",
        help="Show filename extensions in Kodi file lists.",
        section="media",
        category="filelists",
        value_type="boolean",
        writable=True,
        mutation_reason=None,
        supported_kodi_major=(19, 20, 21, 22),
    ),
    SettingPolicy(
        id="filelists.showhidden",
        label="Show hidden files and directories",
        help="Report whether Kodi file lists expose hidden entries.",
        section="media",
        category="filelists",
        value_type="boolean",
        writable=False,
        mutation_reason="hidden-file exposure remains under local user control",
        supported_kodi_major=(19, 20, 21, 22),
    ),
    SettingPolicy(
        id="lookandfeel.skinzoom",
        label="Interface zoom",
        help="Resize Kodi's user interface within the supported zoom range.",
        section="interface",
        category="skin",
        value_type="integer",
        writable=True,
        mutation_reason=None,
        supported_kodi_major=(19, 20, 21, 22),
        minimum=-30,
        maximum=30,
        step=2,
    ),
    SettingPolicy(
        id="locale.country",
        label="Regional display format",
        help="Choose one allowlisted Kodi regional date, time, and temperature format.",
        section="interface",
        category="regional",
        value_type="string",
        writable=True,
        mutation_reason=None,
        supported_kodi_major=(19, 20, 21, 22),
        allowed_values=(
            "Australia (12h)",
            "Australia (24h)",
            "Canada",
            "Central Europe",
            "India (12h)",
            "India (24h)",
            "UK (12h)",
            "UK (24h)",
            "USA (12h)",
            "USA (24h)",
        ),
        max_length=64,
    ),
    SettingPolicy(
        id="subtitles.marginvertical",
        label="Subtitle vertical margin",
        help="Set the top and bottom subtitle margin on Kodi 20 or newer.",
        section="player",
        category="subtitles",
        value_type="number",
        writable=True,
        mutation_reason=None,
        supported_kodi_major=(20, 21, 22),
        minimum=0.0,
        maximum=50.0,
        step=0.05,
    ),
    SettingPolicy(
        id="subtitles.style",
        label="Subtitle style",
        help="Choose normal, bold, italics, or bold italics subtitle rendering.",
        section="player",
        category="subtitles",
        value_type="integer",
        writable=True,
        mutation_reason=None,
        supported_kodi_major=(19, 20, 21, 22),
        allowed_values=(0, 1, 2, 3),
    ),
    SettingPolicy(
        id="videoplayer.adjustrefreshrate",
        label="Adjust display refresh rate",
        help="Report Kodi's display refresh-rate adjustment policy.",
        section="player",
        category="videoplayer",
        value_type="integer",
        writable=False,
        mutation_reason="display-mode switching remains read-only in the first settings administration surface",
        supported_kodi_major=(19, 20, 21, 22),
        allowed_values=(0, 1, 2, 3),
    ),
)

POLICY_BY_ID = {policy.id: policy for policy in _POLICIES}

_SENSITIVE_ID_RE = re.compile(
    r"(?:password|passwd|token|secret|credential|proxy|masterlock|webserver|"
    r"remotecontrol|airplay|smb|nfs|source|path|folder|directory|database)",
    re.IGNORECASE,
)


class SettingsTool:
    """Expose only the product's audited Kodi setting policy."""

    def __init__(self, jsonrpc_tool: Any):
        self.jsonrpc = jsonrpc_tool

    @staticmethod
    def _error(
        message: str,
        error_type: ErrorType,
        *,
        request_id: str = "settings-policy",
        error_code: int | None = None,
    ) -> ResponseMessage:
        return ResponseMessage(
            request_id=request_id,
            result=None,
            error=message,
            error_type=error_type,
            error_code=error_code,
            latency_ms=0,
        )

    @staticmethod
    def _value_matches_type(value: Any, value_type: str) -> bool:
        if value_type == "boolean":
            return isinstance(value, bool)
        if value_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if value_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if value_type == "string":
            return isinstance(value, str)
        return False

    @classmethod
    def _value_within_policy(cls, value: Any, policy: SettingPolicy) -> bool:
        if not cls._value_matches_type(value, policy.value_type):
            return False
        if isinstance(value, str):
            if policy.max_length is not None and len(value) > policy.max_length:
                return False
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                return False
            if (
                "://" in value
                or value.startswith(("/", "\\", "~"))
                or re.match(r"^[A-Za-z]:[\\/]", value)
            ):
                return False
        if policy.allowed_values and value not in policy.allowed_values:
            return False
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value):
                return False
            if policy.minimum is not None and value < policy.minimum:
                return False
            if policy.maximum is not None and value > policy.maximum:
                return False
            if policy.step is not None:
                anchor = policy.minimum if policy.minimum is not None else 0
                try:
                    if (
                        (Decimal(str(value)) - Decimal(str(anchor)))
                        % Decimal(str(policy.step))
                        != 0
                    ):
                        return False
                except (InvalidOperation, ZeroDivisionError):
                    return False
        return True

    @classmethod
    def _metadata_matches_policy(
        cls, metadata: dict[str, Any], policy: SettingPolicy
    ) -> bool:
        if (
            metadata.get("type") != policy.value_type
            or not isinstance(metadata.get("enabled"), bool)
            or not cls._value_within_policy(metadata.get("value"), policy)
            or not cls._value_within_policy(metadata.get("default"), policy)
        ):
            return False
        for key in ("minimum", "maximum", "step"):
            expected = getattr(policy, key)
            if expected is not None and metadata.get(key) != expected:
                return False
        if policy.allowed_values:
            options = metadata.get("options")
            if not isinstance(options, list):
                return False
            actual_values = [
                option.get("value")
                for option in options
                if isinstance(option, dict) and "value" in option
            ]
            if any(value not in actual_values for value in policy.allowed_values):
                return False
        return True

    async def get_setting(self, setting_id: str) -> ResponseMessage:
        if _SENSITIVE_ID_RE.search(setting_id):
            return self._error(
                "sensitive setting is excluded from the MCP settings policy",
                ErrorType.INVALID_OPERATION,
            )
        policy = POLICY_BY_ID.get(setting_id)
        if policy is None:
            return self._error(
                "setting is outside the explicit MCP settings policy",
                ErrorType.INVALID_OPERATION,
            )

        response = await self.jsonrpc.execute_jsonrpc(
            "Settings.GetSettings",
            {
                "level": "expert",
                "filter": {
                    "section": policy.section,
                    "category": policy.category,
                },
            },
        )
        if response.error is not None:
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=response.error,
                error_type=response.error_type,
                error_code=response.error_code,
                latency_ms=response.latency_ms,
            )

        result = response.result
        settings = result.get("settings") if isinstance(result, dict) else None
        if not isinstance(settings, list) or any(
            not isinstance(item, dict) for item in settings
        ):
            return self._error(
                "Kodi returned malformed settings metadata",
                ErrorType.INVALID_RESPONSE,
                request_id=response.request_id,
            )
        metadata = next(
            (item for item in settings if item.get("id") == setting_id),
            None,
        )
        if metadata is None:
            return self._error(
                "supported setting is unavailable on this Kodi target",
                ErrorType.NOT_FOUND,
                request_id=response.request_id,
            )
        if not self._metadata_matches_policy(metadata, policy):
            return self._error(
                "Kodi setting metadata does not match the audited MCP policy",
                ErrorType.INVALID_RESPONSE,
                request_id=response.request_id,
            )

        setting = policy.public_metadata()
        setting.update(
            {
                "value": metadata["value"],
                "default": metadata["default"],
                "enabled": metadata["enabled"],
            }
        )
        return ResponseMessage(
            request_id=response.request_id,
            result={"setting": setting},
            error=None,
            latency_ms=response.latency_ms,
        )

    async def set_setting(self, setting_id: str, value: Any) -> ResponseMessage:
        if _SENSITIVE_ID_RE.search(setting_id):
            return self._error(
                "sensitive setting is excluded from the MCP settings policy",
                ErrorType.INVALID_OPERATION,
            )
        policy = POLICY_BY_ID.get(setting_id)
        if policy is None:
            return self._error(
                "setting is outside the explicit MCP settings policy",
                ErrorType.INVALID_OPERATION,
            )
        if not policy.writable:
            return self._error(
                policy.mutation_reason or "setting is read-only under MCP policy",
                ErrorType.INVALID_OPERATION,
            )
        if not self._value_within_policy(value, policy):
            return self._error(
                "requested value does not satisfy the setting type or policy bounds",
                ErrorType.INVALID_OPERATION,
            )

        before_response = await self.get_setting(setting_id)
        if before_response.error is not None:
            return before_response
        before_setting = (before_response.result or {}).get("setting") or {}
        before = before_setting.get("value")
        if before == value:
            return ResponseMessage(
                request_id=before_response.request_id,
                result={
                    "setting_id": setting_id,
                    "before": before,
                    "requested": value,
                    "after": before,
                    "changed": False,
                    "verified": True,
                },
                error=None,
                latency_ms=before_response.latency_ms,
            )

        set_response = await self.jsonrpc.execute_jsonrpc(
            "Settings.SetSettingValue",
            {"setting": setting_id, "value": value},
        )
        if set_response.error is not None:
            return ResponseMessage(
                request_id=set_response.request_id,
                result=None,
                error=set_response.error,
                error_type=set_response.error_type,
                error_code=set_response.error_code,
                latency_ms=set_response.latency_ms,
            )
        if set_response.result is not True:
            return self._error(
                "Kodi did not confirm the requested setting mutation",
                ErrorType.INVALID_RESPONSE,
                request_id=set_response.request_id,
            )

        after_response = await self.get_setting(setting_id)
        if after_response.error is not None:
            return self._error(
                "Kodi accepted the setting mutation but read-back verification failed",
                after_response.error_type or ErrorType.UNKNOWN_ERROR,
                request_id=after_response.request_id,
                error_code=after_response.error_code,
            )
        after_setting = (after_response.result or {}).get("setting") or {}
        after = after_setting.get("value")
        verified = after == value
        result = {
            "setting_id": setting_id,
            "before": before,
            "requested": value,
            "after": after,
            "changed": before != after,
            "verified": verified,
        }
        if not verified:
            return ResponseMessage(
                request_id=after_response.request_id,
                result=result,
                error="setting postcondition did not match the requested value",
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=after_response.latency_ms,
            )
        return ResponseMessage(
            request_id=after_response.request_id,
            result=result,
            error=None,
            latency_ms=after_response.latency_ms,
        )

    async def list_settings(
        self,
        *,
        section: str | None = None,
        category: str | None = None,
        writable: bool | None = None,
        search: str | None = None,
        start: int = 0,
        limit: int = 10,
    ) -> ResponseMessage:
        query = search.casefold() if search is not None else None
        items = [policy.public_metadata() for policy in _POLICIES]
        if section is not None:
            items = [item for item in items if item["section"] == section]
        if category is not None:
            items = [item for item in items if item["category"] == category]
        if writable is not None:
            items = [item for item in items if item["writable"] is writable]
        if query is not None:
            items = [
                item
                for item in items
                if query in f"{item['id']} {item['label']} {item['help']}".casefold()
            ]

        total = len(items)
        page = items[start : start + limit]
        return ResponseMessage(
            request_id="settings-list",
            result={
                "items": page,
                "empty": not page,
                "pagination": {
                    "start": start,
                    "end": start + len(page),
                    "total": total,
                    "limit": limit,
                    "has_more": start + len(page) < total,
                },
            },
            error=None,
            latency_ms=0,
        )
