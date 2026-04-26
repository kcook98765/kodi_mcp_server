"""Dev-loop helpers for agent-safe artifact publishing + staging.

This module exists to share the *pathless* artifact flow across:
- FastAPI debug/compatibility endpoints ("/tools/*")
- MCP tool handlers (stdio + remote StreamableHTTP)

Design goals:
- No dependency on client-visible filesystem paths.
- Store uploaded artifacts under a server-controlled directory.
- Publish artifacts into the authoritative dev repo tree.
- Stage the current dev repo state to Kodi via the bridge addon.

Notes on scope:
- Managed addon workflows (managed_addon_*) remain separate and intentionally
  require a server-local source tree.
"""

from __future__ import annotations

import base64
import io
import hashlib
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


def _project_root() -> Path:
    # Import lazily so tests can monkeypatch kodi_mcp_server.paths first.
    import kodi_mcp_server.paths as paths

    return paths.PROJECT_ROOT


def _authoritative_repo_root() -> Path:
    # Import lazily so tests can monkeypatch paths/config safely.
    from kodi_mcp_server.config import REPO_ROOT

    return Path(REPO_ROOT)


def _ensure_dev_repo_initialized(*, repo_root: Path) -> None:
    """Ensure repo/dev-repo exists and has minimal metadata files.

    This mirrors the defensive initialization in http_app's background
    registration loop. It keeps staging and update flows usable even if no addon
    has been published yet.
    """

    dev_repo_dir = repo_root / "dev-repo"
    dev_repo_dir.mkdir(parents=True, exist_ok=True)

    addons_xml_path = dev_repo_dir / "addons.xml"
    if not addons_xml_path.exists():
        addons_xml_path.write_text(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<addons>\n'
            '</addons>\n',
            encoding="utf-8",
        )

    addons_md5_path = dev_repo_dir / "addons.xml.md5"
    if not addons_md5_path.exists():
        md5 = hashlib.md5(addons_xml_path.read_bytes()).hexdigest()
        addons_md5_path.write_text(f"{md5}  addons.xml\n", encoding="utf-8")


def inspect_addon_zip(
    *,
    data: bytes | None = None,
    path: Path | None = None,
    addon_id: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """Validate a Kodi addon zip and return addon.xml metadata."""

    if data is None and path is None:
        raise ValueError("data or path is required")

    source = io.BytesIO(data) if data is not None else path
    try:
        with zipfile.ZipFile(source, "r") as archive:
            names = [name for name in archive.namelist() if name and not name.endswith("/")]
            if not names:
                raise ValueError("zip has no files")

            top_levels: set[str] = set()
            for name in names:
                member = Path(name)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError(f"unsafe zip member path: {name}")
                if not member.parts:
                    raise ValueError(f"invalid zip member path: {name}")
                top_levels.add(member.parts[0])

            if len(top_levels) != 1:
                raise ValueError("zip must contain exactly one top-level addon directory")

            root_dir = next(iter(top_levels))
            xml_name = f"{root_dir}/addon.xml"
            if xml_name not in names:
                raise ValueError(f"zip is missing {xml_name}")

            root = ElementTree.fromstring(archive.read(xml_name))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"invalid zip file: {exc}") from exc
    except ElementTree.ParseError as exc:
        raise ValueError(f"invalid addon.xml: {exc}") from exc

    found_id = str(root.attrib.get("id") or "").strip()
    found_version = str(root.attrib.get("version") or "").strip()
    found_name = str(root.attrib.get("name") or "").strip()

    if not found_id:
        raise ValueError("addon.xml is missing addon id")
    if not found_version:
        raise ValueError("addon.xml is missing version")
    if root_dir != found_id:
        raise ValueError(f"top-level directory mismatch: expected {found_id}, found {root_dir}")
    if addon_id and found_id != addon_id:
        raise ValueError(f"addon.xml id mismatch: expected {addon_id}, found {found_id}")
    if version and found_version != version:
        raise ValueError(f"addon.xml version mismatch: expected {version}, found {found_version}")

    return {
        "addon_id": found_id,
        "version": found_version,
        "addon_name": found_name or found_id,
        "top_level_dir": root_dir,
        "members": len(names),
        "size_bytes": len(data) if data is not None else (path.stat().st_size if path is not None else None),
    }


def artifact_upload_zip(
    *,
    zip_base64: str,
    filename: str = "upload.zip",
    addon_id: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """Store an uploaded addon zip in the server-owned artifact store.

    MCP is JSON-based, so this accepts base64-encoded bytes.
    """

    from kodi_mcp_server.artifact_store import ArtifactStore

    zip_base64 = str(zip_base64 or "")
    if not zip_base64.strip():
        raise ValueError("zip_base64 is required")

    # Be tolerant of common data: URL-safe base64 and data: URIs.
    raw = zip_base64.strip()
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]

    try:
        data = base64.b64decode(raw, validate=False)
    except Exception as exc:
        raise ValueError(f"invalid zip_base64: {exc}")

    requested_addon_id = addon_id.strip() if isinstance(addon_id, str) and addon_id.strip() else None
    requested_version = version.strip() if isinstance(version, str) and version.strip() else None
    zip_info = inspect_addon_zip(data=data, addon_id=requested_addon_id, version=requested_version)

    store = ArtifactStore(root_dir=_project_root() / "artifacts")
    record = store.register_bytes(
        data=data,
        filename=str(filename or "upload.zip"),
        addon_id=zip_info["addon_id"],
        version=zip_info["version"],
        addon_name=zip_info["addon_name"],
    )
    return {
        "ok": True,
        "artifact": {
            "artifact_id": record.artifact_id,
            "addon_id": record.addon_id,
            "version": record.version,
            "addon_name": record.addon_name,
        },
        "upload": {
            "filename": filename,
            "size_bytes": len(data),
            "zip": zip_info,
        },
    }


def repo_publish_artifact(
    *,
    artifact_id: str,
    addon_id: str,
    addon_name: str,
    addon_version: str,
    provider_name: str = "kodi_mcp",
) -> dict[str, Any]:
    """Publish an artifact-store zip into repo/dev-repo.

    This reuses the same core logic as the FastAPI endpoint, but is usable from
    MCP tools.
    """

    from kodi_mcp_server.artifact_store import ArtifactStore
    from kodi_mcp_server.repo_ops import RepoPublisher

    request_id = str(uuid.uuid4())

    store = ArtifactStore(root_dir=_project_root() / "artifacts")
    record = store.get(artifact_id)
    if record is None:
        return {
            "ok": False,
            "request_id": request_id,
            "error": f"unknown artifact_id: {artifact_id}",
            "error_type": "not_found",
            "error_code": 404,
            "result": None,
        }

    zip_path = Path(record.path)
    if not zip_path.exists() or not zip_path.is_file():
        return {
            "ok": False,
            "request_id": request_id,
            "error": f"artifact file missing on server: {zip_path}",
            "error_type": "not_found",
            "error_code": 404,
            "result": None,
        }

    try:
        zip_info = inspect_addon_zip(path=zip_path, addon_id=addon_id, version=addon_version)
    except ValueError as exc:
        return {
            "ok": False,
            "request_id": request_id,
            "error": str(exc),
            "error_type": "invalid_artifact",
            "error_code": 400,
            "result": None,
        }

    repo_root = _authoritative_repo_root()
    _ensure_dev_repo_initialized(repo_root=repo_root)
    publisher = RepoPublisher(repo_root=repo_root)

    # RepoPublisher's compatibility publish_addon() derives the expected zip name
    # from addon_id+version. Stage the artifact into a temp dir using that name.
    tmpdir = Path(tempfile.mkdtemp(prefix=f"artifact-publish-{artifact_id}-"))
    try:
        staged_zip = tmpdir / f"{addon_id}-{addon_version}.zip"
        import shutil

        shutil.copy2(zip_path, staged_zip)
        publish_result = publisher.publish_addon(
            addon_zip_path=str(staged_zip),
            addon_id=addon_id,
            addon_name=addon_name,
            addon_version=addon_version,
            provider_name=provider_name,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "request_id": request_id,
            "error": str(exc),
            "error_type": "not_found",
            "error_code": 404,
            "result": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "request_id": request_id,
            "error": f"Failed to publish artifact: {exc}",
            "error_type": "server_error",
            "error_code": 500,
            "result": None,
        }
    finally:
        try:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    # Include repo-visible paths (relative to /repo/content/).
    repo_rel_zip = f"zips/{addon_id}/{addon_id}-{addon_version}.zip"

    # Remove internal filesystem paths from the publish result (agent-facing).
    if isinstance(publish_result, dict):
        publish_result = {
            k: v
            for (k, v) in publish_result.items()
            if k
            not in {
                "source_dir",
                "build_zip_path",
                "zip_path",
                "addons_xml_path",
            }
        }

    return {
        "ok": True,
        "request_id": request_id,
        "result": {
            "artifact_id": record.artifact_id,
            "artifact": {
                "artifact_id": record.artifact_id,
                "addon_id": record.addon_id,
                "version": record.version,
                "addon_name": record.addon_name,
            },
            "artifact_validation": zip_info,
            "publish": publish_result,
            "repo": {
                "addons_xml": "addons.xml",
                "addons_xml_md5": "addons.xml.md5",
                "zip_relpath": repo_rel_zip,
                "zip_url": f"/repo/content/{repo_rel_zip}",
            },
        },
        "error": None,
        "error_type": None,
        "error_code": None,
    }


async def repo_stage_current_dev_repo(
    *,
    repo_version: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Build a dev-repo zip from current repo state and stage it to Kodi."""

    from kodi_mcp_server.managed_addons import build_dev_repo_zip
    from kodi_mcp_server.milestone_a_bridge import stage_dev_repo_zip

    repo_root = _authoritative_repo_root()
    _ensure_dev_repo_initialized(repo_root=repo_root)

    zip_path = build_dev_repo_zip(repo_version=repo_version)
    stage_out = await stage_dev_repo_zip(zip_path=str(zip_path), repo_version=repo_version, verify=verify)
    return {
        "ok": True,
        "repo_zip_path": str(zip_path),
        "stage": stage_out,
    }


async def repo_stage_and_apply_addon(
    *,
    addonid: str,
    runtime_bridge_tool: Any,
    runtime_jsonrpc_tool: Any,
    repo_version: str | None = None,
    verify: bool = True,
    timeout_seconds: int = 45,
    poll_interval_seconds: int = 4,
    target_version: str | None = None,
) -> dict[str, Any]:
    """Stage current repo state (dev-repo zip) then request Kodi update/install.

    This is an agent-safe alternative to managed_addon_build_publish_stage_and_apply
    that works from already-published repo state.
    """

    from kodi_mcp_server.tools.addon_ops import AddonOpsTool

    stage = await repo_stage_current_dev_repo(repo_version=repo_version, verify=verify)

    ops = AddonOpsTool(bridge_tool=runtime_bridge_tool, jsonrpc_tool=runtime_jsonrpc_tool)
    apply = await ops.update_addon(
        addonid=addonid,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )

    apply_dict = apply.to_dict() if hasattr(apply, "to_dict") else {"result": apply.result, "error": apply.error}
    apply_result = apply.result if isinstance(apply.result, dict) else {}
    installed_before = apply_result.get("installed_version_before")
    installed_after = apply_result.get("installed_version_after")
    apply_success = apply.error is None and bool(apply_result.get("success"))
    apply_verified = bool(apply_success)
    apply_status = "applied" if apply_verified else "failed"
    can_retry = False
    failure_reason = None

    if target_version:
        apply_verified = isinstance(installed_after, str) and installed_after == target_version
        if apply_verified:
            apply_status = "already_current" if installed_before == installed_after else "applied"
        elif isinstance(installed_after, str) and installed_after and installed_after != target_version:
            apply_status = "installed_version_mismatch"
            failure_reason = "installed_version_mismatch"
        elif apply_result.get("requires_initial_user_install") is True:
            apply_status = "initial_install_required"
            failure_reason = "initial_install_required"
        elif apply.error:
            apply_status = "apply_error"
            failure_reason = str(apply.error)
            can_retry = True
        else:
            apply_status = "apply_not_verified"
            failure_reason = "apply_not_verified"
            can_retry = True
    elif not apply_success:
        if apply_result.get("requires_initial_user_install") is True:
            apply_status = "initial_install_required"
            failure_reason = "initial_install_required"
        elif apply.error:
            apply_status = "apply_error"
            failure_reason = str(apply.error)
            can_retry = True
        else:
            apply_status = "apply_not_verified"
            failure_reason = "apply_not_verified"
            can_retry = True

    return {
        "ok": bool(stage.get("ok")) and apply_verified,
        "addonid": addonid,
        "target_version": target_version,
        "installed_version_before": installed_before,
        "installed_version_after": installed_after,
        "apply_verified": apply_verified,
        "apply_status": apply_status,
        "can_retry": can_retry,
        "failure_reason": failure_reason,
        "stage": stage,
        "apply": apply_dict,
    }


async def repo_publish_stage_apply_artifact(
    *,
    artifact_id: str,
    addon_id: str,
    addon_name: str,
    addon_version: str,
    runtime_bridge_tool: Any,
    runtime_jsonrpc_tool: Any,
    provider_name: str = "kodi_mcp",
    repo_version: str | None = None,
    verify: bool = True,
    timeout_seconds: int = 45,
    poll_interval_seconds: int = 4,
) -> dict[str, Any]:
    """One-shot agent-safe artifact publish -> stage -> apply -> verify."""

    publish = repo_publish_artifact(
        artifact_id=artifact_id,
        addon_id=addon_id,
        addon_name=addon_name,
        addon_version=addon_version,
        provider_name=provider_name,
    )
    if not publish.get("ok"):
        return {
            "ok": False,
            "artifact_id": artifact_id,
            "addon_id": addon_id,
            "repo_version": repo_version,
            "installed_version_before": None,
            "installed_version_after": None,
            "apply_verified": False,
            "apply_status": "publish_failed",
            "can_retry": False,
            "failure_reason": publish.get("error") or "publish_failed",
            "publish": publish,
            "stage_apply": None,
        }

    stage_apply = await repo_stage_and_apply_addon(
        addonid=addon_id,
        runtime_bridge_tool=runtime_bridge_tool,
        runtime_jsonrpc_tool=runtime_jsonrpc_tool,
        repo_version=repo_version,
        verify=verify,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        target_version=addon_version,
    )

    return {
        "ok": bool(publish.get("ok")) and bool(stage_apply.get("apply_verified")),
        "artifact_id": artifact_id,
        "addon_id": addon_id,
        "repo_version": repo_version,
        "installed_version_before": stage_apply.get("installed_version_before"),
        "installed_version_after": stage_apply.get("installed_version_after"),
        "apply_verified": bool(stage_apply.get("apply_verified")),
        "apply_status": stage_apply.get("apply_status"),
        "can_retry": bool(stage_apply.get("can_retry")),
        "failure_reason": stage_apply.get("failure_reason"),
        "publish": publish,
        "stage_apply": stage_apply,
    }
