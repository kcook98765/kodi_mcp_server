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

    addons_data = load_addons_xml(REPO_ROOT)
    addon_count = len(addons_data.get("addons", []))
    addons_list = addons_data.get("addons", [])

    addons_gz = REPO_ROOT / "addons.xml.gz"
    gz_exists = addons_gz.exists()
    gz_size = gz_exists and addons_gz.stat().st_size

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
                "addons_xml_exists": True,
                "addons_xml_gz_exists": gz_exists,
                "addons_xml_gz_size": gz_size,
            },
            "kodi_can_see": {
                "index_url": f"{REPO_BASE_URL}/repo/",
                "metadata_url": f"{REPO_BASE_URL}/repo/addons.xml.gz",
                "addons_available": addon_count > 0,
            },
        }
    )


@router.get("/repo/info")
async def repo_info():
    """Repo info/discovery endpoint for first-install UX.

    Returns canonical install URLs and metadata for Kodi repository addons.
    """
    # Source repo addon from dev-repo/zips/repository.kodi-mcp (actual published location)
    repo_addon_zips_dir = REPO_ROOT / "dev-repo" / "zips" / "repository.kodi-mcp"
    repo_addon_path = repo_addon_zips_dir if repo_addon_zips_dir.exists() else REPO_ROOT.parent / "repo-addon"
    
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
                "dev_repo_url": f"{REPO_BASE_URL}/repo/dev-repo/",
                "install_dir": f"{REPO_BASE_URL}/repo/install/",
                "latest_zip_url": f"{REPO_BASE_URL}/repo/install/latest.zip" if latest_zip else None,
            },
            "install_urls": {
                "canonical_repo_root": f"{REPO_BASE_URL}/repo/dev-repo/",
                "addons_metadata": f"{REPO_BASE_URL}/repo/dev-repo/addons.xml.gz",
                "repository_addon_zip": f"{REPO_BASE_URL}/repo/install/latest.zip" if latest_zip else None,
                "repository_addon_checksum": zip_checksum,
            },
            "latest_repo_addon": {
                "zip_path": str(latest_versioned) if latest_versioned else None,
                "zip_url": f"{REPO_BASE_URL}/repo/install/latest.zip" if latest_zip else None,
                "version": latest_versioned.stem.split("-")[-1] if latest_versioned else None,
                "size_bytes": latest_versioned.stat().st_size if latest_versioned else None,
                "published_location": str(repo_addon_zips_dir) if repo_addon_zips_dir.exists() else "fallback: repo-addon",
            },
        }
    )


@router.get("/repo/")
async def repo_root_redirect():
    """Redirect /repo/ to /repo/content/ for Kodi compatibility."""
    return RedirectResponse(url="/repo/content/", status_code=302)


@router.get("/repo/install/")
async def install_dir_index():
    """Browsable install directory index."""
    # Source repo addon from dev-repo/zips/repository.kodi-mcp (actual published location)
    repo_addon_zips_dir = REPO_ROOT / "dev-repo" / "zips" / "repository.kodi-mcp"
    repo_addon_path = repo_addon_zips_dir if repo_addon_zips_dir.exists() else REPO_ROOT.parent / "repo-addon"
    
    # Get all repo addon zips (excluding symlinks to -latest.zip)
    zips = [f for f in repo_addon_path.glob("repository.kodi-mcp-*.zip") if f.is_file()]
    zips.sort(key=lambda x: x.name, reverse=True)  # Newest first
    
    if not zips:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("No repository addons found", status_code=404)
    
    html = """<!DOCTYPE html>
<html>
<head>
    <title>Kodi MCP Install Directory</title>
    <style>
        body { font-family: sans-serif; margin: 20px; }
        h1 { color: #333; }
        .zip-item { margin: 10px 0; padding: 10px; background: #f5f5f5; border-radius: 4px; }
        .zip-name { font-weight: bold; }
        .zip-size { color: #666; font-size: 0.9em; }
        .download-link { color: #0066cc; text-decoration: none; }
        .download-link:hover { text-decoration: underline; }
        .latest-badge { background: #4CAF50; color: white; padding: 2px 8px; border-radius: 3px; font-size: 0.8em; }
    </style>
</head>
<body>
    <h1>Kodi MCP Repository Addons</h1>
    <p>Download and install Kodi repository addons:</p>
"""
    
    for zip_path in zips:
        size = zip_path.stat().st_size
        size_kb = size / 1024
        is_latest = zip_path.name.endswith("-latest.zip")
        
        html += f"""
    <div class="zip-item">
        <div class="zip-name">
            {zip_path.name}
            {' <span class="latest-badge">LATEST</span>' if is_latest else ''}
        </div>
        <div class="zip-size">{size_kb:.1f} KB</div>
        <div>
            <a href="{zip_path.name}" class="download-link" download>Download</a> |
            <a href="/repo/install/{zip_path.name}" class="download-link">Direct</a>
        </div>
    </div>
"""
    
    html += """
</body>
</html>
"""
    return HTMLResponse(html)


@router.get("/repo/install/latest.zip")
async def latest_zip_redirect():
    """Serve latest.zip - finds the latest versioned zip from published location."""
    # Source repo addon from dev-repo/zips/repository.kodi-mcp (actual published location)
    repo_addon_zips_dir = REPO_ROOT / "dev-repo" / "zips" / "repository.kodi-mcp"
    repo_addon_path = repo_addon_zips_dir if repo_addon_zips_dir.exists() else REPO_ROOT.parent / "repo-addon"
    
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
    """Serve individual install files from the published repo addon location."""
    # Source repo addon from dev-repo/zips/repository.kodi-mcp (actual published location)
    repo_addon_zips_dir = REPO_ROOT / "dev-repo" / "zips" / "repository.kodi-mcp"
    repo_addon_path = repo_addon_zips_dir if repo_addon_zips_dir.exists() else REPO_ROOT.parent / "repo-addon"
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
