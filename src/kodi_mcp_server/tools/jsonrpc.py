"""
JSON-RPC tools for Kodi MCP Server

Tools for executing Kodi JSON-RPC commands
"""

import uuid
from typing import Optional

from ..models.messages import RequestMessage, ResponseMessage
from ..transport.base import Transport


class JsonRpcTool:
    """Tool for executing Kodi JSON-RPC commands"""

    def __init__(self, transport: Optional[Transport]):
        """
        Initialize JsonRpcTool with a transport instance.

        Args:
            transport: Transport instance for communication with Kodi addon
        """
        self.transport = transport

    async def execute_jsonrpc(
        self, method: str, params: dict | None = None
    ) -> ResponseMessage:
        """
        Execute a Kodi JSON-RPC method and return a structured response.

        Args:
            method: JSON-RPC method name to execute
            params: Parameters to pass to the method

        Returns:
            ResponseMessage containing either the tool result or a placeholder error
            when transport is not implemented.
        """
        request = RequestMessage(
            request_id=str(uuid.uuid4()),
            command="execute_jsonrpc",
            args={
                "method": method,
                "params": params or {},
            },
        )

        if self.transport is None:
            return ResponseMessage(
                request_id=request.request_id,
                result=None,
                error="transport not implemented",
            )

        return await self.transport.send_request(request)

    async def get_application_properties(self) -> ResponseMessage:
        """Retrieve a small set of Kodi application properties."""
        return await self.execute_jsonrpc(
            method="Application.GetProperties",
            params={
                "properties": ["language", "muted", "volume"],
            },
        )

    async def get_active_players(self) -> ResponseMessage:
        """Retrieve the list of active Kodi players."""
        return await self.execute_jsonrpc(method="Player.GetActivePlayers")

    async def get_movies_sample(self, limit: int = 5) -> ResponseMessage:
        """Retrieve a limited sample of movies from Kodi's library."""
        return await self.execute_jsonrpc(
            method="VideoLibrary.GetMovies",
            params={
                "limits": {"start": 0, "end": limit},
                "properties": ["title", "year"],
                "sort": {"method": "title", "order": "ascending"},
            },
        )

    async def get_tvshows_sample(self, limit: int = 5) -> ResponseMessage:
        """Retrieve a limited sample of TV shows from Kodi's library."""
        return await self.execute_jsonrpc(
            method="VideoLibrary.GetTVShows",
            params={
                "limits": {"start": 0, "end": limit},
                "properties": ["title", "year"],
                "sort": {"method": "title", "order": "ascending"},
            },
        )

    async def get_sources(self, media: str = "files") -> ResponseMessage:
        """Retrieve configured Kodi media sources."""
        return await self.execute_jsonrpc(
            method="Files.GetSources",
            params={
                "media": media,
            },
        )

    async def get_addons(self) -> ResponseMessage:
        """Retrieve a sample of installed Kodi addons."""
        return await self.execute_jsonrpc(
            method="Addons.GetAddons",
            params={
                "enabled": True,
                "properties": ["name", "version", "enabled"],
            },
        )

    async def get_addon_details(self, addonid: str) -> ResponseMessage:
        """Retrieve details for a specific Kodi addon."""
        return await self.execute_jsonrpc(
            method="Addons.GetAddonDetails",
            params={
                "addonid": addonid,
                "properties": ["name", "version", "enabled"],
            },
        )

    async def set_addon_enabled(
        self, addonid: str, enabled: bool
    ) -> ResponseMessage:
        """Enable or disable a specific Kodi addon."""
        return await self.execute_jsonrpc(
            method="Addons.SetAddonEnabled",
            params={
                "addonid": addonid,
                "enabled": enabled,
            },
        )

    async def list_directory(
        self,
        path: str = r"C:\Users\kcook\Kodi_NFO_Sidecar_Test\\",
        media: str = "files",
        properties: list[str] | None = None,
        limits: dict | None = None,
    ) -> ResponseMessage:
        """List directory contents for a Kodi path."""
        params = {
            "directory": path,
        }

        if media:
            params["media"] = media
        if properties is not None:
            params["properties"] = properties
        if limits is not None:
            params["limits"] = limits

        return await self.execute_jsonrpc(
            method="Files.GetDirectory",
            params=params,
        )

    async def get_recent_movies(self, limit: int = 5) -> ResponseMessage:
        """Retrieve recently added movies from Kodi's library."""
        return await self.execute_jsonrpc(
            method="VideoLibrary.GetRecentlyAddedMovies",
            params={
                "limits": {"start": 0, "end": limit},
                "properties": ["title", "year", "dateadded"],
            },
        )

    async def get_player_item(self) -> ResponseMessage:
        """Retrieve the current item for Kodi player 1."""
        return await self.execute_jsonrpc(
            method="Player.GetItem",
            params={
                "playerid": 1,
                "properties": ["title", "album", "artist", "season", "episode"],
            },
        )

    async def list_addons(
        self, type: str | None = None, enabled: bool | None = None
    ) -> ResponseMessage:
        """List Kodi addons with optional filters."""
        params = {
            "properties": ["name", "version", "enabled"],
        }
        if type is not None:
            params["type"] = type
        if enabled is not None:
            params["enabled"] = enabled

        return await self.execute_jsonrpc(
            method="Addons.GetAddons",
            params=params,
        )

    async def execute_addon(
        self, addonid: str, params: dict | None = None, wait: bool = False
    ) -> ResponseMessage:
        """Execute a Kodi addon with optional params."""
        return await self.execute_jsonrpc(
            method="Addons.ExecuteAddon",
            params={
                "addonid": addonid,
                "params": params or {},
                "wait": wait,
            },
        )

    async def get_setting_value(self, setting: str) -> ResponseMessage:
        """Retrieve a Kodi setting value."""
        return await self.execute_jsonrpc(
            method="Settings.GetSettingValue",
            params={
                "setting": setting,
            },
        )

    async def get_system_properties(self) -> ResponseMessage:
        """Retrieve a small useful set of system power capabilities."""
        return await self.execute_jsonrpc(
            method="System.GetProperties",
            params={
                "properties": [
                    "canshutdown",
                    "canreboot",
                    "canhibernate",
                    "cansuspend",
                ],
            },
        )

    async def inspect_addon(self, addonid: str) -> ResponseMessage:
        """Return a compact summary for a Kodi addon."""
        details = await self.get_addon_details(addonid=addonid)
        if details.error is not None:
            return ResponseMessage(
                request_id=details.request_id,
                result=None,
                error=details.error,
            )

        addon = (details.result or {}).get("addon", {})
        return ResponseMessage(
            request_id=details.request_id,
            result={
                "addonid": addon.get("addonid", addonid),
                "name": addon.get("name"),
                "version": addon.get("version"),
                "type": addon.get("type"),
                "enabled": addon.get("enabled"),
            },
            error=None,
        )

    async def is_addon_installed(self, addonid: str) -> ResponseMessage:
        """Return whether a Kodi addon is installed."""
        details = await self.get_addon_details(addonid=addonid)
        if details.error is not None:
            return ResponseMessage(
                request_id=details.request_id,
                result=False,
                error=None,
            )

        return ResponseMessage(
            request_id=details.request_id,
            result=True,
            error=None,
        )

    async def is_addon_enabled(self, addonid: str) -> ResponseMessage:
        """Return whether a Kodi addon is enabled."""
        details = await self.get_addon_details(addonid=addonid)
        if details.error is not None:
            return ResponseMessage(
                request_id=details.request_id,
                result=False,
                error=details.error,
            )

        enabled = ((details.result or {}).get("addon") or {}).get("enabled", False)
        return ResponseMessage(
            request_id=details.request_id,
            result=enabled,
            error=None,
        )

    async def ensure_addon_enabled(self, addonid: str) -> ResponseMessage:
        """Enable an addon only if it is not already enabled."""
        details = await self.get_addon_details(addonid=addonid)
        if details.error is not None:
            return ResponseMessage(
                request_id=details.request_id,
                result=None,
                error=details.error,
            )

        addon = (details.result or {}).get("addon", {})
        if addon.get("enabled"):
            return ResponseMessage(
                request_id=details.request_id,
                result={
                    "addonid": addon.get("addonid", addonid),
                    "enabled": True,
                    "changed": False,
                },
                error=None,
            )

        enabled_response = await self.set_addon_enabled(addonid=addonid, enabled=True)
        if enabled_response.error is not None:
            return ResponseMessage(
                request_id=enabled_response.request_id,
                result=None,
                error=enabled_response.error,
            )

        return ResponseMessage(
            request_id=enabled_response.request_id,
            result={
                "addonid": addonid,
                "enabled": True,
                "changed": True,
            },
            error=None,
        )

    async def run_addon_and_report(
        self, addonid: str, wait: bool = False
    ) -> ResponseMessage:
        """Execute an addon and return a compact status summary."""
        execution = await self.execute_addon(addonid=addonid, wait=wait)
        if execution.error is not None:
            return ResponseMessage(
                request_id=execution.request_id,
                result=None,
                error=execution.error,
            )

        return ResponseMessage(
            request_id=execution.request_id,
            result={
                "addonid": addonid,
                "wait": wait,
                "status": execution.result,
            },
            error=None,
        )

    async def get_jsonrpc_version(self) -> ResponseMessage:
        """Retrieve Kodi's JSON-RPC protocol version."""
        return await self.execute_jsonrpc(method="JSONRPC.Version")

    async def introspect_jsonrpc(
        self,
        getdescriptions: bool = False,
        getmetadata: bool = False,
        filterbytransport: bool = False,
        summary: bool = True,
    ) -> ResponseMessage:
        """Retrieve JSON-RPC introspection data, optionally summarized."""
        introspection = await self.execute_jsonrpc(
            method="JSONRPC.Introspect",
            params={
                "getdescriptions": getdescriptions,
                "getmetadata": getmetadata,
                "filterbytransport": filterbytransport,
            },
        )
        if introspection.error is not None or not summary:
            return introspection

        result = introspection.result or {}
        methods = result.get("methods", {}) or {}
        method_names = sorted(methods.keys())
        namespaces = sorted({name.split(".", 1)[0] for name in method_names if "." in name})
        key_namespaces = [
            "Addons",
            "Files",
            "Player",
            "VideoLibrary",
            "Settings",
            "System",
            "Application",
            "JSONRPC",
        ]

        return ResponseMessage(
            request_id=introspection.request_id,
            result={
                "version": result.get("version"),
                "method_count": len(method_names),
                "namespaces": namespaces,
                "key_namespaces": {
                    name: name in namespaces for name in key_namespaces
                },
            },
            error=None,
        )
