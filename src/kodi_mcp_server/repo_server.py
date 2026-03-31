"""Minimal static repo file server for Kodi MCP dev repositories.

Serves only the authoritative project-root repo tree. Legacy `server/repo*`
locations are intentionally not used.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from kodi_mcp_server.config import REPO_ROOT

router = APIRouter()


@router.get("/repo-health")
async def repo_health():
    return JSONResponse(
        {
            "status": "ok",
            "service": "kodi_mcp_repo_server",
            "repo_root": str(REPO_ROOT),
        }
    )


def mount_repo_static(app):
    """Mount the project repo directory as static files."""
    app.mount("/repo", StaticFiles(directory=str(REPO_ROOT)), name="repo")
