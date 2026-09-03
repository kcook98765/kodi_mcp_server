"""Security contract for remote HTTP deployment."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kodi_mcp_server.main as main_module
import kodi_mcp_server.remote_mcp_app as security
from kodi_mcp_server.config import ConfigError
from kodi_mcp_server.http_app import create_base_app


SYNTHETIC_EXPECTED_KEY = "test-only-expected-credential-7b3e"
SYNTHETIC_WRONG_KEY = "test-only-supplied-credential-91af"


def _security_callable(name):
    value = getattr(security, name, None)
    assert callable(value), f"remote security function {name} is not implemented"
    return value


def _scope(*headers: tuple[bytes, bytes], query_string: bytes = b""):
    return {
        "type": "http",
        "path": "/mcp/",
        "headers": list(headers),
        "query_string": query_string,
    }


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.42.1.3", "::1", "[::1]", "localhost", "LOCALHOST."],
)
def test_loopback_bind_classification(host):
    assert _security_callable("is_loopback_bind_host")(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "::",
        "192.168.1.20",
        "10.0.0.20",
        "172.16.0.20",
        "8.8.8.8",
        "fd00::20",
        "fe80::20",
        "mcp.internal",
        "",
    ],
)
def test_remote_capable_bind_classification_is_conservative(host):
    assert _security_callable("is_loopback_bind_host")(host) is False


def test_runtime_bind_host_honors_direct_uvicorn_host_argument(monkeypatch):
    monkeypatch.delenv("MCP_BIND_HOST", raising=False)
    resolve = _security_callable("runtime_bind_host")
    assert resolve(["/venv/bin/uvicorn", "kodi_mcp_server.main:app", "--host", "0.0.0.0"]) == "0.0.0.0"
    assert resolve(["/venv/bin/uvicorn", "kodi_mcp_server.main:app", "--host=::"]) == "::"


def test_runtime_bind_host_rejects_conflicting_uvicorn_and_environment(monkeypatch):
    monkeypatch.setenv("MCP_BIND_HOST", "127.0.0.1")
    with pytest.raises(ConfigError, match="disagrees"):
        _security_callable("runtime_bind_host")(
            ["/venv/bin/uvicorn", "kodi_mcp_server.main:app", "--host", "0.0.0.0"]
        )


def test_runtime_bind_host_defaults_loopback_for_uvicorn_without_host(monkeypatch):
    monkeypatch.delenv("MCP_BIND_HOST", raising=False)
    assert _security_callable("runtime_bind_host")(
        ["/venv/bin/uvicorn", "kodi_mcp_server.main:app"]
    ) == "127.0.0.1"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
@pytest.mark.parametrize("api_key", [None, SYNTHETIC_EXPECTED_KEY])
def test_loopback_policy_allows_optional_auth(host, api_key):
    _security_callable("validate_remote_deployment")(
        bind_host=host,
        api_key=api_key,
        allow_insecure_remote=None,
    )


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.50.10", "fd00::10", "mcp.internal"])
def test_remote_capable_bind_without_key_fails_closed(host):
    with pytest.raises(ConfigError, match="MCP_API_KEY"):
        _security_callable("validate_remote_deployment")(
            bind_host=host,
            api_key=None,
            allow_insecure_remote=None,
        )


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.50.10", "fd00::10", "mcp.internal"])
def test_remote_capable_bind_with_key_is_allowed(host):
    _security_callable("validate_remote_deployment")(
        bind_host=host,
        api_key=SYNTHETIC_EXPECTED_KEY,
        allow_insecure_remote=None,
    )


@pytest.mark.parametrize("value", [None, "", "false", "FALSE", "0"])
def test_insecure_remote_override_is_disabled_by_default_and_false_values(value):
    with pytest.raises(ConfigError, match="MCP_API_KEY"):
        _security_callable("validate_remote_deployment")(
            bind_host="0.0.0.0",
            api_key=None,
            allow_insecure_remote=value,
        )


def test_explicit_insecure_remote_override_allows_bind_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="kodi_mcp_server.remote_security"):
        _security_callable("validate_remote_deployment")(
            bind_host="0.0.0.0",
            api_key=None,
            allow_insecure_remote="TrUe",
        )

    rendered = caplog.text
    assert "insecure remote" in rendered.lower()
    assert "trusted network" in rendered.lower()
    assert SYNTHETIC_EXPECTED_KEY not in rendered
    assert SYNTHETIC_WRONG_KEY not in rendered


@pytest.mark.parametrize("value", ["yes", "on", "1", "truthy", " true "])
def test_malformed_insecure_remote_override_is_configuration_error(value):
    with pytest.raises(ConfigError, match="MCP_ALLOW_INSECURE_REMOTE"):
        _security_callable("validate_remote_deployment")(
            bind_host="0.0.0.0",
            api_key=None,
            allow_insecure_remote=value,
        )


def test_oversized_configured_key_fails_without_secret_reflection():
    oversized = SYNTHETIC_EXPECTED_KEY + ("x" * 5000)
    with pytest.raises(ConfigError) as caught:
        _security_callable("validate_remote_deployment")(
            bind_host="0.0.0.0",
            api_key=oversized,
            allow_insecure_remote=None,
        )
    assert oversized not in str(caught.value)
    assert SYNTHETIC_EXPECTED_KEY not in str(caught.value)


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ([], 401),
        ([(b"x-mcp-api-key", SYNTHETIC_WRONG_KEY.encode())], 401),
        ([(b"x-mcp-api-key", SYNTHETIC_EXPECTED_KEY.encode())], None),
        ([(b"x-mcp-api-key", b"\xffinvalid-utf8")], 401),
        ([(b"x-mcp-api-key", b"x" * 5000)], 401),
        (
            [
                (b"x-mcp-api-key", SYNTHETIC_EXPECTED_KEY.encode()),
                (b"x-mcp-api-key", SYNTHETIC_EXPECTED_KEY.encode()),
            ],
            401,
        ),
        ([(b"authorization", f"Bearer {SYNTHETIC_EXPECTED_KEY}".encode())], 401),
    ],
)
def test_api_key_request_contract(monkeypatch, headers, expected_status):
    monkeypatch.setenv("MCP_API_KEY", SYNTHETIC_EXPECTED_KEY)
    response = security._enforce_api_key(_scope(*headers))

    if expected_status is None:
        assert response is None
    else:
        assert response is not None
        assert response.status_code == expected_status
        body = response.body.decode()
        assert body == "Unauthorized"
        assert SYNTHETIC_EXPECTED_KEY not in body
        assert SYNTHETIC_WRONG_KEY not in body


def test_unicode_api_key_is_compared_as_utf8_bytes(monkeypatch):
    unicode_key = "test-only-κλειδί"
    monkeypatch.setenv("MCP_API_KEY", unicode_key)
    assert security._enforce_api_key(
        _scope((b"x-mcp-api-key", unicode_key.encode("utf-8")))
    ) is None


def test_query_parameter_never_authenticates(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", SYNTHETIC_EXPECTED_KEY)
    response = security._enforce_api_key(
        _scope(query_string=f"mcp_api_key={SYNTHETIC_EXPECTED_KEY}".encode())
    )
    assert response is not None
    assert response.status_code == 401
    assert SYNTHETIC_EXPECTED_KEY not in response.body.decode()


def test_api_key_comparison_uses_constant_time_primitive(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", SYNTHETIC_EXPECTED_KEY)
    calls = []

    def compare_digest(left, right):
        calls.append((left, right))
        return left == right

    primitive = getattr(security, "secrets", None)
    assert primitive is not None, "constant-time comparison primitive is not configured"
    monkeypatch.setattr(primitive, "compare_digest", compare_digest)
    assert security._enforce_api_key(
        _scope((b"x-mcp-api-key", SYNTHETIC_EXPECTED_KEY.encode()))
    ) is None
    assert len(calls) == 1


def test_http_security_gate_covers_mcp_tools_and_sensitive_status(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", SYNTHETIC_EXPECTED_KEY)
    app = create_base_app()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/status")
    async def status():
        return {"sensitive": True}

    @app.post("/tools/mutation-probe")
    async def mutation_probe():
        return {"mutated": False}

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    for method, path in [
        (client.get, "/status"),
        (client.post, "/tools/mutation-probe"),
        (client.post, "/mcp/"),
    ]:
        missing = method(path)
        wrong = method(path, headers={"x-mcp-api-key": SYNTHETIC_WRONG_KEY})
        assert missing.status_code == 401
        assert wrong.status_code == 401
        assert SYNTHETIC_WRONG_KEY not in wrong.text

    accepted = client.post(
        "/tools/mutation-probe",
        headers={"x-mcp-api-key": SYNTHETIC_EXPECTED_KEY},
    )
    assert accepted.status_code == 200


def test_supported_entrypoint_defaults_to_loopback_and_8010(monkeypatch):
    monkeypatch.delenv("MCP_BIND_HOST", raising=False)
    monkeypatch.delenv("MCP_PORT", raising=False)
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.delenv("MCP_ALLOW_INSECURE_REMOTE", raising=False)
    monkeypatch.setattr(main_module, "validate_config", lambda: None)
    monkeypatch.setattr(
        main_module.app.state, "remote_deployment_validated", False, raising=False
    )
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append((app, kwargs)))

    main_module.main()

    assert calls == [(main_module.app, {"host": "127.0.0.1", "port": 8010})]


def test_supported_entrypoint_rejects_remote_bind_before_uvicorn(monkeypatch):
    monkeypatch.setenv("MCP_BIND_HOST", "0.0.0.0")
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.delenv("MCP_ALLOW_INSECURE_REMOTE", raising=False)
    monkeypatch.setattr(main_module, "validate_config", lambda: None)
    monkeypatch.setattr(
        main_module.app.state, "remote_deployment_validated", False, raising=False
    )
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append((app, kwargs)))

    with pytest.raises(ConfigError, match="MCP_API_KEY"):
        main_module.main()
    assert calls == []


def test_supported_entrypoint_rejects_invalid_port_before_uvicorn(monkeypatch):
    monkeypatch.setenv("MCP_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "70000")
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setattr(main_module, "validate_config", lambda: None)
    monkeypatch.setattr(
        main_module.app.state, "remote_deployment_validated", False, raising=False
    )
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append((app, kwargs)))

    with pytest.raises(ConfigError, match="MCP_PORT"):
        main_module.main()
    assert calls == []


def test_supported_entrypoint_allows_authenticated_remote_bind(monkeypatch):
    monkeypatch.setenv("MCP_BIND_HOST", "192.168.50.10")
    monkeypatch.setenv("MCP_PORT", "9010")
    monkeypatch.setenv("MCP_API_KEY", SYNTHETIC_EXPECTED_KEY)
    monkeypatch.delenv("MCP_ALLOW_INSECURE_REMOTE", raising=False)
    monkeypatch.setattr(main_module, "validate_config", lambda: None)
    monkeypatch.setattr(
        main_module.app.state, "remote_deployment_validated", False, raising=False
    )
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append((app, kwargs)))

    main_module.main()

    assert calls == [(main_module.app, {"host": "192.168.50.10", "port": 9010})]
