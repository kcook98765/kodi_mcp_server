"""MCP-facing repo management tool adapter for kodi_mcp_server.

This layer preserves the existing external tool contract while adapting inputs
into the shared internal artifact model used across build and publish steps.
"""

import uuid
from pathlib import Path
from typing import Optional

from ..config import REPO_ROOT
from ..models.messages import ResponseMessage
from ..repo_ops import RepoPublisher


class RepoTool:
    """Thin MCP tool wrapper around repository mutation/build operations."""

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT
        self.publisher = RepoPublisher(repo_root=self.repo_root)

    async def publish_addon_to_repo(
        self,
        addon_zip_path: str,
        addon_id: str,
        addon_name: str,
        addon_version: str,
        provider_name: str = "kodi_mcp",
    ) -> ResponseMessage:
        try:
            result = self.publisher.publish_addon(
                addon_zip_path=addon_zip_path,
                addon_id=addon_id,
                addon_name=addon_name,
                addon_version=addon_version,
                provider_name=provider_name,
            )
            return ResponseMessage(
                request_id=str(uuid.uuid4()),
                result=result,
                error=None,
            )
        except FileNotFoundError as exc:
            return ResponseMessage(
                request_id=str(uuid.uuid4()),
                result=None,
                error=str(exc),
            )
        except Exception as exc:
            return ResponseMessage(
                request_id=str(uuid.uuid4()),
                result=None,
                error=f"Failed to publish addon: {exc}",
            )
