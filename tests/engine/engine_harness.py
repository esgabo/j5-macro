"""In-process engine harness: a server on a thread, fed and read through the transport.

Commands go in through a blocking in-memory reader; every output line is decoded and
collected, so tests can wait for a response or an event with a timeout.
"""

from __future__ import annotations

import itertools
import json
import queue
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from j5_macro.engine import commands
from j5_macro.engine.backend import EngineBackend
from j5_macro.engine.overlay import OverlayFactory
from j5_macro.engine.runs import RunManager
from j5_macro.engine.server import EngineServer
from j5_macro.engine.transport import LineTransport
from j5_macro.protocol.errors import ErrorCode
from j5_macro.runtime.api import (
    ControlError,
    RunEventSink,
    RunFactory,
    RunOptions,
    RunOutcome,
)
from j5_macro.script.model import Script
from j5_macro.steps import StepRegistry, default_registry
from j5_macro.vision.api import ToolkitFactory

TIMEOUT_S = 5.0
HELLO = {"v_min": 1, "v_max": 1, "client": "tests"}

Message = dict[str, Any]


class QueueReader:
    """A ``readline`` that blocks until a line is fed; ``close`` means end of input."""

    def __init__(self) -> None:
        self._lines: queue.Queue[str] = queue.Queue()

    def feed(self, line: str) -> None:
        self._lines.put(line + "\n")

    def close(self) -> None:
        self._lines.put("")

    def readline(self) -> str:
        return self._lines.get(timeout=30)


class CollectingWriter:
    """Collects every written line, decoded, and lets tests wait for one."""

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self._buffer = ""
        self._cond = threading.Condition()

    def write(self, text: str, /) -> int:
        with self._cond:
            self._buffer += text
            *lines, self._buffer = self._buffer.split("\n")
            self.messages.extend(json.loads(line) for line in lines)
            self._cond.notify_all()
        return len(text)

    def flush(self) -> None:
        pass

    def wait_for(self, predicate: Callable[[Message], bool],
                 timeout: float = TIMEOUT_S) -> Message:
        with self._cond:
            found: list[Message] = []

            def ready() -> bool:
                found[:] = [m for m in self.messages if predicate(m)]
                return bool(found)

            if not self._cond.wait_for(ready, timeout):
                raise AssertionError("timed out waiting for a message; got:\n"
                                     + "\n".join(map(json.dumps, self.messages)))
            return found[0]

    def snapshot(self) -> list[Message]:
        with self._cond:
            return list(self.messages)


class Engine:
    """A served engine with helpers to send commands and wait for output."""

    def __init__(self, *, run_factory: RunFactory | None = None,
                 runs_dir: Path | None = None, registry: StepRegistry | None = None,
                 hello: bool = True, backend: EngineBackend | None = None,
                 toolkit_factory: ToolkitFactory | None = None,
                 overlay_factory: OverlayFactory | None = None) -> None:
        self.reader = QueueReader()
        self.writer = CollectingWriter()
        self.server = EngineServer(LineTransport(self.reader, self.writer))
        self.registry = registry if registry is not None else default_registry()
        self.runs: RunManager = commands.install(
            self.server, self.registry, run_factory=run_factory, runs_dir=runs_dir,
            backend=backend, toolkit_factory=toolkit_factory,
            overlay_factory=overlay_factory)
        self._ids = itertools.count(1)
        self.exit_code: int | None = None
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self.writer.wait_for(lambda m: m.get("type") == "ready")
        if hello:
            assert self.call("hello", HELLO)["ok"] is True

    def _serve(self) -> None:
        self.exit_code = self.server.serve()

    # -- commands ---------------------------------------------------------

    def send(self, type: str, payload: Any = None) -> str:
        id = f"c{next(self._ids)}"
        data: Message = {"v": 1, "kind": "command", "type": type, "id": id}
        if payload is not None:
            data["payload"] = payload
        self.reader.feed(json.dumps(data))
        return id

    def response(self, id: str) -> Message:
        return self.writer.wait_for(lambda m: m["kind"] == "response" and m["id"] == id)

    def call(self, type: str, payload: Any = None) -> Message:
        return self.response(self.send(type, payload))

    def ok(self, type: str, payload: Any = None) -> dict[str, Any]:
        resp = self.call(type, payload)
        assert resp["ok"] is True, resp
        result: dict[str, Any] = resp["result"]
        return result

    def error(self, type: str, payload: Any = None) -> dict[str, Any]:
        resp = self.call(type, payload)
        assert resp["ok"] is False, resp
        error: dict[str, Any] = resp["error"]
        return error

    # -- events -----------------------------------------------------------

    def event(self, type: str, run_id: str | None = None) -> Message:
        return self.writer.wait_for(lambda m: m["kind"] == "event" and m["type"] == type
                                    and (run_id is None or m["run_id"] == run_id))

    def run_events(self, run_id: str) -> list[Message]:
        return [m for m in self.writer.snapshot()
                if m["kind"] == "event" and m["run_id"] == run_id]

    def engine_events(self, type: str | None = None) -> list[Message]:
        return [m for m in self.writer.snapshot() if m["kind"] == "event"
                and m["run_id"] is None and (type is None or m["type"] == type)]

    def wait_finished(self, run_id: str) -> Message:
        return self.event("run_finished", run_id)

    # -- helpers ----------------------------------------------------------

    def load_source(self, source: str = "steps:\n  - id: hi\n    log: hello\n") -> str:
        script_id: str = self.ok("load", {"source": source})["script_id"]
        return script_id

    def start(self, script_id: str, **payload: Any) -> str:
        run_id: str = self.ok("run", {"script_id": script_id, "mode": "normal",
                                      **payload})["run_id"]
        return run_id

    def close(self, timeout: float = TIMEOUT_S) -> int | None:
        """End of input; waits for the server loop to return."""
        self.reader.close()
        self._thread.join(timeout)
        assert not self._thread.is_alive(), "the engine did not exit"
        return self.exit_code


# --- fake interpreter -------------------------------------------------------------------


@dataclass
class Behaviour:
    """What a fake run does."""

    outcome: RunOutcome = field(default_factory=lambda: RunOutcome(status="completed"))
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    """Emitted through the sink at the start of ``run``."""
    on_create: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    """Emitted through the sink while the factory runs."""
    block: bool = False
    """Block in ``run`` until :meth:`FakeController.release` or ``stop``."""
    ignore_stop: bool = False
    raises: Exception | None = None
    """Raised by ``run``."""
    control_error: ControlError | None = None
    """Raised by every control method."""
    state: dict[str, Any] = field(default_factory=lambda: {"state": "paused", "step_id": "hi",
                                                           "path": [0], "line": 2,
                                                           "variables": {"x": 1}})
    backend: str | None = None


class FakeController:
    def __init__(self, script: Script, registry: StepRegistry, sink: RunEventSink,
                 options: RunOptions, behaviour: Behaviour) -> None:
        self.script = script
        self.registry = registry
        self.sink = sink
        self.options = options
        self.behaviour = behaviour
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.running = threading.Event()
        self._release = threading.Event()
        self._stop_reason: Literal["user", "interference"] | None = None
        if behaviour.backend is not None:
            self.backend = behaviour.backend
        for type, payload in behaviour.on_create:
            sink(type, payload)

    def release(self) -> None:
        self._release.set()

    def run(self) -> RunOutcome:
        self.running.set()
        for type, payload in self.behaviour.events:
            self.sink(type, payload)
        if self.behaviour.raises is not None:
            raise self.behaviour.raises
        if self.behaviour.block:
            self._release.wait(timeout=30)
        if self._stop_reason is not None and not self.behaviour.ignore_stop:
            return RunOutcome(status="stopped", reason=self._stop_reason)
        return self.behaviour.outcome

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if self.behaviour.control_error is not None:
            raise self.behaviour.control_error

    def pause(self) -> None:
        self._record("pause")

    def resume(self) -> None:
        self._record("resume")

    def step_over(self) -> None:
        self._record("step_over")

    def step_into(self) -> None:
        self._record("step_into")

    def step_out(self) -> None:
        self._record("step_out")

    def stop(self, reason: Literal["user", "interference"] = "user") -> None:
        self._record("stop", reason)
        if not self.behaviour.ignore_stop:
            self._stop_reason = reason
            self._release.set()

    def set_breakpoints(self, step_ids: Iterable[str]) -> None:
        self._record("set_breakpoints", frozenset(step_ids))

    def state(self) -> dict[str, Any]:
        self._record("state")
        return dict(self.behaviour.state)

    def set_variable(self, name: str, value: Any) -> None:
        self._record("set_variable", name, value)

    def evaluate(self, expression: str) -> Any:
        self._record("evaluate", expression)
        return {"expr": expression}


class FakeFactory:
    """A :data:`RunFactory` that builds :class:`FakeController` s and remembers them."""

    def __init__(self, behaviour: Behaviour | None = None) -> None:
        self.behaviour = behaviour if behaviour is not None else Behaviour()
        self.controllers: list[FakeController] = []
        self.error: Exception | None = None

    def __call__(self, script: Script, registry: StepRegistry, sink: RunEventSink,
                 options: RunOptions) -> FakeController:
        if self.error is not None:
            raise self.error
        controller = FakeController(script, registry, sink, options, self.behaviour)
        self.controllers.append(controller)
        return controller

    @property
    def last(self) -> FakeController:
        return self.controllers[-1]


def control_error(message: str = "not paused") -> ControlError:
    return ControlError(ErrorCode.INVALID_STATE, message)
