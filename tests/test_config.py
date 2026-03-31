"""Tests for config validation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kodi_mcp_server.config import ConfigError, validate_config


def test_validate_config_success_with_all_values():
    """validate_config() succeeds when all required values present."""
    # This test verifies that validation passes with valid config
    # Note: The .env file in project/ is loaded at module import time
    # This test assumes the workspace has valid KODI_JSONRPC_URL and KODI_BRIDGE_BASE_URL
    validate_config()  # Should not raise
