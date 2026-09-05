from __future__ import annotations

import pytest
from mcp.types import CallToolRequestParams, PaginatedRequestParams

from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_server.models.messages import ResponseMessage
from kodi_mcp_server.repository_readiness import inspect_repository_readiness


def _evidence(*, installed=True, enabled=True, version="1.0.4"):
    return {
        "ok": True,
        "addon_id": "repository.kodi-mcp",
        "installed": installed,
        "enabled": enabled,
        "installed_version": version if installed else None,
        "configured_identity": {
            "addon_id": "repository.kodi-mcp" if installed else None,
            "version": version if installed else None,
        },
        "urls": {
            "metadata": "http://repo.test/repo/content/addons.xml",
            "checksum": "http://repo.test/repo/content/addons.xml.md5",
            "datadir": "http://repo.test/repo/content/zips/",
        },
        "metadata": {"reachable": True, "parseable": True, "addon_count": 1},
        "checksum": {"reachable": True, "match": True},
        "package": {"observable": True, "reachable": True},
        "catalog_refresh": {"observable": False, "state": "unknown"},
        "catalog_ingestion": {"observable": False, "state": "unknown"},
    }


class _Bridge:
    def __init__(self, evidence=None, error=None):
        self.evidence = evidence or _evidence()
        self.error = error

    async def get_repository_readiness(self):
        if self.error:
            return ResponseMessage("bridge", None, self.error)
        return ResponseMessage(
            "bridge",
            {"transport": {"ok": True}, "result": self.evidence},
            None,
        )


@pytest.mark.asyncio
async def test_canonical_identity_and_configured_urls_match(monkeypatch):
    monkeypatch.setattr("kodi_mcp_server.repository_readiness.REPO_BASE_URL", "http://repo.test")
    result = await inspect_repository_readiness(_Bridge())
    assert result.error is None
    assert result.result["canonical_version"] == "1.0.4"
    assert result.result["identity_match"] is True
    assert result.result["urls_match_server_configuration"] is True
    assert result.result["ready"] is True
    assert result.result["catalog_ingestion"] == {"observable": False, "state": "unknown"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("installed", "enabled", "version", "identity_match"),
    [
        (False, False, "1.0.4", False),
        (True, False, "1.0.4", True),
        (True, True, "1.0.3", False),
        (True, True, "1.0.5", False),
    ],
)
async def test_nonready_identity_and_enablement_states(
    monkeypatch, installed, enabled, version, identity_match
):
    monkeypatch.setattr("kodi_mcp_server.repository_readiness.REPO_BASE_URL", "http://repo.test")
    result = await inspect_repository_readiness(
        _Bridge(_evidence(installed=installed, enabled=enabled, version=version))
    )
    assert result.result["identity_match"] is identity_match
    assert result.result["ready"] is False


@pytest.mark.asyncio
async def test_bridge_error_is_surfaced():
    result = await inspect_repository_readiness(_Bridge(error="bridge unavailable"))
    assert result.error == "bridge unavailable"


@pytest.mark.asyncio
async def test_public_tool_is_read_only_zero_argument_and_rejects_inputs():
    bridge = _Bridge()
    server, _ = build_mcp_server(
        {"bridge": bridge, "jsonrpc": object(), "notifications": None}
    )
    list_handler = server.get_request_handler("tools/list")
    tools = (await list_handler.handler(None, PaginatedRequestParams())).tools
    tool = next(item for item in tools if item.name == "repository_readiness")
    assert tool.input_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.open_world_hint is False

    call_handler = server.get_request_handler("tools/call")
    for arguments in (
        {"url": "https://example.invalid"},
        {"path": "/tmp/addon.xml"},
        {"addon_id": "repository.other"},
        {"method": "POST"},
        {"credentials": "secret"},
    ):
        result = await call_handler.handler(
            None, CallToolRequestParams(name="repository_readiness", arguments=arguments)
        )
        assert result.is_error is True
        assert result.structured_content["error_type"] == "invalid_params"
