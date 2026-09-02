"""Bounded, normalized video-library discovery operations."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from kodi_mcp_server.models.messages import ErrorType, ResponseMessage


_COUNT_REQUESTS = (
    ("movies", "VideoLibrary.GetMovies"),
    ("tvshows", "VideoLibrary.GetTVShows"),
    ("seasons", "VideoLibrary.GetSeasons"),
    ("episodes", "VideoLibrary.GetEpisodes"),
)

_SEARCH_SPECS = {
    "movie": (
        "VideoLibrary.GetMovies",
        "movies",
        "movieid",
        ["title", "year", "playcount", "runtime", "dateadded", "genre", "art"],
    ),
    "tvshow": (
        "VideoLibrary.GetTVShows",
        "tvshows",
        "tvshowid",
        [
            "title",
            "year",
            "playcount",
            "runtime",
            "dateadded",
            "genre",
            "art",
            "episode",
            "season",
            "watchedepisodes",
        ],
    ),
    "episode": (
        "VideoLibrary.GetEpisodes",
        "episodes",
        "episodeid",
        [
            "title",
            "showtitle",
            "season",
            "episode",
            "playcount",
            "runtime",
            "dateadded",
            "art",
            "tvshowid",
            "seasonid",
        ],
    ),
}

_ART_KEYS = ("poster", "fanart", "thumb", "tvshow.poster", "season.poster")
_SENSITIVE_ART_MARKERS = (
    "smb:",
    "nfs:",
    "file:",
    "special:",
    "x-plex-token=",
    "access_token=",
    "api_key=",
    "apikey=",
    "password=",
)

_SEASON_PROPERTIES = [
    "title",
    "season",
    "showtitle",
    "playcount",
    "episode",
    "watchedepisodes",
    "art",
    "tvshowid",
]
_EPISODE_PROPERTIES = _SEARCH_SPECS["episode"][3]
_BROWSE_SPECS = {
    "recent_movies": (
        "VideoLibrary.GetRecentlyAddedMovies",
        "movies",
        "movieid",
        "movie",
        _SEARCH_SPECS["movie"][3],
        {},
        {"method": "dateadded", "order": "descending"},
    ),
    "recent_episodes": (
        "VideoLibrary.GetRecentlyAddedEpisodes",
        "episodes",
        "episodeid",
        "episode",
        _EPISODE_PROPERTIES,
        {},
        {"method": "dateadded", "order": "descending"},
    ),
    "movie_genres": (
        "VideoLibrary.GetGenres",
        "genres",
        "genreid",
        "genre",
        ["title", "thumbnail"],
        {"type": "movie"},
        {"method": "title", "order": "ascending"},
    ),
    "tvshow_genres": (
        "VideoLibrary.GetGenres",
        "genres",
        "genreid",
        "genre",
        ["title", "thumbnail"],
        {"type": "tvshow"},
        {"method": "title", "order": "ascending"},
    ),
    "movie_sets": (
        "VideoLibrary.GetMovieSets",
        "sets",
        "setid",
        "movie_set",
        ["title", "art", "thumbnail"],
        {},
        {"method": "title", "order": "ascending"},
    ),
    "movie_tags": (
        "VideoLibrary.GetTags",
        "tags",
        "tagid",
        "tag",
        ["title"],
        {"type": "movie"},
        {"method": "title", "order": "ascending"},
    ),
    "tvshow_tags": (
        "VideoLibrary.GetTags",
        "tags",
        "tagid",
        "tag",
        ["title"],
        {"type": "tvshow"},
        {"method": "title", "order": "ascending"},
    ),
}


def _safe_artwork(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    artwork: dict[str, str] = {}
    for key in _ART_KEYS:
        reference = value.get(key)
        if not isinstance(reference, str) or not reference or len(reference) > 2048:
            continue
        decoded = unquote(reference).lower()
        if any(marker in decoded for marker in _SENSITIVE_ART_MARKERS):
            continue
        if re.search(r"://[^/\s]*@", decoded):
            continue
        artwork[key] = reference
        if len(artwork) == 3:
            break
    return artwork


def _nullable_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _normalize_item(
    media_type: str, item: Any, id_key: str
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    item_id = item.get(id_key)
    title = item.get("title") or item.get("label")
    if (
        not isinstance(item_id, int)
        or isinstance(item_id, bool)
        or not isinstance(title, str)
    ):
        return None
    playcount = _nullable_int(item.get("playcount")) or 0
    normalized: dict[str, Any] = {
        "id": item_id,
        "media_type": media_type,
        "title": title,
        "watched": playcount > 0,
        "playcount": playcount,
        "runtime_seconds": _nullable_int(item.get("runtime")),
        "date_added": (
            item.get("dateadded")
            if isinstance(item.get("dateadded"), str)
            else None
        ),
        "artwork": _safe_artwork(item.get("art")),
    }
    if media_type in {"movie", "tvshow"}:
        normalized["year"] = _nullable_int(item.get("year"))
        normalized["genres"] = [
            genre for genre in (item.get("genre") or []) if isinstance(genre, str)
        ]
    if media_type == "tvshow":
        normalized.update(
            {
                "episode_count": _nullable_int(item.get("episode")),
                "season_count": _nullable_int(item.get("season")),
                "watched_episode_count": _nullable_int(item.get("watchedepisodes")),
            }
        )
    if media_type == "episode":
        normalized.update(
            {
                "show_title": (
                    item.get("showtitle")
                    if isinstance(item.get("showtitle"), str)
                    else None
                ),
                "tvshow_id": _nullable_int(item.get("tvshowid")),
                "season_id": _nullable_int(item.get("seasonid")),
                "season": _nullable_int(item.get("season")),
                "episode": _nullable_int(item.get("episode")),
            }
        )
    return normalized


def _normalize_season(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    season_id = item.get("seasonid")
    title = item.get("title") or item.get("label")
    tvshow_id = item.get("tvshowid")
    season = item.get("season")
    if (
        not isinstance(season_id, int)
        or isinstance(season_id, bool)
        or not isinstance(title, str)
        or not isinstance(tvshow_id, int)
        or isinstance(tvshow_id, bool)
        or not isinstance(season, int)
        or isinstance(season, bool)
    ):
        return None
    playcount = _nullable_int(item.get("playcount")) or 0
    return {
        "id": season_id,
        "media_type": "season",
        "title": title,
        "show_title": (
            item.get("showtitle")
            if isinstance(item.get("showtitle"), str)
            else None
        ),
        "tvshow_id": tvshow_id,
        "season": season,
        "watched": playcount > 0,
        "playcount": playcount,
        "episode_count": _nullable_int(item.get("episode")),
        "watched_episode_count": _nullable_int(item.get("watchedepisodes")),
        "artwork": _safe_artwork(item.get("art")),
    }


def _normalize_discovery_item(
    category: str, media_type: str, item: Any, id_key: str
) -> dict[str, Any] | None:
    if media_type in {"movie", "episode"}:
        return _normalize_item(media_type, item, id_key)
    if not isinstance(item, dict):
        return None
    item_id = item.get(id_key)
    title = item.get("title") or item.get("label")
    if (
        not isinstance(item_id, int)
        or isinstance(item_id, bool)
        or not isinstance(title, str)
    ):
        return None
    art = dict(item.get("art") or {}) if isinstance(item.get("art"), dict) else {}
    if isinstance(item.get("thumbnail"), str) and item.get("thumbnail"):
        art.setdefault("thumb", item["thumbnail"])
    normalized = {
        "id": item_id,
        "media_type": media_type,
        "title": title,
        "artwork": _safe_artwork(art),
    }
    if media_type in {"genre", "tag"}:
        normalized["library_type"] = (
            "tvshow" if category.startswith("tvshow_") else "movie"
        )
    return normalized


def _pagination(result: Any, requested_limit: int) -> dict[str, Any] | None:
    limits = result.get("limits") if isinstance(result, dict) else None
    if not isinstance(limits, dict):
        return None
    start, end, total = (limits.get(name) for name in ("start", "end", "total"))
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (start, end, total)
    ):
        return None
    return {
        "start": start,
        "end": end,
        "total": total,
        "limit": requested_limit,
        "has_more": end < total,
    }


class LibraryTool:
    """Curated remote-user video-library queries over Kodi JSON-RPC."""

    def __init__(self, jsonrpc_tool: Any):
        self.jsonrpc = jsonrpc_tool

    async def summary(self) -> ResponseMessage:
        """Return native Kodi totals while transferring at most one item per type."""

        counts: dict[str, int] = {}
        request_id: str | None = None
        total_latency = 0
        for name, method in _COUNT_REQUESTS:
            response = await self.jsonrpc.execute_jsonrpc(
                method, {"limits": {"start": 0, "end": 1}}
            )
            request_id = response.request_id
            total_latency += int(response.latency_ms or 0)
            if response.error is not None:
                return response

            result = response.result
            limits = result.get("limits") if isinstance(result, dict) else None
            total = limits.get("total") if isinstance(limits, dict) else None
            if (
                not isinstance(total, int)
                or isinstance(total, bool)
                or total < 0
            ):
                return ResponseMessage(
                    request_id=request_id or "library-summary",
                    result=None,
                    error=(
                        f"malformed Kodi response for {method}: "
                        "missing valid limits.total"
                    ),
                    error_type=ErrorType.INVALID_RESPONSE,
                    latency_ms=total_latency,
                )
            counts[name] = total

        return ResponseMessage(
            request_id=request_id or "library-summary",
            result={"counts": counts},
            error=None,
            latency_ms=total_latency,
        )

    async def search(
        self, *, query: str, media_type: str, start: int = 0, limit: int = 10
    ) -> ResponseMessage:
        """Run one Kodi-native title-contains search."""

        method, result_key, id_key, properties = _SEARCH_SPECS[media_type]
        response = await self.jsonrpc.execute_jsonrpc(
            method,
            {
                "properties": properties,
                "filter": {
                    "field": "title",
                    "operator": "contains",
                    "value": query,
                },
                "limits": {"start": start, "end": start + limit},
                "sort": {"method": "title", "order": "ascending"},
            },
        )
        if response.error is not None:
            return response

        result = response.result
        items = result.get(result_key) if isinstance(result, dict) else None
        pagination = _pagination(result, limit)
        if (
            not isinstance(items, list)
            or len(items) > limit
            or pagination is None
        ):
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=(
                    f"malformed Kodi response for {method}: "
                    f"missing valid {result_key} or limits"
                ),
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        normalized = [_normalize_item(media_type, item, id_key) for item in items]
        if any(item is None for item in normalized):
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=f"malformed Kodi response for {method}: invalid media item",
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        return ResponseMessage(
            request_id=response.request_id,
            result={
                "query": query,
                "media_type": media_type,
                "search": {"field": "title", "operator": "contains"},
                "items": normalized,
                "empty": not normalized,
                "pagination": pagination,
            },
            error=None,
            latency_ms=response.latency_ms,
        )

    async def _tvshow_details(
        self, tvshow_id: int
    ) -> tuple[dict[str, Any] | None, ResponseMessage | None]:
        response = await self.jsonrpc.execute_jsonrpc(
            "VideoLibrary.GetTVShowDetails",
            {"tvshowid": tvshow_id, "properties": ["title"]},
        )
        if response.error is not None:
            if response.error_code == -32602:
                return None, ResponseMessage(
                    request_id=response.request_id,
                    result=None,
                    error=f"TV show {tvshow_id} was not found",
                    error_type=ErrorType.NOT_FOUND,
                    error_code=response.error_code,
                    latency_ms=response.latency_ms,
                )
            return None, response
        result = response.result
        details = result.get("tvshowdetails") if isinstance(result, dict) else None
        title = details.get("title") if isinstance(details, dict) else None
        returned_id = details.get("tvshowid") if isinstance(details, dict) else None
        if (
            not isinstance(title, str)
            or not isinstance(returned_id, int)
            or isinstance(returned_id, bool)
        ):
            return None, ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=(
                    "malformed Kodi response for VideoLibrary.GetTVShowDetails: "
                    "missing tvshowdetails identity"
                ),
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        return {"id": returned_id, "title": title}, None

    async def seasons(
        self, *, tvshow_id: int, start: int = 0, limit: int = 10
    ) -> ResponseMessage:
        """List one TV show's seasons after validating its Kodi ID."""

        tvshow, failure = await self._tvshow_details(tvshow_id)
        if failure is not None:
            return failure
        response = await self.jsonrpc.execute_jsonrpc(
            "VideoLibrary.GetSeasons",
            {
                "tvshowid": tvshow_id,
                "properties": _SEASON_PROPERTIES,
                "limits": {"start": start, "end": start + limit},
                "sort": {"method": "season", "order": "ascending"},
            },
        )
        if response.error is not None:
            return response
        result = response.result
        items = result.get("seasons") if isinstance(result, dict) else None
        pagination = _pagination(result, limit)
        if (
            not isinstance(items, list)
            or len(items) > limit
            or pagination is None
        ):
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=(
                    "malformed Kodi response for VideoLibrary.GetSeasons: "
                    "missing valid seasons or limits"
                ),
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        normalized = [_normalize_season(item) for item in items]
        if any(item is None for item in normalized):
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error="malformed Kodi response for VideoLibrary.GetSeasons: invalid season",
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        return ResponseMessage(
            request_id=response.request_id,
            result={
                "tvshow": tvshow,
                "items": normalized,
                "empty": not normalized,
                "pagination": pagination,
            },
            error=None,
            latency_ms=response.latency_ms,
        )

    async def episodes(
        self,
        *,
        tvshow_id: int,
        season: int,
        start: int = 0,
        limit: int = 10,
    ) -> ResponseMessage:
        """List one TV show's season episodes after validating its Kodi ID."""

        tvshow, failure = await self._tvshow_details(tvshow_id)
        if failure is not None:
            return failure
        response = await self.jsonrpc.execute_jsonrpc(
            "VideoLibrary.GetEpisodes",
            {
                "tvshowid": tvshow_id,
                "season": season,
                "properties": _EPISODE_PROPERTIES,
                "limits": {"start": start, "end": start + limit},
                "sort": {"method": "episode", "order": "ascending"},
            },
        )
        if response.error is not None:
            return response
        result = response.result
        items = result.get("episodes") if isinstance(result, dict) else None
        pagination = _pagination(result, limit)
        if (
            not isinstance(items, list)
            or len(items) > limit
            or pagination is None
        ):
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=(
                    "malformed Kodi response for VideoLibrary.GetEpisodes: "
                    "missing valid episodes or limits"
                ),
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        normalized = [
            _normalize_item("episode", item, "episodeid") for item in items
        ]
        if any(item is None for item in normalized):
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error="malformed Kodi response for VideoLibrary.GetEpisodes: invalid episode",
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        return ResponseMessage(
            request_id=response.request_id,
            result={
                "tvshow": tvshow,
                "season": season,
                "items": normalized,
                "empty": not normalized,
                "pagination": pagination,
            },
            error=None,
            latency_ms=response.latency_ms,
        )

    async def browse(
        self, *, category: str, start: int = 0, limit: int = 10
    ) -> ResponseMessage:
        """Browse one curated recent/genre/set/tag category."""

        method, result_key, id_key, media_type, properties, extra, sort = (
            _BROWSE_SPECS[category]
        )
        params = {
            **extra,
            "properties": properties,
            "limits": {"start": start, "end": start + limit},
            "sort": sort,
        }
        response = await self.jsonrpc.execute_jsonrpc(method, params)
        if response.error is not None:
            return response
        result = response.result
        items = result.get(result_key) if isinstance(result, dict) else None
        pagination = _pagination(result, limit)
        if (
            not isinstance(items, list)
            or len(items) > limit
            or pagination is None
        ):
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=(
                    f"malformed Kodi response for {method}: "
                    f"missing valid {result_key} or limits"
                ),
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        normalized = [
            _normalize_discovery_item(category, media_type, item, id_key)
            for item in items
        ]
        if any(item is None for item in normalized):
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=f"malformed Kodi response for {method}: invalid discovery item",
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        return ResponseMessage(
            request_id=response.request_id,
            result={
                "category": category,
                "items": normalized,
                "empty": not normalized,
                "pagination": pagination,
            },
            error=None,
            latency_ms=response.latency_ms,
        )
