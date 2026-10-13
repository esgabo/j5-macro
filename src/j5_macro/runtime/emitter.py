"""Builds run events with the typed payload classes and hands them to the engine's sink.

Building every payload from ``j5_macro.protocol.payloads.events`` keeps the interpreter
honest: a payload that does not fit the contract fails here, not in a client.
"""

from __future__ import annotations

from typing import Any, Literal

from j5_macro.protocol.messages import ErrorInfo
from j5_macro.protocol.payloads.codec import Payload
from j5_macro.protocol.payloads.commands import RunState
from j5_macro.protocol.payloads.events import (
    BranchTakenEvent,
    BreakpointHitEvent,
    LogEvent,
    LogLevel,
    LoopIterationEvent,
    StateChangedEvent,
    StateReason,
    StepFailedEvent,
    StepFinishedEvent,
    StepSkippedEvent,
    StepStartedEvent,
    VariableChangedEvent,
)
from j5_macro.runtime.api import RunEventSink
from j5_macro.script.model import Step

Iterations = list[int] | None


class Emitter:
    """One method per event the interpreter emits."""

    def __init__(self, sink: RunEventSink) -> None:
        self._sink = sink

    def _send(self, type_: str, payload: Payload) -> None:
        self._sink(type_, payload.to_json())

    @staticmethod
    def _where(step: Step, iterations: Iterations) -> dict[str, Any]:
        return {"step_id": step.id, "path": list(step.path), "iterations": iterations,
                "line": step.line}

    def state(self, state: RunState, reason: StateReason | None = None) -> None:
        self._send("state_changed", StateChangedEvent(state=state, reason=reason))

    def step_started(self, step: Step, its: Iterations, params: dict[str, Any]) -> None:
        self._send("step_started", StepStartedEvent(**self._where(step, its), type=step.type,
                                                    params=params))

    def step_finished(self, step: Step, its: Iterations, duration_ms: int,
                      result: dict[str, Any] | None) -> None:
        self._send("step_finished", StepFinishedEvent(**self._where(step, its),
                                                      duration_ms=duration_ms, result=result))

    def step_failed(self, step: Step, its: Iterations, error: ErrorInfo,
                    screenshot: str | None = None) -> None:
        self._send("step_failed", StepFailedEvent(**self._where(step, its), error=error,
                                                  screenshot=screenshot))

    def step_skipped(self, step: Step, its: Iterations,
                     reason: Literal["user"]) -> None:
        self._send("step_skipped", StepSkippedEvent(**self._where(step, its), reason=reason))

    def breakpoint_hit(self, step: Step, its: Iterations) -> None:
        self._send("breakpoint_hit", BreakpointHitEvent(**self._where(step, its)))

    def branch_taken(self, step: Step, its: Iterations, condition: Any, value: bool,
                     branch: Literal["then", "else"]) -> None:
        self._send("branch_taken", BranchTakenEvent(**self._where(step, its),
                                                    condition=condition, value=value,
                                                    branch=branch))

    def loop_iteration(self, step: Step, its: Iterations, index: int,
                       total: int | None) -> None:
        self._send("loop_iteration", LoopIterationEvent(**self._where(step, its), index=index,
                                                        total=total))

    def variable_changed(self, name: str, old: Any, new: Any, step_id: str | None) -> None:
        self._send("variable_changed", VariableChangedEvent(name=name, old=old, new=new,
                                                            step_id=step_id))

    def log(self, level: LogLevel, message: str, step_id: str | None = None) -> None:
        self._send("log", LogEvent(level=level, message=message, step_id=step_id))
