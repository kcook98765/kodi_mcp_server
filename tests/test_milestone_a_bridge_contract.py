from pathlib import Path

import pytest


def test_parse_envelope_accepts_flat_result_shape():
    from kodi_mcp_server.milestone_a_bridge import _parse_envelope

    parsed = _parse_envelope(
        {
            "transport": {"ok": True, "bridge": "service.kodi_mcp"},
            "result": {
                "ok": True,
                "state": {"repo_zip": {"saved_path": "/tmp/dev-repo.zip"}},
                "derived": {"dev_setup_available": True},
            },
        }
    )

    assert parsed.transport_ok is True
    assert parsed.business_ok is True
    assert parsed.envelope["result"]["derived"]["dev_setup_available"] is True


@pytest.mark.asyncio
async def test_stage_dev_repo_zip_reports_install_hint(tmp_path: Path, monkeypatch):
    import kodi_mcp_server.milestone_a_bridge as milestone

    zip_path = tmp_path / "repository.kodi-mcp-1.0.0.zip"
    zip_path.write_bytes(b"PK\x03\x04repo")

    class _FakeClient:
        async def repo_stage_upload(self, **kwargs):
            class _Resp:
                error = None
                error_type = None
                result = {
                    "transport": {"ok": True},
                    "result": {
                        "ok": True,
                        "repo_id": kwargs["repo_id"],
                        "saved_path": "/profile/repo_stage/dev-repo.zip",
                    },
                }

            return _Resp()

    async def _fake_state():
        class _Resp:
            error = None
            result = {
                "transport": {"ok": True},
                "result": {
                    "ok": True,
                    "state": {
                        "repo_zip": {"saved_path": "/profile/repo_stage/dev-repo.zip"},
                    },
                    "derived": {"dev_setup_available": True},
                    "install_hint": {
                        "action": "Kodi UI: Add-ons > Install from zip file",
                        "path": "/profile/repo_stage/dev-repo.zip",
                    },
                },
            }

        return milestone._parse_envelope(_Resp.result), _Resp()

    monkeypatch.setattr(milestone, "build_bridge_client", lambda: _FakeClient())
    monkeypatch.setattr(milestone, "read_addon_state", _fake_state)

    out = await milestone.stage_dev_repo_zip(zip_path=str(zip_path), verify=True)

    assert out["upload"]["transport_ok"] is True
    assert out["state"]["dev_setup_available"] is True
    assert out["state"]["install_hint"]["path"].endswith("dev-repo.zip")
