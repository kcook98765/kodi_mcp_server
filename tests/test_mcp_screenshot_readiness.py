import base64
import json

import pytest

from kodi_mcp_server.models.messages import ResponseMessage
from tests.png_fixtures import png_rgba


BLACK_PNG = png_rgba([
    [(0, 0, 0, 255), (0, 0, 0, 255)],
    [(0, 0, 0, 255), (0, 0, 0, 255)],
])
DARK_UI_PNG = png_rgba([
    [(0, 0, 0, 255), (0, 0, 0, 255)],
    [(0, 0, 0, 255), (32, 48, 64, 255)],
])


class _SequenceBridge:
    def __init__(self, captures, *, active_players=None):
        self._captures = list(captures)
        self.active_players = list(active_players or [])
        self.capture_calls = 0
        self.state_calls = 0

    async def gui_screenshot(self, include_image=False):
        index = min(self.capture_calls, len(self._captures) - 1)
        content = self._captures[index]
        self.capture_calls += 1
        return ResponseMessage(
            request_id=f"capture-{self.capture_calls}",
            result={
                "ok": True,
                "filename": f"capture-{self.capture_calls}.png",
                "content_type": "image/png",
                "size_bytes": len(content),
                "image_base64": base64.b64encode(content).decode("ascii") if include_image else None,
            },
            error=None,
        )

    async def gui_state(self):
        self.state_calls += 1
        return ResponseMessage(
            request_id=f"state-{self.state_calls}",
            result={
                "ok": True,
                "current_window": "Home",
                "current_window_id": 10000,
                "current_dialog_id": 9999,
                "conditions": {
                    "fullscreen_video": False,
                    "player_has_media": bool(self.active_players),
                    "player_has_video": bool(self.active_players),
                    "player_playing": bool(self.active_players),
                    "player_paused": False,
                },
                "active_players": self.active_players,
            },
            error=None,
        )


class _UnusedJsonRpc:
    pass


async def _call_screenshot(monkeypatch, bridge):
    import kodi_mcp_mcp.server_core as server_core
    from kodi_mcp_mcp.server_core import build_mcp_server
    from mcp.types import CallToolRequestParams

    stored = []

    def fake_store(image_base64):
        stored.append(base64.b64decode(image_base64))
        return {
            "screenshot_id": "stored",
            "filename": "stored.png",
            "url": "http://server/screenshots/stored.png",
            "content_type": "image/png",
            "format": "png",
            "size_bytes": len(stored[-1]),
            "width": 2,
            "height": 2,
            "sha256": "fixture",
        }

    monkeypatch.setattr(server_core, "store_screenshot_from_base64", fake_store)
    server, _ = build_mcp_server({"bridge": bridge, "jsonrpc": _UnusedJsonRpc(), "notifications": None})
    result = await server.get_request_handler("tools/call").handler(
        None, CallToolRequestParams(name="kodi_gui_screenshot", arguments={})
    )
    return result, json.loads(result.content[0].text), stored


@pytest.mark.asyncio
async def test_screenshot_retries_uniform_black_home_frames_until_valid(monkeypatch):
    bridge = _SequenceBridge([BLACK_PNG, BLACK_PNG, DARK_UI_PNG])

    result, envelope, stored = await _call_screenshot(monkeypatch, bridge)

    assert result.is_error is False
    assert bridge.capture_calls == 3
    assert stored == [DARK_UI_PNG]
    assert envelope["data"]["capture_validation"] == {
        "status": "valid_after_retry",
        "attempts": 3,
        "retries": 2,
        "max_attempts": 5,
        "retry_timeout_seconds": 2.0,
        "retry_condition": "effectively_uniform_black_home_without_active_media_or_dialog",
    }


@pytest.mark.asyncio
async def test_screenshot_allows_fourth_capture_seen_in_live_kodi22_recovery(monkeypatch):
    bridge = _SequenceBridge([BLACK_PNG, BLACK_PNG, BLACK_PNG, DARK_UI_PNG])

    result, envelope, stored = await _call_screenshot(monkeypatch, bridge)

    assert result.is_error is False
    assert bridge.capture_calls == 4
    assert stored == [DARK_UI_PNG]
    assert envelope["data"]["capture_validation"]["attempts"] == 4
    assert envelope["data"]["capture_validation"]["retries"] == 3


@pytest.mark.asyncio
async def test_screenshot_allows_fifth_capture_within_live_retry_deadline(monkeypatch):
    bridge = _SequenceBridge([BLACK_PNG, BLACK_PNG, BLACK_PNG, BLACK_PNG, DARK_UI_PNG])

    result, envelope, stored = await _call_screenshot(monkeypatch, bridge)

    assert result.is_error is False
    assert bridge.capture_calls == 5
    assert stored == [DARK_UI_PNG]
    assert envelope["data"]["capture_validation"]["attempts"] == 5
    assert envelope["data"]["capture_validation"]["retries"] == 4


@pytest.mark.asyncio
async def test_screenshot_black_home_failure_is_bounded_and_not_stored(monkeypatch):
    bridge = _SequenceBridge([BLACK_PNG])

    result, envelope, stored = await _call_screenshot(monkeypatch, bridge)

    assert result.is_error is True
    assert bridge.capture_calls == 5
    assert bridge.state_calls == 5
    assert stored == []
    assert envelope["error_type"] == "screenshot_not_ready"
    assert envelope["error_code"] == "BLACK_FRAME"
    validation = envelope["raw"]["capture_validation"]
    assert validation["status"] == "failed_black_frame"
    assert validation["attempts"] == 5
    assert validation["retries"] == 4
    assert len(validation["attempt_diagnostics"]) == 5


@pytest.mark.asyncio
async def test_screenshot_does_not_reject_black_active_media(monkeypatch):
    bridge = _SequenceBridge([BLACK_PNG], active_players=[{"playerid": 1, "type": "video"}])

    result, envelope, stored = await _call_screenshot(monkeypatch, bridge)

    assert result.is_error is False
    assert bridge.capture_calls == 1
    assert bridge.state_calls == 1
    assert stored == [BLACK_PNG]
    assert envelope["data"]["capture_validation"]["status"] == (
        "accepted_uniform_black_context_may_be_legitimate"
    )


@pytest.mark.asyncio
async def test_screenshot_accepts_dark_ui_with_nonblack_detail_without_retry(monkeypatch):
    bridge = _SequenceBridge([DARK_UI_PNG])

    result, envelope, stored = await _call_screenshot(monkeypatch, bridge)

    assert result.is_error is False
    assert bridge.capture_calls == 1
    assert bridge.state_calls == 0
    assert stored == [DARK_UI_PNG]
    assert envelope["data"]["capture_validation"]["status"] == "valid"
