"""Small PNG files built without numpy or OpenCV, for tests of embedded images."""

from __future__ import annotations

import base64
import struct
import zlib


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def png(width: int = 6, height: int = 4, *, seed: int = 0, alpha: int | None = None) -> bytes:
    """An RGB PNG with varied pixels (``seed`` changes them), or RGBA with every
    pixel's alpha set to ``alpha``."""
    rows = []
    for y in range(height):
        row = bytearray([0])  # filter: none
        for x in range(width):
            pixel = [(x * 40 + seed * 7) % 256, (y * 60 + seed * 13) % 256,
                     (x * y * 25 + seed) % 256]
            row += bytes(pixel if alpha is None else [*pixel, alpha])
        rows.append(bytes(row))
    header = struct.pack(">IIBBBBB", width, height, 8, 2 if alpha is None else 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(b"".join(rows))) + _chunk(b"IEND", b""))


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
