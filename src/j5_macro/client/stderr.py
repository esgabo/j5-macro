"""Continuous draining of the engine's stderr, keeping only the last lines."""

from __future__ import annotations

import asyncio
from collections import deque

STDERR_TAIL_LINES = 50
_CHUNK = 64 * 1024
_MAX_PARTIAL = 64 * 1024


class StderrTail:
    """Reads a stream until EOF so the pipe never fills, remembering the tail."""

    def __init__(self, max_lines: int = STDERR_TAIL_LINES) -> None:
        self._max_lines = max_lines
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._partial = b""

    @property
    def lines(self) -> list[str]:
        """The last lines, including an unterminated final line if any."""
        tail = list(self._lines)
        if self._partial:
            tail.append(self._decode(self._partial))
        return tail[-self._max_lines:]

    async def drain(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(_CHUNK):
            self.feed(chunk)

    def feed(self, chunk: bytes) -> None:
        *complete, self._partial = (self._partial + chunk).split(b"\n")
        for raw in complete:
            self._lines.append(self._decode(raw))
        if len(self._partial) > _MAX_PARTIAL:
            self._lines.append(self._decode(self._partial))
            self._partial = b""

    @staticmethod
    def _decode(raw: bytes) -> str:
        return raw.rstrip(b"\r").decode("utf-8", errors="replace")
