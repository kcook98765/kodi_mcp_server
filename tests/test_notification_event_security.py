"""Credential-only sanitization for Kodi notification event payloads."""

import copy
import json

from kodi_mcp_server.targets.security import sanitize_notification_event_payload


def test_notification_sanitizer_preserves_provenance_and_redacts_credentials():
    event = {
        "method": "Player.OnAVStart",
        "sender": "xbmc",
        "host": "media-box.local",
        "hostname": "kodi-living-room",
        "port": 9090,
        "url": "https://media.example/movie.mkv?quality=4k&item=7",
        "uri": "plugin://plugin.video.example/?action=play&item=7",
        "path": "/media/Movies/Example.mkv",
        "file": "smb://media.example/Movies/Example.mkv",
        "endpoint": "library://video/movies/titles.xml/",
        "params": {
            "data": {
                "password": "pass-secret",
                "passwd": "passwd-secret",
                "token": "token-secret",
                "access_token": "access-secret",
                "api_key": "api-secret",
                "authorization": "Bearer authorization-secret",
                "auth": "Basic auth-secret",
                "secret": "field-secret",
                "credential": "credential-secret",
                "credentials": {"user": "operator", "value": "credentials-secret"},
                "player_auth": {"scheme": "Bearer", "value": "suffix-auth-secret"},
                "source_credentials": ["first-credential", "second-credential"],
            },
            "headers": {
                "Cookie": "session=cookie-secret",
                "Set-Cookie": "session=set-cookie-secret",
            },
            "items": [
                {
                    "media_url": (
                        "https://url-user:url-pass@media.example/watch"
                        "?item=7&token=query-token&quality=4k"
                    ),
                    "download_url": (
                        "https://media.example/file?api_key=query-key"
                        "&path=%2Fsafe%2Fmovie.mkv"
                    ),
                },
                {"_auth": "private-auth", "_credentials": {"token": "nested-token"}},
            ],
        },
    }
    original = copy.deepcopy(event)

    sanitized = sanitize_notification_event_payload(event)

    assert event == original
    assert sanitized is not event
    for key in ("method", "sender", "host", "hostname", "port", "url", "uri", "path", "file", "endpoint"):
        assert sanitized[key] == event[key]
    assert sanitized["params"]["data"] == {
        key: ({nested_key: "[redacted]" for nested_key in value} if isinstance(value, dict)
              else ["[redacted]" for _ in value] if isinstance(value, list)
              else "[redacted]")
        for key, value in event["params"]["data"].items()
    }
    assert sanitized["params"]["headers"] == {
        "Cookie": "[redacted]",
        "Set-Cookie": "[redacted]",
    }
    assert sanitized["params"]["items"][0] == {
        "media_url": (
            "https://media.example/watch?item=7&token=%5Bredacted%5D&quality=4k"
        ),
        "download_url": (
            "https://media.example/file?api_key=%5Bredacted%5D"
            "&path=%2Fsafe%2Fmovie.mkv"
        ),
    }
    assert sanitized["params"]["items"][1] == {
        "_auth": "[redacted]",
        "_credentials": {"token": "[redacted]"},
    }

    serialized = json.dumps(sanitized)
    for secret in (
        "pass-secret", "passwd-secret", "token-secret", "access-secret",
        "api-secret", "authorization-secret", "auth-secret", "field-secret",
        "credential-secret", "credentials-secret", "suffix-auth-secret",
        "first-credential", "second-credential", "cookie-secret",
        "set-cookie-secret", "url-user", "url-pass", "query-token",
        "query-key", "private-auth", "nested-token",
    ):
        assert secret not in serialized


def test_notification_sanitizer_does_not_replace_short_secrets_in_safe_values():
    event = {
        "method": "Other.OnNotification",
        "params": {
            "token": "mcp",
            "addon": "service.kodi_mcp",
            "message": "mcp ready on host and port",
            "host": "mcp-box.local",
            "path": "/var/lib/kodi_mcp/cache",
            "url": "plugin://service.kodi_mcp/?action=status&label=mcp",
            "nested": [{"credentials": ["mcp", {"secret": "mcp"}]}],
        },
    }
    original = copy.deepcopy(event)

    sanitized = sanitize_notification_event_payload(event)

    assert event == original
    assert sanitized["params"]["token"] == "[redacted]"
    assert sanitized["params"]["nested"] == [
        {"credentials": ["[redacted]", {"secret": "[redacted]"}]}
    ]
    assert sanitized["params"]["addon"] == "service.kodi_mcp"
    assert sanitized["params"]["message"] == "mcp ready on host and port"
    assert sanitized["params"]["host"] == "mcp-box.local"
    assert sanitized["params"]["path"] == "/var/lib/kodi_mcp/cache"
    assert sanitized["params"]["url"] == (
        "plugin://service.kodi_mcp/?action=status&label=mcp"
    )


def test_notification_sanitizer_handles_word_boundaries_queries_and_fragments():
    event = {
        "params": {
            "access_token": "snake-secret",
            "access-token": "kebab-secret",
            "accessToken": "camel-secret",
            "AccessToken": "pascal-secret",
            "apiKey": "api-secret",
            "clientSecret": "client-secret",
            "setCookie": "cookie-secret",
            "clientCredentials": {"value": "credentials-secret"},
            "proxyAuth": "proxy-secret",
            "url": (
                "https://media.example/watch?safe=keep&accessToken=query-token"
                "&apiKey=query-key&tokenizedLabel=keep#access_token=fragment-secret"
            ),
            "fragment_urls": [
                "https://media.example/a#access_token=fragment-access",
                "https://media.example/b#token=fragment-token",
                "https://media.example/c#AccessToken=fragment-camel",
                "https://media.example/d#chapter-2",
            ],
            "authenticationStatus": "ready",
            "tokenizedLabel": "ordinary",
            "secretaryName": "Alex",
            "authoritativeSource": "Kodi",
            "proxyAuthor": "maintainer",
        }
    }
    original = copy.deepcopy(event)

    sanitized = sanitize_notification_event_payload(event)
    params = sanitized["params"]

    assert event == original
    for key in (
        "access_token", "access-token", "accessToken", "AccessToken", "apiKey",
        "clientSecret", "setCookie", "proxyAuth",
    ):
        assert params[key] == "[redacted]"
    assert params["clientCredentials"] == {"value": "[redacted]"}
    assert params["url"] == (
        "https://media.example/watch?safe=keep&accessToken=%5Bredacted%5D"
        "&apiKey=%5Bredacted%5D&tokenizedLabel=keep"
    )
    assert params["fragment_urls"] == [
        "https://media.example/a",
        "https://media.example/b",
        "https://media.example/c",
        "https://media.example/d",
    ]
    assert {
        key: params[key]
        for key in (
            "authenticationStatus", "tokenizedLabel", "secretaryName",
            "authoritativeSource", "proxyAuthor",
        )
    } == {
        "authenticationStatus": "ready",
        "tokenizedLabel": "ordinary",
        "secretaryName": "Alex",
        "authoritativeSource": "Kodi",
        "proxyAuthor": "maintainer",
    }
    serialized = json.dumps(sanitized)
    for secret in (
        "snake-secret", "kebab-secret", "camel-secret", "pascal-secret",
        "api-secret", "client-secret", "cookie-secret", "credentials-secret",
        "proxy-secret", "query-token", "query-key", "fragment-secret",
        "fragment-access", "fragment-token", "fragment-camel",
    ):
        assert secret not in serialized


def test_notification_sanitizer_removes_file_url_fragments_but_preserves_paths():
    event = {
        "file_url": "file:///safe/path#access_token=file-secret",
        "chapter_url": "file:///safe/movie.mkv#chapter-2",
        "path": "/safe/path#chapter-2",
    }
    original = copy.deepcopy(event)

    sanitized = sanitize_notification_event_payload(event)

    assert event == original
    assert sanitized == {
        "file_url": "file:///safe/path",
        "chapter_url": "file:///safe/movie.mkv",
        "path": "/safe/path#chapter-2",
    }


def test_notification_sanitizer_redacts_plural_families_without_substrings():
    event = {
        "passwords": ["password-one", "password-two"],
        "tokens": "tokens-secret",
        "accessTokens": "access-tokens-secret",
        "apiKeys": "api-keys-secret",
        "clientSecrets": "client-secrets-secret",
        "servicePasswords": "suffix-passwords-secret",
        "sessionTokens": "suffix-tokens-secret",
        "sourceApiKeys": "suffix-api-keys-secret",
        "upstreamClientSecrets": "suffix-client-secrets-secret",
        "url": (
            "https://media.example/watch?safe=keep&apiKeys=query-api-keys"
            "&accessTokens=query-access-tokens&clientSecrets=query-client-secrets"
            "&tokensAvailable=3"
        ),
        "tokensAvailable": 3,
        "passwordStrengths": "strong",
        "clientSecretaries": ["Alex"],
    }
    original = copy.deepcopy(event)

    sanitized = sanitize_notification_event_payload(event)

    assert event == original
    for key in (
        "passwords", "tokens", "accessTokens", "apiKeys", "clientSecrets",
        "servicePasswords", "sessionTokens", "sourceApiKeys",
        "upstreamClientSecrets",
    ):
        expected = ["[redacted]", "[redacted]"] if key == "passwords" else "[redacted]"
        assert sanitized[key] == expected
    assert sanitized["url"] == (
        "https://media.example/watch?safe=keep&apiKeys=%5Bredacted%5D"
        "&accessTokens=%5Bredacted%5D&clientSecrets=%5Bredacted%5D"
        "&tokensAvailable=3"
    )
    assert sanitized["tokensAvailable"] == 3
    assert sanitized["passwordStrengths"] == "strong"
    assert sanitized["clientSecretaries"] == ["Alex"]
    serialized = json.dumps(sanitized)
    for secret in (
        "password-one", "password-two", "tokens-secret", "access-tokens-secret",
        "api-keys-secret", "client-secrets-secret", "suffix-passwords-secret",
        "suffix-tokens-secret", "suffix-api-keys-secret",
        "suffix-client-secrets-secret", "query-api-keys", "query-access-tokens",
        "query-client-secrets",
    ):
        assert secret not in serialized


def test_notification_sanitizer_distinguishes_urls_from_drive_and_path_strings():
    values = (
        "file:///safe/path#access_token=file-secret",
        "custom:///safe/path#access_token=custom-secret",
        "custom://host/path#token=host-secret",
        "https://example.test/path#chapter",
        "C://safe/path#chapter",
        "c://safe/path#chapter",
        "D://folder/file",
        "/safe/path#chapter",
        "relative/path#chapter",
        "service:kodi_mcp",
        "plugin.video.example",
        "foo/bar:baz",
        r"C:\safe\path",
    )
    payload = {"items": list(values), "tuple": values}
    original = copy.deepcopy(payload)
    expected = (
        "file:///safe/path",
        "custom:///safe/path",
        "custom://host/path",
        "https://example.test/path",
        "C://safe/path#chapter",
        "c://safe/path#chapter",
        "D://folder/file",
        "/safe/path#chapter",
        "relative/path#chapter",
        "service:kodi_mcp",
        "plugin.video.example",
        "foo/bar:baz",
        r"C:\safe\path",
    )

    sanitized = sanitize_notification_event_payload(payload)

    assert payload == original
    assert sanitized == {"items": list(expected), "tuple": expected}
