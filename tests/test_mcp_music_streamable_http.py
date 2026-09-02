"""Issue #8 music workflow through the real StreamableHTTP MCP path."""

import json

from fastapi import FastAPI
from jsonschema import Draft202012Validator
from starlette.testclient import TestClient

from kodi_mcp_server.models.messages import ResponseMessage


class _Bridge:
    pass


class _JsonRpc:
    async def execute_jsonrpc(self, method, params=None):
        params = params or {}
        limits = params.get("limits", {"start": 0, "end": 1})
        start = limits.get("start", 0)
        query = ((params.get("filter") or {}).get("value") or "").casefold()
        filter_value = params.get("filter") or {}

        if method == "AudioLibrary.GetArtistDetails":
            return self._ok(
                method,
                {
                    "artistdetails": {
                        "artistid": params["artistid"],
                        "artist": "Björk",
                        "isalbumartist": True,
                    }
                },
            )
        if method == "AudioLibrary.GetAlbumDetails":
            return self._ok(
                method,
                {
                    "albumdetails": {
                        "albumid": params["albumid"],
                        "title": "Debut",
                        "artist": ["Björk"],
                        "artistid": [11],
                        "compilation": False,
                    }
                },
            )
        if method == "AudioLibrary.GetArtists":
            if query:
                items = [] if "missing" in query else [
                    {"artistid": 11, "artist": "Björk", "isalbumartist": True}
                ]
                return self._page(method, "artists", items, start, len(items))
            return self._page(method, "artists", [], start, 133)
        if method in {
            "AudioLibrary.GetAlbums",
            "AudioLibrary.GetRecentlyAddedAlbums",
        }:
            if query or "artistid" in filter_value:
                items = [{
                    "albumid": 22,
                    "title": "Debut",
                    "artist": ["Björk"],
                    "artistid": [11],
                    "compilation": False,
                }]
                return self._page(method, "albums", items, start, 1)
            return self._page(method, "albums", [], start, 254)
        if method in {
            "AudioLibrary.GetSongs",
            "AudioLibrary.GetRecentlyAddedSongs",
        }:
            if query or "albumid" in filter_value:
                items = [{
                    "songid": 33,
                    "title": "Human Behaviour",
                    "artist": ["Björk"],
                    "album": "Debut",
                    "albumid": 22,
                    "disc": 1,
                    "track": 1,
                    "duration": 252,
                }]
                return self._page(method, "songs", items, start, 1)
            return self._page(method, "songs", [], start, 3302)
        if method == "AudioLibrary.GetGenres":
            return self._page(
                method,
                "genres",
                [{"genreid": 44, "title": "Alternative"}],
                start,
                1,
            )
        raise AssertionError(f"unexpected method: {method}")

    @staticmethod
    def _ok(method, result):
        return ResponseMessage(request_id=method, result=result, error=None)

    @classmethod
    def _page(cls, method, key, items, start, total):
        return cls._ok(
            method,
            {
                key: items,
                "limits": {
                    "start": start,
                    "end": start + len(items),
                    "total": total,
                },
            },
        )


def _sse(response):
    assert response.status_code == 200
    data = [
        line.removeprefix("data:").strip()
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert data
    return json.loads(data[-1])


def test_music_remote_workflow_over_streamable_http(monkeypatch):
    import kodi_mcp_server.remote_mcp_app as remote_mcp_app

    monkeypatch.setattr(
        remote_mcp_app,
        "build_runtime",
        lambda: {"bridge": _Bridge(), "jsonrpc": _JsonRpc(), "notifications": None},
    )
    remote_app, remote_lifespan = remote_mcp_app.create_remote_mcp()

    async def lifespan(_: FastAPI):
        async with remote_lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", remote_app)

    with TestClient(app) as client:
        init = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "music-acceptance", "version": "0"},
                },
            },
        )
        headers = {
            "mcp-session-id": init.headers["mcp-session-id"],
            "mcp-protocol-version": "2025-11-25",
        }
        tools_body = _sse(
            client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers=headers,
            )
        )
        tools = {tool["name"]: tool for tool in tools_body["result"]["tools"]}
        music_names = {
            "kodi_music_summary",
            "kodi_music_search",
            "kodi_music_browse",
            "kodi_artist_albums",
            "kodi_album_songs",
        }
        assert music_names <= tools.keys()
        for name in music_names:
            assert tools[name]["annotations"]["readOnlyHint"] is True
            Draft202012Validator.check_schema(tools[name]["outputSchema"])

        calls = [
            ("kodi_music_summary", {}),
            ("kodi_music_search", {"query": "Björk", "media_type": "artist"}),
            ("kodi_music_search", {"query": "Debut", "media_type": "album"}),
            ("kodi_music_search", {"query": "Human", "media_type": "song"}),
            ("kodi_artist_albums", {"artist_id": 11}),
            ("kodi_album_songs", {"album_id": 22}),
            ("kodi_music_browse", {"category": "genres"}),
            ("kodi_music_search", {"query": "missing", "media_type": "artist"}),
            ("kodi_music_search", {"query": "invalid", "media_type": "song", "limit": 51}),
            ("kodi_music_summary", {}),
        ]
        results = []
        for request_id, (name, arguments) in enumerate(calls, start=3):
            body = _sse(
                client.post(
                    "/mcp/",
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    },
                    headers=headers,
                )
            )
            result = body["result"]
            Draft202012Validator(tools[name]["outputSchema"]).validate(
                result["structuredContent"]
            )
            results.append(result)

        assert results[0]["structuredContent"]["data"]["counts"] == {
            "artists": 133,
            "albums": 254,
            "songs": 3302,
        }
        assert results[1]["structuredContent"]["data"]["items"][0]["id"] == 11
        assert results[2]["structuredContent"]["data"]["items"][0]["id"] == 22
        assert results[3]["structuredContent"]["data"]["items"][0]["id"] == 33
        assert results[4]["structuredContent"]["data"]["items"][0]["id"] == 22
        assert results[5]["structuredContent"]["data"]["items"][0]["id"] == 33
        assert results[6]["structuredContent"]["data"]["items"][0]["id"] == 44
        assert results[7]["structuredContent"]["data"]["empty"] is True
        assert results[8]["isError"] is True
        assert results[8]["structuredContent"]["error_type"] == "invalid_params"
        assert results[9]["isError"] is False
