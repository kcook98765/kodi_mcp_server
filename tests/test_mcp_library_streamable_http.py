"""Issue #7 library workflow through the real StreamableHTTP MCP path."""

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

        if method == "VideoLibrary.GetTVShowDetails":
            return self._ok(
                method,
                {"tvshowdetails": {"tvshowid": 7, "title": "Example Show"}},
            )
        if method == "VideoLibrary.GetMovies":
            if "missing" in query:
                return self._page(method, "movies", [], start, 0)
            if query:
                return self._page(
                    method,
                    "movies",
                    [{"movieid": 5, "title": "Alpha Movie", "playcount": 0}],
                    start,
                    1,
                )
            return self._page(method, "movies", [{"movieid": 1}], start, 10)
        if method == "VideoLibrary.GetTVShows":
            if query:
                return self._page(
                    method,
                    "tvshows",
                    [{"tvshowid": 7, "title": "Example Show", "playcount": 0}],
                    start,
                    1,
                )
            return self._page(method, "tvshows", [{"tvshowid": 1}], start, 3)
        if method == "VideoLibrary.GetSeasons":
            if "tvshowid" in params:
                return self._page(
                    method,
                    "seasons",
                    [
                        {
                            "seasonid": 8,
                            "title": "Season 1",
                            "showtitle": "Example Show",
                            "tvshowid": 7,
                            "season": 1,
                            "playcount": 0,
                        }
                    ],
                    start,
                    1,
                )
            return self._page(method, "seasons", [{"seasonid": 1}], start, 5)
        if method == "VideoLibrary.GetEpisodes":
            if "tvshowid" in params:
                return self._page(
                    method,
                    "episodes",
                    [
                        {
                            "episodeid": 9,
                            "title": "Pilot",
                            "showtitle": "Example Show",
                            "tvshowid": 7,
                            "seasonid": 8,
                            "season": 1,
                            "episode": 1,
                            "playcount": 0,
                        }
                    ],
                    start,
                    1,
                )
            if query:
                return self._page(method, "episodes", [], start, 0)
            return self._page(method, "episodes", [{"episodeid": 1}], start, 20)
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


def test_library_remote_workflow_over_streamable_http(monkeypatch):
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
                    "clientInfo": {"name": "library-acceptance", "version": "0"},
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
        library_names = {
            "kodi_library_summary",
            "kodi_library_search",
            "kodi_library_browse",
            "kodi_tv_seasons",
            "kodi_tv_episodes",
        }
        assert library_names <= tools.keys()
        for name in library_names:
            assert tools[name]["annotations"]["readOnlyHint"] is True
            Draft202012Validator.check_schema(tools[name]["outputSchema"])

        calls = [
            ("kodi_library_summary", {}),
            ("kodi_library_search", {"query": "Alpha", "media_type": "movie"}),
            ("kodi_library_search", {"query": "Example", "media_type": "tvshow"}),
            ("kodi_tv_seasons", {"tvshow_id": 7}),
            ("kodi_tv_episodes", {"tvshow_id": 7, "season": 1}),
            ("kodi_library_search", {"query": "missing", "media_type": "movie"}),
            (
                "kodi_library_search",
                {"query": "invalid", "media_type": "movie", "limit": 51},
            ),
            ("kodi_library_summary", {}),
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

        assert results[0]["structuredContent"]["data"]["counts"]["movies"] == 10
        assert results[1]["structuredContent"]["data"]["items"][0]["id"] == 5
        assert results[2]["structuredContent"]["data"]["items"][0]["id"] == 7
        assert results[3]["structuredContent"]["data"]["items"][0]["season"] == 1
        assert results[4]["structuredContent"]["data"]["items"][0]["episode"] == 1
        assert results[5]["structuredContent"]["data"]["empty"] is True
        assert results[6]["isError"] is True
        assert results[6]["structuredContent"]["error_type"] == "invalid_params"
        assert results[7]["isError"] is False
