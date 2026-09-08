import json

import pytest

from kodi_mcp_server.targets.model import TargetValidationError
from kodi_mcp_server.targets.registry import (
    DuplicateTargetError,
    LegacyTargetSettings,
    TargetConfigWarning,
    TargetRegistry,
)


def _legacy():
    return LegacyTargetSettings(
        jsonrpc_url="http://proxy:8080/jsonrpc",
        bridge_url="http://proxy:8765",
        websocket_url="ws://proxy:9090/jsonrpc",
        tcp_host="proxy",
        tcp_port=9090,
        timeout_seconds=17,
    )


def _configured(target_id, host):
    return {
        "id": target_id,
        "name": target_id,
        "endpoints": {
            "jsonrpc_url": f"http://{host}:8080/jsonrpc",
            "bridge_url": f"http://{host}:8765",
        },
        "auth": {
            "jsonrpc_username": f"env:{target_id.upper().replace('-', '_')}_USER",
            "jsonrpc_password": f"env:{target_id.upper().replace('-', '_')}_PASSWORD",
            "bridge_token": f"env:{target_id.upper().replace('-', '_')}_TOKEN",
        },
    }


def test_registry_synthesizes_exact_legacy_default_target():
    registry = TargetRegistry.from_sources(legacy=_legacy())

    target = registry.get("default")
    assert target.target_id == "default"
    assert target.name == "Default Kodi"
    assert target.endpoints.jsonrpc_url == "http://proxy:8080/jsonrpc"
    assert target.endpoints.bridge_url == "http://proxy:8765"
    assert target.endpoints.websocket_url == "ws://proxy:9090/jsonrpc"
    assert target.endpoints.tcp_host == "proxy"
    assert target.endpoints.tcp_port == 9090
    assert target.timeout_seconds == 17
    assert target.auth.to_dict() == {
        "jsonrpc_username": "env:KODI_JSONRPC_USERNAME",
        "jsonrpc_password": "env:KODI_JSONRPC_PASSWORD",
        "bridge_token": "env:KODI_BRIDGE_TOKEN",
    }


def test_registry_loads_file_then_applies_inline_precedence(tmp_path):
    target_file = tmp_path / "targets.json"
    target_file.write_text(
        json.dumps([_configured("living-room", "from-file"), _configured("bedroom", "bedroom")]),
        encoding="utf-8",
    )
    inline = json.dumps([_configured("living-room", "from-inline")])

    registry = TargetRegistry.from_sources(
        legacy=_legacy(), targets_file=target_file, targets_json=inline
    )

    assert [target.target_id for target in registry.list()] == [
        "bedroom",
        "default",
        "living-room",
    ]
    assert registry.get("living-room").endpoints.bridge_url == "http://from-inline:8765"


def test_registry_rejects_direct_duplicate_additions():
    registry = TargetRegistry.from_sources(legacy=_legacy())

    with pytest.raises(DuplicateTargetError, match="default"):
        registry.add(registry.get("default"))


def test_registry_warns_and_keeps_first_duplicate_within_one_source():
    inline = json.dumps(
        [_configured("living-room", "first"), _configured("living-room", "second")]
    )

    with pytest.warns(TargetConfigWarning, match="duplicate target id"):
        registry = TargetRegistry.from_sources(legacy=_legacy(), targets_json=inline)

    assert registry.get("living-room").endpoints.bridge_url == "http://first:8765"


def test_registry_warns_and_skips_malformed_additional_entries():
    malformed = _configured("bad", "bad")
    malformed["endpoints"]["jsonrpc_url"] = "ws://bad:8080/jsonrpc"
    missing_endpoint = _configured("also-bad", "bad")
    missing_endpoint["endpoints"]["bridge_url"] = ""
    inline_secret = _configured("secret-bad", "bad")
    inline_secret["auth"]["bridge_token"] = "warning-secret-sentinel"
    inline = json.dumps(
        [malformed, missing_endpoint, inline_secret, _configured("good", "good")]
    )

    with pytest.warns(TargetConfigWarning) as recorded:
        registry = TargetRegistry.from_sources(legacy=_legacy(), targets_json=inline)

    assert len(recorded) == 3
    assert all("warning-secret-sentinel" not in str(item.message) for item in recorded)
    assert [target.target_id for target in registry.list()] == ["default", "good"]


def test_registry_default_uses_target_validation_for_malformed_legacy_endpoint():
    malformed = LegacyTargetSettings(
        jsonrpc_url="ftp://proxy/jsonrpc",
        bridge_url="http://proxy:8765",
    )

    with pytest.raises(TargetValidationError, match="jsonrpc_url"):
        TargetRegistry.from_sources(legacy=malformed)
