import pytest

from kodi_mcp_server.targets.model import (
    Target,
    TargetAuthReferences,
    TargetEndpoints,
    TargetValidationError,
)


def _target(**overrides):
    values = {
        "target_id": "kodi-21.dev",
        "name": "Kodi 21 Development",
        "endpoints": TargetEndpoints(
            jsonrpc_url="http://kodi21.example:8080/jsonrpc",
            bridge_url="https://kodi21.example:8765",
            websocket_url="wss://kodi21.example:9090/jsonrpc",
            tcp_host="kodi21.example",
            tcp_port=9090,
        ),
        "auth": TargetAuthReferences(
            jsonrpc_username="env:KODI21_USERNAME",
            jsonrpc_password="env:KODI21_PASSWORD",
            bridge_token="env:KODI21_BRIDGE_TOKEN",
        ),
        "groups": ("dev", "omega", "dev"),
        "tags": ("zeta", "kodi-21"),
        "expected_kodi_version": "21.3",
        "timeout_seconds": 12,
    }
    values.update(overrides)
    return Target(**values)


def test_target_construction_normalizes_collections_and_round_trips():
    target = _target()

    assert target.groups == ("dev", "omega")
    assert target.tags == ("kodi-21", "zeta")
    assert Target.from_dict(target.to_dict()) == target
    assert list(target.to_dict()) == [
        "id",
        "name",
        "endpoints",
        "auth",
        "groups",
        "tags",
        "expected_kodi_version",
        "timeout_seconds",
    ]


@pytest.mark.parametrize("target_id", ["", "Kodi21", "-kodi", "kodi 21", "x" * 65])
def test_target_rejects_invalid_ids(target_id):
    with pytest.raises(TargetValidationError, match="target id"):
        _target(target_id=target_id)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("jsonrpc_url", "ws://kodi:8080/jsonrpc"),
        ("bridge_url", "ftp://kodi:8765"),
        ("websocket_url", "http://kodi:9090/jsonrpc"),
        ("jsonrpc_url", "http:///jsonrpc"),
        ("jsonrpc_url", "http://user:secret@kodi:8080/jsonrpc"),
    ],
)
def test_target_rejects_endpoint_schemes_or_missing_authority(field, value):
    endpoint_values = {
        "jsonrpc_url": "http://kodi:8080/jsonrpc",
        "bridge_url": "http://kodi:8765",
        "websocket_url": "ws://kodi:9090/jsonrpc",
    }
    endpoint_values[field] = value

    with pytest.raises(TargetValidationError, match=field):
        _target(endpoints=TargetEndpoints(**endpoint_values))


def test_target_parsing_rejects_inline_auth_secrets_and_unknown_fields():
    payload = _target().to_dict()
    payload["auth"]["bridge_token"] = "inline-secret"

    with pytest.raises(TargetValidationError, match="auth reference") as exc_info:
        Target.from_dict(payload)
    assert "inline-secret" not in str(exc_info.value)

    payload = _target().to_dict()
    payload["management"] = {"provider": "lab"}
    with pytest.raises(TargetValidationError, match="unknown target fields"):
        Target.from_dict(payload)


def test_target_parsing_rejects_non_collection_groups():
    payload = _target().to_dict()
    payload["groups"] = "dev"

    with pytest.raises(TargetValidationError, match="groups"):
        Target.from_dict(payload)


def test_target_endpoint_rejects_falsy_non_string_url():
    with pytest.raises(TargetValidationError, match="jsonrpc_url"):
        TargetEndpoints.from_dict(
            {"jsonrpc_url": 0, "bridge_url": "http://kodi:8765"}
        )


def test_target_repr_does_not_expose_auth_reference_names():
    rendered = repr(_target())

    assert "KODI21_USERNAME" not in rendered
    assert "KODI21_PASSWORD" not in rendered
    assert "KODI21_BRIDGE_TOKEN" not in rendered
