"""Lazy, per-target construction and caching of transport-backed tools."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from kodi_mcp_server.composition import (
    build_bridge_tool,
    build_jsonrpc_tool,
    build_notification_probe,
)

from .model import Target
from .registry import TargetRegistry
from .resolver import resolve_target


@dataclass(frozen=True, repr=False)
class ResolvedTargetAuth:
    """Ephemeral credential values used only to construct transports."""

    jsonrpc_username: str = ""
    jsonrpc_password: str = ""
    bridge_token: str = ""


@dataclass(frozen=True)
class TargetTransports:
    """Transport-backed tools for one target."""

    jsonrpc: Any
    bridge: Any
    notifications: Any | None


class TargetTransportPool:
    """Build each target transport set once and reuse it for process lifetime."""

    def __init__(
        self,
        registry: TargetRegistry,
        *,
        environ: Mapping[str, str] | None = None,
        legacy_default_auth: ResolvedTargetAuth | None = None,
    ) -> None:
        self._registry = registry
        self._environ = os.environ if environ is None else environ
        self._legacy_default_auth = legacy_default_auth
        self._cache: dict[str, TargetTransports] = {}

    def get(self, target_id: str = "default") -> TargetTransports:
        cached = self._cache.get(target_id)
        if cached is not None:
            return cached
        return self.get_for_target(resolve_target(self._registry, target_id))

    def get_for_target(self, target: Target) -> TargetTransports:
        """Return transports for an already-resolved canonical target."""

        cached = self._cache.get(target.target_id)
        if cached is not None:
            return cached
        transports = self._build(target)
        self._cache[target.target_id] = transports
        return transports

    def _build(self, target: Target) -> TargetTransports:
        auth = (
            self._legacy_default_auth
            if target.target_id == "default" and self._legacy_default_auth is not None
            else ResolvedTargetAuth(
                jsonrpc_username=self._resolve_reference(
                    target.auth.jsonrpc_username
                ),
                jsonrpc_password=self._resolve_reference(
                    target.auth.jsonrpc_password
                ),
                bridge_token=self._resolve_reference(target.auth.bridge_token),
            )
        )
        jsonrpc = build_jsonrpc_tool(
            url=target.endpoints.jsonrpc_url,
            username=auth.jsonrpc_username,
            password=auth.jsonrpc_password,
            timeout=target.timeout_seconds,
        )
        bridge = build_bridge_tool(
            base_url=target.endpoints.bridge_url,
            timeout=target.timeout_seconds,
            token=auth.bridge_token,
        )
        try:
            notifications = build_notification_probe(
                tcp_host=target.endpoints.tcp_host,
                tcp_port=target.endpoints.tcp_port,
                websocket_url=target.endpoints.websocket_url,
                timeout=target.timeout_seconds,
            )
        except Exception:
            # Preserve the current optional-dependency behavior of build_runtime.
            notifications = None
        return TargetTransports(
            jsonrpc=jsonrpc,
            bridge=bridge,
            notifications=notifications,
        )

    def _resolve_reference(self, reference: str | None) -> str:
        if reference is None:
            return ""
        _, variable = reference.split(":", 1)
        return self._environ.get(variable, "")
