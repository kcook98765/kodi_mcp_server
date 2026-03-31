#!/usr/bin/env python3
"""Publish the canonical repository.kodi_mcp_dev addon metadata into the authoritative repo tree.

This keeps the published repo-addon metadata aligned with the canonical source so
Kodi does not keep seeing stale embedded URLs.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_ADDON_XML = PROJECT_ROOT / "kodi_addon" / "packages" / "repository.kodi_mcp_dev" / "addon.xml"
PUBLISHED_ADDON_XML = PROJECT_ROOT / "repo" / "repository.kodi_mcp_dev" / "addon.xml"


def main() -> int:
    PUBLISHED_ADDON_XML.parent.mkdir(parents=True, exist_ok=True)
    PUBLISHED_ADDON_XML.write_text(CANONICAL_ADDON_XML.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"source={CANONICAL_ADDON_XML}")
    print(f"published={PUBLISHED_ADDON_XML}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
