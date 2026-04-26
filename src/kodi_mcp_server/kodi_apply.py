"""Best-effort Kodi-side apply helpers (post-initial repo install).

These helpers are intentionally narrow:
- refresh Kodi repo state
- inspect addon install state
- request install/update via existing bridge builtins

They are designed to make the *repeat* dev loop agent-runnable after a user has
installed the dev repository add-on at least once.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from kodi_mcp_server.managed_addons import managed_addon_build_publish_and_stage, managed_addon_get
from kodi_mcp_server.tools.addon_ops import AddonOpsTool
from kodi_mcp_server.tools.bridge import BridgeTool
from kodi_mcp_server.tools.jsonrpc import JsonRpcTool


async def kodi_refresh_dev_repo_state(*, bridge_tool: BridgeTool) -> dict[str, Any]:
    """Best-effort refresh of Kodi repository state."""

    refresh = await bridge_tool.trigger_repo_refresh()
    return {
        "ok": refresh.error is None,
        "attempted": {
            "repo_refresh": True,
        },
        "repo_refresh": {
            "error": refresh.error,
            "result": refresh.result,
        },
    }


async def kodi_get_addon_install_state(*, bridge_tool: BridgeTool, addon_id: str) -> dict[str, Any]:
    """Read-only addon install state using the bridge addon inspection endpoint."""

    info = await bridge_tool.get_bridge_addon_info(addonid=addon_id)
    raw = info.result if isinstance(info.result, dict) else {}
    return {
        "ok": info.error is None,
        "addon_id": addon_id,
        "installed": raw.get("installed"),
        "enabled": raw.get("enabled"),
        "version": raw.get("version"),
        "error": info.error,
        "raw": raw,
    }


async def kodi_install_or_update_addon(
    *,
    bridge_tool: BridgeTool,
    jsonrpc_tool: JsonRpcTool,
    addon_id: str,
    timeout_seconds: int = 45,
    poll_interval_seconds: int = 4,
) -> dict[str, Any]:
    """Best-effort install/update of an addon from Kodi's repositories.

    Notes:
    - This assumes the dev repository add-on has already been installed at least once.
    - Uses existing bridge builtins (UpdateAddonRepos + InstallAddon) via AddonOpsTool.
    """

    before = await kodi_get_addon_install_state(bridge_tool=bridge_tool, addon_id=addon_id)
    action = "install" if not before.get("installed") else "update"

    ops = AddonOpsTool(bridge_tool=bridge_tool, jsonrpc_tool=jsonrpc_tool)
    update = await ops.update_addon(
        addonid=addon_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )

    return {
        "ok": update.error is None and bool((update.result or {}).get("success")),
        "action": action,
        "error": update.error,
        "result": update.result,
        "addon_before": before,
    }


async def kodi_check_repo_ready_for_addon(
    *,
    bridge_tool: BridgeTool,
    jsonrpc_tool: JsonRpcTool,
    addon_id: str,
    repo_addon_id: str = "repository.kodi-mcp",
) -> dict[str, Any]:
    """Best-effort, read-only repo readiness check.

    Attempts to determine:
    - bridge reachability
    - whether the repository addon is installed/enabled
    - whether the target addon is visible in Kodi's repo metadata

    Notes:
    - Kodi JSON-RPC support for listing *available* (non-installed) addons varies.
      When unsupported, addon_visible_in_repo will be null and notes will explain.
    """

    notes: list[str] = []

    health = await bridge_tool.get_bridge_health()
    bridge_reachable = bool(health.error is None)
    if not bridge_reachable:
        return {
            "bridge_reachable": False,
            "repo_installed": None,
            "repo_enabled": None,
            "addon_visible_in_repo": None,
            "notes": ["bridge unreachable"],
        }

    # Repo addon presence (installed/enabled)
    repo_installed = False
    repo_enabled = None
    try:
        repo_details = await jsonrpc_tool.get_addon_details(addonid=repo_addon_id)
        if repo_details.error is None and isinstance(repo_details.result, dict):
            details = (repo_details.result or {}).get("addon") or {}
            if isinstance(details, dict):
                repo_installed = True
                repo_enabled = details.get("enabled")
        else:
            # Fall back: list repository-type addons
            repo_list = await jsonrpc_tool.list_addons(type="xbmc.addon.repository", enabled=None)
            addons = (repo_list.result or {}).get("addons") if isinstance(repo_list.result, dict) else None
            if isinstance(addons, list):
                for item in addons:
                    if not isinstance(item, dict):
                        continue
                    if item.get("addonid") == repo_addon_id:
                        repo_installed = True
                        repo_enabled = item.get("enabled")
                        break
    except Exception as exc:
        notes.append(f"repo check failed: {exc}")
        repo_installed = None
        repo_enabled = None

    # Addon visibility in repo metadata (best-effort)
    addon_visible_in_repo: bool | None = None
    try:
        # Some Kodi builds support installed=false to list available (repo) addons.
        resp = await jsonrpc_tool.execute_jsonrpc(
            method="Addons.GetAddons",
            params={
                "installed": False,
                "properties": ["name", "version"],
            },
        )
        if resp.error is None and isinstance(resp.result, dict) and isinstance(resp.result.get("addons"), list):
            addon_visible_in_repo = any(
                isinstance(a, dict) and a.get("addonid") == addon_id for a in (resp.result.get("addons") or [])
            )
        else:
            addon_visible_in_repo = None
            notes.append("cannot confirm addon visibility via JSON-RPC (unsupported or no addon list)")
    except Exception as exc:
        addon_visible_in_repo = None
        notes.append(f"cannot confirm addon visibility via JSON-RPC: {exc}")

    return {
        "bridge_reachable": True,
        "repo_installed": repo_installed,
        "repo_enabled": repo_enabled,
        "addon_visible_in_repo": addon_visible_in_repo,
        "notes": notes,
    }


async def managed_addon_build_publish_stage_and_apply(
    *,
    managed_addon_id: str,
    version_policy: str,
    bridge_tool: BridgeTool,
    jsonrpc_tool: JsonRpcTool,
    explicit_version: str | None = None,
    repo_version: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Orchestrate: build/publish/stage -> refresh -> apply install/update -> readback."""

    notes: list[str] = []

    build_publish_stage = await managed_addon_build_publish_and_stage(
        managed_addon_id=managed_addon_id,
        version_policy=version_policy,
        explicit_version=explicit_version,
        repo_version=repo_version,
        verify=verify,
    )

    target_version = None
    try:
        build_obj = build_publish_stage.get("build") if isinstance(build_publish_stage, dict) else None
        if isinstance(build_obj, dict):
            v = build_obj.get("version")
            if isinstance(v, str) and v.strip():
                target_version = v.strip()
    except Exception:
        target_version = None

    # If we can't build/publish/stage, stop here (agent cannot safely apply).
    if not bool(build_publish_stage.get("ok")):
        return {
            "ok": False,
            "managed_addon_id": managed_addon_id,
            "addon_id": None,
            "build_publish_stage": build_publish_stage,
            "refresh": None,
            "addon_before": None,
            "apply": None,
            "addon_after": None,
            "verification": {
                "target_version": target_version,
                "installed_version_before": None,
                "installed_version_after": None,
                "apply_verified": False,
                "apply_status": "failed",
                "can_retry": False,
                "retry_delay_seconds": None,
                "retry_hint": "Run validation and inspect errors.",
                "notes": ["build/publish/stage failed"],
            },
        }

    registry = managed_addon_get(managed_addon_id=managed_addon_id)
    entry = registry.get("managed_addon") if registry.get("ok") else None
    addon_id = (entry or {}).get("addon_id") if isinstance(entry, dict) else None
    addon_id = str(addon_id or "").strip() or None

    refresh: Dict[str, Any] | None = None
    repo_ready_check: Dict[str, Any] | None = None
    addon_before: Dict[str, Any] | None = None
    apply: Dict[str, Any] | None = None
    addon_after: Dict[str, Any] | None = None

    if addon_id:
        refresh = await kodi_refresh_dev_repo_state(bridge_tool=bridge_tool)
        repo_ready_check = await kodi_check_repo_ready_for_addon(
            bridge_tool=bridge_tool,
            jsonrpc_tool=jsonrpc_tool,
            addon_id=addon_id,
        )
        addon_before = await kodi_get_addon_install_state(bridge_tool=bridge_tool, addon_id=addon_id)

        # If repo isn't installed (likely missing one-time setup), avoid an unhelpful apply attempt.
        if isinstance(repo_ready_check, dict) and repo_ready_check.get("repo_installed") is False:
            notes.append("repository addon not installed (one-time setup likely missing)")
            apply = {
                "ok": False,
                "action": "refresh_only",
                "error": "repo_not_installed",
                "result": None,
                "addon_before": addon_before,
            }
        elif isinstance(repo_ready_check, dict) and repo_ready_check.get("addon_visible_in_repo") is False:
            notes.append("addon not visible in Kodi repo metadata")
            apply = {
                "ok": False,
                "action": "refresh_only",
                "error": "addon_not_found",
                "result": None,
                "addon_before": addon_before,
            }
        else:
            apply = await kodi_install_or_update_addon(
                bridge_tool=bridge_tool,
                jsonrpc_tool=jsonrpc_tool,
                addon_id=addon_id,
            )
        addon_after = await kodi_get_addon_install_state(bridge_tool=bridge_tool, addon_id=addon_id)

        # Bounded verification loop: poll a few times for version to settle.
        if target_version:
            for _ in range(3):
                current = (addon_after or {}).get("version")
                if isinstance(current, str) and current == target_version:
                    break
                await asyncio.sleep(1)
                addon_after = await kodi_get_addon_install_state(bridge_tool=bridge_tool, addon_id=addon_id)

    if not addon_id:
        return {
            "ok": False,
            "managed_addon_id": managed_addon_id,
            "addon_id": None,
            "build_publish_stage": build_publish_stage,
            "refresh": None,
            "repo_ready_check": None,
            "addon_before": None,
            "apply": None,
            "addon_after": None,
            "verification": {
                "target_version": target_version,
                "installed_version_before": None,
                "installed_version_after": None,
                "apply_verified": False,
                "apply_status": "addon_not_found",
                "can_retry": False,
                "retry_delay_seconds": None,
                "retry_hint": "Run validation and inspect errors.",
                "notes": ["managed_addon registry entry missing addon_id"],
            },
        }

    installed_version_before = (addon_before or {}).get("version") if isinstance(addon_before, dict) else None
    installed_version_after = (addon_after or {}).get("version") if isinstance(addon_after, dict) else None

    apply_verified = bool(target_version and isinstance(installed_version_after, str) and installed_version_after == target_version)

    # Classify common blocker conditions conservatively.
    apply_status = "failed"
    try:
        if isinstance(repo_ready_check, dict) and repo_ready_check.get("bridge_reachable") is False:
            apply_status = "bridge_unreachable"
            notes.append("bridge unreachable")
        elif isinstance(repo_ready_check, dict) and repo_ready_check.get("repo_installed") is False:
            apply_status = "repo_not_installed"
        elif isinstance(repo_ready_check, dict) and repo_ready_check.get("addon_visible_in_repo") is False:
            apply_status = "addon_not_found"
        elif not isinstance(addon_before, dict) or not addon_before.get("ok"):
            apply_status = "bridge_unreachable"
            notes.append("bridge addon inspection failed")
        elif isinstance(addon_before.get("installed"), bool) and addon_before.get("installed") and target_version and installed_version_before == target_version:
            apply_status = "already_current"
            apply_verified = True
        elif isinstance(refresh, dict) and not refresh.get("ok"):
            apply_status = "repo_not_ready"
            notes.append("repo refresh failed")
        elif apply_verified and installed_version_before != installed_version_after:
            apply_status = "applied"
        elif apply_verified:
            apply_status = "already_current"
        else:
            attempted_action = (apply or {}).get("action") if isinstance(apply, dict) else None
            if attempted_action == "install":
                apply_status = "install_attempted_not_verified"
            elif attempted_action == "update":
                apply_status = "update_attempted_not_verified"
            else:
                apply_status = "failed"
    except Exception:
        apply_status = "failed"

    # Overall ok is tied to verification.
    ok = bool(build_publish_stage.get("ok")) and bool(addon_id) and bool(apply_verified)

    # Self-describing retry guidance (deterministic, derived from apply_status only).
    can_retry: bool
    retry_hint: str
    retry_delay_seconds: int | None
    if apply_status in {"applied", "already_current"}:
        can_retry = False
        retry_delay_seconds = None
        retry_hint = "No retry needed."
    elif apply_status == "repo_not_installed":
        can_retry = False
        retry_delay_seconds = None
        retry_hint = (
            "Install repository.kodi-mcp once, then use Kodi UI: Add-ons > "
            "Install from repository > Kodi MCP Repository for first installs."
        )
    elif apply_status == "repo_not_ready":
        can_retry = True
        retry_delay_seconds = 2
        retry_hint = "Retry after short delay; repo may still be refreshing."
    elif apply_status == "addon_not_found":
        can_retry = True
        retry_delay_seconds = 4
        retry_hint = "Retry once; if still not found, repo may not be installed."
    elif apply_status in {"install_attempted_not_verified", "update_attempted_not_verified"}:
        can_retry = True
        retry_delay_seconds = 2
        retry_hint = "Retry; Kodi may not have applied update yet."
    elif apply_status == "bridge_unreachable":
        can_retry = True
        retry_delay_seconds = 2
        retry_hint = "Ensure Kodi is running and bridge reachable."
    else:
        can_retry = False
        retry_delay_seconds = None
        retry_hint = "Run validation and inspect errors."

    return {
        "ok": ok,
        "managed_addon_id": managed_addon_id,
        "addon_id": addon_id,
        "build_publish_stage": build_publish_stage,
        "refresh": refresh,
        "repo_ready_check": repo_ready_check,
        "addon_before": addon_before,
        "apply": apply,
        "addon_after": addon_after,
        "verification": {
            "target_version": target_version,
            "installed_version_before": installed_version_before,
            "installed_version_after": installed_version_after,
            "apply_verified": bool(apply_verified),
            "apply_status": apply_status,
            "can_retry": can_retry,
            "retry_delay_seconds": retry_delay_seconds,
            "retry_hint": retry_hint,
            "notes": notes,
            "manual_first_install": {
                "required": (
                    apply_status == "repo_not_installed"
                    or (
                        apply_status == "install_attempted_not_verified"
                        and isinstance(addon_before, dict)
                        and addon_before.get("installed") is False
                    )
                ),
                "repository_addon_id": "repository.kodi-mcp",
                "repository_name": "Kodi MCP Repository",
                "target_addon_id": addon_id,
                "steps": [
                    "Install repository.kodi-mcp once if it is not installed",
                    "Open Kodi Add-ons",
                    "Choose Install from repository",
                    "Open Kodi MCP Repository",
                    f"Select {addon_id}",
                    "Choose Install",
                    "Rerun the managed apply/update workflow",
                ],
            },
        },
    }
