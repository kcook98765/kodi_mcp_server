"""Server-side screenshot storage for remote-safe Kodi GUI capture."""

from __future__ import annotations

import base64
import os
import time
import uuid
from pathlib import Path
from typing import Any

from kodi_mcp_server.config import (
    REPO_BASE_URL,
    SCREENSHOT_MAX_FILES,
    SCREENSHOT_RETENTION_SECONDS,
    SCREENSHOT_STORE_DIR,
)


def screenshot_root() -> Path:
    return Path(SCREENSHOT_STORE_DIR).expanduser()


def cleanup_screenshots(
    *,
    root: Path | None = None,
    retention_seconds: int = SCREENSHOT_RETENTION_SECONDS,
    max_files: int = SCREENSHOT_MAX_FILES,
    now: float | None = None,
) -> dict[str, Any]:
    """Remove old screenshots and trim the store to the configured maximum."""

    root = root or screenshot_root()
    now = time.time() if now is None else now
    if not root.exists():
        return {"removed": 0, "remaining": 0}

    removed = 0
    files = sorted((p for p in root.glob("*.png") if p.is_file()), key=lambda p: p.stat().st_mtime)

    if retention_seconds > 0:
        cutoff = now - retention_seconds
        retained = []
        for path in files:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
                else:
                    retained.append(path)
            except FileNotFoundError:
                continue
        files = retained

    if max_files > 0 and len(files) > max_files:
        for path in files[: len(files) - max_files]:
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                continue
        files = files[len(files) - max_files :]

    return {"removed": removed, "remaining": len(files)}


def store_screenshot_from_base64(image_base64: str, *, root: Path | None = None) -> dict[str, Any]:
    """Persist a base64 PNG screenshot and return server-visible metadata."""

    root = root or screenshot_root()
    root.mkdir(parents=True, exist_ok=True)
    cleanup = cleanup_screenshots(root=root)

    content = base64.b64decode(image_base64, validate=True)
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("screenshot image is not a PNG")

    screenshot_id = "%s-%s" % (int(time.time() * 1000), uuid.uuid4().hex[:12])
    filename = "%s.png" % screenshot_id
    path = root / filename
    path.write_bytes(content)
    os.utime(path, None)

    return {
        "screenshot_id": screenshot_id,
        "filename": filename,
        "path": str(path),
        "url": "%s/screenshots/%s" % (REPO_BASE_URL.rstrip("/"), filename),
        "content_type": "image/png",
        "size_bytes": len(content),
        "cleanup": cleanup,
        "retention_seconds": SCREENSHOT_RETENTION_SECONDS,
        "max_files": SCREENSHOT_MAX_FILES,
    }


def screenshot_path_for(filename: str) -> Path | None:
    name = Path(str(filename or "")).name
    if not name.endswith(".png"):
        return None
    path = screenshot_root() / name
    if not path.exists() or not path.is_file():
        return None
    return path
