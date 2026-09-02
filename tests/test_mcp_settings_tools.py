"""Typed, policy-bounded Kodi settings MCP coverage."""

import json

import pytest
from mcp.types import CallToolRequestParams

from kodi_mcp_server.models.messages import ErrorType, ResponseMessage


class _Bridge:
    pass


class _NoJsonRpc:
    async def execute_jsonrpc(self, method, params=None):
        raise AssertionError(f"listing the static safety policy called {method}")


class _SettingsJsonRpc:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def execute_jsonrpc(self, method, params=None):
        self.calls.append((method, params or {}))
        response = self.responses[method]
        if isinstance(response, list):
            response = response.pop(0)
        return response


def _ok(method, result):
    return ResponseMessage(request_id=method, result=result, error=None)


def _metadata(setting_id, value, *, value_type="boolean", default=None, **extra):
    if default is None:
        default = False if value_type == "boolean" else value
    return {
        "id": setting_id,
        "label": "Untrusted Kodi label",
        "help": "Untrusted Kodi help",
        "type": value_type,
        "level": "standard",
        "enabled": True,
        "parent": "",
        "default": default,
        "value": value,
        **extra,
    }


async def _call(jsonrpc, name, arguments=None):
    from kodi_mcp_mcp.server_core import build_mcp_server

    server, _ = build_mcp_server(
        {"bridge": _Bridge(), "jsonrpc": jsonrpc, "notifications": None}
    )
    return await server.get_request_handler("tools/call").handler(
        None, CallToolRequestParams(name=name, arguments=arguments or {})
    )


def _envelope(result):
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_settings_list_discovers_only_bounded_policy_entries():
    result = await _call(
        _NoJsonRpc(),
        "kodi_settings_list",
        {"category": "filelists", "writable": True, "limit": 2},
    )

    assert result.is_error is False
    data = _envelope(result)["data"]
    assert [item["id"] for item in data["items"]] == [
        "filelists.showextensions",
    ]
    assert data["items"][0]["readable"] is True
    assert data["items"][0]["writable"] is True
    assert data["pagination"] == {
        "start": 0,
        "end": 1,
        "total": 1,
        "limit": 2,
        "has_more": False,
    }


@pytest.mark.asyncio
async def test_setting_get_returns_supported_writable_value_and_metadata():
    metadata = {
        "id": "filelists.showextensions",
        "label": "Show file extensions",
        "help": "Kodi metadata is validated but product text is authoritative.",
        "type": "boolean",
        "level": "standard",
        "enabled": True,
        "parent": "",
        "default": True,
        "value": False,
    }
    jsonrpc = _SettingsJsonRpc(
        {"Settings.GetSettings": _ok("Settings.GetSettings", {"settings": [metadata]})}
    )

    result = await _call(
        jsonrpc,
        "kodi_setting_get",
        {"setting_id": "filelists.showextensions"},
    )

    assert result.is_error is False
    setting = _envelope(result)["data"]["setting"]
    assert setting["id"] == "filelists.showextensions"
    assert setting["type"] == "boolean"
    assert setting["value"] is False
    assert setting["default"] is True
    assert setting["readable"] is True
    assert setting["writable"] is True
    assert jsonrpc.calls == [
        (
            "Settings.GetSettings",
            {
                "level": "expert",
                "filter": {"section": "media", "category": "filelists"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_setting_get_returns_explicit_read_only_policy_reason():
    setting_id = "filelists.showhidden"
    jsonrpc = _SettingsJsonRpc(
        {
            "Settings.GetSettings": _ok(
                "Settings.GetSettings",
                {"settings": [_metadata(setting_id, False)]},
            )
        }
    )

    result = await _call(jsonrpc, "kodi_setting_get", {"setting_id": setting_id})

    setting = _envelope(result)["data"]["setting"]
    assert setting["readable"] is True
    assert setting["writable"] is False
    assert "local user control" in setting["mutation_unavailable_reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting_id", "error_text"),
    [
        ("unknown.arbitrary", "outside the explicit"),
        ("services.webserverpassword", "sensitive"),
        ("network.proxytoken", "sensitive"),
        ("sources.videospath", "sensitive"),
    ],
)
async def test_setting_get_rejects_unsupported_or_sensitive_ids_without_downstream_call(
    setting_id, error_text
):
    jsonrpc = _NoJsonRpc()

    result = await _call(jsonrpc, "kodi_setting_get", {"setting_id": setting_id})

    rendered = result.content[0].text
    assert result.is_error is True
    assert error_text in _envelope(result)["error"]
    assert "password" not in rendered
    assert "proxytoken" not in rendered
    assert "videospath" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_result",
    [None, {}, {"settings": "not-a-list"}, {"settings": ["not-an-object"]}],
)
async def test_setting_get_rejects_malformed_kodi_metadata(bad_result):
    jsonrpc = _SettingsJsonRpc(
        {"Settings.GetSettings": _ok("Settings.GetSettings", bad_result)}
    )

    result = await _call(
        jsonrpc,
        "kodi_setting_get",
        {"setting_id": "filelists.showextensions"},
    )

    assert result.is_error is True
    assert _envelope(result)["error_type"] == "invalid_response"


@pytest.mark.asyncio
async def test_setting_get_reports_supported_setting_missing_on_target():
    jsonrpc = _SettingsJsonRpc(
        {"Settings.GetSettings": _ok("Settings.GetSettings", {"settings": []})}
    )

    result = await _call(
        jsonrpc,
        "kodi_setting_get",
        {"setting_id": "subtitles.marginvertical"},
    )

    assert result.is_error is True
    assert _envelope(result)["error_type"] == "not_found"


@pytest.mark.asyncio
async def test_setting_get_preserves_downstream_error_and_next_read_recovers():
    setting_id = "filelists.showextensions"
    jsonrpc = _SettingsJsonRpc(
        {
            "Settings.GetSettings": [
                ResponseMessage(
                    request_id="failed",
                    result=None,
                    error="connection error: refused",
                    error_type=ErrorType.NETWORK_ERROR,
                ),
                _ok(
                    "recovered",
                    {"settings": [_metadata(setting_id, True, default=True)]},
                ),
            ]
        }
    )

    failed = await _call(jsonrpc, "kodi_setting_get", {"setting_id": setting_id})
    recovered = await _call(jsonrpc, "kodi_setting_get", {"setting_id": setting_id})

    assert failed.is_error is True
    assert _envelope(failed)["error_type"] == "network_error"
    assert recovered.is_error is False
    assert _envelope(recovered)["data"]["setting"]["value"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sensitive_value",
    [
        "https://user:visible-password@example.invalid/path",
        "smb://user:visible-password@nas/private",
        "/home/user/private/settings.xml",
        "safe\u0000control",
    ],
)
async def test_setting_get_never_returns_credential_url_path_or_control_value(
    sensitive_value
):
    setting_id = "locale.country"
    jsonrpc = _SettingsJsonRpc(
        {
            "Settings.GetSettings": _ok(
                "Settings.GetSettings",
                {
                    "settings": [
                        _metadata(
                            setting_id,
                            sensitive_value,
                            value_type="string",
                            default="USA (12h)",
                            options=[{"label": "unsafe", "value": sensitive_value}],
                        )
                    ]
                },
            )
        }
    )

    result = await _call(jsonrpc, "kodi_setting_get", {"setting_id": setting_id})

    rendered = result.content[0].text
    assert result.is_error is True
    assert _envelope(result)["error_type"] == "invalid_response"
    assert sensitive_value not in rendered


@pytest.mark.asyncio
async def test_setting_set_boolean_performs_one_mutation_and_verifies_postcondition():
    setting_id = "filelists.showextensions"
    jsonrpc = _SettingsJsonRpc(
        {
            "Settings.GetSettings": [
                _ok(
                    "before",
                    {"settings": [_metadata(setting_id, False, default=True)]},
                ),
                _ok(
                    "after",
                    {"settings": [_metadata(setting_id, True, default=True)]},
                ),
            ],
            "Settings.SetSettingValue": _ok("set", True),
        }
    )

    result = await _call(
        jsonrpc,
        "kodi_setting_set",
        {"setting_id": setting_id, "value": True},
    )

    assert result.is_error is False
    data = _envelope(result)["data"]
    assert data["setting_id"] == setting_id
    assert data["before"] is False
    assert data["requested"] is True
    assert data["after"] is True
    assert data["changed"] is True
    assert data["verified"] is True
    assert jsonrpc.calls == [
        (
            "Settings.GetSettings",
            {
                "level": "expert",
                "filter": {"section": "media", "category": "filelists"},
            },
        ),
        (
            "Settings.SetSettingValue",
            {"setting": setting_id, "value": True},
        ),
        (
            "Settings.GetSettings",
            {
                "level": "expert",
                "filter": {"section": "media", "category": "filelists"},
            },
        ),
    ]


@pytest.mark.asyncio
async def test_setting_set_boolean_accepts_false():
    setting_id = "filelists.showextensions"
    jsonrpc = _SettingsJsonRpc(
        {
            "Settings.GetSettings": [
                _ok("before", {"settings": [_metadata(setting_id, True, default=True)]}),
                _ok("after", {"settings": [_metadata(setting_id, False, default=True)]}),
            ],
            "Settings.SetSettingValue": _ok("set", True),
        }
    )

    result = await _call(
        jsonrpc, "kodi_setting_set", {"setting_id": setting_id, "value": False}
    )

    assert result.is_error is False
    assert _envelope(result)["data"]["after"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_value", ["false", 0, 1, None])
async def test_setting_set_boolean_rejects_wrong_type_before_downstream(wrong_value):
    result = await _call(
        _NoJsonRpc(),
        "kodi_setting_set",
        {"setting_id": "filelists.showextensions", "value": wrong_value},
    )

    assert result.is_error is True
    assert _envelope(result)["error_type"] in {"invalid_params", "invalid_operation"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "valid"),
    [(-30, True), (30, True), (-31, False), (31, False), (3, False), (3.7, False)],
)
async def test_setting_set_integer_enforces_type_range_and_step(value, valid):
    setting_id = "lookandfeel.skinzoom"
    metadata = _metadata(
        setting_id,
        0,
        value_type="integer",
        default=0,
        minimum=-30,
        maximum=30,
        step=2,
    )
    responses = {"Settings.GetSettings": _ok("before", {"settings": [metadata]})}
    if valid:
        responses["Settings.GetSettings"] = [
            responses["Settings.GetSettings"],
            _ok("after", {"settings": [{**metadata, "value": value}]}),
        ]
        responses["Settings.SetSettingValue"] = _ok("set", True)
    jsonrpc = _SettingsJsonRpc(responses)

    result = await _call(
        jsonrpc, "kodi_setting_set", {"setting_id": setting_id, "value": value}
    )

    assert result.is_error is (not valid)
    assert any(method == "Settings.SetSettingValue" for method, _ in jsonrpc.calls) is valid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "valid"),
    [(0.0, True), (4.95, True), (50.0, True), (-0.05, False), (50.05, False), (4.96, False)],
)
async def test_setting_set_number_enforces_finite_range_and_step(value, valid):
    setting_id = "subtitles.marginvertical"
    metadata = _metadata(
        setting_id,
        5.0,
        value_type="number",
        default=4.95,
        minimum=0.0,
        maximum=50.0,
        step=0.05,
    )
    responses = {"Settings.GetSettings": _ok("before", {"settings": [metadata]})}
    if valid:
        responses["Settings.GetSettings"] = [
            responses["Settings.GetSettings"],
            _ok("after", {"settings": [{**metadata, "value": value}]}),
        ]
        responses["Settings.SetSettingValue"] = _ok("set", True)
    jsonrpc = _SettingsJsonRpc(responses)

    result = await _call(
        jsonrpc, "kodi_setting_set", {"setting_id": setting_id, "value": value}
    )

    assert result.is_error is (not valid)
    assert any(method == "Settings.SetSettingValue" for method, _ in jsonrpc.calls) is valid


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "4.95"])
async def test_setting_set_number_rejects_nonfinite_or_wrong_type(value):
    result = await _call(
        _NoJsonRpc(),
        "kodi_setting_set",
        {"setting_id": "subtitles.marginvertical", "value": value},
    )

    assert result.is_error is True
    assert _envelope(result)["error_type"] in {"invalid_params", "invalid_operation"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "valid"), [(3, True), (4, False), ("3", False)])
async def test_setting_set_enum_accepts_only_advertised_policy_values(value, valid):
    setting_id = "subtitles.style"
    options = [{"label": str(item), "value": item} for item in range(4)]
    metadata = _metadata(
        setting_id,
        0,
        value_type="integer",
        default=0,
        options=options,
    )
    responses = {"Settings.GetSettings": _ok("before", {"settings": [metadata]})}
    if valid:
        responses["Settings.GetSettings"] = [
            responses["Settings.GetSettings"],
            _ok("after", {"settings": [{**metadata, "value": value}]}),
        ]
        responses["Settings.SetSettingValue"] = _ok("set", True)
    jsonrpc = _SettingsJsonRpc(responses)

    result = await _call(
        jsonrpc, "kodi_setting_set", {"setting_id": setting_id, "value": value}
    )

    assert result.is_error is (not valid)
    assert any(method == "Settings.SetSettingValue" for method, _ in jsonrpc.calls) is valid


@pytest.mark.asyncio
async def test_setting_set_bounded_string_enum_verifies_success():
    setting_id = "locale.country"
    allowed = [
        "Australia (12h)", "Australia (24h)", "Canada", "Central Europe",
        "India (12h)", "India (24h)", "UK (12h)", "UK (24h)",
        "USA (12h)", "USA (24h)",
    ]
    options = [{"label": item, "value": item} for item in allowed]
    before = _metadata(
        setting_id,
        "USA (12h)",
        value_type="string",
        default="USA (12h)",
        options=options,
    )
    jsonrpc = _SettingsJsonRpc(
        {
            "Settings.GetSettings": [
                _ok("before", {"settings": [before]}),
                _ok("after", {"settings": [{**before, "value": "Canada"}]}),
            ],
            "Settings.SetSettingValue": _ok("set", True),
        }
    )

    result = await _call(
        jsonrpc,
        "kodi_setting_set",
        {"setting_id": setting_id, "value": "Canada"},
    )

    assert result.is_error is False
    assert _envelope(result)["data"]["after"] == "Canada"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    ["x" * 65, "Not advertised", "USA (12h)\u0000", "https://user:pass@example.invalid"],
)
async def test_setting_set_string_rejects_unbounded_unadvertised_or_sensitive_value(value):
    result = await _call(
        _NoJsonRpc(),
        "kodi_setting_set",
        {"setting_id": "locale.country", "value": value},
    )

    assert result.is_error is True
    assert value not in result.content[0].text


@pytest.mark.asyncio
async def test_setting_set_schema_rejection_does_not_echo_oversized_value():
    value = "visible-secret-" * 20

    result = await _call(
        _NoJsonRpc(),
        "kodi_setting_set",
        {"setting_id": "locale.country", "value": value},
    )

    assert result.is_error is True
    assert value not in result.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setting_id",
    ["filelists.showhidden", "services.webserver", "unknown.arbitrary"],
)
async def test_setting_set_policy_rejects_read_only_unsafe_and_unknown_before_mutation(
    setting_id
):
    result = await _call(
        _NoJsonRpc(),
        "kodi_setting_set",
        {"setting_id": setting_id, "value": True},
    )

    assert result.is_error is True


@pytest.mark.asyncio
async def test_setting_set_downstream_failure_does_not_poison_subsequent_read():
    setting_id = "filelists.showextensions"
    metadata = _metadata(setting_id, False, default=True)
    jsonrpc = _SettingsJsonRpc(
        {
            "Settings.GetSettings": [
                _ok("before", {"settings": [metadata]}),
                _ok("recovered", {"settings": [metadata]}),
            ],
            "Settings.SetSettingValue": ResponseMessage(
                request_id="failed-set",
                result=None,
                error="request timeout",
                error_type=ErrorType.TIMEOUT,
            ),
        }
    )

    failed = await _call(
        jsonrpc, "kodi_setting_set", {"setting_id": setting_id, "value": True}
    )
    recovered = await _call(jsonrpc, "kodi_setting_get", {"setting_id": setting_id})

    assert failed.is_error is True
    assert _envelope(failed)["error_type"] == "timeout"
    assert recovered.is_error is False


@pytest.mark.asyncio
async def test_setting_set_fails_when_readback_postcondition_mismatches():
    setting_id = "filelists.showextensions"
    metadata = _metadata(setting_id, False, default=True)
    jsonrpc = _SettingsJsonRpc(
        {
            "Settings.GetSettings": [
                _ok("before", {"settings": [metadata]}),
                _ok("after", {"settings": [metadata]}),
            ],
            "Settings.SetSettingValue": _ok("set", True),
        }
    )

    result = await _call(
        jsonrpc, "kodi_setting_set", {"setting_id": setting_id, "value": True}
    )

    envelope = _envelope(result)
    assert result.is_error is True
    assert envelope["error_type"] == "invalid_response"
    assert envelope["data"] == {
        "setting_id": setting_id,
        "before": False,
        "requested": True,
        "after": False,
        "changed": False,
        "verified": False,
    }


@pytest.mark.asyncio
async def test_setting_set_noop_is_verified_without_mutation():
    setting_id = "filelists.showextensions"
    jsonrpc = _SettingsJsonRpc(
        {
            "Settings.GetSettings": _ok(
                "same", {"settings": [_metadata(setting_id, True, default=True)]}
            )
        }
    )

    result = await _call(
        jsonrpc, "kodi_setting_set", {"setting_id": setting_id, "value": True}
    )

    data = _envelope(result)["data"]
    assert result.is_error is False
    assert data["changed"] is False
    assert data["verified"] is True
    assert [method for method, _ in jsonrpc.calls] == ["Settings.GetSettings"]
