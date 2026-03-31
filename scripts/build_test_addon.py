#!/usr/bin/env python3
"""Bump the script.kodi_mcp_test patch version, update marker, and build a versioned zip.

Artifact flow:
- source of truth: kodi_addon/packages/script.kodi_mcp_test/
- compatibility build output: addon/script.kodi_mcp_test-<version>.zip
- publish destination (separate step): repo/dev-repo/zips/...
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kodi_mcp_server.artifacts import build_test_addon_artifact

_CURRENT_ARTIFACT = build_test_addon_artifact(version="0.0.0")
ADDON_DIR = _CURRENT_ARTIFACT.source_dir
ADDON_XML = _CURRENT_ARTIFACT.addon_xml_path
DEFAULT_PY = ADDON_DIR / "default.py"


def main() -> int:
    text = ADDON_XML.read_text(encoding="utf-8")
    match = re.search(r'version="(\d+)\.(\d+)\.(\d+)"', text)
    if not match:
        print(f"FAIL: could not find version in {ADDON_XML}")
        return 1

    major, minor, patch = map(int, match.groups())
    new_version = f"{major}.{minor}.{patch + 1}"
    new_marker = f"KODI_MCP_TEST_ADDON_EXECUTED_V{new_version.replace('.', '_')}"

    new_text = re.sub(r'version="\d+\.\d+\.\d+"', f'version="{new_version}"', text, count=1)
    ADDON_XML.write_text(new_text, encoding="utf-8")

    default_text = DEFAULT_PY.read_text(encoding="utf-8")
    default_text = re.sub(
        r'TEST_MARKER = ".*"',
        f'TEST_MARKER = "{new_marker}"',
        default_text,
        count=1,
    )
    DEFAULT_PY.write_text(default_text, encoding="utf-8")

    artifact = build_test_addon_artifact(version=new_version)
    zip_path = artifact.build_legacy_zip()

    print(f"new_version={new_version}")
    print(f"marker={new_marker}")
    print(f"zip_path={zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
