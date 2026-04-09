"""
Pydantic request models for kodi_mcp_server POST endpoints.

Provides structured request validation for POST-style API endpoints.
"""

from typing import Optional

from pydantic import BaseModel, field_validator


class ExecuteAddonRequest(BaseModel):
    """Request to execute an addon via bridge."""

    addonid: str


class ExecuteBuiltinRequest(BaseModel):
    """Request to execute a Kodi builtin command."""

    command: str
    addonid: Optional[str] = None


class EnsureAddonEnabledRequest(BaseModel):
    """Request to ensure an addon is enabled."""

    addonid: str


class WriteLogMarkerRequest(BaseModel):
    """Request to write a log marker."""

    message: str


class UploadAddonZipRequest(BaseModel):
    """Request to upload an addon ZIP file."""

    local_zip_path: str


class PublishAddonRequest(BaseModel):
    """Request to publish an addon to the repository server."""

    addon_zip_path: str
    addon_id: str
    addon_name: str
    addon_version: str
    provider_name: str = "kodi_mcp"


class PublishArtifactRequest(BaseModel):
    """Publish a previously-registered artifact into the dev repo.

    Agent-safe: refers to server-owned artifact_id rather than server filesystem paths.
    """

    artifact_id: str
    addon_id: str
    addon_name: str
    addon_version: str
    provider_name: str = "kodi_mcp"


class UpdateAddonRequest(BaseModel):
    """Request to update an addon."""

    addonid: str
    timeout_seconds: int = 30
    poll_interval_seconds: int = 4


class RestartBridgeAddonRequest(BaseModel):
    """Request to restart the bridge addon."""

    timeout_seconds: int = 30

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v < 1:
            raise ValueError("timeout_seconds must be at least 1")
        return v
