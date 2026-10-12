import json

import pytest

from j5_macro.protocol import (
    PROTOCOL_VERSION,
    Command,
    ErrorInfo,
    Event,
    ProtocolError,
    Response,
    decode,
    encode,
)


def roundtrip(message):
    line = encode(message)
    assert "\n" not in line
    return decode(line)


def test_command_roundtrip():
    cmd = Command(type="run", id="c7", payload={"script_id": "s1", "mode": "step"})
    assert roundtrip(cmd) == cmd


def test_response_ok_and_error_roundtrip():
    ok = Response(type="run", id="c7", ok=True, result={"run_id": "r-1"})
    err = Response(type="run", id="c8", ok=False,
                   error=ErrorInfo(code="busy", message="another run is active"))
    assert roundtrip(ok) == ok
    assert roundtrip(err) == err


def test_response_requires_consistent_error():
    with pytest.raises(ValueError):
        Response(type="run", id="c1", ok=False)
    with pytest.raises(ValueError):
        Response(type="run", id="c1", ok=True, error=ErrorInfo(code="x", message="y"))


def test_event_roundtrip_and_engine_level_event():
    ev = Event(type="step_started", seq=14, ts="2026-10-12T10:15:03.412Z", t_ms=5120,
               run_id="r-1", payload={"step_id": "abrir_menu", "path": [0, 2], "line": 12})
    ready = Event(type="ready", seq=1, ts="2026-10-12T10:15:00.000Z", t_ms=0,
                  payload={"v": PROTOCOL_VERSION})
    assert roundtrip(ev) == ev
    assert roundtrip(ready).run_id is None


def test_unknown_fields_are_ignored():
    line = json.dumps({"v": 1, "kind": "command", "type": "hello", "id": "c1",
                       "payload": {}, "future_field": True})
    assert decode(line) == Command(type="hello", id="c1")


def test_non_ascii_text_survives():
    cmd = Command(type="load", id="c2", payload={"path": "guías/año.yaml"})
    assert "año" in encode(cmd)
    assert roundtrip(cmd) == cmd


@pytest.mark.parametrize("line", ["not json", "[]", '{"v":1,"kind":"nope"}',
                                  '{"v":1,"kind":"event","type":"x"}'])
def test_invalid_lines_raise_protocol_error(line):
    with pytest.raises(ProtocolError):
        decode(line)
