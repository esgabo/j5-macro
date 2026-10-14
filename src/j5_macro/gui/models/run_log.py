"""The live log of a run: one line per event, the run state and the current step.

:class:`RunLog` is fed the events of one run in ``seq`` order (docs/contract.md,
"Events") and keeps what the run panel shows. Event types it does not know still get a
line, so nothing the engine says is hidden.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from j5_macro.protocol import Event

IDLE = "idle"
FINISHED = "finished"
"""States of :attr:`RunLog.state` besides the engine's ``running``, ``paused`` and
``stopping``."""


@dataclass(frozen=True, slots=True)
class LogLine:
    seq: int
    type: str
    text: str
    level: str = "info"
    """``debug``, ``info``, ``warning`` or ``error``: how to show it."""
    step_id: str | None = None
    event: Event | None = None
    """The event the line comes from, for panels that show more of it."""


@dataclass(slots=True)
class RunLog:
    run_id: str | None = None
    state: str = IDLE
    """``idle`` before the run, then the engine's ``running``, ``paused`` or
    ``stopping``, and ``finished`` after ``run_finished``."""
    pause_reason: str | None = None
    current_step_id: str | None = None
    """The step being run or paused at (the last ``step_started``); None outside one."""
    status: str | None = None
    """``run_finished``'s ``status``: ``completed``, ``stopped`` or ``failed``."""
    finished: Mapping[str, Any] | None = None
    """The whole ``run_finished`` payload."""
    lines: list[LogLine] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.run_id is not None and self.state != FINISHED

    def apply(self, event: Event) -> LogLine:
        """Record one event of the run and return its line."""
        payload = event.payload
        if event.type == "run_started":
            self.run_id = event.run_id
            self.state = "running"
        elif event.type == "state_changed":
            self.state = str(payload.get("state", self.state))
            reason = payload.get("reason")
            self.pause_reason = str(reason) if self.state == "paused" and reason else None
        elif event.type == "step_started":
            self.current_step_id = _step_id(payload)
        elif event.type == "run_finished":
            self.state = FINISHED
            self.current_step_id = None
            self.pause_reason = None
            status = payload.get("status")
            self.status = str(status) if status is not None else None
            self.finished = dict(payload)
        line = LogLine(seq=event.seq, type=event.type, text=format_event(event),
                       level=event_level(event), step_id=_step_id(payload), event=event)
        self.lines.append(line)
        return line


def event_level(event: Event) -> str:
    payload = event.payload
    if event.type == "log":
        level = payload.get("level")
        return str(level) if level in ("debug", "info", "warning", "error") else "info"
    if event.type in ("step_failed", "user_interference"):
        return "error"
    if event.type == "run_finished":
        status = payload.get("status")
        return "info" if status == "completed" else "error" if status == "failed" else "warning"
    if event.type in ("image_search", "input_planned", "input_executed", "input_state",
                      "loop_iteration"):
        return "debug"
    return "info"


def format_event(event: Event) -> str:
    """A one-line description of an event."""
    formatter = _FORMATTERS.get(event.type)
    if formatter is not None:
        return formatter(event.payload)
    return f"{event.type} {_json(event.payload)}" if event.payload else event.type


def _run_started(p: Mapping[str, Any]) -> str:
    return (f"run started: {p.get('script_name', '')} ({p.get('mode', '')} mode, seed "
            f"{p.get('seed')}, backend {p.get('backend')})")


def _state_changed(p: Mapping[str, Any]) -> str:
    reason = p.get("reason")
    return f"state: {p.get('state')}" + (f" ({reason})" if reason else "")


def _step_started(p: Mapping[str, Any]) -> str:
    return f"▶ {p.get('step_id')} ({p.get('type')})"


def _step_finished(p: Mapping[str, Any]) -> str:
    text = f"✓ {p.get('step_id')} ({p.get('duration_ms', 0)} ms)"
    result = p.get("result")
    if isinstance(result, Mapping) and result.get("dry_run") and result.get("description"):
        text += f": {result['description']}"
    return text


def _step_failed(p: Mapping[str, Any]) -> str:
    error = p.get("error")
    message = error.get("message") if isinstance(error, Mapping) else error
    return f"✗ {p.get('step_id')}: {message}"


def _step_skipped(p: Mapping[str, Any]) -> str:
    return f"↷ {p.get('step_id')} skipped ({p.get('reason', 'user')})"


def _log(p: Mapping[str, Any]) -> str:
    return f"[{p.get('level', 'info')}] {p.get('message', '')}"


def _image_search(p: Mapping[str, Any]) -> str:
    found = "found" if p.get("found") else "not found"
    score = p.get("best_score")
    score_text = f"{score:.3f}" if isinstance(score, int | float) else "-"
    return (f"image {p.get('template')}: {found} (score {score_text}, threshold "
            f"{p.get('threshold')}, attempt {p.get('attempt')})")


def _breakpoint_hit(p: Mapping[str, Any]) -> str:
    return f"breakpoint at {p.get('step_id')}"


def _branch_taken(p: Mapping[str, Any]) -> str:
    return (f"{p.get('step_id')}: {p.get('condition')} is {_json(p.get('value'))}, "
            f"{p.get('branch')}")


def _loop_iteration(p: Mapping[str, Any]) -> str:
    index = p.get("index")
    total = p.get("total")
    number = index + 1 if isinstance(index, int) else index
    return f"{p.get('step_id')}: iteration {number}" + (f" of {total}" if total else "")


def _variable_changed(p: Mapping[str, Any]) -> str:
    return f"{p.get('name')} = {_json(p.get('new'))}"


def _run_finished(p: Mapping[str, Any]) -> str:
    duration = p.get("duration_ms")
    text = f"run {p.get('status')}"
    if isinstance(duration, int):
        text += f" in {duration / 1000:.1f} s"
    reason = p.get("reason")
    if reason:
        text += f" ({reason})"
    warnings = p.get("warnings")
    if isinstance(warnings, int) and warnings:
        text += f", {warnings} warning{'s' if warnings != 1 else ''}"
    error = p.get("error")
    if isinstance(error, Mapping) and error.get("message"):
        text += f": {error['message']}"
    return text


_FORMATTERS: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "run_started": _run_started,
    "state_changed": _state_changed,
    "step_started": _step_started,
    "step_finished": _step_finished,
    "step_failed": _step_failed,
    "step_skipped": _step_skipped,
    "log": _log,
    "image_search": _image_search,
    "breakpoint_hit": _breakpoint_hit,
    "branch_taken": _branch_taken,
    "loop_iteration": _loop_iteration,
    "variable_changed": _variable_changed,
    "run_finished": _run_finished,
}


def _step_id(payload: Mapping[str, Any]) -> str | None:
    step_id = payload.get("step_id")
    return step_id if isinstance(step_id, str) else None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
