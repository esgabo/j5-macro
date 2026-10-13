"""In-process tests of the engine server through the line transport."""

from __future__ import annotations

import io
import json
import re
from typing import Any

from j5_macro.engine.app import create_server
from j5_macro.engine.commands import vision_installed
from j5_macro.engine.events import EventSequencer
from j5_macro.engine.server import EVENT_BUFFER_SIZE, EngineServer
from j5_macro.engine.transport import LineTransport
from j5_macro.protocol.messages import Command
from j5_macro.steps import default_registry

CAPABILITIES = {"backend": None, "input_strategies": [],
                "step_types": default_registry().names(), "vision": vision_installed(),
                "display_scales": []}
HELLO = {"v_min": 1, "v_max": 1, "client": "tests"}


def cmd(type: str, id: str, payload: Any = None) -> str:
    data: dict[str, Any] = {"v": 1, "kind": "command", "type": type, "id": id}
    if payload is not None:
        data["payload"] = payload
    return json.dumps(data)


def serve(*lines: str, server_setup: Any = None) -> tuple[int, list[dict[str, Any]]]:
    """Run a fresh server over the given input lines; return exit code and output."""
    output = io.StringIO()
    transport = LineTransport(io.StringIO("".join(line + "\n" for line in lines)), output)
    server = create_server(transport)
    if server_setup is not None:
        server_setup(server)
    code = server.serve()
    text = output.getvalue()
    assert text.endswith("\n")
    return code, [json.loads(line) for line in text.splitlines()]


def responses(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {m["id"]: m for m in messages if m["kind"] == "response"}


def events(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in messages if m["kind"] == "event"]


def test_ready_is_first_and_eof_exits_cleanly() -> None:
    code, out = serve()
    assert code == 0
    assert len(out) == 1
    ready = out[0]
    assert ready["kind"] == "event"
    assert ready["type"] == "ready"
    assert ready["run_id"] is None
    assert ready["seq"] == 1
    assert ready["t_ms"] >= 0
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", ready["ts"])
    assert ready["payload"] == {"v": 1, "backend": None, "capabilities": CAPABILITIES}


def test_hello_success() -> None:
    _, out = serve(cmd("hello", "h", {"v_min": 1, "v_max": 5, "client": "cli"}))
    resp = responses(out)["h"]
    assert resp["ok"] is True
    assert resp["type"] == "hello"
    assert resp["result"] == {"v": 1, "capabilities": CAPABILITIES}


def test_hello_version_mismatch() -> None:
    _, out = serve(cmd("hello", "h", {"v_min": 2, "v_max": 3, "client": "cli"}),
                   cmd("get_events", "g", {"run_id": None, "from_seq": 1}))
    resp = responses(out)
    assert resp["h"]["ok"] is False
    assert resp["h"]["error"]["code"] == "validation_failed"
    assert "no common protocol version" in resp["h"]["error"]["message"]
    # A failed hello does not unlock other commands.
    assert resp["g"]["error"]["code"] == "invalid_state"


def test_hello_invalid_payload() -> None:
    _, out = serve(
        cmd("hello", "missing", {"v_max": 1, "client": "cli"}),
        cmd("hello", "bool", {"v_min": True, "v_max": 1, "client": "cli"}),
        cmd("hello", "client", {"v_min": 1, "v_max": 1, "client": 7}),
        cmd("hello", "range", {"v_min": 2, "v_max": 1, "client": "cli"}),
        cmd("hello", "list", [1, 2]),
    )
    for resp in responses(out).values():
        assert resp["ok"] is False
        assert resp["error"]["code"] == "validation_failed"


def test_command_before_hello_is_invalid_state() -> None:
    _, out = serve(cmd("get_events", "g", {"run_id": None, "from_seq": 1}),
                   cmd("run", "r", {"script_id": "s"}))
    for resp in responses(out).values():
        assert resp["error"]["code"] == "invalid_state"
        assert resp["error"]["details"] == {"reason": "hello_required"}


def test_get_events_from_buffer() -> None:
    _, out = serve(cmd("hello", "h", HELLO), "not json", "[]",
                   cmd("get_events", "all", {"run_id": None, "from_seq": 0}),
                   cmd("get_events", "tail", {"run_id": None, "from_seq": 3}))
    emitted = events(out)
    assert [e["type"] for e in emitted] == ["ready", "log", "log"]
    resp = responses(out)
    assert resp["all"]["result"]["events"] == emitted
    assert resp["tail"]["result"]["events"] == emitted[2:]


def test_get_events_validation_and_unknown_run() -> None:
    _, out = serve(cmd("hello", "h", HELLO),
                   cmd("get_events", "run", {"run_id": "r-1", "from_seq": 1}),
                   cmd("get_events", "noseq", {"run_id": None}),
                   cmd("get_events", "norun", {"from_seq": 1}))
    resp = responses(out)
    assert resp["run"]["error"]["code"] == "not_running"
    assert resp["noseq"]["error"]["code"] == "validation_failed"
    # A missing run_id means the engine-level stream (typed payloads omit null fields).
    assert resp["norun"]["ok"] is True
    assert resp["norun"]["result"]["events"][0]["type"] == "ready"


def test_event_buffer_is_bounded() -> None:
    server = create_server(LineTransport(io.StringIO(""), io.StringIO()))
    for i in range(EVENT_BUFFER_SIZE + 5):
        server.log("info", str(i))
    kept = server.buffered_events(0)
    assert len(kept) == EVENT_BUFFER_SIZE
    assert kept[0].seq == 6
    assert kept[-1].seq == EVENT_BUFFER_SIZE + 5


def test_unknown_command_is_validation_failed() -> None:
    _, out = serve(cmd("frobnicate", "x", {}), cmd("hello", "h", HELLO),
                   cmd("frobnicate", "y", {}))
    resp = responses(out)
    assert resp["x"]["error"]["code"] == "validation_failed"
    assert resp["y"]["error"]["code"] == "validation_failed"


def test_malformed_lines_become_log_events() -> None:
    bad = [
        "{not json",
        "42",
        json.dumps({"v": 1, "kind": "command", "type": "hello"}),  # missing id
        json.dumps({"v": 1, "kind": "command", "type": "hello", "id": 5}),  # id not str
        json.dumps({"v": 1, "kind": "event", "type": "ready", "seq": 1, "ts": "x",
                    "t_ms": 0}),
        json.dumps({"v": 1, "kind": "weird", "type": "x", "id": "1"}),
    ]
    code, out = serve(*bad, "", "   ", cmd("hello", "h", HELLO))
    assert code == 0
    logs = [e for e in events(out) if e["type"] == "log"]
    assert len(logs) == len(bad)  # blank lines are skipped silently
    for log in logs:
        assert log["run_id"] is None
        assert log["payload"]["level"] == "error"
        assert log["payload"]["message"]
    assert responses(out)["h"]["ok"] is True


def test_handler_exception_is_internal_error_and_loop_continues() -> None:
    def boom(command: Command) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    def setup(server: EngineServer) -> None:
        server.register("boom", boom, requires_hello=False)

    code, out = serve(cmd("boom", "b", {}), cmd("hello", "h", HELLO), server_setup=setup)
    assert code == 0
    resp = responses(out)
    assert resp["b"]["ok"] is False
    assert resp["b"]["error"]["code"] == "internal_error"
    assert "kaboom" in resp["b"]["error"]["message"]
    assert "RuntimeError: kaboom" in resp["b"]["error"]["details"]["traceback"]
    assert resp["h"]["ok"] is True


def test_seq_strictly_increasing_and_t_ms_non_decreasing() -> None:
    _, out = serve(*(["oops"] * 50))
    emitted = events(out)
    assert len(emitted) == 51
    assert [e["seq"] for e in emitted] == list(range(1, 52))
    t_ms = [e["t_ms"] for e in emitted]
    assert t_ms == sorted(t_ms)


def test_sequencer_uses_monotonic_clock() -> None:
    ticks = iter([5_000_000, 5_000_000, 7_400_000, 1_005_000_000])
    seq = EventSequencer("r-1", monotonic_ns=lambda: next(ticks))
    first, second, third = seq.next("a"), seq.next("b", {"k": 1}), seq.next("c")
    assert (first.seq, second.seq, third.seq) == (1, 2, 3)
    assert (first.t_ms, second.t_ms, third.t_ms) == (0, 2, 1000)
    assert first.run_id == "r-1"
    assert second.payload == {"k": 1}
    assert seq.last_seq == 3


def test_unicode_survives_the_transport() -> None:
    _, out = serve(cmd("hello", "h", {"v_min": 1, "v_max": 1, "client": "clïent ✓"}))
    assert responses(out)["h"]["ok"] is True
