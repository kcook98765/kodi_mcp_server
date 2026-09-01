"""Server-side screenshot storage for remote-safe Kodi GUI capture."""

from __future__ import annotations

import base64
import hashlib
import os
import struct
import time
import uuid
import zlib
from pathlib import Path
from typing import Any

from kodi_mcp_server.config import (
    REPO_BASE_URL,
    SCREENSHOT_MAX_FILES,
    SCREENSHOT_RETENTION_SECONDS,
    SCREENSHOT_STORE_DIR,
)


BLACK_FRAME_CHANNEL_MAX = 0


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    distance_left = abs(estimate - left)
    distance_above = abs(estimate - above)
    distance_upper_left = abs(estimate - upper_left)
    if distance_left <= distance_above and distance_left <= distance_upper_left:
        return left
    if distance_above <= distance_upper_left:
        return above
    return upper_left


def _filter_png_row(row: bytes, previous: bytes, channels: int, filter_type: int) -> bytes:
    """Return the PNG-filtered representation of one known raw scanline."""

    filtered = bytearray(len(row))
    for index, value in enumerate(row):
        left = row[index - channels] if index >= channels else 0
        above = previous[index]
        upper_left = previous[index - channels] if index >= channels else 0
        if filter_type == 0:
            predictor = 0
        elif filter_type == 1:
            predictor = left
        elif filter_type == 2:
            predictor = above
        elif filter_type == 3:
            predictor = (left + above) // 2
        elif filter_type == 4:
            predictor = _paeth_predictor(left, above, upper_left)
        else:
            raise ValueError("screenshot PNG uses an unknown row filter")
        filtered[index] = (value - predictor) & 0xFF
    return bytes(filtered)


def inspect_screenshot_from_base64(image_base64: str) -> dict[str, Any]:
    """Validate a PNG and conservatively detect an effectively uniform black frame."""

    content = base64.b64decode(image_base64, validate=True)
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("screenshot image is not a PNG")
    if len(content) < 33 or content[12:16] != b"IHDR":
        raise ValueError("screenshot PNG is missing a valid IHDR header")

    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", content[16:29]
    )
    result: dict[str, Any] = {
        "size_bytes": len(content),
        "width": width,
        "height": height,
        "sha256": hashlib.sha256(content).hexdigest(),
        "pixel_analysis_supported": False,
        "effectively_uniform_black": False,
        "black_channel_max": BLACK_FRAME_CHANNEL_MAX,
    }

    # Kodi's TakeScreenshot output is 8-bit non-interlaced RGB/RGBA. Unknown
    # PNG layouts remain valid captures but are never rejected as black.
    if (
        bit_depth != 8
        or color_type not in (2, 6)
        or compression != 0
        or filtering != 0
        or interlace != 0
        or width <= 0
        or height <= 0
    ):
        return result

    idat_parts: list[bytes] = []
    offset = 8
    saw_iend = False
    while offset + 12 <= len(content):
        length = struct.unpack(">I", content[offset : offset + 4])[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(content):
            raise ValueError("screenshot PNG contains a truncated chunk")
        chunk_type = content[offset + 4 : offset + 8]
        chunk_data = content[offset + 8 : offset + 8 + length]
        if chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            saw_iend = True
            break
        offset = chunk_end
    if not idat_parts or not saw_iend:
        raise ValueError("screenshot PNG is missing image data or IEND")

    channels = 3 if color_type == 2 else 4
    stride = width * channels
    decoded = zlib.decompress(b"".join(idat_parts))
    expected = height * (stride + 1)
    if len(decoded) != expected:
        raise ValueError("screenshot PNG has an unexpected decoded size")

    zero_row = bytes(stride)
    black_row = (
        bytes((0, 0, 0)) * width
        if color_type == 2
        else bytes((0, 0, 0, 255)) * width
    )
    first_row_encodings = {
        filter_type: _filter_png_row(black_row, zero_row, channels, filter_type)
        for filter_type in range(5)
    }
    later_row_encodings = {
        filter_type: _filter_png_row(black_row, black_row, channels, filter_type)
        for filter_type in range(5)
    }

    cursor = 0
    effectively_uniform_black = True
    for row_index in range(height):
        filter_type = decoded[cursor]
        cursor += 1
        if filter_type not in first_row_encodings:
            raise ValueError("screenshot PNG uses an unknown row filter")
        source = decoded[cursor : cursor + stride]
        cursor += stride
        expected_row = (
            first_row_encodings[filter_type]
            if row_index == 0
            else later_row_encodings[filter_type]
        )
        if source != expected_row:
            effectively_uniform_black = False
            break

    result.update(
        {
            "pixel_analysis_supported": True,
            "effectively_uniform_black": effectively_uniform_black,
            "max_rgb_channel": 0 if effectively_uniform_black else None,
        }
    )
    return result


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
    if len(content) < 24 or content[12:16] != b"IHDR":
        raise ValueError("screenshot PNG is missing a valid IHDR header")
    width, height = struct.unpack(">II", content[16:24])

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
        "format": "png",
        "size_bytes": len(content),
        "width": width,
        "height": height,
        "sha256": hashlib.sha256(content).hexdigest(),
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
