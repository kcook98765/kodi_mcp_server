import json

import kodi_mcp_server.targets.transport_pool as transport_pool_module
from kodi_mcp_server.targets.registry import LegacyTargetSettings, TargetRegistry
from kodi_mcp_server.targets.transport_pool import (
    ResolvedTargetAuth,
    TargetTransportPool,
)


def _registry():
    return TargetRegistry.from_sources(
        legacy=LegacyTargetSettings(
            jsonrpc_url="http://proxy:8080/jsonrpc",
            bridge_url="http://proxy:8765",
            websocket_url="ws://proxy:9090/jsonrpc",
            tcp_host="proxy",
            tcp_port=9090,
            timeout_seconds=17,
        ),
        targets_json=json.dumps(
            [
                {
                    "id": "secondary",
                    "name": "Secondary Kodi",
                    "endpoints": {
                        "jsonrpc_url": "https://secondary:8443/jsonrpc",
                        "bridge_url": "https://secondary:8766",
                        "websocket_url": "wss://secondary:9091/jsonrpc",
                        "tcp_host": "secondary",
                        "tcp_port": 9091,
                    },
                    "auth": {
                        "jsonrpc_username": "env:SECONDARY_USER",
                        "jsonrpc_password": "env:SECONDARY_PASSWORD",
                        "bridge_token": "env:SECONDARY_TOKEN",
                    },
                    "timeout_seconds": 23,
                }
            ]
        ),
    )


def test_transport_pool_builds_default_with_exact_legacy_settings_and_reuses_it():
    pool = TargetTransportPool(
        _registry(),
        environ={},
        legacy_default_auth=ResolvedTargetAuth(
            jsonrpc_username="legacy-user",
            jsonrpc_password="legacy-password",
            bridge_token="legacy-token",
        ),
    )

    first = pool.get("default")
    second = pool.get("default")

    assert second is first
    assert first.jsonrpc.transport.url == "http://proxy:8080/jsonrpc"
    assert first.jsonrpc.transport.username == "legacy-user"
    assert first.jsonrpc.transport.password == "legacy-password"
    assert first.jsonrpc.transport.timeout == 17
    assert first.bridge.client.base_url == "http://proxy:8765"
    assert first.bridge.client.token == "legacy-token"
    assert first.bridge.client.timeout == 17
    assert first.notifications.websocket_url == "ws://proxy:9090/jsonrpc"
    assert first.notifications.tcp_host == "proxy"
    assert first.notifications.tcp_port == 9090
    assert first.notifications.timeout == 17


def test_transport_pool_resolves_refs_and_keeps_distinct_target_state():
    pool = TargetTransportPool(
        _registry(),
        environ={
            "SECONDARY_USER": "second-user",
            "SECONDARY_PASSWORD": "second-password",
            "SECONDARY_TOKEN": "second-token",
        },
        legacy_default_auth=ResolvedTargetAuth(
            jsonrpc_username="default-user",
            jsonrpc_password="default-password",
            bridge_token="default-token",
        ),
    )

    default = pool.get("default")
    secondary = pool.get("secondary")

    assert secondary is pool.get("secondary")
    assert secondary is not default
    assert secondary.jsonrpc is not default.jsonrpc
    assert secondary.bridge is not default.bridge
    assert secondary.notifications is not default.notifications
    assert secondary.jsonrpc.transport.url == "https://secondary:8443/jsonrpc"
    assert secondary.jsonrpc.transport.username == "second-user"
    assert secondary.jsonrpc.transport.password == "second-password"
    assert secondary.jsonrpc.transport.timeout == 23
    assert secondary.bridge.client.base_url == "https://secondary:8766"
    assert secondary.bridge.client.token == "second-token"
    assert secondary.notifications.websocket_url == "wss://secondary:9091/jsonrpc"


def test_cached_target_id_lookup_does_not_resolve_again(monkeypatch):
    pool = TargetTransportPool(_registry(), environ={})
    first = pool.get("default")

    def _unexpected_resolution(*_args, **_kwargs):
        raise AssertionError("cached target id must not be resolved again")

    monkeypatch.setattr(transport_pool_module, "resolve_target", _unexpected_resolution)

    assert pool.get("default") is first


def test_transport_pool_accepts_an_already_resolved_target_without_resolving_again(monkeypatch):
    registry = _registry()
    target = registry.get("secondary")
    pool = TargetTransportPool(
        registry,
        environ={
            "SECONDARY_USER": "second-user",
            "SECONDARY_PASSWORD": "second-password",
            "SECONDARY_TOKEN": "second-token",
        },
    )

    def _unexpected_resolution(*_args, **_kwargs):
        raise AssertionError("already-resolved target must not be resolved again")

    monkeypatch.setattr(transport_pool_module, "resolve_target", _unexpected_resolution)

    first = pool.get_for_target(target)
    second = pool.get_for_target(target)

    assert second is first
    assert first.jsonrpc.transport.url == "https://secondary:8443/jsonrpc"
    assert first.bridge.client.base_url == "https://secondary:8766"


def test_resolved_auth_repr_does_not_expose_credential_values():
    auth = ResolvedTargetAuth(
        jsonrpc_username="sensitive-user",
        jsonrpc_password="sensitive-password",
        bridge_token="sensitive-token",
    )

    rendered = repr(auth)
    assert "sensitive-user" not in rendered
    assert "sensitive-password" not in rendered
    assert "sensitive-token" not in rendered
