from __future__ import annotations

import hashlib
import inspect
import json
import zipfile
from pathlib import Path

import pytest
from mcp.types import CallToolRequestParams, PaginatedRequestParams

from kodi_mcp_mcp.server_core import build_mcp_server
from kodi_mcp_server.models.messages import ResponseMessage
from kodi_mcp_server.repository_bootstrap import (
    REPOSITORY_ADDON_ID,
    REPOSITORY_ADDON_VERSION,
    RepositoryBootstrapError,
    install_repository_bootstrap,
    validate_repository_bootstrap_zip,
)


def _write_zip(path: Path, *, addon_id=REPOSITORY_ADDON_ID, extra=False) -> str:
    addon_xml = (
        '<addon id="%s" name="Kodi MCP Repository" version="%s">'
        '<extension point="xbmc.addon.repository" /></addon>'
        % (addon_id, REPOSITORY_ADDON_VERSION)
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("repository.kodi-mcp/addon.xml", addon_xml)
        archive.writestr("repository.kodi-mcp/service.py", "")
        archive.writestr("repository.kodi-mcp/addons.xml", "<addons />")
        if extra:
            archive.writestr("repository.kodi-mcp/unexpected.py", "")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_canonical_repository_is_accepted(tmp_path: Path):
    path = tmp_path / "repository.zip"
    digest = _write_zip(path)
    result = validate_repository_bootstrap_zip(path, expected_sha256=digest)
    assert result["addon_id"] == REPOSITORY_ADDON_ID
    assert result["version"] == REPOSITORY_ADDON_VERSION
    assert result["sha256"] == digest


@pytest.mark.parametrize(
    ("kind", "message"),
    [("wrong_id", "addon id"), ("extra", "layout"), ("sha", "SHA-256"), ("missing", "missing")],
)
def test_noncanonical_repository_is_rejected(tmp_path: Path, kind: str, message: str):
    path = tmp_path / "repository.zip"
    if kind != "missing":
        digest = _write_zip(path, addon_id="repository.other" if kind == "wrong_id" else REPOSITORY_ADDON_ID, extra=kind == "extra")
    else:
        digest = "0" * 64
    with pytest.raises(RepositoryBootstrapError, match=message):
        validate_repository_bootstrap_zip(
            path,
            expected_sha256=("f" * 64 if kind == "sha" else digest),
        )


class _Bridge:
    def __init__(self, *, install_ok=True):
        self.install_ok = install_ok
        self.staged = None

    async def get_bridge_addon_info(self, addonid):
        state = {
            "addon_id": addonid,
            "installed": self.staged is not None,
            "enabled": self.staged is not None,
            "version": REPOSITORY_ADDON_VERSION if self.staged is not None else None,
        }
        return ResponseMessage("inspect", state, None)

    async def stage_repository_bootstrap(self, *, zip_path, version, sha256):
        self.staged = {"zip_path": zip_path, "version": version, "sha256": sha256}
        return ResponseMessage(
            "stage",
            {"transport": {"ok": True}, "result": {"ok": True, "repo_zip": {"sha256": sha256}}},
            None,
        )

    async def install_repository_bootstrap(self):
        if not self.install_ok:
            return ResponseMessage("install", None, "bridge unavailable")
        return ResponseMessage(
            "install",
            {
                "transport": {"ok": True},
                "result": {
                    "ok": True,
                    "action": "installed",
                    "addon_id": REPOSITORY_ADDON_ID,
                    "version": REPOSITORY_ADDON_VERSION,
                    "artifact_sha256": self.staged["sha256"],
                },
            },
            None,
        )


@pytest.mark.asyncio
async def test_bridge_error_is_surfaced(tmp_path: Path, monkeypatch):
    path = tmp_path / "repository.zip"
    _write_zip(path)
    monkeypatch.setattr(
        "kodi_mcp_server.repository_bootstrap.build_repo_addon",
        lambda **kwargs: {"status": "ok", "output_zip": str(path)},
    )
    result = await install_repository_bootstrap(_Bridge(install_ok=False))
    assert result.error is not None
    assert "bridge unavailable" in result.error


@pytest.mark.asyncio
async def test_public_tool_is_zero_argument_and_rejects_deployment_identity():
    bridge = _Bridge()
    server, _ = build_mcp_server(
        {"bridge": bridge, "jsonrpc": object(), "notifications": None}
    )
    list_handler = server.get_request_handler("tools/list")
    tools = (await list_handler.handler(None, PaginatedRequestParams())).tools
    tool = next(item for item in tools if item.name == "repository_bootstrap_install")
    assert tool.input_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    call_handler = server.get_request_handler("tools/call")
    for arguments in (
        {"path": "/tmp/other.zip"},
        {"url": "https://example.invalid/addon.zip"},
        {"addon_id": "plugin.video.other"},
        {"builtin": "InstallFromZip"},
    ):
        result = await call_handler.handler(
            None,
            CallToolRequestParams(name="repository_bootstrap_install", arguments=arguments),
        )
        envelope = json.loads(result.content[0].text)
        assert envelope["ok"] is False
        assert envelope["error_type"] == "invalid_params"


def test_bridge_client_operation_accepts_no_deployment_identity():
    from kodi_mcp_server.transport.http_bridge import HttpBridgeClient

    assert list(inspect.signature(HttpBridgeClient.install_repository_bootstrap).parameters) == ["self"]


@pytest.mark.parametrize(
    "base_url",
    ["http://127.0.0.1:8765", "http://192.168.50.20:8765", "http://kodi.local:8765"],
)
@pytest.mark.asyncio
async def test_bridge_client_keeps_configured_network_endpoint(base_url: str, monkeypatch):
    module = __import__(
        "kodi_mcp_server.transport.http_bridge", fromlist=["HttpBridgeClient"]
    )
    observed = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"transport":{"ok":true},"result":{"ok":true}}'

    def _urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["method"] = request.method
        return _Response()

    monkeypatch.setattr(module.urllib_request, "urlopen", _urlopen)
    client = module.HttpBridgeClient(base_url=base_url)
    result = await client.install_repository_bootstrap()
    assert result.error is None
    assert observed == {
        "url": base_url + "/repo/bootstrap/install",
        "method": "POST",
    }
