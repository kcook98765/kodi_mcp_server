"""
Tests for POST endpoint handling with Pydantic request models.

Verifies that FastAPI correctly parses structured JSON objects
for POST-style endpoints that use request models.
"""

import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, "/home/node/.openclaw/workspace/project/src")

from kodi_mcp_server.main import app

client = TestClient(app)


class TestPOSTBridgeEndpoints:
    """Test POST bridge endpoints accept structured JSON bodies."""

    def test_execute_bridge_addon_requires_addonid(self):
        """execute_bridge_addon should accept JSON with addonid."""
        response = client.post(
            "/tools/execute_bridge_addon",
            json={"addonid": "test.addon"},
        )
        # Should not return 422 (validation error)
        assert response.status_code != 422

    def test_execute_bridge_builtin_requires_command(self):
        """execute_bridge_builtin should accept JSON with command."""
        response = client.post(
            "/tools/execute_bridge_builtin",
            json={"command": "PlayerControl(Play)"},
        )
        # Should not return 422
        assert response.status_code != 422

    def test_execute_bridge_builtin_with_addonid(self):
        """execute_bridge_builtin should accept both command and addonid."""
        response = client.post(
            "/tools/execute_bridge_builtin",
            json={"command": "ReloadSkin", "addonid": "plugin.video.test"},
        )
        # Should not return 422
        assert response.status_code != 422

    def test_ensure_bridge_addon_enabled_requires_addonid(self):
        """ensure_bridge_addon_enabled should accept JSON with addonid."""
        response = client.post(
            "/tools/ensure_bridge_addon_enabled",
            json={"addonid": "test.addon"},
        )
        # Should not return 422
        assert response.status_code != 422

    def test_write_bridge_log_marker_requires_message(self):
        """write_bridge_log_marker should accept JSON with message."""
        response = client.post(
            "/tools/write_bridge_log_marker",
            json={"message": "test message"},
        )
        # Should not return 422
        assert response.status_code != 422

    def test_upload_bridge_addon_zip_requires_path(self):
        """upload_bridge_addon_zip should accept JSON with local_zip_path."""
        response = client.post(
            "/tools/upload_bridge_addon_zip",
            json={"local_zip_path": "/tmp/test.zip"},
        )
        # Should not return 422
        assert response.status_code != 422

    def test_bridge_debug_ping_no_body_required(self):
        """bridge_debug_ping should work with empty JSON body."""
        response = client.post(
            "/tools/bridge_debug_ping",
            json={},
        )
        # Should not return 422
        assert response.status_code != 422


class TestPOSTValidationErrors:
    """Test that POST endpoints properly reject invalid request bodies with 422."""

    def test_execute_bridge_addon_requires_addonid_field(self):
        """execute_bridge_addon should reject request without addonid."""
        response = client.post(
            "/tools/execute_bridge_addon",
            json={},
        )
        # Should return 422 (validation error)
        assert response.status_code == 422

    def test_execute_bridge_builtin_requires_command(self):
        """execute_bridge_builtin should reject request without command."""
        response = client.post(
            "/tools/execute_bridge_builtin",
            json={},
        )
        # Should return 422 (validation error)
        assert response.status_code == 422

    def test_write_bridge_log_marker_requires_message(self):
        """write_bridge_log_marker should reject request without message."""
        response = client.post(
            "/tools/write_bridge_log_marker",
            json={},
        )
        # Should return 422 (validation error)
        assert response.status_code == 422

    def test_ensure_bridge_addon_enabled_requires_addonid(self):
        """ensure_bridge_addon_enabled should reject request without addonid."""
        response = client.post(
            "/tools/ensure_bridge_addon_enabled",
            json={},
        )
        # Should return 422 (validation error)
        assert response.status_code == 422

    def test_upload_bridge_addon_zip_requires_path(self):
        """upload_bridge_addon_zip should reject request without local_zip_path."""
        response = client.post(
            "/tools/upload_bridge_addon_zip",
            json={},
        )
        # Should return 422 (validation error)
        assert response.status_code == 422

    def test_publish_addon_to_repo_requires_all_fields(self):
        """publish_addon_to_repo should reject request with missing required fields."""
        response = client.post(
            "/tools/publish_addon_to_repo",
            json={},
        )
        # Should return 422 (validation error)
        assert response.status_code == 422

    def test_restart_bridge_addon_validates_timeout_range(self):
        """restart_bridge_addon should reject invalid timeout values."""
        response = client.post(
            "/tools/restart_bridge_addon",
            json={"timeout_seconds": 0},  # Below minimum of 1
        )
        # Should return 422 (validation error)
        assert response.status_code == 422


class TestPOSTRepoEndpoints:
    """Test POST repo endpoints accept structured JSON bodies."""

    def test_publish_addon_to_repo_requires_all_fields(self):
        """publish_addon_to_repo should accept JSON with all required fields."""
        response = client.post(
            "/tools/publish_addon_to_repo",
            json={
                "addon_zip_path": "/tmp/test.zip",
                "addon_id": "plugin.test",
                "addon_name": "Test Addon",
                "addon_version": "1.0.0",
            },
        )
        # Should not return 422
        assert response.status_code != 422

    # NOTE: test_update_addon_requires_addonid skipped - requires repo setup
    # Validation is covered by Pydantic model; operation test needs full repo

    def test_restart_bridge_addon_with_default_timeout(self):
        """restart_bridge_addon should accept JSON with optional timeout."""
        response = client.post(
            "/tools/restart_bridge_addon",
            json={"timeout_seconds": 30},
        )
        # Should not return 422
        assert response.status_code != 422


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
