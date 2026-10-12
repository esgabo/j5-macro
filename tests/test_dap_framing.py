"""DAP ``Content-Length`` framing."""

from __future__ import annotations

import json

import pytest

from j5_macro.dap.framing import FrameDecoder, FramingError, encode_message


def frame(body: str) -> bytes:
    data = body.encode("utf-8")
    return f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data


def test_encode_counts_utf8_bytes() -> None:
    data = encode_message({"text": "ñandú 🦤"})
    header, body = data.split(b"\r\n\r\n", 1)
    assert header == f"Content-Length: {len(body)}".encode("ascii")
    assert len(body) > len(json.loads(body)["text"]) + 10  # bytes, not characters
    assert json.loads(body.decode("utf-8")) == {"text": "ñandú 🦤"}


def test_round_trip() -> None:
    message = {"seq": 1, "type": "request", "command": "initialize", "arguments": {"a": [1]}}
    assert FrameDecoder().feed(encode_message(message)) == [message]


def test_partial_reads_byte_by_byte() -> None:
    data = encode_message({"seq": 1, "text": "héllo"})
    decoder = FrameDecoder()
    received = []
    for index in range(len(data)):
        received += decoder.feed(data[index:index + 1])
        if index < len(data) - 1:
            assert received == []
    assert received == [{"seq": 1, "text": "héllo"}]


def test_split_inside_a_multibyte_character() -> None:
    data = encode_message({"text": "€"})
    cut = data.index("€".encode()) + 1
    decoder = FrameDecoder()
    assert decoder.feed(data[:cut]) == []
    assert decoder.feed(data[cut:]) == [{"text": "€"}]


def test_several_messages_in_one_chunk() -> None:
    chunk = b"".join(encode_message({"seq": n}) for n in range(1, 4))
    chunk += encode_message({"seq": 4})[:10]
    decoder = FrameDecoder()
    assert decoder.feed(chunk) == [{"seq": 1}, {"seq": 2}, {"seq": 3}]
    assert decoder.feed(encode_message({"seq": 4})[10:]) == [{"seq": 4}]


def test_extra_headers_and_case_are_accepted() -> None:
    body = b'{"seq":7}'
    data = (b"content-length: " + str(len(body)).encode() +
            b"\r\nContent-Type: application/vscode-jsonrpc; charset=utf-8\r\n\r\n" + body)
    assert FrameDecoder().feed(data) == [{"seq": 7}]


def test_invalid_body_is_skipped_and_reported() -> None:
    decoder = FrameDecoder()
    assert decoder.feed(frame("{nope") + frame("[1]") + frame('{"seq":2}')) == [{"seq": 2}]
    assert len(decoder.errors) == 2


@pytest.mark.parametrize("header", [
    b"Content-Type: x\r\n\r\n{}",
    b"Content-Length: abc\r\n\r\n{}",
    b"garbage\r\n\r\n{}",
])
def test_bad_headers_raise(header: bytes) -> None:
    with pytest.raises(FramingError):
        FrameDecoder().feed(header)


def test_too_long_header_or_body_raises() -> None:
    with pytest.raises(FramingError):
        FrameDecoder().feed(b"X" * 9000)
    with pytest.raises(FramingError):
        FrameDecoder(max_body=10).feed(b"Content-Length: 11\r\n\r\n")
