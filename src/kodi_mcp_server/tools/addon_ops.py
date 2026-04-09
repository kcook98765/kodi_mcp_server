"""Thin orchestration helpers for addon update/restart workflows."""

import asyncio
import re
import uuid
from pathlib import Path

from ..config import REPO_ROOT
from ..models.messages import ResponseMessage
from .bridge import BridgeTool
from .jsonrpc import JsonRpcTool

BRIDGE_ADDON_ID = "service.kodi_mcp"


class AddonOpsTool:
    """High-level orchestration for validated addon-management workflows."""

    def __init__(self, bridge_tool: BridgeTool, jsonrpc_tool: JsonRpcTool):
        self.bridge_tool = bridge_tool
        self.jsonrpc_tool = jsonrpc_tool

    def _read_repo_version(self, addonid: str) -> str | None:
        addons_xml = REPO_ROOT / "dev-repo" / "addons.xml"
        text = addons_xml.read_text(encoding="utf-8")
        match = re.search(rf'<addon id="{re.escape(addonid)}"[^>]*version="([^"]+)"', text)
        return match.group(1) if match else None

    async def wait_for_addon_version(
        self,
        addonid: str,
        version: str,
        timeout_seconds: int = 30,
        poll_interval_seconds: int = 4,
    ) -> ResponseMessage:
        request_id = str(uuid.uuid4())
        observed_versions: list[str | None] = []
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_result = None
        last_error = None

        while True:
            info = await self.bridge_tool.get_bridge_addon_info(addonid=addonid)
            last_result = info.result or {}
            last_error = info.error
            installed_version = last_result.get("version") if isinstance(last_result, dict) else None
            observed_versions.append(installed_version)

            if info.error is None and installed_version == version:
                return ResponseMessage(
                    request_id=request_id,
                    result={
                        "addon_id": addonid,
                        "target_version": version,
                        "observed_versions": observed_versions,
                        "final_state": last_result,
                        "success": True,
                        "timed_out": False,
                    },
                    error=None,
                )

            if asyncio.get_running_loop().time() >= deadline:
                return ResponseMessage(
                    request_id=request_id,
                    result={
                        "addon_id": addonid,
                        "target_version": version,
                        "observed_versions": observed_versions,
                        "final_state": last_result,
                        "success": False,
                        "timed_out": True,
                    },
                    error=last_error,
                )

            await asyncio.sleep(poll_interval_seconds)

    async def update_addon(
        self,
        addonid: str,
        timeout_seconds: int = 30,
        poll_interval_seconds: int = 4,
    ) -> ResponseMessage:
        request_id = str(uuid.uuid4())
        if addonid == BRIDGE_ADDON_ID:
            return ResponseMessage(
                request_id=request_id,
                result=None,
                error="service.kodi_mcp remains manual-install only; use the bridge-addon bump/build/install workflow",
            )

        repo_version = self._read_repo_version(addonid)
        if not repo_version:
            return ResponseMessage(
                request_id=request_id,
                result=None,
                error=f"addon {addonid} not found in repo metadata",
            )

        # Product rule: for a *brand-new* addon, the initial install must be
        # user-driven in the Kodi UI (Install from repository). Agents can only
        # automate refresh/update flows after the first install.
        before = await self.bridge_tool.get_bridge_addon_info(addonid=addonid)
        before_installed = None
        try:
            if isinstance(before.result, dict):
                before_installed = before.result.get("installed")
        except Exception:
            before_installed = None

        if before.error is None and before_installed is False:
            return ResponseMessage(
                request_id=request_id,
                result={
                    "addon_id": addonid,
                    "repo_version": repo_version,
                    "is_published_in_repo": True,
                    "is_installed": False,
                    "requires_initial_user_install": True,
                    "suggested_user_action": (
                        "In Kodi UI: Add-ons → Install from repository → Kodi MCP Repository → "
                        f"install '{addonid}' (v{repo_version})."
                    ),
                    "next_automatable_step": "After initial install, run update_addon again to refresh and apply updates.",
                },
                error=None,
            )

        refresh = await self.bridge_tool.execute_bridge_builtin(command="UpdateAddonRepos")
        install = await self.bridge_tool.execute_bridge_builtin(command="InstallAddon", addonid=addonid)
        wait = await self.wait_for_addon_version(
            addonid=addonid,
            version=repo_version,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        after = await self.bridge_tool.get_bridge_addon_info(addonid=addonid)

        result = {
            "addon_id": addonid,
            "repo_version": repo_version,
            "installed_version_before": (before.result or {}).get("version") if before.result else None,
            "installed_version_after": (after.result or {}).get("version") if after.result else None,
            "installed": (after.result or {}).get("installed") if after.result else None,
            "enabled": (after.result or {}).get("enabled") if after.result else None,
            "refresh_result": refresh.result,
            "install_result": install.result,
            "wait_result": wait.result,
            "success": wait.error is None and bool((wait.result or {}).get("success")),
        }
        errors = [err for err in [before.error, refresh.error, install.error, wait.error, after.error] if err]
        return ResponseMessage(
            request_id=request_id,
            result=result,
            error=" | ".join(errors) if errors else None,
        )

    async def restart_bridge_addon(self, timeout_seconds: int = 30) -> ResponseMessage:
        request_id = str(uuid.uuid4())
        before = await self.bridge_tool.get_bridge_addon_info(addonid=BRIDGE_ADDON_ID)

        disable = await self.jsonrpc_tool.set_addon_enabled(addonid=BRIDGE_ADDON_ID, enabled=False)
        await asyncio.sleep(1)
        enable = await self.jsonrpc_tool.set_addon_enabled(addonid=BRIDGE_ADDON_ID, enabled=True)

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        health = None
        after = None
        while True:
            health = await self.bridge_tool.get_bridge_health()
            after = await self.bridge_tool.get_bridge_addon_info(addonid=BRIDGE_ADDON_ID)
            if health.error is None and (health.result or {}).get("status") == "ok":
                break
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(2)

        result = {
            "addon_id": BRIDGE_ADDON_ID,
            "before": before.result,
            "after": after.result if after else None,
            "disable_result": disable.result,
            "enable_result": enable.result,
            "health_result": health.result if health else None,
            "success": health is not None and health.error is None and (health.result or {}).get("status") == "ok",
        }
        errors = [err for err in [before.error, disable.error, enable.error, getattr(health, 'error', None), getattr(after, 'error', None)] if err]
        return ResponseMessage(
            request_id=request_id,
            result=result,
            error=" | ".join(errors) if errors else None,
        )
