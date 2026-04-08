"""Managed local addon registry + dev-loop helpers (Milestone B).

This module persists a small registry of local addon source paths so the MCP
server can later rebuild/publish artifacts repeatedly without any GitHub logic.

This module owns:
- managed-addon registry persistence
- build addon zip from an external/local source tree
- publish built addon zip into the dev repo
- build + stage a dev repo zip via the existing Milestone A addon bridge
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from zipfile import ZIP_DEFLATED, ZipFile

from kodi_mcp_server.addon_xml import read_addon_id, read_addon_id_and_version, read_addon_version
from kodi_mcp_server.milestone_a_bridge import stage_dev_repo_zip
from kodi_mcp_server.paths import AUTHORITATIVE_REPO_ROOT, LEGACY_ADDON_ARTIFACTS_ROOT, PROJECT_DIR
from kodi_mcp_server.repo_ops import RepoPublisher


REGISTRY_SCHEMA_VERSION = 1
REGISTRY_PATH = PROJECT_DIR / "managed_addons.json"

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _now() -> int:
    return int(time.time())


def load_managed_registry() -> dict[str, Any]:
    """Load managed addons registry (or return an empty initialized registry)."""

    if not REGISTRY_PATH.exists():
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "updated_at": _now(),
            "addons": {},
        }

    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8") or "{}")
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    data.setdefault("schema_version", REGISTRY_SCHEMA_VERSION)
    data.setdefault("updated_at", _now())
    data.setdefault("addons", {})
    if not isinstance(data.get("addons"), dict):
        data["addons"] = {}
    return data


def save_managed_registry(registry: dict[str, Any]) -> dict[str, Any]:
    """Save managed registry, updating updated_at."""

    if not isinstance(registry, dict):
        registry = {}
    registry["schema_version"] = REGISTRY_SCHEMA_VERSION
    registry["updated_at"] = _now()
    registry.setdefault("addons", {})

    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8")
    return registry


def _validate_source_path(source_path: Path) -> Path:
    source_path = source_path.expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"source_path not found: {source_path}")
    if not source_path.is_dir():
        raise NotADirectoryError(f"source_path is not a directory: {source_path}")
    addon_xml_path = source_path / "addon.xml"
    if not addon_xml_path.exists():
        raise FileNotFoundError(f"addon.xml not found: {addon_xml_path}")
    return source_path


def build_addon_zip_from_source(source_path: Path, addon_id: str, version: str) -> Path:
    """Build a Kodi-compatible versioned addon zip from an external source tree.

    Requirements:
    - zip filename: {addon_id}-{version}.zip
    - output directory: PROJECT_ROOT/addon (LEGACY_ADDON_ARTIFACTS_ROOT)
    - zip internal paths: {addon_id}/...
    """

    output_dir = LEGACY_ADDON_ARTIFACTS_ROOT
    output_dir.mkdir(parents=True, exist_ok=True)

    zip_name = f"{addon_id}-{version}.zip"
    zip_path = output_dir / zip_name

    if zip_path.exists():
        zip_path.unlink()

    excluded_dir_names = {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
    }
    excluded_file_names = {
        ".DS_Store",
        "Thumbs.db",
    }

    with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
        for path in sorted(source_path.rglob("*")):
            if not path.is_file():
                continue

            parts = set(path.parts)
            if parts.intersection(excluded_dir_names):
                continue
            if path.name in excluded_file_names:
                continue
            if path.suffix.lower() in {".pyc", ".pyo"}:
                continue

            rel = path.relative_to(source_path).as_posix()
            arcname = f"{addon_id}/{rel}"
            zf.write(path, arcname)

    return zip_path


def build_dev_repo_zip(repo_version: str | None = None) -> Path:
    """Build a zip containing the current dev repo artifacts.

    Source directory:
        PROJECT_ROOT/repo/dev-repo
    Output directory:
        PROJECT_ROOT/addon (LEGACY_ADDON_ARTIFACTS_ROOT)
    """

    dev_repo_dir = AUTHORITATIVE_REPO_ROOT / "dev-repo"
    if not dev_repo_dir.exists():
        raise FileNotFoundError(f"dev repo dir not found: {dev_repo_dir}")

    output_dir = LEGACY_ADDON_ARTIFACTS_ROOT
    output_dir.mkdir(parents=True, exist_ok=True)

    suffix = f"-{repo_version}" if repo_version else ""
    zip_name = f"dev-repo{suffix}.zip"
    zip_path = output_dir / zip_name
    if zip_path.exists():
        zip_path.unlink()

    excluded_dir_names = {
        ".git",
        "__pycache__",
    }
    excluded_file_names = {
        ".DS_Store",
        "Thumbs.db",
    }

    with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
        for path in sorted(dev_repo_dir.rglob("*")):
            if not path.is_file():
                continue

            parts = set(path.parts)
            if parts.intersection(excluded_dir_names):
                continue
            if path.name in excluded_file_names:
                continue

            rel = path.relative_to(dev_repo_dir).as_posix()
            # IMPORTANT: zip should contain dev-repo directory contents at root.
            zf.write(path, rel)

    return zip_path


async def build_and_stage_dev_repo_zip(
    *,
    repo_version: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Build the dev repo zip then stage it via the Kodi addon bridge."""

    zip_path = build_dev_repo_zip(repo_version=repo_version)
    stage_result = await stage_dev_repo_zip(zip_path=str(zip_path), repo_version=repo_version, verify=verify)
    return {
        "repo_zip_path": str(zip_path),
        "addon_stage_result": stage_result,
    }


def _write_addon_version(addon_xml_path: Path, version: str) -> None:
    text = addon_xml_path.read_text(encoding="utf-8", errors="replace")
    # Replace only the first version=... occurrence (the addon tag attribute).
    new_text, count = re.subn(r'version="[^"]+"', f'version="{version}"', text, count=1)
    if count != 1:
        raise ValueError(f"could not update addon version in {addon_xml_path}")
    addon_xml_path.write_text(new_text, encoding="utf-8")


def determine_build_version(
    addon_xml_path: Path,
    *,
    version_policy: str,
    explicit_version: str | None = None,
) -> str:
    """Determine (and optionally apply) the version to build.

    Policies:
    - use_addon_xml: read version from addon.xml (no mutation)
    - bump_patch: require semantic X.Y.Z, bump patch, write back
    - set_explicit: write explicit_version into addon.xml
    """

    version_policy = str(version_policy or "").strip().lower()
    if version_policy not in {"use_addon_xml", "bump_patch", "set_explicit"}:
        raise ValueError(f"invalid version_policy: {version_policy}")

    if version_policy == "use_addon_xml":
        return read_addon_version(addon_xml_path)

    if version_policy == "set_explicit":
        explicit_version = str(explicit_version or "").strip()
        if not explicit_version:
            raise ValueError("explicit_version is required for set_explicit")
        _write_addon_version(addon_xml_path, explicit_version)
        return explicit_version

    # bump_patch
    current = read_addon_version(addon_xml_path)
    match = _SEMVER_RE.match(str(current or "").strip())
    if not match:
        raise ValueError(f"version is not semantic X.Y.Z (cannot bump_patch): {current}")
    major, minor, patch = map(int, match.groups())
    bumped = f"{major}.{minor}.{patch + 1}"
    _write_addon_version(addon_xml_path, bumped)
    return bumped


def managed_addon_register(source_path: str) -> dict[str, Any]:
    """Register/update a managed addon entry for a local source tree."""

    src = _validate_source_path(Path(source_path))
    addon_xml_path = src / "addon.xml"
    addon_id, version = read_addon_id_and_version(addon_xml_path)

    now = _now()
    registry = load_managed_registry()
    addons = registry.setdefault("addons", {})

    managed_addon_id = addon_id
    existing = addons.get(managed_addon_id)
    if isinstance(existing, dict):
        registered_at = int(existing.get("registered_at") or now)
    else:
        registered_at = now

    entry = {
        "managed_addon_id": managed_addon_id,
        "addon_id": addon_id,
        "source_path": str(src),
        "addon_xml_path": str(addon_xml_path),
        "registered_at": registered_at,
        "last_seen_at": now,
        "enabled": True,
        "last_observed_version": version,
        # Placeholder for later milestones.
        "last_build": (existing or {}).get("last_build") if isinstance(existing, dict) else None,
    }

    addons[managed_addon_id] = entry
    save_managed_registry(registry)

    return {
        "ok": True,
        "managed_addon": entry,
    }


def managed_addon_get(managed_addon_id: str) -> dict[str, Any]:
    registry = load_managed_registry()
    addons = registry.get("addons") or {}
    entry = addons.get(managed_addon_id)
    if not isinstance(entry, dict):
        return {"ok": False, "error_code": "NOT_FOUND", "managed_addon_id": managed_addon_id}
    return {"ok": True, "managed_addon": entry}


def managed_addon_list() -> dict[str, Any]:
    registry = load_managed_registry()
    addons = registry.get("addons") or {}
    if not isinstance(addons, dict):
        addons = {}
    # return as a stable list
    items = [addons[k] for k in sorted(addons.keys()) if isinstance(addons.get(k), dict)]
    return {"ok": True, "managed_addons": items, "count": len(items)}


def managed_addon_build(
    managed_addon_id: str,
    version_policy: str,
    explicit_version: str | None = None,
) -> dict[str, Any]:
    """Build a versioned zip for a previously registered managed addon."""

    registry = load_managed_registry()
    addons = registry.get("addons") or {}
    entry = addons.get(managed_addon_id)
    if not isinstance(entry, dict):
        return {"ok": False, "error_code": "NOT_FOUND", "managed_addon_id": managed_addon_id}

    if not entry.get("enabled", True):
        return {"ok": False, "error_code": "DISABLED", "managed_addon_id": managed_addon_id}

    source_path = Path(str(entry.get("source_path") or ""))
    try:
        src = _validate_source_path(source_path)
    except Exception as exc:
        return {"ok": False, "error_code": "INVALID_SOURCE_PATH", "message": str(exc)}

    addon_xml_path = src / "addon.xml"

    try:
        observed_addon_id = read_addon_id(addon_xml_path)
    except Exception as exc:
        return {"ok": False, "error_code": "ADDON_XML_PARSE_FAILED", "message": str(exc)}

    expected_addon_id = str(entry.get("addon_id") or "").strip()
    if observed_addon_id != expected_addon_id:
        return {
            "ok": False,
            "error_code": "ADDON_ID_MISMATCH",
            "expected_addon_id": expected_addon_id,
            "observed_addon_id": observed_addon_id,
        }

    try:
        version = determine_build_version(
            addon_xml_path,
            version_policy=version_policy,
            explicit_version=explicit_version,
        )
    except Exception as exc:
        return {"ok": False, "error_code": "VERSION_POLICY_FAILED", "message": str(exc)}

    zip_path = build_addon_zip_from_source(src, expected_addon_id, version)
    zip_name = zip_path.name

    now = _now()
    entry["last_seen_at"] = now
    entry["last_observed_version"] = version
    entry["last_build"] = {
        "version": version,
        "built_at": now,
        "zip_name": zip_name,
        "zip_path": str(zip_path),
        "repo_zip_path": None,
    }

    addons[managed_addon_id] = entry
    registry["addons"] = addons
    save_managed_registry(registry)

    return {
        "ok": True,
        "managed_addon_id": managed_addon_id,
        "build": {
            "addon_id": expected_addon_id,
            "version": version,
            "zip_name": zip_name,
            "zip_path": str(zip_path),
        },
    }


def managed_addon_publish(managed_addon_id: str) -> dict[str, Any]:
    """Publish the last built zip for a managed addon into the dev repo."""

    registry = load_managed_registry()
    addons = registry.get("addons") or {}
    entry = addons.get(managed_addon_id)
    if not isinstance(entry, dict):
        return {"ok": False, "error_code": "NOT_FOUND", "managed_addon_id": managed_addon_id}

    if not entry.get("enabled", True):
        return {"ok": False, "error_code": "DISABLED", "managed_addon_id": managed_addon_id}

    addon_id = str(entry.get("addon_id") or "").strip()
    if not addon_id:
        return {"ok": False, "error_code": "INVALID_REGISTRY_ENTRY", "message": "missing addon_id"}

    last_build = entry.get("last_build")
    if not isinstance(last_build, dict):
        return {"ok": False, "error_code": "NO_LAST_BUILD", "managed_addon_id": managed_addon_id}

    version = str(last_build.get("version") or "").strip()
    zip_path_raw = str(last_build.get("zip_path") or "").strip()
    if not version:
        return {"ok": False, "error_code": "INVALID_LAST_BUILD", "message": "missing last_build.version"}
    if not zip_path_raw:
        return {"ok": False, "error_code": "INVALID_LAST_BUILD", "message": "missing last_build.zip_path"}

    zip_path = Path(zip_path_raw)
    if not zip_path.exists():
        return {
            "ok": False,
            "error_code": "BUILD_ZIP_MISSING",
            "zip_path": str(zip_path),
        }

    # Publish using the existing repo publisher so addons.xml/md5 are regenerated.
    publisher = RepoPublisher(repo_root=AUTHORITATIVE_REPO_ROOT)
    publish_result = publisher.publish_addon(
        addon_zip_path=str(zip_path),
        addon_id=addon_id,
        addon_name=addon_id,
        addon_version=version,
        provider_name="kodi_mcp",
    )

    now = _now()
    entry["last_seen_at"] = now
    # Update last_build to include published repo path
    entry.setdefault("last_build", {})
    entry["last_build"]["repo_zip_path"] = str(publish_result.get("zip_path")) if isinstance(publish_result, dict) else None

    addons[managed_addon_id] = entry
    registry["addons"] = addons
    save_managed_registry(registry)

    repo_dev_root = AUTHORITATIVE_REPO_ROOT / "dev-repo"
    return {
        "ok": True,
        "managed_addon_id": managed_addon_id,
        "publish": {
            "repo_zip_path": str(publish_result.get("zip_path")),
            "addons_xml_path": str(repo_dev_root / "addons.xml"),
            "addons_xml_md5_path": str(repo_dev_root / "addons.xml.md5"),
            "action": "added_or_updated",
        },
    }


def managed_addon_build_and_publish(
    managed_addon_id: str,
    version_policy: str,
    explicit_version: str | None = None,
) -> dict[str, Any]:
    """Orchestrate: build then publish."""

    build_result = managed_addon_build(
        managed_addon_id=managed_addon_id,
        version_policy=version_policy,
        explicit_version=explicit_version,
    )
    if not build_result.get("ok"):
        return build_result

    publish_result = managed_addon_publish(managed_addon_id)
    if not publish_result.get("ok"):
        return publish_result

    return {
        "ok": True,
        "managed_addon_id": managed_addon_id,
        "build": build_result.get("build"),
        "publish": publish_result.get("publish"),
    }


async def managed_addon_build_publish_and_stage(
    managed_addon_id: str,
    version_policy: str,
    explicit_version: str | None = None,
    repo_version: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Build addon -> publish to dev repo -> build dev repo zip -> stage to Kodi."""

    build_publish_result = managed_addon_build_and_publish(
        managed_addon_id=managed_addon_id,
        version_policy=version_policy,
        explicit_version=explicit_version,
    )
    if not build_publish_result.get("ok"):
        return build_publish_result

    stage = await build_and_stage_dev_repo_zip(repo_version=repo_version, verify=verify)

    return {
        "ok": True,
        "managed_addon_id": managed_addon_id,
        "build": build_publish_result.get("build"),
        "publish": build_publish_result.get("publish"),
        "stage": stage,
    }
