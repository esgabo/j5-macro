"""A scripted stand-in for the engine used by the ``j5`` CLI tests.

Usage: ``python fake_cli_engine.py SCENARIO [RECORD_FILE]``. Speaks the M0 handshake
(``ready``, then ``hello``) and fakes ``load``, ``list_step_types``, ``run``, ``stop``
and ``resume`` as described in docs/contract.md ("Commands", "Events"). Every command
received is appended to RECORD_FILE as one JSON line, so tests can check payloads and
wait for a given command.

Scenarios:

- ``valid``: the script loads; ``run`` completes, with a loop, a branch, a variable
  change and a log (the log echoes the run's ``vars``).
- ``warnings``: the script loads with two warnings; ``run`` completes.
- ``invalid``: ``load`` fails with two errors and one warning in ``details``.
- ``not_found``: ``load`` fails with ``script_not_found`` and no details.
- ``failed``: ``run`` fails on its second step.
- ``interference``: the run ends ``stopped`` with reason ``interference``.
- ``interference_pause``: the user moves the mouse during the first step and the run
  pauses with reason ``interference``; ``resume`` runs the step again and completes,
  ``step_over`` skips it (pausing with reason ``step``) and the run completes after the
  next ``resume``.
- ``busy``: ``run`` answers ``busy``.
- ``slow``: the run goes on for a minute unless ``stop`` arrives.
- ``deaf``: like ``slow``, but ``stop`` is acknowledged and then ignored.
- ``pause``: the run pauses after its first step until ``resume``.
- ``crash_load``: writes a traceback to stderr and exits with code 3 on ``load``.
- ``crash_run``: dies the same way after the first step of the run.
- ``crash_held``: like ``crash_run``, after reporting ``shift``, ``char:a`` and
  ``button:left`` as held with ``input_state``.
- ``debug``: ``load`` reports the step tree of :data:`DEBUG_STEPS` and the debugging
  commands work: in ``step`` mode the run pauses before ``open_menu``; every stepping
  command runs one step and pauses before the next (reason ``step``); ``resume`` runs
  until a client breakpoint (``breakpoint_hit``) or the end; ``get_state``,
  ``evaluate`` (a variable name or a JSON literal), ``set_variable`` (a ``value``, or
  an ``expression`` read the same way) and ``set_breakpoints`` answer from a small
  variable table with a masked secret.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any

from j5_macro.protocol import Command, ErrorInfo, Event, Response, decode, encode

START = time.monotonic()

STEP_TYPES = [
    {"name": "click", "doc": "Click on an image or at coordinates.", "params": [
        {"name": "image", "type": "string", "required": False, "doc": "Template to find."},
        {"name": "button", "type": "string", "required": False, "default": "left"},
    ]},
    {"name": "log", "doc": "Write a message to the run log.", "params": [
        {"name": "message", "type": "string", "required": True},
    ]},
]


DEBUG_STEPS = [
    {"id": "open_menu", "type": "click", "path": [0], "line": 2, "params": {"x": 10, "y": 20}},
    {"id": "repeat", "type": "loop", "path": [1], "line": 4, "params": {"times": 2},
     "blocks": {"do": [{"id": "bump", "type": "set", "path": [1, 0, 0], "line": 7,
                        "params": {"name": "count", "expr": "count + 1"}}]}},
    {"id": "save", "type": "key", "path": [2], "line": 9, "params": {"press": "ctrl+s"},
     "break": True},
]
DEBUG_ORDER = ["open_menu", "repeat", "bump", "save"]
"""Where the ``debug`` run pauses, in order (one iteration of ``repeat``)."""
DEBUG_NODES = {"open_menu": DEBUG_STEPS[0], "repeat": DEBUG_STEPS[1],
               "bump": DEBUG_STEPS[1]["blocks"]["do"][0], "save": DEBUG_STEPS[2]}


class FakeCliEngine:
    def __init__(self, scenario: str, record: str | None) -> None:
        self.scenario = scenario
        self.record = record
        self.greeted = False
        self.lock = threading.Lock()
        self.seqs: dict[str | None, int] = {}
        self.run_start = START
        self.stop_requested = threading.Event()
        self.resumed = threading.Event()
        self.controls: queue.Queue[str] = queue.Queue()
        """``resume`` and ``step_over`` commands, in arrival order."""
        self.runner: threading.Thread | None = None
        self.variables: dict[str, Any] = {"count": 1, "password": "***"}
        self.breakpoints: set[str] = set()
        self.position = 0
        """``debug``: index in DEBUG_ORDER of the step the run is at."""
        self.state = "running"

    # -- output ---------------------------------------------------------------

    def write(self, line: str) -> None:
        with self.lock:
            sys.stdout.buffer.write(line.encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()

    def event(self, type_: str, run_id: str | None = None,
              payload: dict[str, Any] | None = None) -> None:
        with self.lock:
            seq = self.seqs.get(run_id, 0) + 1
            self.seqs[run_id] = seq
        origin = START if run_id is None else self.run_start
        self.write(encode(Event(type=type_, seq=seq, run_id=run_id, payload=payload or {},
                                ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
                                t_ms=int((time.monotonic() - origin) * 1000))))

    def ok(self, command: Command, result: dict[str, Any] | None = None) -> None:
        self.write(encode(Response(type=command.type, id=command.id, ok=True,
                                   result=result or {})))

    def error(self, command: Command, code: str, message: str,
              details: dict[str, Any] | None = None) -> None:
        self.write(encode(Response(type=command.type, id=command.id, ok=False,
                                   error=ErrorInfo(code=code, message=message,
                                                   details=details))))

    def crash(self) -> None:
        sys.stderr.write("Traceback (most recent call last):\nRuntimeError: boom\n")
        sys.stderr.flush()
        os._exit(3)

    # -- input ----------------------------------------------------------------

    def serve(self) -> int:
        self.event("ready", payload={"v": 1, "backend": "fake", "capabilities": {}})
        for raw in sys.stdin.buffer:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            message = decode(line)
            assert isinstance(message, Command)
            if self.record:
                with open(self.record, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"type": message.type,
                                             "payload": message.payload}) + "\n")
            self.handle(message)
        return 0

    def handle(self, command: Command) -> None:
        if command.type == "hello":
            self.greeted = True
            self.ok(command, {"v": 1, "capabilities": {
                "backend": "fake", "input_strategies": ["direct"], "step_types": ["log"]}})
        elif not self.greeted:
            self.error(command, "invalid_state", "send hello first")
        elif command.type == "load":
            self.load(command)
        elif command.type == "list_step_types":
            self.ok(command, {"step_types": STEP_TYPES})
        elif command.type == "run":
            self.start_run(command)
        elif command.type == "stop":
            self.ok(command)
            self.stop_requested.set()
            self.controls.put("stop")
        elif command.type == "resume":
            self.ok(command)
            self.resumed.set()
            self.controls.put("resume")
        elif command.type in ("step_over", "step_into", "step_out"):
            self.ok(command)
            self.controls.put(command.type)
        elif self.scenario == "debug" and command.type in (
                "get_state", "evaluate", "set_variable", "set_breakpoints"):
            self.debug_command(command)
        else:
            self.error(command, "invalid_state", f"{command.type} is not implemented",
                       {"reason": "not_implemented"})

    def load(self, command: Command) -> None:
        path = str(command.payload.get("path", ""))
        if self.scenario == "crash_load":
            self.crash()
        if not os.path.isabs(path):
            self.error(command, "validation_failed", f"path is not absolute: {path}")
        elif self.scenario == "not_found":
            self.error(command, "script_not_found", f"cannot open {path}")
        elif self.scenario == "invalid":
            errors = [
                {"code": "unknown_step_type", "message": "unknown step type 'clik'",
                 "step_id": "clik_1", "line": 4},
                {"code": "validation_failed", "message": "sleep: missing duration",
                 "line": 9},
            ]
            warnings = [{"message": "generated id 'sleep_2'", "step_id": "sleep_2",
                         "line": 9}]
            self.error(command, "unknown_step_type", errors[0]["message"],
                       {"errors": errors, "warnings": warnings})
        else:
            warnings: list[dict[str, Any]] = []
            if self.scenario == "warnings":
                warnings = [{"message": "generated id 'click_1'", "step_id": "click_1",
                             "line": 3},
                            {"message": "script has no name"}]
            steps = DEBUG_STEPS if self.scenario == "debug" else []
            self.ok(command, {"script_id": "s-1", "steps": steps, "warnings": warnings})

    # -- runs -----------------------------------------------------------------

    def start_run(self, command: Command) -> None:
        if self.scenario == "busy":
            self.error(command, "busy", "another run is active")
            return
        self.ok(command, {"run_id": "r-1"})
        self.run_start = time.monotonic()
        self.runner = threading.Thread(target=self.play, args=(dict(command.payload),),
                                       daemon=True)
        self.runner.start()

    def play(self, payload: dict[str, Any]) -> None:
        r = "r-1"
        seed = payload.get("seed", 1234)
        self.event("run_started", r, {"script_id": payload.get("script_id"),
                                      "script_name": "Save report",
                                      "mode": payload.get("mode"), "seed": seed,
                                      "backend": "fake",
                                      "input_profile": payload.get("input_profile") or {}})
        if self.scenario == "debug":
            self.play_debug(r, payload.get("mode") == "step")
            return
        self.event("state_changed", r, {"state": "running"})
        if self.scenario in ("slow", "deaf"):
            self.play_slow(r)
            return
        self.event("step_started", r, {"step_id": "open_menu", "path": [0], "line": 3,
                                       "type": "click", "params": {"x": 10, "y": 20}})
        if self.scenario == "crash_held":
            self.event("input_state", r, {"held": ["shift"]})
            self.event("input_state", r, {"held": ["shift", "char:a", "button:left"]})
        if self.scenario in ("crash_run", "crash_held"):
            self.crash()
        self.event("step_finished", r, {"step_id": "open_menu", "path": [0],
                                        "duration_ms": 12})
        if self.scenario == "pause":
            self.event("state_changed", r, {"state": "paused", "reason": "user"})
            self.resumed.wait(30)
            self.event("state_changed", r, {"state": "running", "reason": "user"})
        if self.scenario == "failed":
            self.event("step_started", r, {"step_id": "wait_dialog", "path": [1],
                                           "type": "wait_image",
                                           "params": {"image": "dialog.png"}})
            error = {"code": "image_not_found", "message": "dialog.png not found",
                     "step_id": "wait_dialog"}
            self.event("step_failed", r, {"step_id": "wait_dialog", "path": [1],
                                          "error": error})
            self.event("run_finished", r, {"status": "failed", "duration_ms": 1500,
                                           "error": error})
            return
        if self.scenario == "interference_pause":
            self.play_interference_pause(r)
            return
        if self.scenario == "interference":
            self.event("user_interference", r, {"expected": [1, 2], "actual": [5, 6],
                                                "action": "stop"})
            self.event("run_finished", r, {"status": "stopped", "reason": "interference",
                                           "duration_ms": 300})
            return
        self.event("loop_iteration", r, {"step_id": "repeat", "path": [1], "index": 0,
                                         "total": 2})
        self.event("branch_taken", r, {"step_id": "check", "path": [1, 0, 0],
                                       "iterations": [0], "condition": "count > 1",
                                       "value": False, "branch": "else"})
        self.event("step_started", r, {"step_id": "bump", "path": [1, 0, 0, 1, 0],
                                       "iterations": [0], "type": "set",
                                       "params": {"count": 2}})
        self.event("variable_changed", r, {"name": "count", "old": 1, "new": 2,
                                           "step_id": "bump"})
        self.event("step_finished", r, {"step_id": "bump", "path": [1, 0, 0, 1, 0],
                                        "duration_ms": 0})
        self.event("log", r, {"level": "info",
                              "message": "vars=" + json.dumps(payload.get("vars") or {},
                                                              sort_keys=True)})
        self.event("run_finished", r, {"status": "completed", "duration_ms": 2345})

    def play_interference_pause(self, r: str) -> None:
        step = {"step_id": "open_menu", "path": [0]}
        self.event("user_interference", r, {"expected": [10, 20], "actual": [300, 400],
                                            "action": "pause"})
        self.event("state_changed", r, {"state": "paused", "reason": "interference"})
        if self.controls.get(timeout=30) == "resume":
            self.event("state_changed", r, {"state": "running", "reason": "user"})
            self.event("step_started", r, {**step, "line": 3, "type": "click",
                                           "params": {"x": 10, "y": 20}})
            self.event("step_finished", r, {**step, "duration_ms": 12})
        else:
            self.event("state_changed", r, {"state": "running", "reason": "step"})
            self.event("step_skipped", r, {**step, "reason": "user"})
            self.event("state_changed", r, {"state": "paused", "reason": "step"})
            self.controls.get(timeout=30)
            self.event("state_changed", r, {"state": "running", "reason": "user"})
        self.event("run_finished", r, {"status": "completed", "duration_ms": 500})

    # -- debug ----------------------------------------------------------------

    def where(self) -> dict[str, Any]:
        node = DEBUG_NODES[DEBUG_ORDER[min(self.position, len(DEBUG_ORDER) - 1)]]
        place = {"step_id": node["id"], "path": node["path"], "line": node["line"]}
        if len(node["path"]) > 1:
            place["iterations"] = [0]
        return place

    def pause(self, r: str, reason: str) -> str:
        """Pause and wait for the next control command."""
        self.state = "paused"
        self.event("state_changed", r, {"state": "paused", "reason": reason})
        command = self.controls.get(timeout=30)
        if command != "stop":
            self.state = "running"
            self.event("state_changed", r, {"state": "running",
                                            "reason": "user" if command == "resume" else "step"})
        return command

    def play_debug(self, r: str, step_mode: bool) -> None:
        command = self.pause(r, "step") if step_mode else "resume"
        while command != "stop" and self.position < len(DEBUG_ORDER):
            place = self.where()
            node = DEBUG_NODES[place["step_id"]]
            self.event("step_started", r, {**place, "type": node["type"],
                                           "params": node["params"]})
            self.event("step_finished", r, {**place, "duration_ms": 1})
            self.position += 1
            if self.position >= len(DEBUG_ORDER):
                break
            following = self.where()
            if command != "resume":
                command = self.pause(r, "step")
            elif following["step_id"] in self.breakpoints:
                self.event("breakpoint_hit", r, following)
                command = self.pause(r, "breakpoint")
        if command == "stop":
            self.event("state_changed", r, {"state": "stopping", "reason": "user"})
            self.event("run_finished", r, {"status": "stopped", "reason": "user",
                                           "duration_ms": 50})
            return
        self.event("run_finished", r, {"status": "completed", "duration_ms": 60})

    def debug_command(self, command: Command) -> None:
        payload = command.payload
        if command.type == "set_breakpoints":
            self.breakpoints = set(payload.get("step_ids") or [])
            self.ok(command)
        elif command.type == "get_state":
            self.ok(command, {"run_id": "r-1", "state": self.state, **self.where(),
                              "variables": self.variables})
        elif self.state != "paused":
            self.error(command, "invalid_state", f"{command.type} needs a paused run")
        elif command.type == "evaluate":
            expression = str(payload.get("expression"))
            if expression in self.variables:
                self.ok(command, {"value": self.variables[expression]})
                return
            try:
                self.ok(command, {"value": json.loads(expression)})
            except ValueError:
                self.error(command, "expression_error",
                           f"unknown variable {expression!r} in expression {expression!r}",
                           {"expression": expression})
        else:  # set_variable
            name = str(payload.get("name"))
            value = payload.get("value")
            if "expression" in payload:
                expression = str(payload["expression"])
                if expression in self.variables:
                    value = self.variables[expression]
                else:
                    try:
                        value = json.loads(expression)
                    except ValueError:
                        self.error(command, "expression_error",
                                   f"unknown variable {expression!r} in expression "
                                   f"{expression!r}", {"expression": expression})
                        return
            old = self.variables.get(name)
            self.variables[name] = value
            self.event("variable_changed", "r-1", {"name": name, "old": old, "new": value})
            self.ok(command)

    def play_slow(self, r: str) -> None:
        for n in range(600):
            if self.stop_requested.wait(0.1):
                break
            self.event("step_started", r, {"step_id": f"wait_{n}", "path": [n],
                                           "type": "sleep", "params": {"duration": "100ms"}})
        else:
            self.event("run_finished", r, {"status": "completed", "duration_ms": 60000})
            return
        if self.scenario == "deaf":
            return
        self.event("state_changed", r, {"state": "stopping", "reason": "user"})
        self.event("run_finished", r, {"status": "stopped", "reason": "user",
                                       "duration_ms": 500})


def main() -> int:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "valid"
    record = sys.argv[2] if len(sys.argv) > 2 else None
    return FakeCliEngine(scenario, record).serve()


if __name__ == "__main__":
    sys.exit(main())
