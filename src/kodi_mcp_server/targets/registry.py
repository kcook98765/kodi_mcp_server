"""Target registry and deterministic configuration-source loading.

The implicit legacy ``default`` target is always authoritative. Optional file
configuration is loaded next and inline JSON last; inline entries replace a
same-ID file entry. Duplicate IDs inside one source keep the first entry and
warn. Malformed optional sources or entries warn and are skipped so they cannot
break the legacy single-target runtime.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import (
    Target,
    TargetAuthReferences,
    TargetEndpoints,
    TargetValidationError,
    derive_mutation_domain_id,
)


class TargetConfigWarning(UserWarning):
    """Warning emitted for ignored optional target configuration."""


class DuplicateTargetError(ValueError):
    """Raised when direct registry mutation would replace an existing target."""


@dataclass(frozen=True)
class LegacyTargetSettings:
    """Current single-target settings used to synthesize ``default``."""

    jsonrpc_url: str
    bridge_url: str
    websocket_url: str = ""
    tcp_host: str = ""
    tcp_port: int = 9090
    timeout_seconds: int = 10


class TargetRegistry:
    """Internal collection of configured Kodi targets."""

    def __init__(self) -> None:
        self._targets: dict[str, Target] = {}

    def add(self, target: Target) -> None:
        if target.target_id in self._targets:
            raise DuplicateTargetError(f"duplicate target id: {target.target_id}")
        self._targets[target.target_id] = target

    def get(self, target_id: str) -> Target:
        return self._targets[target_id]

    def list(self) -> list[Target]:
        return [self._targets[target_id] for target_id in sorted(self._targets)]

    @classmethod
    def from_sources(
        cls,
        *,
        legacy: LegacyTargetSettings,
        targets_file: str | Path | None = None,
        targets_json: str | None = None,
    ) -> "TargetRegistry":
        registry = cls()
        registry.add(cls._legacy_default(legacy))

        file_targets = cls._read_file_source(targets_file)
        cls._merge_optional_source(registry, file_targets, "target file")

        inline_targets = cls._read_json_source(targets_json, "inline target JSON")
        cls._merge_optional_source(registry, inline_targets, "inline target JSON")
        cls._reject_conflicting_endpoint_domains(registry)
        cls._warn_shared_mutation_domains(registry)
        return registry

    @staticmethod
    def _reject_conflicting_endpoint_domains(registry: "TargetRegistry") -> None:
        ordered = [registry.get("default")] + [
            target for target in registry.list() if target.target_id != "default"
        ]
        by_endpoint: dict[str, Target] = {}
        for target in ordered:
            endpoint_domain = derive_mutation_domain_id(
                target.target_id, target.endpoints.bridge_url
            )
            existing = by_endpoint.get(endpoint_domain)
            if existing is None:
                by_endpoint[endpoint_domain] = target
                continue
            if existing.mutation_domain_id != target.mutation_domain_id:
                del registry._targets[target.target_id]
                warnings.warn(
                    f"target {target.target_id} ignored: mutation domain conflicts with {existing.target_id} for the same canonical bridge endpoint",
                    TargetConfigWarning,
                    stacklevel=2,
                )

    @staticmethod
    def _warn_shared_mutation_domains(registry: "TargetRegistry") -> None:
        domains: dict[str, list[str]] = {}
        for target in registry.list():
            domains.setdefault(str(target.mutation_domain_id), []).append(target.target_id)
        for target_ids in domains.values():
            if len(target_ids) > 1:
                warnings.warn(
                    f"targets {', '.join(target_ids)} share mutation domain and will serialize mutations",
                    TargetConfigWarning,
                    stacklevel=2,
                )

    @staticmethod
    def _legacy_default(settings: LegacyTargetSettings) -> Target:
        return Target(
            target_id="default",
            name="Default Kodi",
            endpoints=TargetEndpoints(
                jsonrpc_url=settings.jsonrpc_url,
                bridge_url=settings.bridge_url,
                websocket_url=settings.websocket_url,
                tcp_host=settings.tcp_host,
                tcp_port=settings.tcp_port,
            ),
            auth=TargetAuthReferences(
                jsonrpc_username="env:KODI_JSONRPC_USERNAME",
                jsonrpc_password="env:KODI_JSONRPC_PASSWORD",
                bridge_token="env:KODI_BRIDGE_TOKEN",
            ),
            timeout_seconds=settings.timeout_seconds,
        )

    @classmethod
    def _read_file_source(cls, path: str | Path | None) -> list[Any]:
        if path in (None, ""):
            return []
        target_path = Path(path).expanduser()
        try:
            text = target_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            warnings.warn(
                f"optional target file {target_path} was ignored: {exc}",
                TargetConfigWarning,
                stacklevel=2,
            )
            return []
        return cls._read_json_source(text, f"target file {target_path}")

    @staticmethod
    def _read_json_source(text: str | None, source_name: str) -> list[Any]:
        if not text:
            return []
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            warnings.warn(
                f"optional {source_name} was ignored: {exc}",
                TargetConfigWarning,
                stacklevel=2,
            )
            return []
        if not isinstance(value, list):
            warnings.warn(
                f"optional {source_name} was ignored: root must be an array",
                TargetConfigWarning,
                stacklevel=2,
            )
            return []
        return value

    @staticmethod
    def _merge_optional_source(
        registry: "TargetRegistry", entries: list[Any], source_name: str
    ) -> None:
        parsed: dict[str, Target] = {}
        for index, entry in enumerate(entries):
            try:
                target = Target.from_dict(entry)
                if not target.endpoints.jsonrpc_url or not target.endpoints.bridge_url:
                    raise TargetValidationError(
                        "configured targets require jsonrpc_url and bridge_url"
                    )
                if target.target_id == "default":
                    raise TargetValidationError(
                        "optional configuration cannot replace the legacy default target"
                    )
                if target.target_id in parsed:
                    warnings.warn(
                        f"{source_name} entry {index} ignored: duplicate target id {target.target_id}",
                        TargetConfigWarning,
                        stacklevel=2,
                    )
                    continue
                parsed[target.target_id] = target
            except (TargetValidationError, TypeError) as exc:
                warnings.warn(
                    f"{source_name} entry {index} ignored: {exc}",
                    TargetConfigWarning,
                    stacklevel=2,
                )

        # Sources are merged in increasing precedence. Replacing a lower-priority
        # optional source is intentional; ``default`` was rejected above.
        registry._targets.update(parsed)
