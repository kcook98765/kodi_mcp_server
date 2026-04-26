import base64
import os
import time


PNG_1X1 = base64.b64encode(
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
).decode("ascii")


def test_store_screenshot_from_base64_writes_png(tmp_path, monkeypatch):
    import kodi_mcp_server.screenshot_store as store

    monkeypatch.setattr(store, "SCREENSHOT_RETENTION_SECONDS", 600)
    monkeypatch.setattr(store, "SCREENSHOT_MAX_FILES", 10)
    monkeypatch.setattr(store, "REPO_BASE_URL", "http://server.example")

    result = store.store_screenshot_from_base64(PNG_1X1, root=tmp_path)

    assert result["url"].startswith("http://server.example/screenshots/")
    assert result["filename"].endswith(".png")
    assert (tmp_path / result["filename"]).read_bytes().startswith(b"\x89PNG")


def test_cleanup_screenshots_removes_old_and_trims_count(tmp_path):
    import kodi_mcp_server.screenshot_store as store

    now = time.time()
    old = tmp_path / "old.png"
    old.write_bytes(b"\x89PNG\r\n\x1a\nold")
    os.utime(old, (now - 1000, now - 1000))

    keep_1 = tmp_path / "keep-1.png"
    keep_1.write_bytes(b"\x89PNG\r\n\x1a\n1")
    os.utime(keep_1, (now - 10, now - 10))

    keep_2 = tmp_path / "keep-2.png"
    keep_2.write_bytes(b"\x89PNG\r\n\x1a\n2")
    os.utime(keep_2, (now - 5, now - 5))

    result = store.cleanup_screenshots(root=tmp_path, retention_seconds=100, max_files=1, now=now)

    assert result["removed"] == 2
    assert not old.exists()
    assert not keep_1.exists()
    assert keep_2.exists()
