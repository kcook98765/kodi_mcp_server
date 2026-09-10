"""Read-only managed-addon validation with explicit dependencies."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from kodi_mcp_server.tools.bridge import BridgeTool


async def validate_managed_addon_state(
    *,
    managed_addon_id: str,
    managed_addon_record: Mapping[str, Any] | None,
    authoritative_repo_root: Path,
    health_bridge_tool: BridgeTool,
    state_bridge_tool: BridgeTool,
) -> dict[str, Any]:
    """Return the existing managed-addon readiness diagnostic."""

    entry = managed_addon_record
    last_build = entry.get("last_build") if isinstance(entry, Mapping) else None

    registry_exists = isinstance(entry, Mapping)
    enabled = bool(entry.get("enabled", False)) if isinstance(entry, Mapping) else False
    source_path = (
        str(entry.get("source_path") or "") if isinstance(entry, Mapping) else ""
    )
    addon_id = str(entry.get("addon_id") or "") if isinstance(entry, Mapping) else ""
    last_observed_version = (
        str(entry.get("last_observed_version") or "")
        if isinstance(entry, Mapping)
        else ""
    )

    def _exists(path: str) -> bool:
        try:
            return bool(path and Path(path).exists())
        except Exception:
            return False

    last_build_zip_exists = (
        _exists(str((last_build or {}).get("zip_path") or ""))
        if isinstance(last_build, Mapping)
        else False
    )
    published_repo_zip_exists = (
        _exists(str((last_build or {}).get("repo_zip_path") or ""))
        if isinstance(last_build, Mapping)
        else False
    )

    dev_repo_dir = authoritative_repo_root / "dev-repo"
    dev_repo_exists = dev_repo_dir.exists()
    addons_xml_exists = (dev_repo_dir / "addons.xml").exists()
    addons_xml_md5_exists = (dev_repo_dir / "addons.xml.md5").exists()

    reachable = False
    bridge_error = None
    mcp_state_read_ok = False
    registration_present = None
    registration_stale = None
    repo_zip_file_exists = None
    repo_zip_special_path = None
    dev_setup_available = None

    try:
        health = await health_bridge_tool.get_bridge_health()
        bridge_error = getattr(health, "error", None)
        reachable = bool(bridge_error is None)
    except Exception as exc:
        reachable = False
        bridge_error = str(exc)

    if reachable:
        try:
            response = await state_bridge_tool.get_mcp_state()
            payload = response.result or {}
            transport = payload.get("transport")
            mcp_state_read_ok = bool(
                isinstance(transport, dict) and transport.get("ok") is True
            )
            derived = None
            repo_zip = None
            if response.error is None and isinstance(payload.get("result"), dict):
                result_obj = payload.get("result") or {}
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

    ready_for_build = bool(
        registry_exists
        and enabled
        and source_path
        and _exists(source_path)
        and _exists(str(Path(source_path) / "addon.xml"))
    )
    ready_for_publish = bool(
        ready_for_build and last_build_zip_exists and dev_repo_exists
    )
    ready_for_stage = bool(
        ready_for_publish
        and reachable
        and mcp_state_read_ok
        and bool(registration_present)
        and not bool(registration_stale)
    )
    ready_for_kodi_install = bool(
        ready_for_stage
        and bool(repo_zip_file_exists)
        and bool(dev_setup_available)
    )

    return {
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
