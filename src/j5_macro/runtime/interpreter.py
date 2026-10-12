"""The interpreter: :func:`create_run` builds a :class:`~j5_macro.runtime.api.RunController`.

:class:`Interpreter` is the thread-safe facade the engine talks to. The script runs in
:class:`~j5_macro.runtime.machine.Machine` on the worker thread that calls :meth:`run`;
control commands from the protocol thread go through
:class:`~j5_macro.runtime.control.RunControl` (contract, "Run states and debugging
semantics").
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from j5_macro.expr import ExpressionError, evaluate
from j5_macro.input.api import SessionFactory
from j5_macro.protocol.errors import ErrorCode
from j5_macro.runtime.api import (
    ControlError,
    RunController,
    RunEventSink,
    RunOptions,
    RunOutcome,
)
from j5_macro.runtime.clock import Clock, MonotonicClock
from j5_macro.runtime.control import RunControl, RunStopped
from j5_macro.runtime.emitter import Emitter
from j5_macro.runtime.input import RunInput
from j5_macro.runtime.machine import Machine, failure_info
from j5_macro.runtime.resolve import ConditionFn
from j5_macro.script.model import Script
from j5_macro.steps.registry import StepRegistry


class Interpreter:
    """One run of one script (implements ``RunController``)."""

    def __init__(self, script: Script, registry: StepRegistry, emit: RunEventSink,
                 options: RunOptions, *, conditions: Mapping[str, ConditionFn] | None = None,
                 clock: Clock | None = None,
                 session_factory: SessionFactory | None = None) -> None:
        clock = clock or MonotonicClock()
        self._mode = options.mode
        self._events = Emitter(emit)
        self._control = RunControl(clock, paused=options.mode == "step",
                                   breakpoints=options.breakpoints)
        self._input = RunInput(backend=options.backend, reason=options.backend_reason,
                               dry_run=options.mode == "dry", emit=emit, control=self._control,
                               factory=session_factory,
                               base=(script.input, options.input_profile))
        self._machine = Machine(script, registry, self._events, self._control, clock, options,
                                dict(conditions or {}), self._input)

    @property
    def backend(self) -> str | None:
        """The input backend's name (``run_started.backend``), None without one."""
        backend = self._input.backend
        return None if backend is None else backend.name

    def run(self) -> RunOutcome:
        if self._mode == "step":
            self._events.state("paused", "step")
        else:
            self._events.state("running")
        try:
            self._machine.execute()
        except RunStopped as stopped:
            self._events.state("stopping", stopped.reason)
            return RunOutcome(status="stopped", reason=stopped.reason)
        except Exception as exc:
            return RunOutcome(status="failed", error=failure_info(exc))
        finally:
            self._release()
        return RunOutcome(status="completed")

    def _release(self) -> None:
        """Release every key and button still held, however the run ended."""
        try:
            self._input.release()
        except Exception as exc:
            self._events.log("error", f"could not release held keys and buttons: {exc}")

    def pause(self) -> None:
        self._control.request_pause()

    def resume(self) -> None:
        self._control.send("resume")

    def step_over(self) -> None:
        self._control.send("step_over")

    def step_into(self) -> None:
        self._control.send("step_into")

    def step_out(self) -> None:
        self._control.send("step_out")

    def stop(self, reason: Literal["user", "interference"] = "user") -> None:
        self._control.stop(reason)

    def set_breakpoints(self, step_ids: Iterable[str]) -> None:
        self._control.set_breakpoints(step_ids)

    def state(self) -> dict[str, Any]:
        with self._control.lock:
            step = self._machine.position
            snapshot: dict[str, Any] = {"state": self._control.state,
                                        "variables": dict(self._machine.variables)}
            if step is not None:
                snapshot.update(step_id=step.id, path=list(step.path))
                if step.line is not None:
                    snapshot["line"] = step.line
            return snapshot

    def set_variable(self, name: str, value: Any) -> None:
        with self._control.lock:
            self._control.require_paused("set_variable")
            self._machine.assign(name, value, None)

    def evaluate(self, expression: str) -> Any:
        with self._control.lock:
            self._control.require_paused("evaluate")
            variables = dict(self._machine.variables)
        try:
            return evaluate(expression, variables)
        except ExpressionError as exc:
            error = ControlError(ErrorCode.EXPRESSION_ERROR, exc.info.message)
            error.info = exc.info
            raise error from None


def create_run(script: Script, registry: StepRegistry, emit: RunEventSink,
               options: RunOptions, *, conditions: Mapping[str, ConditionFn] | None = None,
               clock: Clock | None = None,
               session_factory: SessionFactory | None = None) -> RunController:
    """Create a run (the engine's ``RunFactory``).

    ``conditions`` are the predefined conditions (``image_visible`` ...), empty in M1;
    ``clock`` replaces the real clock in tests. ``session_factory`` creates the run's
    input session when ``options.backend`` is set and the mode is not ``dry``; it
    defaults to ``j5_macro.input.session.create_session``, imported on first use.
    """
    return Interpreter(script, registry, emit, options, conditions=conditions, clock=clock,
                       session_factory=session_factory)
