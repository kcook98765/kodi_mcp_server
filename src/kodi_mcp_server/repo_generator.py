"""Repository addon packaging and generation utilities.

Provides tools to create installable Kodi repository addons that point at
the server-served repository URL.
"""

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from jinja2 import Environment, FileSystemLoader

from kodi_mcp_server.config import REPO_BASE_URL, REPO_ROOT


class RepoGeneratorError(Exception):
    """Raised when repo addon generation fails."""

    pass


def get_checksum(data: bytes) -> str:
    """Generate base64-encoded SHA256 checksum."""
    return hashlib.sha256(data).hexdigest()


def load_addons_xml(repo_root: Path = REPO_ROOT) -> Dict:
    """Load and parse addons.xml from repository root.

    Returns:
        Dict with addon list from addons.xml
    """
    addons_file = repo_root / "addons.xml"
    if not addons_file.exists():
        return {"addons": []}

    # Simple XML parsing for addons list
    try:
        import xml.etree.ElementTree as ET

        tree = ET.parse(addons_file)
        root = tree.getroot()
        addons = []
        for addon in root.findall("addon"):
            addons.append(
                {
                    "id": addon.get("id"),
                    "version": addon.get("version"),
                    "name": addon.get("name"),
                }
            )
        return {"addons": addons}
    except Exception as e:
        return {"addons": [], "error": str(e)}


def render_template(
    template_path: Path,
    output_path: Path,
    context: Dict,
) -> None:
    """Render a Jinja2 template to output file.

    Args:
        template_path: Path to template file
        output_path: Where to write rendered output
        context: Dict of template variables
    """
    if not template_path.exists():
        raise RepoGeneratorError(f"Template not found: {template_path}")

    env = Environment(loader=FileSystemLoader(template_path.parent))
    template = env.get_template(template_path.name)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template.render(**context), encoding="utf-8")


def build_repo_addon(
    repo_version: str = "1.0.0",
    repo_base_url: Optional[str] = None,
    output_zip: Optional[Path] = None,
    repo_root: Path = REPO_ROOT,
) -> Dict:
    """Build an installable Kodi repository addon zip.

    Args:
        repo_version: Version string for the repo addon
        repo_base_url: Canonical URL where repo is served (e.g. http://host:8001)
        output_zip: Output path for generated .zip (auto-generated if None)
        repo_root: Path to repository root directory

    Returns:
        Dict with build status, output path, and metadata
    """
    repo_base_url = repo_base_url or REPO_BASE_URL

    if output_zip is None:
        output_zip = repo_root.parent / "repo-addon" / f"repository.kodi-mcp-{repo_version}.zip"

    # Create staging directory
    staging = tempfile.mkdtemp(prefix="repo-addon-")
    staging_path = Path(staging)

    try:
        # Load existing addons from repo
        addons_data = load_addons_xml(repo_root)
        addon_count = len(addons_data.get("addons", []))

        # Prepare template context
        context = {
            "repo_version": repo_version,
            "repo_base_url": repo_base_url,
        }

        # Copy addon.xml
        addon_xml_template = Path(__file__).parent.parent.parent / "templates" / "addon.xml"
        addon_xml_output = staging_path / "addon.xml"
        render_template(addon_xml_template, addon_xml_output, context)

        # Copy repository.xml (metadata)
        repository_xml_template = (
            Path(__file__).parent.parent.parent / "templates" / "repository.xml"
        )
        repository_xml_output = staging_path / "repository.xml"
        render_template(repository_xml_template, repository_xml_output, context)

        # Create service.py stub (Kodi repo addons need this)
        service_py = staging_path / "service.py"
        service_py.write_text(
            '#!/usr/bin/env python3\n# Kodi repository service stub\n# Required for valid addon structure\nprint("Kodi MCP Repository Service")\n',
            encoding="utf-8",
        )

        # Create metadata/addons.xml for the repo addon itself
        metadata_dir = staging_path / "metadata" / "addons"
        metadata_dir.mkdir(parents=True, exist_ok=True)

        # Create basic addons.xml for repo's own metadata
        addons_xml = '<addons>\n'
        for addon in addons_data.get("addons", []):
            addons_xml += f'    <addon id="{addon.get("id")}" version="{addon.get("version")}" name="{addon.get("name")}"/>\n'
        addons_xml += "</addons>"

        (staging_path / "addons.xml").write_text(addons_xml, encoding="utf-8")

        # Create ZIP
        output_zip.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in staging_path.rglob("*"):
                if file_path.is_file():
                    arc_name = file_path.relative_to(staging_path)
                    zf.write(file_path, arc_name)

        return {
            "status": "ok",
            "output_zip": str(output_zip),
            "repo_version": repo_version,
            "repo_base_url": repo_base_url,
            "addon_count": addon_count,
            "staging_path": str(staging_path),
        }

    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "staging_path": staging_path if "staging_path" in dir() else None,
        }
    finally:
        # Cleanup staging
        if os.path.exists(staging):
            shutil.rmtree(staging)


def generate_addons_xml_gz(repo_root: Path = REPO_ROOT, output: Optional[Path] = None):
    """Generate addons.xml.gz for repository serving.

    Args:
        repo_root: Path to repository root
        output: Optional custom output path

    Returns:
        Path to generated .gz file
    """
    import gzip

    if output is None:
        output = repo_root / "addons.xml.gz"

    addons_file = repo_root / "addons.xml"
    if not addons_file.exists():
        raise RepoGeneratorError("addons.xml not found in repository root")

    with open(addons_file, "rb") as f_in:
        content = f_in.read()

    with gzip.open(output, "wb") as f_out:
        f_out.write(content)

    return output
