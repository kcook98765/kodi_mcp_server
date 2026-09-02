"""FastAPI adapter for the validated first-install bridge bundle."""

from __future__ import annotations

from html import escape
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response

from kodi_mcp_server.bridge_bootstrap import BootstrapBundleError, load_bootstrap_bundle
from kodi_mcp_server.config import BRIDGE_BOOTSTRAP_MANIFEST_PATH, REPO_BASE_URL


def configure_bootstrap_app(
    app,
    *,
    manifest_path: Path | str | None = None,
    base_url: str | None = None,
):
    """Attach fail-closed download routes for the one supported bridge addon."""

    configured_manifest = Path(manifest_path or BRIDGE_BOOTSTRAP_MANIFEST_PATH)
    configured_base_url = base_url or REPO_BASE_URL

    def bundle():
        try:
            return load_bootstrap_bundle(configured_manifest)
        except BootstrapBundleError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/bootstrap/", response_class=HTMLResponse)
    async def bootstrap_index():
        validated = bundle()
        return (
            "<!doctype html><html><head><title>Kodi MCP bridge bootstrap</title></head>"
            "<body><h1>Kodi MCP bridge bootstrap</h1><ul>"
            '<li><a href="service.kodi_mcp.zip">service.kodi_mcp.zip</a></li>'
            '<li><a href="manifest.json">manifest.json</a></li>'
            "</ul><p>Version: "
            f"{escape(validated.version)}</p><p>SHA-256: "
            f"{validated.artifact_sha256}</p></body></html>"
        )

    @app.get("/bootstrap/manifest.json")
    async def bootstrap_manifest():
        return bundle().public_manifest(configured_base_url)

    @app.api_route("/bootstrap/service.kodi_mcp.zip", methods=["GET", "HEAD"])
    async def bootstrap_bridge_zip():
        validated = bundle()
        return Response(
            content=validated.artifact_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{validated.addon_id}-{validated.version}.zip"'
                )
            },
        )

    return app
