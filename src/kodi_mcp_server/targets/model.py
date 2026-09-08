"""Strict, secret-free representation of one Kodi target."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlparse

_TARGET_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,63})$")
_AUTH_REFERENCE_RE = re.compile(r"^env:[A-Za-z_][A-Za-z0-9_]*$")


class TargetValidationError(ValueError):
    """Raised when target configuration is malformed."""


def _unknown_fields(data: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise TargetValidationError(f"unknown {label} fields: {', '.join(unknown)}")


def _validate_url(value: str, field: str, schemes: set[str], *, optional: bool = False) -> None:
    if not isinstance(value, str):
        raise TargetValidationError(f"{field} must be a string")
    if optional and not value:
        return
    parsed = urlparse(value)
    if parsed.scheme not in schemes or not parsed.netloc:
        expected = "/".join(sorted(schemes))
        raise TargetValidationError(
            f"{field} must be an absolute {expected} URL with an authority"
        )
    if parsed.username is not None or parsed.password is not None:
        raise TargetValidationError(
            f"{field} must not contain inline credentials; use auth references"
        )


def _validate_auth_reference(value: str | None, field: str) -> None:
    if value is not None and (
        not isinstance(value, str) or not _AUTH_REFERENCE_RE.fullmatch(value)
    ):
        raise TargetValidationError(
            f"{field} auth reference must use env:VARIABLE; inline secrets are not allowed"
        )


@dataclass(frozen=True)
class TargetEndpoints:
    """Network coordinates for one Kodi and its local bridge."""

    jsonrpc_url: str
    bridge_url: str
    websocket_url: str = ""
    tcp_host: str = ""
    tcp_port: int = 9090

    def __post_init__(self) -> None:
        # Empty HTTP endpoints remain representable only for the legacy stdio
        # missing-config path; registry-authored targets require both values.
        _validate_url(self.jsonrpc_url, "jsonrpc_url", {"http", "https"}, optional=True)
        _validate_url(self.bridge_url, "bridge_url", {"http", "https"}, optional=True)
        _validate_url(
            self.websocket_url,
            "websocket_url",
            {"ws", "wss"},
            optional=True,
        )
        if not isinstance(self.tcp_host, str):
            raise TargetValidationError("tcp_host must be a string")
        if isinstance(self.tcp_port, bool) or not isinstance(self.tcp_port, int):
            raise TargetValidationError("tcp_port must be an integer")
        if not 1 <= self.tcp_port <= 65535:
            raise TargetValidationError("tcp_port must be from 1 to 65535")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TargetEndpoints":
        if not isinstance(data, Mapping):
            raise TargetValidationError("endpoints must be an object")
        _unknown_fields(
            data,
            {"jsonrpc_url", "bridge_url", "websocket_url", "tcp_host", "tcp_port"},
            "endpoint",
        )
        try:
            return cls(
                jsonrpc_url=data["jsonrpc_url"],
                bridge_url=data["bridge_url"],
                websocket_url=data.get("websocket_url", ""),
                tcp_host=data.get("tcp_host", ""),
                tcp_port=data.get("tcp_port", 9090),
            )
        except KeyError as exc:
            raise TargetValidationError(f"missing endpoint field: {exc.args[0]}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "jsonrpc_url": self.jsonrpc_url,
            "bridge_url": self.bridge_url,
            "websocket_url": self.websocket_url,
            "tcp_host": self.tcp_host,
            "tcp_port": self.tcp_port,
        }


@dataclass(frozen=True, repr=False)
class TargetAuthReferences:
    """Environment references for credentials; never credential values."""

    jsonrpc_username: str | None = None
    jsonrpc_password: str | None = None
    bridge_token: str | None = None

    def __post_init__(self) -> None:
        _validate_auth_reference(self.jsonrpc_username, "jsonrpc_username")
        _validate_auth_reference(self.jsonrpc_password, "jsonrpc_password")
        _validate_auth_reference(self.bridge_token, "bridge_token")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "TargetAuthReferences":
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise TargetValidationError("auth must be an object")
        _unknown_fields(
            data,
            {"jsonrpc_username", "jsonrpc_password", "bridge_token"},
            "auth",
        )
        return cls(
            jsonrpc_username=data.get("jsonrpc_username"),
            jsonrpc_password=data.get("jsonrpc_password"),
            bridge_token=data.get("bridge_token"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "jsonrpc_username": self.jsonrpc_username,
            "jsonrpc_password": self.jsonrpc_password,
            "bridge_token": self.bridge_token,
        }


@dataclass(frozen=True)
class Target:
    """Stable internal identity and connection description for one Kodi."""

    target_id: str
    name: str
    endpoints: TargetEndpoints
    auth: TargetAuthReferences = field(
        default_factory=TargetAuthReferences,
        repr=False,
    )
    groups: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    expected_kodi_version: str | None = None
    timeout_seconds: int = 10

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, str) or not _TARGET_ID_RE.fullmatch(
            self.target_id
        ):
            raise TargetValidationError(
                "target id must be 1-64 lowercase letters, digits, dots, underscores, or dashes and start alphanumeric"
            )
        if not isinstance(self.name, str) or not self.name.strip():
            raise TargetValidationError("target name must be a nonempty string")
        if not isinstance(self.endpoints, TargetEndpoints):
            raise TargetValidationError("endpoints must be TargetEndpoints")
        if not isinstance(self.auth, TargetAuthReferences):
            raise TargetValidationError("auth must be TargetAuthReferences")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, int
        ):
            raise TargetValidationError("timeout_seconds must be an integer")
        if self.timeout_seconds < 1:
            raise TargetValidationError("timeout_seconds must be positive")
        if self.expected_kodi_version is not None and not isinstance(
            self.expected_kodi_version, str
        ):
            raise TargetValidationError("expected_kodi_version must be a string or null")
        object.__setattr__(self, "groups", self._normalize_labels(self.groups, "groups"))
        object.__setattr__(self, "tags", self._normalize_labels(self.tags, "tags"))

    @staticmethod
    def _normalize_labels(values: tuple[str, ...], field: str) -> tuple[str, ...]:
        if not isinstance(values, (tuple, list)) or any(
            not isinstance(value, str) or not value for value in values
        ):
            raise TargetValidationError(f"{field} must contain nonempty strings")
        return tuple(sorted(set(values)))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Target":
        """Parse the internal configuration form, not an agent-facing schema."""
        if not isinstance(data, Mapping):
            raise TargetValidationError("target must be an object")
        _unknown_fields(
            data,
            {
                "id",
                "name",
                "endpoints",
                "auth",
                "groups",
                "tags",
                "expected_kodi_version",
                "timeout_seconds",
            },
            "target",
        )
        try:
            return cls(
                target_id=data["id"],
                name=data["name"],
                endpoints=TargetEndpoints.from_dict(data["endpoints"]),
                auth=TargetAuthReferences.from_dict(data.get("auth")),
                groups=data.get("groups", ()),
                tags=data.get("tags", ()),
                expected_kodi_version=data.get("expected_kodi_version"),
                timeout_seconds=data.get("timeout_seconds", 10),
            )
        except KeyError as exc:
            raise TargetValidationError(f"missing target field: {exc.args[0]}") from exc

    def to_dict(self) -> dict[str, Any]:
        """Serialize internal configuration; never use as a public tool result."""
        return {
            "id": self.target_id,
            "name": self.name,
            "endpoints": self.endpoints.to_dict(),
            "auth": self.auth.to_dict(),
            "groups": list(self.groups),
            "tags": list(self.tags),
            "expected_kodi_version": self.expected_kodi_version,
            "timeout_seconds": self.timeout_seconds,
        }
