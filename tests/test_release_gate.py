from __future__ import annotations

import subprocess

import pytest

from kodi_mcp_server.paths import PROJECT_ROOT


def test_release_identity_accepts_current_source_and_rejects_version_drift():
    from kodi_mcp_server.release_gate import GateError, check_release_identity

    current_sha = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    identity = check_release_identity(
        project_root=PROJECT_ROOT,
        expected_version="0.2.2",
        expected_sha=current_sha,
    )
    assert identity.pop("future_tag") == "v0.2.2"
    assert set(identity.values()) == {"0.2.2"}

    with pytest.raises(GateError, match=r"expected version '0\.2\.3'"):
        check_release_identity(
            project_root=PROJECT_ROOT,
            expected_version="0.2.3",
        )


def test_release_identity_rejects_malformed_inputs():
    from kodi_mcp_server.release_gate import GateError, check_release_identity

    with pytest.raises(GateError, match="semantic version"):
        check_release_identity(project_root=PROJECT_ROOT, expected_version="$(bad)")
    with pytest.raises(GateError, match="40 lowercase hexadecimal"):
        check_release_identity(
            project_root=PROJECT_ROOT,
            expected_version="0.2.1",
            expected_sha="main",
        )


def test_openapi_gate_is_unique_deterministic_and_warning_free():
    from kodi_mcp_server.release_gate import check_openapi

    result = check_openapi()
    assert result["operation_count"] > 0
    assert result["unique_operation_ids"] == result["operation_count"]
    assert result["duplicate_warnings"] == 0
    assert result["deterministic"] is True


def test_streamable_http_gate_matches_authoritative_tool_contract():
    from kodi_mcp_mcp.tool_contract import EXPECTED_TOOL_NAMES
    from kodi_mcp_server.release_gate import check_streamable_http

    result = check_streamable_http()
    assert result["tool_count"] == len(EXPECTED_TOOL_NAMES)
    assert result["unique_tool_count"] == len(EXPECTED_TOOL_NAMES)
    assert set(result["tool_names"]) == EXPECTED_TOOL_NAMES
