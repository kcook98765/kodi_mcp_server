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
import hashlib
import tempfile
import uuid
from pathlib import Path
from typing import Any


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

    store = ArtifactStore(root_dir=_project_root() / "artifacts")
    record = store.register_bytes(
        data=data,
        filename=str(filename or "upload.zip"),
        addon_id=(addon_id.strip() if isinstance(addon_id, str) and addon_id.strip() else None),
        version=(version.strip() if isinstance(version, str) and version.strip() else None),
    )
    return {
        "ok": True,
        "artifact": {
            "artifact_id": record.artifact_id,
            "addon_id": record.addon_id,
            "version": record.version,
        },
        "upload": {
            "filename": filename,
            "size_bytes": len(data),
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
            },
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

    return {
        "ok": bool(stage.get("ok")) and apply.error is None and bool((apply.result or {}).get("success")),
        "addonid": addonid,
        "stage": stage,
        "apply": apply.to_dict() if hasattr(apply, "to_dict") else {"result": apply.result, "error": apply.error},
    }
