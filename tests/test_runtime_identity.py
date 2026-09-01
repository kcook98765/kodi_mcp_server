from __future__ import annotations

import re
import subprocess

from kodi_mcp_server import __version__
from kodi_mcp_server.paths import PROJECT_ROOT
from kodi_mcp_server.runtime_identity import capture_runtime_identity


def test_runtime_identity_uses_package_version_and_current_source_fingerprint():
    identity = capture_runtime_identity()
    expected_head = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert identity["version"] == __version__
    assert identity["version"] != "0.0.0"
    assert identity["git_sha"] == expected_head
    assert identity["source_root"] == str(PROJECT_ROOT)
    assert re.fullmatch(r"[0-9a-f]{64}", identity["source_fingerprint_sha256"])
    assert identity["build_id"].startswith(f"{__version__}+g{expected_head[:12]}.")
