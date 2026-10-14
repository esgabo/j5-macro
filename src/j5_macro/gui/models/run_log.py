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

from j5_macro.gui.models.debugger import PauseInfo
from j5_macro.protocol import Event

LEVELS = ("debug", "info", "warning", "error")
"""Line levels, least important first (the log filter shows a level and those after)."""
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
    pause: PauseInfo | None = None
    """Why and where the run is paused; None while it is not."""
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(LEVELS, 0))
    """Lines per level so far."""
    warnings: int = 0
    """``log`` events of level ``warning`` (what ``run_finished.warnings`` counts)."""
    mode: str | None = None
    seed: int | None = None
    _failure: Mapping[str, Any] | None = None
    _interference: Mapping[str, Any] | None = None

    @property
    def active(self) -> bool:
        return self.run_id is not None and self.state != FINISHED

    def apply(self, event: Event) -> LogLine:
        """Record one event of the run and return its line."""
        payload = event.payload
        if event.type == "run_started":
            self.run_id = event.run_id
            self.state = "running"
            mode, seed = payload.get("mode"), payload.get("seed")
            self.mode = str(mode) if isinstance(mode, str) else None
            self.seed = seed if isinstance(seed, int) else None
        elif event.type == "state_changed":
            self.state = str(payload.get("state", self.state))
            reason = payload.get("reason")
            self.pause_reason = str(reason) if self.state == "paused" and reason else None
            self.pause = self._pause_info() if self.state == "paused" else None
            if self.state != "paused":
                self._failure = self._interference = None
        elif event.type in ("step_started", "breakpoint_hit"):
            self.current_step_id = _step_id(payload)
        elif event.type == "step_failed":
            self._failure = dict(payload)
        elif event.type == "user_interference":
            self._interference = dict(payload)
        elif event.type == "run_finished":
            self.state = FINISHED
            self.current_step_id = None
            self.pause_reason = None
            self.pause = None
            status = payload.get("status")
            self.status = str(status) if status is not None else None
            self.finished = dict(payload)
            warnings = payload.get("warnings")
            if isinstance(warnings, int):
                self.warnings = warnings
        if event.type == "log" and payload.get("level") == "warning":
            self.warnings += 1
        line = LogLine(seq=event.seq, type=event.type, text=format_event(event),
                       level=event_level(event), step_id=_step_id(payload), event=event)
        self.lines.append(line)
        self.counts[line.level] = self.counts.get(line.level, 0) + 1
        return line

    def _pause_info(self) -> PauseInfo:
        reason = self.pause_reason
        error: Mapping[str, Any] | None = None
        screenshot: str | None = None
        step_id = self.current_step_id
        if reason == "error" and self._failure is not None:
            raw = self._failure.get("error")
            error = raw if isinstance(raw, Mapping) else {"message": str(raw)}
            shot = self._failure.get("screenshot")
            screenshot = shot if isinstance(shot, str) else None
            step_id = _step_id(self._failure) or step_id
        interference = self._interference if reason == "interference" else None
        return PauseInfo(reason=reason, step_id=step_id, error=error,
                         screenshot=screenshot, interference=interference)

    def summary(self) -> str:
        """One line about the run: its state, or how it ended (duration, warnings)."""
        return run_summary(self)


def level_visible(level: str, minimum: str) -> bool:
    """Whether a line of ``level`` shows with the filter at ``minimum``."""
    order = {name: index for index, name in enumerate(LEVELS)}
    return order.get(level, 1) >= order.get(minimum, 0)


def run_summary(log: RunLog) -> str:
    if log.run_id is None:
        return ""
    parts: list[str] = []
    if log.state == FINISHED and log.finished is not None:
        status = str(log.finished.get("status", ""))
        duration = log.finished.get("duration_ms")
        text = status.capitalize()
        reason = log.finished.get("reason")
        if reason:
            text += f" ({reason})"
        if isinstance(duration, int):
            text += f" in {duration / 1000:.1f} s"
        parts.append(text)
    else:
        parts.append(log.state.capitalize())
    if log.mode:
        parts.append(f"{log.mode} mode")
    parts.append(f"{log.warnings} warning{'s' if log.warnings != 1 else ''}")
    errors = log.counts.get("error", 0)
    if errors:
        parts.append(f"{errors} error line{'s' if errors != 1 else ''}")
    if log.seed is not None:
        parts.append(f"seed {log.seed}")
    return " · ".join(parts)


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


def _input_planned(p: Mapping[str, Any]) -> str:
    device = p.get("device")
    text = f"{device} plan ({p.get('strategy')})"
    if device == "keyboard":
        chars, wpm = p.get("chars"), p.get("wpm")
        if chars is not None:
            text += f": {chars} char{'s' if chars != 1 else ''}"
        if isinstance(wpm, int | float):
            text += f" at {wpm:.0f} wpm"
        return text
    start, end = _point(p.get("from")), _point(p.get("to"))
    if start and end:
        text += f": {start} → {end}"
    elif end:
        text += f": to {end}"
    duration = p.get("duration_ms")
    if isinstance(duration, int):
        text += f" in {duration} ms"
    return text


def _input_executed(p: Mapping[str, Any]) -> str:
    text = f"{p.get('device')} input done in {p.get('actual_duration_ms')} ms"
    return text + " (aborted)" if p.get("aborted") else text


def _user_interference(p: Mapping[str, Any]) -> str:
    return (f"the mouse was moved: expected at {_point(p.get('expected'))}, was at "
            f"{_point(p.get('actual'))} ({p.get('action')})")


def _keys_released(p: Mapping[str, Any]) -> str:
    keys = p.get("keys")
    names = ", ".join(str(k) for k in keys) if isinstance(keys, list) else ""
    return f"released {names} ({p.get('reason')})"


def _point(value: object) -> str:
    if isinstance(value, list | tuple) and len(value) == 2:
        return f"({value[0]}, {value[1]})"
    return ""


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
    "input_planned": _input_planned,
    "input_executed": _input_executed,
    "user_interference": _user_interference,
    "keys_released": _keys_released,
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
