import binascii
import hashlib
import struct
import zlib
from functools import lru_cache


def png_rgba(rows):
    height = len(rows)
    width = len(rows[0])
    raw = b"".join(b"\x00" + b"".join(bytes(pixel) for pixel in row) for row in rows)

    def chunk(kind, payload):
        checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@lru_cache(maxsize=4)
def large_noise_png(seed=1, width=1280, height=720):
    pixels = hashlib.shake_256(str(seed).encode("ascii")).digest(width * height * 4)
    stride = width * 4
    raw = b"".join(b"\x00" + pixels[offset : offset + stride] for offset in range(0, len(pixels), stride))

    def chunk(kind, payload):
        checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, level=1))
        + chunk(b"IEND", b"")
    )
