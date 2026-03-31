"""Minimal test fixtures for kodi_mcp_server tests."""
import os
from pathlib import Path
import pytest


def _load_test_env():
    """Load test environment from .env.test if present."""
    env_path = Path.cwd() / ".env.test"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))


_load_test_env()


# pytest provides monkeypatch fixture automatically via pytest-mock plugin
# no additional fixture needed

