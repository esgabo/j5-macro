"""Run lifecycle: ``run``, the control commands and run-level ``get_events``
(docs/contract.md, "Commands", "Events" and "Run states and debugging semantics").

The engine owns everything about a run that is visible on the wire or on disk (see
:mod:`j5_macro.runtime.api`): the ``run_id``, event stamping, ``run_started`` and
``run_finished``, persistence and the one-run-at-a-time rule. The interpreter, reached
through a :data:`~j5_macro.runtime.api.RunFactory`, executes the script on a worker
thread and reports through the event sink.
"""

from __future__ import annotations

import contextlib
import importlib
import secrets
import threading
import time
import traceback
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from j5_macro.engine.backend import EngineBackend
from j5_macro.engine.dispatch import CommandError
from j5_macro.engine.events import EventSequencer, format_ts, utc_now
from j5_macro.engine.persistence import RUNS_KEPT, RunFiles, RunStore
from j5_macro.engine.scripts import LoadedScript, ScriptStore
from j5_macro.engine.server import EngineServer
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import Command, ErrorInfo, Event, JsonObject, encode
from j5_macro.protocol.payloads import (
    EvaluatePayload,
    GetEventsPayload,
    RunPayload,
    RunRef,
    SetBreakpointsPayload,
    SetVariablePayload,
    StopPayload,
)
from j5_macro.runtime.api import ControlError, RunController, RunFactory, RunOptions, RunOutcome

SHUTDOWN_TIMEOUT_S = 5.0
"""How long end of input waits for an active run to finish after stopping it."""

NO_BACKEND = "none"
"""``run_started.backend`` when there is no input backend."""

STOP_REASONS = ("user", "interference")

INTERPRETER_MODULE = "j5_macro.runtime.interpreter"
"""Provides ``create_run``, the default :data:`~j5_macro.runtime.api.RunFactory`."""


def new_run_id(now: datetime | None = None) -> str:
    """``r-YYYYMMDD-HHMMSS-<4 hex>``, from the UTC start time."""
    moment = (now or utc_now()).astimezone(UTC)
    return f"r-{moment:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def new_seed() -> int:
    """A random non-negative seed that fits in a signed 32-bit integer."""
    return secrets.randbits(31)


def default_run_factory() -> RunFactory:
    """The interpreter's ``create_run``, imported lazily so the engine starts without it."""
    try:
        interpreter = importlib.import_module(INTERPRETER_MODULE)
    except ImportError as exc:
        raise CommandError(
            ErrorCode.INTERNAL_ERROR,
            f"the interpreter is not available (j5_macro.runtime.interpreter): {exc}",
        ) from exc
    factory = getattr(interpreter, "create_run", None)
    if factory is None:
        raise CommandError(ErrorCode.INTERNAL_ERROR,
                           "j5_macro.runtime.interpreter has no 'create_run'")
    return cast(RunFactory, factory)


def merge_profile(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """The effective input profile: ``override`` on top of the script's ``input``, merged
    per device (``mouse``, ``keyboard``) so overriding one key keeps the others."""
    merged = {key: dict(value) if isinstance(value, dict) else value
              for key, value in base.items()}
    for key, value in (override or {}).items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**current, **value}
        else:
            merged[key] = value
    return merged


class Run:
    """One run: its event stream, its controller and its files."""

    def __init__(self, server: EngineServer, run_id: str, entry: LoadedScript,
                 options: RunOptions, files_meta: dict[str, Any]) -> None:
        self.server = server
        self.run_id = run_id
        self.script_id = entry.script_id
        self.options = options
        self.sequencer = EventSequencer(run_id)
        self.events: list[Event] = []
        self.controller: RunController | None = None
        self.files: RunFiles | None = None
        self.meta = files_meta
        self.finished = threading.Event()
        self._lock = threading.Lock()
        self._started = False
        self._closed = False
        self._pending: list[tuple[str, JsonObject]] = []
        self._start_ns = 0
        self._persist_failed = False

    # -- events -----------------------------------------------------------

    def sink(self, type: str, payload: dict[str, Any]) -> None:
        """The :data:`~j5_macro.runtime.api.RunEventSink` handed to the interpreter."""
        with self._lock:
            if self._closed:
                dropped = True
            elif not self._started:
                # Emitted while the controller was being created: published right
                # after run_started, which is always the first event of a run.
                self._pending.append((type, payload))
                return
            else:
                dropped = False
                self._publish(type, payload)
        if dropped:
            self.server.log("warning",
                            f"run {self.run_id}: dropped '{type}' emitted after run_finished")

    def start(self, payload: JsonObject) -> None:
        """Emit ``run_started`` and then anything the controller emitted while created."""
        with self._lock:
            self._start_ns = time.monotonic_ns()
            self._started = True
            self._publish("run_started", payload)
            pending, self._pending = self._pending, []
            for type, event_payload in pending:
                self._publish(type, event_payload)

    def finish(self, outcome: RunOutcome) -> JsonObject:
        """Emit ``run_finished`` (the last event of the run); returns its payload."""
        with self._lock:
            duration_ms = max(0, (time.monotonic_ns() - self._start_ns) // 1_000_000)
            payload: JsonObject = {"status": outcome.status, "duration_ms": duration_ms}
            if outcome.reason is not None:
                payload["reason"] = outcome.reason
            if outcome.error is not None:
                payload["error"] = outcome.error.to_json()
            self._publish("run_finished", payload)
            self._closed = True
        return payload

    def events_from(self, from_seq: int) -> list[Event]:
        with self._lock:
            return [event for event in self.events if event.seq >= from_seq]

    def _publish(self, type: str, payload: JsonObject) -> None:
        """Stamp, buffer, persist and write one event. Called with ``_lock`` held, so
        the wire order and the file order both match ``seq``."""
        event = self.sequencer.next(type, payload)
        line = encode(event)
        self.events.append(event)
        if self.files is not None and not self._persist_failed:
            try:
                self.files.append(line)
            except (OSError, ValueError) as exc:
                self._persist_failed = True
                self.server.log("warning", f"run {self.run_id}: cannot write "
                                f"{self.files.events_path}: {exc}; events are no longer saved")
        # If the client is gone, end of input will stop the run.
        with contextlib.suppress(OSError):
            self.server.transport.write_line(line)


class RunManager:
    """The active run (at most one) and the finished runs of this engine session."""

    def __init__(self, server: EngineServer, scripts: ScriptStore,
                 run_factory: RunFactory | None, runs_dir: Path | None,
                 *, keep: int = RUNS_KEPT, backend: EngineBackend | None = None) -> None:
        self.server = server
        self.backend = backend
        """The engine's input backend, handed to every run; None when not configured."""
        self.scripts = scripts
        self._factory = run_factory
        self.store = RunStore(runs_dir, keep) if runs_dir is not None else None
        self.keep = keep
        self.shutdown_timeout = SHUTDOWN_TIMEOUT_S
        self.active: Run | None = None
        self.runs: OrderedDict[str, Run] = OrderedDict()
        self._lock = threading.Lock()

    # -- lookup -----------------------------------------------------------

    def get(self, run_id: str) -> Run:
        """Any run of this session (active or finished); ``not_running`` otherwise."""
        with self._lock:
            run = self.runs.get(run_id)
        if run is None:
            raise CommandError(ErrorCode.NOT_RUNNING, f"unknown run_id: {run_id!r}",
                               {"run_id": run_id})
        return run

    def active_run(self) -> Run | None:
        with self._lock:
            return self.active

    def controller(self, run_id: str) -> RunController:
        """The controller of the active run ``run_id``; ``not_running`` otherwise."""
        run = self.active_run()
        if run is None or run.run_id != run_id or run.controller is None:
            if run is None:
                message = f"no active run (got run_id {run_id!r})"
            else:
                message = f"run {run_id!r} is not the active run ({run.run_id})"
            raise CommandError(ErrorCode.NOT_RUNNING, message, {"run_id": run_id})
        return run.controller

    # -- starting and finishing -------------------------------------------

    def start(self, payload: RunPayload) -> Run:
        entry = self.scripts.get(payload.script_id)
        with self._lock:
            if self.active is not None:
                raise CommandError(ErrorCode.BUSY,
                                   f"run {self.active.run_id} is still active",
                                   {"run_id": self.active.run_id})
            started = utc_now()
            run_id = self._unique_run_id(started)
            seed = payload.seed if payload.seed is not None else new_seed()
            options = RunOptions(
                mode=payload.mode,
                seed=seed,
                variables=dict(payload.vars or {}),
                breakpoints=entry.breakpoints,
                input_profile=payload.input_profile,
                captures=payload.captures,
                # Created by _create_files before the run starts; artifacts (captures)
                # are only written there if that succeeded.
                run_dir=None if self.store is None else self.store.root / run_id,
                backend=None if self.backend is None else self.backend.backend,
                backend_reason=None if self.backend is None else self.backend.reason,
            )
            meta: JsonObject = {
                "run_id": run_id,
                "script_id": entry.script_id,
                "script_name": entry.script.name,
                "script_path": None if entry.path is None else str(entry.path),
                "mode": payload.mode,
                "seed": seed,
                "started": format_ts(started),
            }
            run = Run(self.server, run_id, entry, options, meta)
            factory = self._factory if self._factory is not None else default_run_factory()
            try:
                run.controller = factory(entry.script, self.scripts.registry, run.sink, options)
            except ControlError as exc:
                raise CommandError.from_info(exc.info) from exc
            run_started: JsonObject = {
                "script_id": entry.script_id,
                "mode": payload.mode,
                "seed": seed,
                "backend": self._backend_name(run.controller),
                "input_profile": merge_profile(entry.script.input, payload.input_profile),
            }
            self.active = run
            self.runs[run_id] = run
            self._forget_old_runs()
        # Outside the lock: persistence may be slow, and failures only log.
        run.files = self._create_files(run)
        run.start(run_started)
        return run

    def _backend_name(self, controller: RunController) -> str:
        """``run_started.backend``: the controller's backend name if it reports one, else
        the engine's, else ``none``."""
        named = getattr(controller, "backend", None)
        if isinstance(named, str) and named:
            return named
        if self.backend is not None and self.backend.name:
            return self.backend.name
        return NO_BACKEND

    def stop_active(self, message: str) -> str | None:
        """Stop the active run with reason ``user`` after logging ``message`` in it (the
        emergency stop key). Returns its run_id, or None when no run is active."""
        run = self.active_run()
        if run is None or run.controller is None:
            return None
        run.sink("log", {"level": "warning", "message": message})
        run.controller.stop("user")
        return run.run_id

    def launch(self, run: Run) -> None:
        """Start the run's worker thread."""
        thread = threading.Thread(target=self._work, args=(run,),
                                  name=f"j5-run-{run.run_id}", daemon=True)
        thread.start()

    def _work(self, run: Run) -> None:
        assert run.controller is not None
        try:
            outcome = run.controller.run()
        except Exception as exc:
            outcome = RunOutcome(status="failed", error=ErrorInfo(
                code=ErrorCode.INTERNAL_ERROR.value,
                message=f"the interpreter raised: {exc}",
                details={"traceback": traceback.format_exc()},
            ))
        try:
            with self._lock:
                finished = run.finish(outcome)
                if self.active is run:
                    self.active = None
            self._finish_files(run, finished)
        finally:
            run.finished.set()

    def _unique_run_id(self, started: datetime) -> str:
        while True:
            run_id = new_run_id(started)
            if run_id not in self.runs and (
                    self.store is None or not (self.store.root / run_id).exists()):
                return run_id

    def _forget_old_runs(self) -> None:
        while len(self.runs) > self.keep:
            oldest = next(iter(self.runs))
            if self.runs[oldest] is self.active:
                break
            del self.runs[oldest]

    # -- persistence (never fatal) ----------------------------------------

    def _create_files(self, run: Run) -> RunFiles | None:
        if self.store is None:
            return None
        try:
            self.store.prune(protect=frozenset({run.run_id}), room_for=0)
        except OSError as exc:
            self.server.log("warning", f"cannot prune old runs in {self.store.root}: {exc}")
        try:
            return self.store.create(run.run_id, run.meta)
        except OSError as exc:
            self.server.log("warning", f"run {run.run_id}: cannot create its folder in "
                            f"{self.store.root}: {exc}; this run is not saved")
            return None

    def _finish_files(self, run: Run, finished: JsonObject) -> None:
        if run.files is None:
            return
        fields: JsonObject = {
            "finished": format_ts(utc_now()),
            "status": finished["status"],
            "reason": finished.get("reason"),
            "duration_ms": finished["duration_ms"],
        }
        if "error" in finished:
            fields["error"] = finished["error"]
        try:
            run.files.finish(fields)
        except OSError as exc:
            self.server.log("warning", f"run {run.run_id}: cannot write "
                            f"{run.files.meta_path}: {exc}")

    # -- shutdown ---------------------------------------------------------

    def shutdown(self) -> None:
        """Stop the active run, if any, and wait up to :attr:`shutdown_timeout` seconds
        for its ``run_finished`` and its files (also for a run that just finished)."""
        deadline = time.monotonic() + self.shutdown_timeout
        run = self.active_run()
        if run is not None and run.controller is not None:
            try:
                run.controller.stop("user")
            except Exception as exc:
                self.server.log("error", f"run {run.run_id}: stop failed at shutdown: {exc}")
        with self._lock:
            pending = [r for r in self.runs.values() if not r.finished.is_set()]
        for item in pending:
            if not item.finished.wait(max(0.0, deadline - time.monotonic())):
                self.server.log("error", f"run {item.run_id} did not finish within "
                                f"{self.shutdown_timeout:g} s of end of input; exiting anyway")


# --- handlers ---------------------------------------------------------------------------


def install(server: EngineServer, runs: RunManager) -> None:
    def forward(run_id: str, apply: Callable[[RunController], Any]) -> Any:
        """Apply a control command to the active run; ``ControlError`` becomes the
        error response."""
        controller = runs.controller(run_id)
        try:
            return apply(controller)
        except ControlError as exc:
            raise CommandError.from_info(exc.info) from exc

    def run(command: Command) -> JsonObject:
        active = runs.start(RunPayload.from_json(command.payload))
        server.after_response(lambda: runs.launch(active))
        return {"run_id": active.run_id}

    def simple(method: str) -> Callable[[Command], JsonObject]:
        def handler(command: Command) -> JsonObject:
            run_id = RunRef.from_json(command.payload).run_id
            forward(run_id, lambda controller: getattr(controller, method)())
            return {}
        return handler

    def stop(command: Command) -> JsonObject:
        payload = StopPayload.from_json(command.payload)
        reason = payload.reason if payload.reason is not None else "user"
        if reason not in STOP_REASONS:
            raise CommandError(ErrorCode.VALIDATION_FAILED,
                               f"payload.reason: expected one of {', '.join(STOP_REASONS)}",
                               {"field": "reason"})
        forward(payload.run_id,
                lambda c: c.stop(cast(Literal["user", "interference"], reason)))
        return {}

    def get_state(command: Command) -> JsonObject:
        run_id = RunRef.from_json(command.payload).run_id
        state: dict[str, Any] = forward(run_id, lambda c: dict(c.state()))
        return {**state, "run_id": run_id}

    def set_variable(command: Command) -> JsonObject:
        payload = SetVariablePayload.from_json(command.payload)
        forward(payload.run_id, lambda c: c.set_variable(payload.name, payload.value))
        return {}

    def evaluate(command: Command) -> JsonObject:
        payload = EvaluatePayload.from_json(command.payload)
        return {"value": forward(payload.run_id, lambda c: c.evaluate(payload.expression))}

    def set_breakpoints(command: Command) -> JsonObject:
        payload = SetBreakpointsPayload.from_json(command.payload)
        entry = runs.scripts.set_breakpoints(payload.script_id, payload.step_ids)
        active = runs.active_run()
        if active is not None and active.script_id == entry.script_id:
            forward(active.run_id, lambda c: c.set_breakpoints(entry.breakpoints))
        return {}

    def get_events(command: Command) -> JsonObject:
        payload = GetEventsPayload.from_json(command.payload)
        if payload.run_id is None:
            selected = server.buffered_events(payload.from_seq)
        else:
            selected = runs.get(payload.run_id).events_from(payload.from_seq)
        return {"events": [event.to_json() for event in selected]}

    server.register("run", run)
    for name in ("pause", "resume", "step_over", "step_into", "step_out"):
        server.register(name, simple(name))
    server.register("stop", stop)
    server.register("get_state", get_state)
    server.register("set_variable", set_variable)
    server.register("evaluate", evaluate)
    server.register("set_breakpoints", set_breakpoints)
    server.register("get_events", get_events)
    server.on_shutdown(runs.shutdown)
