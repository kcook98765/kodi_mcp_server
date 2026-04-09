from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    from kodi_mcp_server.repo_app import configure_repo_app
    from kodi_mcp_server.mcp_app import configure_mcp_app

    app = FastAPI()
    configure_repo_app(app)
    configure_mcp_app(app)
    return app


def test_update_addon_returns_initial_install_hint_when_not_installed(tmp_path: Path, monkeypatch):
    """If addon is in repo metadata but not installed in Kodi, update_addon should not attempt install.

    It should return a structured hint for initial manual install.
    """

    # Patch repo root to isolated temp so _read_repo_version reads our test file.
    repo_root = tmp_path / "repo"
    dev_repo = repo_root / "dev-repo"
    dev_repo.mkdir(parents=True, exist_ok=True)
    (dev_repo / "addons.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<addons>\n'
        '<addon id="script.kodi_mcp_test" name="Kodi MCP Test Script" version="0.0.1" provider-name="kodi_mcp">\n'
        '  <requires><import addon="xbmc.python" version="3.0.0"/></requires>\n'
        '  <extension point="xbmc.python.script" library="default.py"/>\n'
        '</addon>\n'
        '</addons>\n',
        encoding="utf-8",
    )

    # Ensure config uses the patched paths.
    import kodi_mcp_server.paths as paths

    monkeypatch.setattr(paths, "AUTHORITATIVE_REPO_ROOT", repo_root, raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(paths, "PROJECT_DIR", tmp_path / "project", raising=False)
    monkeypatch.setenv("REPO_BASE_URL", "http://testserver")
    monkeypatch.setenv("KODI_JSONRPC_URL", "http://localhost:8080/jsonrpc")
    monkeypatch.setenv("KODI_BRIDGE_BASE_URL", "http://localhost:8765")

    import importlib
    import kodi_mcp_server.config as config
    import kodi_mcp_server.repo_server as repo_server

    importlib.reload(config)
    importlib.reload(repo_server)

    # Monkeypatch composition to provide a bridge tool that reports not installed.
    # This keeps the test local without any real Kodi bridge.
    from kodi_mcp_server.models.messages import ResponseMessage

    class _DummyBridgeTool:
        async def get_bridge_addon_info(self, addonid: str) -> ResponseMessage:
            return ResponseMessage(
                request_id="dummy",
                result={"installed": False, "enabled": False, "version": None},
                error=None,
            )

        async def execute_bridge_builtin(self, command: str, addonid: str | None = None) -> ResponseMessage:
            raise AssertionError("Should not attempt builtins when initial install is required")

    class _DummyJsonRpcTool:
        pass

    # mcp_app imports build_addon_ops_tool directly, so patch both the
    # composition module and the imported symbol in mcp_app.
    import kodi_mcp_server.composition as composition
    import kodi_mcp_server.mcp_app as mcp_app

    factory = lambda: __import__("kodi_mcp_server.tools.addon_ops", fromlist=["AddonOpsTool"]).AddonOpsTool(
        _DummyBridgeTool(), _DummyJsonRpcTool()
    )
    monkeypatch.setattr(composition, "build_addon_ops_tool", factory)
    monkeypatch.setattr(mcp_app, "build_addon_ops_tool", factory)

    app = _make_app()
    client = TestClient(app)

    resp = client.post("/tools/update_addon", json={"addonid": "script.kodi_mcp_test"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("error") is None
    result = payload.get("result")
    assert isinstance(result, dict)
    assert result.get("requires_initial_user_install") is True
    assert result.get("is_published_in_repo") is True
    assert result.get("is_installed") is False
    assert result.get("repo_version") == "0.0.1"



