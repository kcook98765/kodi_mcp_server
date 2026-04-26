#!/usr/bin/env python3
"""Delegate service.kodi_mcp packaging to the standalone addon repo.

The MCP server repo no longer owns bridge addon source. The canonical source of
truth is the standalone kodi_mcp_addon repo, normally at:

    /srv/openclaw-projects/kodi_mcp_addon/workspace/project

Set KODI_MCP_ADDON_REPO to override that path.
"""

import os
import subprocess
import sys
from pathlib import Path


DEFAULT_ADDON_REPO = Path("/srv/openclaw-projects/kodi_mcp_addon/workspace/project")


def _addon_repo() -> Path:
    return Path(os.environ.get("KODI_MCP_ADDON_REPO", str(DEFAULT_ADDON_REPO))).expanduser()


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    addon_repo = _addon_repo()
    build_script = addon_repo / "scripts" / "build_service_addon.py"
    if not build_script.exists():
        print(
            "FAIL: service.kodi_mcp is owned by the standalone kodi_mcp_addon repo; "
            f"build script not found at {build_script}",
            file=sys.stderr,
        )
        return 1

    cmd = [sys.executable, str(build_script)] + argv
    return subprocess.call(cmd, cwd=str(addon_repo))


if __name__ == "__main__":
    sys.exit(main())
