"""Debug Adapter Protocol framing: ``Content-Length`` headers and a UTF-8 JSON body.

Each message is ``Content-Length: <bytes>\\r\\n\\r\\n<body>``, where the length counts the
bytes of the UTF-8 body, not its characters. Other header fields are allowed and ignored.
"""

from __future__ import annotations

import json
from typing import Any

HEADER_END = b"\r\n\r\n"
MAX_HEADER = 8 * 1024
"""Longest accepted header block; anything longer is not a DAP stream."""
MAX_BODY = 64 * 1024 * 1024


class FramingError(Exception):
    """The stream is not valid DAP framing; it cannot be resynchronised."""


def encode_message(message: dict[str, Any]) -> bytes:
    """Frame one message."""
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + HEADER_END + body


class FrameDecoder:
    """Turns chunks of bytes, split anywhere, into messages.

    A body that is not a JSON object is skipped and recorded in :attr:`errors` (the
    framing is still intact); a malformed header raises :class:`FramingError`.
    """

    def __init__(self, max_body: int = MAX_BODY) -> None:
        self._buffer = bytearray()
        self._max_body = max_body
        self._length: int | None = None
        self.errors: list[str] = []

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer.extend(data)
        messages: list[dict[str, Any]] = []
        while True:
            if self._length is None:
                end = self._buffer.find(HEADER_END)
                if end < 0:
                    if len(self._buffer) > MAX_HEADER:
                        raise FramingError("no end of header within "
                                           f"{MAX_HEADER} bytes")
                    return messages
                self._length = self._parse_header(bytes(self._buffer[:end]))
                del self._buffer[:end + len(HEADER_END)]
            if len(self._buffer) < self._length:
                return messages
            body = bytes(self._buffer[:self._length])
            del self._buffer[:self._length]
            self._length = None
            message = self._parse_body(body)
            if message is not None:
                messages.append(message)

    def _parse_header(self, header: bytes) -> int:
        try:
            text = header.decode("ascii")
        except UnicodeDecodeError as exc:
            raise FramingError("header is not ASCII") from exc
        length: int | None = None
        for line in text.split("\r\n"):
            if not line.strip():
                continue
            name, sep, value = line.partition(":")
            if not sep:
                raise FramingError(f"malformed header line {line!r}")
            if name.strip().lower() == "content-length":
                value = value.strip()
                if not value.isdigit():
                    raise FramingError(f"invalid Content-Length {value!r}")
                length = int(value)
        if length is None:
            raise FramingError("header without Content-Length")
        if length > self._max_body:
            raise FramingError(f"message of {length} bytes is too long")
        return length

    def _parse_body(self, body: bytes) -> dict[str, Any] | None:
        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.errors.append(f"invalid message body: {exc}")
            return None
        if not isinstance(message, dict):
            self.errors.append("message body is not a JSON object")
            return None
        return message
