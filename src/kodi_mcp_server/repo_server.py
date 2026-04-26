"""Kodi MCP repository server with short paths, discovery, and install UX.

Provides:
- Short repo path: /repo/ -> /repo/dev-repo/
- Repo info endpoint: /repo/info
- Browsable install directory: /repo/install/
- Latest zip alias: /repo/install/latest.zip

Serves only the authoritative project-root repo tree. Legacy `server/repo*`
locations are intentionally not used.
"""

import os
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from kodi_mcp_server.config import REPO_BASE_URL, REPO_ROOT
from kodi_mcp_server.screenshot_store import cleanup_screenshots, screenshot_path_for

router = APIRouter()


@router.get("/repo-health")
async def repo_health():
    """Basic repo health check."""
    return JSONResponse(
        {
            "status": "ok",
            "service": "kodi_mcp_repo_server",
            "repo_root": str(REPO_ROOT),
        }
    )


@router.get("/repo/health")
async def repo_health_detailed():
    """Detailed repo health and visibility for debugging."""
    from kodi_mcp_server.repo_generator import load_addons_xml

    # The repo metadata that Kodi should consume is whatever we actually serve
    # from /repo/content/*.
    served_root = REPO_ROOT / "dev-repo" if (REPO_ROOT / "dev-repo").exists() else REPO_ROOT

    addons_data = load_addons_xml(served_root)
    addon_count = len(addons_data.get("addons", []))
    addons_list = addons_data.get("addons", [])

    addons_xml = served_root / "addons.xml"
    addons_gz = served_root / "addons.xml.gz"
    gz_exists = addons_gz.exists()
    gz_size = gz_exists and addons_gz.stat().st_size

    xml_exists = addons_xml.exists()

    metadata_path = "/repo/content/addons.xml.gz" if gz_exists else "/repo/content/addons.xml"

    return JSONResponse(
        {
            "status": "ok",
            "service": "kodi_mcp_repo_server",
            "repo_base_url": REPO_BASE_URL,
            "repo_root": str(REPO_ROOT),
            "addons": {
                "count": addon_count,
                "addons": addons_list[:20],
            },
            "metadata": {
                "addons_xml_exists": xml_exists,
                "addons_xml_gz_exists": gz_exists,
                "addons_xml_gz_size": gz_size,
                "served_repo_root": str(served_root),
                "served_metadata_path": metadata_path,
            },
            "kodi_can_see": {
                "index_url": f"{REPO_BASE_URL}/repo/",
                "metadata_url": f"{REPO_BASE_URL}{metadata_path}",
                "addons_available": addon_count > 0,
            },
        }
    )


@router.get("/screenshots/{filename}")
async def screenshot_file(filename: str):
    """Serve a previously captured server-side Kodi screenshot by opaque filename."""

    cleanup_screenshots()
    path = screenshot_path_for(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path, media_type="image/png", filename=path.name)


@router.get("/repo/info")
async def repo_info():
    """Repo info/discovery endpoint for first-install UX.

    Returns canonical install URLs and metadata for Kodi repository addons.
    """
    # Source repo addon from workspace/repo-addon/ (authoritative published location)
    repo_addon_path = REPO_ROOT.parent / "repo-addon"

    latest_zip = repo_addon_path / "repository.kodi-mcp-latest.zip"
    versioned_zips = [f for f in repo_addon_path.glob("repository.kodi-mcp-*.zip")
                      if not f.name.endswith("-latest.zip") and f.is_file()]

    # Get latest versioned zip
    latest_versioned = max(versioned_zips, key=os.path.getmtime) if versioned_zips else None

    # Generate checksum if zip exists
    zip_checksum = None
    if latest_versioned and latest_versioned.exists():
        import hashlib
        with open(latest_versioned, 'rb') as f:
            zip_checksum = hashlib.sha256(f.read()).hexdigest()

    return JSONResponse(
        {
            "status": "ok",
            "service": "kodi_mcp_repo_server",
            "repo_info": {
                "name": "Kodi MCP Server Repository",
                "description": "Official repository for Kodi MCP Server middle-layer backend",
                "short_url": f"{REPO_BASE_URL}/repo/",
                # Canonical served repo root (mounted by mount_repo_static)
                "dev_repo_url": f"{REPO_BASE_URL}/repo/content/",
                "install_dir": f"{REPO_BASE_URL}/repo/install/",
                "latest_zip_url": f"{REPO_BASE_URL}/repo/install/latest.zip" if latest_zip else None,
            },
            "install_urls": {
                "canonical_repo_root": f"{REPO_BASE_URL}/repo/content/",
                # Prefer compressed metadata if present, else fall back to plain xml.
                "addons_metadata": f"{REPO_BASE_URL}/repo/content/addons.xml.gz"
                if (REPO_ROOT / "dev-repo" / "addons.xml.gz").exists()
                else f"{REPO_BASE_URL}/repo/content/addons.xml",
                "repository_addon_zip": f"{REPO_BASE_URL}/repo/install/latest.zip" if latest_zip else None,
                "repository_addon_checksum": zip_checksum,
            },
            "latest_repo_addon": {
                "zip_path": str(latest_versioned) if latest_versioned else None,
                "zip_url": f"{REPO_BASE_URL}/repo/install/latest.zip" if latest_zip else None,
                "version": latest_versioned.stem.split("-")[-1] if latest_versioned else None,
                "size_bytes": latest_versioned.stat().st_size if latest_versioned else None,
                "published_location": str(repo_addon_path),
            },
        }
    )


@router.get("/repo/")
async def repo_root_redirect():
    """Redirect /repo/ to /repo/content/ for Kodi compatibility."""
    return RedirectResponse(url="/repo/content/", status_code=302)


@router.get("/repo/install/")
async def install_dir_index():
    """Plain directory listing for Kodi-compatible enumeration.

    Returns a simple HTML page with direct links to repository addon zips.
    Kodi can parse this for source browsing.
    Only exposes the canonical repository.kodi-mcp package from repo-addon/.
    """
    # Source repo addon from workspace/repo-addon/ (authoritative published location)
    repo_addon_path = REPO_ROOT.parent / "repo-addon"

    # Get all repo addon zips - only show repository.kodi-mcp packages
    zips = [f for f in repo_addon_path.glob("repository.kodi-mcp-*.zip")]
    zips.sort(key=lambda x: x.name, reverse=True)  # Newest first

    if not zips:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("No repository addons found", status_code=404)

    # Build a simple HTML directory listing with direct links
    # Kodi's source browser can parse basic HTML <a> tags
    lines = ['<!DOCTYPE html>', '<html><head><title>Repository Addons</title></head>', '<body>', '<h1>Repository Addons</h1>', '<ul>']

    for zip_path in zips:
        size = zip_path.stat().st_size
        size_kb = size / 1024
        is_latest = zip_path.name.endswith("-latest.zip")
        size_str = f"{size_kb:.1f} KB"

        # Simple list item with link
        lines.append(f'<li><a href="{zip_path.name}">{zip_path.name}</a> ({size_str})')
        if is_latest:
            lines.append(f'<small>[LATEST]</small></li>')
        else:
            lines.append('</li>')

    lines.append('</ul>')
    lines.append('</body>')
    lines.append('</html>')

    return HTMLResponse(content='\n'.join(lines), media_type="text/html")


@router.get("/repo/install/latest.zip")
async def latest_zip_redirect():
    """Serve latest.zip - finds the latest versioned zip from published location.

    Only exposes the canonical repository.kodi-mcp package from workspace/repo-addon/.
    This prevents stale repo addon versions from being served.
    """
    # Source repo addon from workspace/repo-addon/ (authoritative published location)
    repo_addon_path = REPO_ROOT.parent / "repo-addon"

    # First check for explicit latest symlink
    latest_symlink = repo_addon_path / "repository.kodi-mcp-latest.zip"
    if latest_symlink.exists() and latest_symlink.is_symlink():
        try:
            actual_zip = latest_symlink.resolve()
            return FileResponse(
                actual_zip,
                media_type="application/zip",
                filename="repository.kodi-mcp-latest.zip"
            )
        except Exception:
            pass

    # Fallback: find latest versioned zip (not -latest.zip itself)
    versioned_zips = [f for f in repo_addon_path.glob("repository.kodi-mcp-*.zip")
                      if not f.name.endswith("-latest.zip") and f.is_file()]

    if not versioned_zips:
        raise HTTPException(status_code=404, detail="No repository addon found")

    latest = max(versioned_zips, key=os.path.getmtime)

    # Serve directly without redirect (better for Kodi)
    return FileResponse(
        latest,
        media_type="application/zip",
        filename="repository.kodi-mcp-latest.zip"
    )


@router.get("/repo/install/repository.kodi-mcp-latest.zip")
async def latest_versioned_alias():
    """Alias repository.kodi-mcp-latest.zip -> current versioned zip."""
    return await latest_zip_redirect()


@router.get("/repo/install/{filename:path}")
async def install_file(filename: str):
    """Serve individual install files from the published repo addon location.

    Only serves repository.kodi-mcp-* packages from workspace/repo-addon/.
    This prevents exposure of stale or unintended addon versions.
    """
    # Source repo addon from workspace/repo-addon/ (authoritative published location)
    repo_addon_path = REPO_ROOT.parent / "repo-addon"
    file_path = repo_addon_path / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        file_path,
        media_type="application/zip",
        filename=filename
    )


def mount_repo_static(app):
    """Mount the project repo directory as static files.

    Mounts the dev-repo subdirectory at /repo/content/ for Kodi access.
    This serves addons.xml.gz, addons.xml, and all package files.
    """
    dev_repo = REPO_ROOT / "dev-repo"
    if dev_repo.exists():
        # Mount at /repo/content to serve dev-repo files
        # Use html=False to prevent directory listing, check_dir=True for validation
        from starlette.requests import Request
        from starlette.responses import FileResponse, PlainTextResponse
        from fastapi.responses import RedirectResponse

        @app.get("/repo/content/", include_in_schema=False)
        async def repo_content_root(request: Request):
            """Redirect /repo/content/ to /repo/content/addons.xml.gz for Kodi."""
            addons_gz = dev_repo / "addons.xml.gz"
            if addons_gz.exists():
                return RedirectResponse(url="/repo/content/addons.xml.gz")
            addons_xml = dev_repo / "addons.xml"
            if addons_xml.exists():
                return RedirectResponse(url="/repo/content/addons.xml")
            return PlainTextResponse("Kodi MCP Repository", status_code=200)

        @app.get("/repo/content/{path:path}", include_in_schema=False)
        async def repo_content_file(path: str, request: Request):
            """Serve files from dev-repo directory."""
            file_path = dev_repo / path
            if not file_path.exists():
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail="File not found")
            if file_path.is_dir():
                return PlainTextResponse("Directory listing not available", status_code=404)
            return FileResponse(file_path)
    else:
        # Fallback: mount the repo root itself
        repo_dir = REPO_ROOT
        if repo_dir.exists():
            from fastapi import Request
            from fastapi.responses import FileResponse, RedirectResponse, PlainTextResponse

            @app.get("/repo/content/", include_in_schema=False)
            async def repo_content_root_fallback(request: Request):
                """Fallback redirect for /repo/content/."""
                return PlainTextResponse("Kodi MCP Repository", status_code=200)

            @app.get("/repo/content/{path:path}", include_in_schema=False)
            async def repo_content_file_fallback(path: str, request: Request):
                """Fallback: serve files from repo root."""
                file_path = repo_dir / path
                if not file_path.exists():
                    from fastapi import HTTPException
                    raise HTTPException(status_code=404, detail="File not found")
                if file_path.is_dir():
                    return PlainTextResponse("Directory listing not available", status_code=404)
                return FileResponse(file_path)
