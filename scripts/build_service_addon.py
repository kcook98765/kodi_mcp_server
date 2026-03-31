#!/usr/bin/env python3
"""Bump the service.kodi_mcp patch version and build a versioned manual-install zip.

Canonical source of truth:
- kodi_addon/packages/service.kodi_mcp/

Compatibility build output:
- addon/service.kodi_mcp-<version>.zip

This script is the canonical manual bridge-addon release path:
1. bump version in canonical source
2. build versioned install zip
3. hand off to the operator for manual Kodi install
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kodi_mcp_server.artifacts import build_service_addon_artifact

_CURRENT_ARTIFACT = build_service_addon_artifact(version="0.0.0")
ADDON_XML = _CURRENT_ARTIFACT.addon_xml_path


def main() -> int:
    text = ADDON_XML.read_text(encoding="utf-8")
    match = re.search(r'version="(\d+)\.(\d+)\.(\d+)"', text)
    if not match:
        print(f"FAIL: could not find semantic version in {ADDON_XML}")
        return 1

    major, minor, patch = map(int, match.groups())
    old_version = f"{major}.{minor}.{patch}"
    new_version = f"{major}.{minor}.{patch + 1}"

    new_text = re.sub(
        r'version="\d+\.\d+\.\d+"',
        f'version="{new_version}"',
        text,
        count=1,
    )
    ADDON_XML.write_text(new_text, encoding="utf-8")

    artifact = build_service_addon_artifact(version=new_version)
    zip_path = artifact.build_legacy_zip()

    print(f"addon_id={artifact.addon_id}")
    print(f"old_version={old_version}")
    print(f"new_version={new_version}")
    print(f"source_dir={artifact.source_dir}")
    print(f"zip_path={zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
