from __future__ import annotations

import re
import subprocess
import tomllib

from kodi_mcp_mcp.server_core import SERVER_VERSION
from kodi_mcp_server import __version__
from kodi_mcp_server.http_app import create_base_app
from kodi_mcp_server.paths import PROJECT_ROOT
from kodi_mcp_server.runtime_identity import capture_runtime_identity


TARGET_VERSION = "0.2.3"


def test_product_version_sources_are_synchronized():
    project_metadata = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert project_metadata["project"]["version"] == TARGET_VERSION
    assert __version__ == TARGET_VERSION
    assert create_base_app().version == TARGET_VERSION
    assert SERVER_VERSION == TARGET_VERSION


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
