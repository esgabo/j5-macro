"""A scriptable stand-in for ``python -m j5_macro.engine`` used by the client tests.

Usage: ``python fake_engine.py MODE``. Speaks JSON-lines over stdio like the real
engine (``ready`` first, ``hello`` before anything else, ``get_events``) and adds
test commands:

- ``echo``: result ``{"echo": payload}``.
- ``fail``: error ``validation_failed``.
- ``emit``: payload ``{"events": [...]}``; each item is ``{"type", "run_id"?, "payload"?,
  "hidden"?}`` (hidden events are recorded for ``get_events`` but not written) or
  ``{"resend": seq, "run_id"?}`` (writes an already emitted event again).

Modes:

- ``normal``: the above.
- ``out_of_order``: ``echo`` commands are answered in pairs, the second one first, with
  events interleaved.
- ``gap``: right after ``hello`` emits streams with missing seqs, a duplicate and an
  unknown event type.
- ``crash``: after ``hello`` reports held keys; the next command makes it write a
  traceback to stderr and exit with code 3.
- ``noisy``: floods stderr (far beyond the pipe buffer) at start, on each command and
  on exit.
- ``hang``: ignores EOF on stdin and never exits on its own.
- ``stubborn``: like ``hang`` and also ignores SIGTERM where the OS allows it.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from datetime import UTC, datetime
from typing import Any

from j5_macro.protocol import Command, ErrorInfo, Event, Response, decode, encode

START = time.monotonic()


class FakeEngine:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.greeted = False
        self.seqs: dict[str | None, int] = {}
        self.history: list[Event] = []
        self.held_back: list[Command] = []

    # -- output ---------------------------------------------------------------

    def write(self, line: str) -> None:
        sys.stdout.buffer.write(line.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()

    def stderr(self, text: str) -> None:
        sys.stderr.buffer.write(text.encode("utf-8") + b"\n")
        sys.stderr.buffer.flush()

    def event(self, type_: str, run_id: str | None = None,
              payload: dict[str, Any] | None = None, *, hidden: bool = False) -> Event:
        seq = self.seqs.get(run_id, 0) + 1
        self.seqs[run_id] = seq
        event = Event(type=type_, seq=seq, run_id=run_id, payload=payload or {},
                      ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
                      t_ms=int((time.monotonic() - START) * 1000))
        self.history.append(event)
        if not hidden:
            self.write(encode(event))
        return event

    def resend(self, run_id: str | None, seq: int) -> None:
        for event in self.history:
            if event.run_id == run_id and event.seq == seq:
                self.write(encode(event))

    def ok(self, command: Command, result: dict[str, Any] | None = None) -> None:
        self.write(encode(Response(type=command.type, id=command.id, ok=True,
                                   result=result or {})))

    def error(self, command: Command, code: str, message: str) -> None:
        self.write(encode(Response(type=command.type, id=command.id, ok=False,
                                   error=ErrorInfo(code=code, message=message))))

    # -- input ----------------------------------------------------------------

    def run(self) -> int:
        if self.mode == "noisy":
            self.noise("start", 5000)
        self.event("ready", payload={"v": 1, "backend": "fake", "capabilities": {}})
        for raw in sys.stdin.buffer:
            line = raw.decode("utf-8").strip()
            if line:
                message = decode(line)
                assert isinstance(message, Command)
                self.handle(message)
        if self.mode == "noisy":
            self.noise("exit", 1000)
            self.stderr("bye")
        if self.mode in ("hang", "stubborn"):
            while True:
                time.sleep(3600)
        return 0

    def noise(self, label: str, lines: int) -> None:
        for n in range(lines):
            self.stderr(f"{label} noise {n} ñandú " + "x" * 100)

    def handle(self, command: Command) -> None:
        if self.mode == "crash" and self.greeted:
            self.stderr("Traceback (most recent call last):\nRuntimeError: boom")
            os._exit(3)
        if self.mode == "noisy":
            self.noise(command.type, 1000)
        if command.type == "hello":
            self.greeted = True
            self.ok(command, {"v": 1, "capabilities": {
                "backend": "fake", "input_strategies": ["direct"], "step_types": ["log"]}})
            self.after_hello()
        elif not self.greeted:
            self.error(command, "invalid_state", "send hello first")
        elif command.type == "echo":
            self.echo(command)
        elif command.type == "fail":
            self.error(command, "validation_failed", "fail on purpose")
        elif command.type == "emit":
            self.emit(command.payload.get("events", []))
            self.ok(command)
        elif command.type == "get_events":
            run_id = command.payload.get("run_id")
            from_seq = int(command.payload.get("from_seq", 1))
            events = [e.to_json() for e in self.history
                      if e.run_id == run_id and e.seq >= from_seq]
            self.ok(command, {"events": events})
        else:
            self.error(command, "validation_failed", f"unknown command {command.type}")

    def echo(self, command: Command) -> None:
        if self.mode != "out_of_order":
            self.ok(command, {"echo": command.payload})
            return
        self.held_back.append(command)
        if len(self.held_back) == 2:
            first, second = self.held_back
            self.held_back.clear()
            self.event("log", payload={"level": "info", "message": "before second"})
            self.ok(second, {"echo": second.payload})
            self.event("log", payload={"level": "info", "message": "before first"})
            self.ok(first, {"echo": first.payload})

    def emit(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            if "resend" in item:
                self.resend(item.get("run_id"), int(item["resend"]))
            else:
                self.event(item["type"], item.get("run_id"), item.get("payload"),
                           hidden=bool(item.get("hidden")))

    def after_hello(self) -> None:
        if self.mode == "gap":
            self.emit([
                {"type": "run_started", "run_id": "r-1", "payload": {"script_id": "s1"}},
                {"type": "step_started", "run_id": "r-1", "payload": {"step_id": "a"}},
                {"type": "step_finished", "run_id": "r-1", "hidden": True},
                {"type": "log", "run_id": "r-1", "hidden": True},
                {"type": "from_the_future", "run_id": "r-1", "payload": {"new": True}},
                {"resend": 2, "run_id": "r-1"},
                {"type": "run_finished", "run_id": "r-1", "payload": {"status": "completed"}},
                {"type": "log", "hidden": True, "payload": {"message": "lost"}},
                {"type": "log", "payload": {"message": "after the gap"}},
            ])
        elif self.mode == "crash":
            self.event("input_state", payload={"held": ["shift"]})
            self.event("input_state", payload={"held": ["ctrl", "mouse_left"]})


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
    if mode == "stubborn" and hasattr(signal, "SIGTERM") and os.name == "posix":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    return FakeEngine(mode).run()


if __name__ == "__main__":
    sys.exit(main())
