#!/usr/bin/env python3
"""Publish the canonical repository.kodi_mcp_dev addon metadata and the built zip artifact into the authoritative repo tree.

This keeps the published repo-addon metadata and the live built artifact aligned with the canonical source so
Kodi does not keep seeing stale embedded URLs or missing artifacts.
"""

import sys
import zipfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from kodi_mcp_server.repository_addon_manifest import load_repository_addon_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # Project repo root (not workspace root)
# Canonical source: templates/repository-addon/addon.xml (used by build_repo_addon)
CANONICAL_ADDON_XML = PROJECT_ROOT / "templates" / "repository-addon" / "addon.xml"
PUBLISHED_ADDON_XML = PROJECT_ROOT / "repo" / "repository.kodi-mcp" / "addon.xml"

# Default artifact location matching repo_generator.py's build_repo_addon output path
# build_repo_addon outputs to REPO_ROOT.parent / "repo-addon" = PROJECT_ROOT / "repo-addon"
DEFAULT_ARTIFACT_PATH = PROJECT_ROOT / "repo-addon"


def ensure_live_zip_path(parent: Path) -> None:
    """Ensure the parent directory of the live zip exists."""
    parent.mkdir(parents=True, exist_ok=True)


def find_canonical_repo_artifact(base_path: Path) -> Optional[Path]:
    """Find the exact manifest-selected repository addon artifact."""
    if not base_path.exists():
        return None

    candidate = base_path / load_repository_addon_manifest().artifact_filename
    return candidate if candidate.is_file() else None


def sync_zip_artifact(source: Path, dest_dir: Path) -> Path:
    """
    Copy the built repository zip to the live published location with a timestamped filename.
    Returns the path to the published artifact.
    """
    ensure_live_zip_path(dest_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    live_zip = dest_dir / f"repository.kodi-mcp-live-{timestamp}.zip"
    
    # Validate source zip
    with zipfile.ZipFile(source, 'r') as zf:
        zf.testzip()
    
    # Copy file contents
    live_zip.write_bytes(source.read_bytes())
    
    return live_zip


def main() -> int:
    PUBLISHED_ADDON_XML.parent.mkdir(parents=True, exist_ok=True)
    PUBLISHED_ADDON_XML.write_text(CANONICAL_ADDON_XML.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"source={CANONICAL_ADDON_XML}")
    print(f"published={PUBLISHED_ADDON_XML}")
    
    # Sync the built repository zip artifact to the live published path
    # Source: use the exact version named by the canonical manifest.
    built_artifact = find_canonical_repo_artifact(DEFAULT_ARTIFACT_PATH)
    
    if built_artifact:
        published_zip = sync_zip_artifact(built_artifact, PROJECT_ROOT / "repo")
        print(f"artifact_published={published_zip}")
    else:
        print(f"warning: built artifact not found in {DEFAULT_ARTIFACT_PATH}, skipping artifact publication")
        print("tip: run 'python -m kodi_mcp_server build_repo' first to create the artifact")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
