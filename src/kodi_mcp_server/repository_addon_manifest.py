"""Canonical, source-controlled identity for the generated repository addon."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources


_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_RESOURCE_NAME = "repository_addon_manifest.json"


@dataclass(frozen=True)
class RepositoryAddonManifest:
    schema_version: int
    addon_id: str
    version: str
    name: str
    provider: str
    repository_extension: str

    @property
    def artifact_filename(self) -> str:
        return f"{self.addon_id}-{self.version}.zip"


@lru_cache(maxsize=1)
def load_repository_addon_manifest() -> RepositoryAddonManifest:
    """Load and validate the packaged repository-addon identity manifest."""

    resource = resources.files("kodi_mcp_server").joinpath(_RESOURCE_NAME)
    try:
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"repository addon manifest is unavailable: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("repository addon manifest must be a JSON object")

    required = (
        "addon_id",
        "version",
        "name",
        "provider",
        "repository_extension",
    )
    values = {key: payload.get(key) for key in required}
    if payload.get("schema_version") != 1:
        raise RuntimeError("repository addon manifest schema_version must be 1")
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise RuntimeError("repository addon manifest has missing invariant fields")
    if values["addon_id"] != "repository.kodi-mcp":
        raise RuntimeError("repository addon manifest has an unsupported addon_id")
    if values["repository_extension"] != "xbmc.addon.repository":
        raise RuntimeError("repository addon manifest has an unsupported extension")
    if not _SEMVER_RE.fullmatch(values["version"]):
        raise RuntimeError("repository addon manifest version must be semantic x.y.z")
    if any("url" in key.lower() for key in payload):
        raise RuntimeError("repository addon manifest must not contain environment URLs")

    return RepositoryAddonManifest(
        schema_version=1,
        addon_id=values["addon_id"],
        version=values["version"],
        name=values["name"],
        provider=values["provider"],
        repository_extension=values["repository_extension"],
    )
