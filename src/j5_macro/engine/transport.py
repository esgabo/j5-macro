"""Line transport between the engine and one client.

The server only sees a :class:`LineTransport`; stdio is one binding of it, and
tests bind it to in-memory streams.
"""

from __future__ import annotations

import io
import sys
import threading
from typing import Protocol


class LineReader(Protocol):
    def readline(self) -> str: ...


class LineWriter(Protocol):
    def write(self, text: str, /) -> int: ...

    def flush(self) -> None: ...


class LineTransport:
    """Reads lines from ``reader`` and writes whole lines to ``writer``.

    Writes are serialized with a lock so lines from different threads never
    interleave, and each line is flushed as soon as it is written.
    """

    def __init__(self, reader: LineReader, writer: LineWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._write_lock = threading.Lock()

    def read_line(self) -> str | None:
        """Next line without its line terminator, or None at end of input."""
        line = self._reader.readline()
        if line == "":
            return None
        return line.rstrip("\r\n")

    def write_line(self, line: str) -> None:
        if "\n" in line or "\r" in line:
            raise ValueError("a protocol line cannot contain line breaks")
        with self._write_lock:
            self._writer.write(line + "\n")
            self._writer.flush()


def stdio_transport() -> LineTransport:
    """Bind the transport to the process stdin/stdout as UTF-8 JSON lines.

    Both streams are reconfigured to UTF-8 with ``\\n`` line endings, so the platform
    locale (e.g. cp1252 on Windows) never matters. Invalid UTF-8 input is replaced
    rather than raised, so it surfaces as a malformed line. ``sys.stdout`` is then
    pointed at stderr, so a stray ``print`` can never corrupt the protocol.
    """
    reader, writer = sys.stdin, sys.stdout
    if isinstance(reader, io.TextIOWrapper):
        reader.reconfigure(encoding="utf-8", errors="replace", newline=None)
    if isinstance(writer, io.TextIOWrapper):
        writer.reconfigure(encoding="utf-8", errors="strict", newline="\n",
                           write_through=True)
    sys.stdout = sys.stderr
    return LineTransport(reader, writer)
