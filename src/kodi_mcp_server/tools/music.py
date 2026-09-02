"""Bounded, normalized music-library discovery operations."""

from __future__ import annotations

from typing import Any

from kodi_mcp_server.models.messages import ErrorType, ResponseMessage
from kodi_mcp_server.tools.library import _pagination, _safe_artwork


_SEARCH_SPECS = {
    "artist": (
        "AudioLibrary.GetArtists",
        "artists",
        "artistid",
        "artist",
        ["genre", "isalbumartist", "art"],
        {"albumartistsonly": False},
        {"method": "artist", "order": "ascending"},
    ),
    "album": (
        "AudioLibrary.GetAlbums",
        "albums",
        "albumid",
        "album",
        [
            "title",
            "artist",
            "artistid",
            "year",
            "genre",
            "playcount",
            "compilation",
            "albumduration",
            "art",
        ],
        {"includesingles": True},
        {"method": "title", "order": "ascending"},
    ),
    "song": (
        "AudioLibrary.GetSongs",
        "songs",
        "songid",
        "title",
        [
            "title",
            "artist",
            "album",
            "albumid",
            "track",
            "disc",
            "duration",
            "playcount",
            "year",
            "genre",
            "art",
        ],
        {"includesingles": True},
        {"method": "title", "order": "ascending"},
    ),
}


_BROWSE_SPECS = {
    "recent_albums": (
        "AudioLibrary.GetRecentlyAddedAlbums",
        "albums",
        "albumid",
        "album",
        _SEARCH_SPECS["album"][4],
        {},
        {"method": "dateadded", "order": "descending"},
    ),
    "recent_songs": (
        "AudioLibrary.GetRecentlyAddedSongs",
        "songs",
        "songid",
        "song",
        _SEARCH_SPECS["song"][4],
        {},
        {"method": "dateadded", "order": "descending"},
    ),
    "genres": (
        "AudioLibrary.GetGenres",
        "genres",
        "genreid",
        "genre",
        ["title", "thumbnail"],
        {},
        {"method": "title", "order": "ascending"},
    ),
}


_COUNT_REQUESTS = (
    (
        "artists",
        "AudioLibrary.GetArtists",
        {"albumartistsonly": False},
    ),
    (
        "albums",
        "AudioLibrary.GetAlbums",
        {"includesingles": True},
    ),
    (
        "songs",
        "AudioLibrary.GetSongs",
        {"includesingles": True},
    ),
)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:20] if isinstance(item, str)]


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value[:20]
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0
    ]


def _page_items(result: Any, key: str) -> list[Any] | None:
    if not isinstance(result, dict):
        return None
    items = result.get(key)
    if isinstance(items, list):
        return items
    limits = result.get("limits")
    if key not in result and isinstance(limits, dict) and limits.get("total") == 0:
        return []
    return None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _normalize_genre(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    genre_id = item.get("genreid")
    title = item.get("title") or item.get("label")
    if (
        not isinstance(genre_id, int)
        or isinstance(genre_id, bool)
        or genre_id < 0
        or not isinstance(title, str)
    ):
        return None
    art = dict(item.get("art") or {}) if isinstance(item.get("art"), dict) else {}
    thumbnail = item.get("thumbnail")
    if isinstance(thumbnail, str) and thumbnail:
        art.setdefault("thumb", thumbnail)
    return {
        "id": genre_id,
        "media_type": "genre",
        "title": title,
        "artwork": _safe_artwork(art),
    }


def _normalize_item(media_type: str, item: Any, id_key: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    item_id = item.get(id_key)
    title_key = "artist" if media_type == "artist" else "title"
    title = item.get(title_key) or item.get("label")
    if (
        not isinstance(item_id, int)
        or isinstance(item_id, bool)
        or item_id < 0
        or not isinstance(title, str)
    ):
        return None

    if media_type == "artist":
        is_album_artist = item.get("isalbumartist")
        return {
            "id": item_id,
            "media_type": media_type,
            "name": title,
            "genres": _string_list(item.get("genre")),
            "is_album_artist": (
                is_album_artist if isinstance(is_album_artist, bool) else None
            ),
            "artwork": _safe_artwork(item.get("art")),
        }

    if media_type == "album":
        compilation = item.get("compilation")
        return {
            "id": item_id,
            "media_type": media_type,
            "title": title,
            "artists": _string_list(item.get("artist")),
            "artist_ids": _int_list(item.get("artistid")),
            "year": _nonnegative_int(item.get("year")),
            "genres": _string_list(item.get("genre")),
            "playcount": _nonnegative_int(item.get("playcount")) or 0,
            "compilation": compilation if isinstance(compilation, bool) else False,
            "duration_seconds": _nonnegative_int(item.get("albumduration")),
            "artwork": _safe_artwork(item.get("art")),
        }

    album = item.get("album")
    return {
        "id": item_id,
        "media_type": media_type,
        "title": title,
        "artists": _string_list(item.get("artist")),
        "album": album if isinstance(album, str) else None,
        "album_id": _nonnegative_int(item.get("albumid")),
        "track": _nonnegative_int(item.get("track")),
        "disc": _nonnegative_int(item.get("disc")),
        "duration_seconds": _nonnegative_int(item.get("duration")),
        "playcount": _nonnegative_int(item.get("playcount")) or 0,
        "year": _nonnegative_int(item.get("year")),
        "genres": _string_list(item.get("genre")),
        "artwork": _safe_artwork(item.get("art")),
    }


class MusicTool:
    """Curated remote-user music-library queries over Kodi JSON-RPC."""

    def __init__(self, jsonrpc_tool: Any):
        self.jsonrpc = jsonrpc_tool

    async def summary(self) -> ResponseMessage:
        """Return native Kodi totals while transferring at most one item per type."""

        counts: dict[str, int] = {}
        request_id: str | None = None
        total_latency = 0
        for name, method, options in _COUNT_REQUESTS:
            response = await self.jsonrpc.execute_jsonrpc(
                method,
                {**options, "limits": {"start": 0, "end": 1}},
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
                    request_id=request_id or "music-summary",
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
            request_id=request_id or "music-summary",
            result={"counts": counts},
            error=None,
            latency_ms=total_latency,
        )

    async def search(
        self, *, query: str, media_type: str, start: int = 0, limit: int = 10
    ) -> ResponseMessage:
        """Run one Kodi-native contains search on the type's identity field."""

        method, result_key, id_key, field, properties, extra, sort = (
            _SEARCH_SPECS[media_type]
        )
        response = await self.jsonrpc.execute_jsonrpc(
            method,
            {
                **extra,
                "properties": properties,
                "filter": {"field": field, "operator": "contains", "value": query},
                "limits": {"start": start, "end": start + limit},
                "sort": sort,
            },
        )
        if response.error is not None:
            return response

        result = response.result
        items = _page_items(result, result_key)
        pagination = _pagination(result, limit)
        if not isinstance(items, list) or len(items) > limit or pagination is None:
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
                error=f"malformed Kodi response for {method}: invalid music item",
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        return ResponseMessage(
            request_id=response.request_id,
            result={
                "query": query,
                "media_type": media_type,
                "search": {"field": field, "operator": "contains"},
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
        """Browse one curated recent or genre category."""

        method, result_key, id_key, media_type, properties, extra, sort = (
            _BROWSE_SPECS[category]
        )
        response = await self.jsonrpc.execute_jsonrpc(
            method,
            {
                **extra,
                "properties": properties,
                "limits": {"start": start, "end": start + limit},
                "sort": sort,
            },
        )
        if response.error is not None:
            return response
        result = response.result
        items = _page_items(result, result_key)
        pagination = _pagination(result, limit)
        if not isinstance(items, list) or len(items) > limit or pagination is None:
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
            _normalize_genre(item)
            if media_type == "genre"
            else _normalize_item(media_type, item, id_key)
            for item in items
        ]
        if any(item is None for item in normalized):
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=f"malformed Kodi response for {method}: invalid music item",
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

    async def _details(
        self, media_type: str, item_id: int
    ) -> tuple[dict[str, Any] | None, ResponseMessage | None]:
        if media_type == "artist":
            method = "AudioLibrary.GetArtistDetails"
            id_key = "artistid"
            result_key = "artistdetails"
        else:
            method = "AudioLibrary.GetAlbumDetails"
            id_key = "albumid"
            result_key = "albumdetails"
        response = await self.jsonrpc.execute_jsonrpc(
            method,
            {id_key: item_id, "properties": _SEARCH_SPECS[media_type][4]},
        )
        if response.error is not None:
            if response.error_code == -32602:
                return None, ResponseMessage(
                    request_id=response.request_id,
                    result=None,
                    error=f"{media_type} {item_id} was not found",
                    error_type=ErrorType.NOT_FOUND,
                    error_code=response.error_code,
                    latency_ms=response.latency_ms,
                )
            return None, response
        result = response.result
        details = result.get(result_key) if isinstance(result, dict) else None
        normalized = _normalize_item(media_type, details, id_key)
        if normalized is None or normalized["id"] != item_id:
            return None, ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=f"malformed Kodi response for {method}: missing {result_key} identity",
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        return normalized, None

    async def artist_albums(
        self, *, artist_id: int, start: int = 0, limit: int = 10
    ) -> ResponseMessage:
        """List albums for one validated Kodi artist ID."""

        artist, failure = await self._details("artist", artist_id)
        if failure is not None:
            return failure
        method = "AudioLibrary.GetAlbums"
        response = await self.jsonrpc.execute_jsonrpc(
            method,
            {
                "properties": _SEARCH_SPECS["album"][4],
                "filter": {"artistid": artist_id},
                "includesingles": True,
                "allroles": False,
                "limits": {"start": start, "end": start + limit},
                "sort": {"method": "title", "order": "ascending"},
            },
        )
        if response.error is not None:
            return response
        result = response.result
        items = _page_items(result, "albums")
        pagination = _pagination(result, limit)
        if not isinstance(items, list) or len(items) > limit or pagination is None:
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=f"malformed Kodi response for {method}: missing valid albums or limits",
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        normalized = [_normalize_item("album", item, "albumid") for item in items]
        if any(item is None for item in normalized):
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=f"malformed Kodi response for {method}: invalid album",
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        return ResponseMessage(
            request_id=response.request_id,
            result={
                "artist": artist,
                "items": normalized,
                "empty": not normalized,
                "pagination": pagination,
            },
            error=None,
            latency_ms=response.latency_ms,
        )

    async def album_songs(
        self, *, album_id: int, start: int = 0, limit: int = 10
    ) -> ResponseMessage:
        """List songs for one validated Kodi album ID in track order."""

        album, failure = await self._details("album", album_id)
        if failure is not None:
            return failure
        method = "AudioLibrary.GetSongs"
        response = await self.jsonrpc.execute_jsonrpc(
            method,
            {
                "properties": _SEARCH_SPECS["song"][4],
                "filter": {"albumid": album_id},
                "includesingles": True,
                "limits": {"start": start, "end": start + limit},
                "sort": {"method": "track", "order": "ascending"},
            },
        )
        if response.error is not None:
            return response
        result = response.result
        items = _page_items(result, "songs")
        pagination = _pagination(result, limit)
        if not isinstance(items, list) or len(items) > limit or pagination is None:
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=f"malformed Kodi response for {method}: missing valid songs or limits",
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        normalized = [_normalize_item("song", item, "songid") for item in items]
        if any(item is None for item in normalized):
            return ResponseMessage(
                request_id=response.request_id,
                result=None,
                error=f"malformed Kodi response for {method}: invalid song",
                error_type=ErrorType.INVALID_RESPONSE,
                latency_ms=response.latency_ms,
            )
        return ResponseMessage(
            request_id=response.request_id,
            result={
                "album": album,
                "items": normalized,
                "empty": not normalized,
                "pagination": pagination,
            },
            error=None,
            latency_ms=response.latency_ms,
        )
