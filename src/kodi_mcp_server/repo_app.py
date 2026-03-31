"""Repo-serving HTTP/static functionality for kodi_mcp_server."""

from kodi_mcp_server.repo_server import mount_repo_static, router as repo_router


def configure_repo_app(app):
    """Attach repo-serving routes and static mounts to the shared app."""
    app.include_router(repo_router)
    mount_repo_static(app)
    return app
