"""A saved run in detail, as ``get_run`` reports it (docs/contract.md, "Run history"),
and the rows of the history list.

:class:`RunDetail` holds the run's ``run.json``, its events as log lines (formatted
like the live log, :mod:`j5_macro.gui.models.run_log`), its captures and, for each
image search that failed a step, a :class:`SearchFailure` that pairs the failure's
capture with the template it looked for, its best score and where the best match was
in the capture.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any

from j5_macro.gui.models.run_log import LogLine, RunLog
from j5_macro.gui.models.runs import RunSummary
from j5_macro.gui.models.screen import embedded_name, rect_within, resolve_file
from j5_macro.protocol import Event


def format_started(started: str, tz: tzinfo | None = None) -> str:
    """``2026-10-12T10:00:00.000Z`` as local time, ``2026-10-12 12:00:00``; the text as
    it is when it is not a timestamp."""
    try:
        moment = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError:
        return started
    if moment.tzinfo is None:
        return moment.strftime("%Y-%m-%d %H:%M:%S")
    return moment.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """One line of the history list."""

    run_id: str
    started: str
    mode: str
    status: str
    duration: str
    warnings: str
    level: str
    """``info`` (completed), ``warning`` (stopped, unfinished) or ``error`` (failed)."""

    @property
    def columns(self) -> list[str]:
        return [self.started, self.mode, self.status, self.duration, self.warnings,
                self.run_id]


HISTORY_COLUMNS = ["Started", "Mode", "Status", "Duration", "Warnings", "Run"]


def history_row(run: RunSummary, tz: tzinfo | None = None) -> HistoryRow:
    status = run.status or "(unfinished)"
    level = ("info" if run.status == "completed" else "error" if run.status == "failed"
             else "warning")
    warnings = "" if not run.warnings else str(run.warnings)
    return HistoryRow(run_id=run.run_id, started=format_started(run.started, tz),
                      mode=run.mode, status=status, duration=run.duration_text,
                      warnings=warnings, level=level)


@dataclass(frozen=True, slots=True)
class SearchFailure:
    """A failed image search: the capture saved when the step failed, paired with the
    template, the best score and the best location."""

    step_id: str
    template: str
    """As written in the script: a path (relative to the script) or ``#name``."""
    capture: str
    """Path relative to the run's directory (``captures/0001-find-3.png``)."""
    message: str = ""
    code: str = ""
    best_score: float | None = None
    threshold: float | None = None
    bbox: tuple[int, int, int, int] | None = None
    """The best location on the desktop."""
    region: tuple[int, int, int, int] | None = None
    """The searched rectangle on the desktop: the area the capture shows."""
    attempts: int | None = None

    @property
    def bbox_in_capture(self) -> tuple[int, int, int, int] | None:
        if self.bbox is None or self.region is None:
            return None
        return rect_within(self.bbox, self.region)

    @property
    def embedded(self) -> str | None:
        """The entry name for a ``#name`` template."""
        return embedded_name(self.template)

    def template_file(self, script_path: str | None) -> str | None:
        """The template's file, for a path template."""
        if self.embedded is not None or "${" in self.template:
            return None
        return resolve_file(self.template, script_path)

    @property
    def score_text(self) -> str:
        score = "-" if self.best_score is None else f"{self.best_score:.3f}"
        threshold = "" if self.threshold is None else f" (threshold {self.threshold:g})"
        return f"best score {score}{threshold}"


@dataclass(frozen=True, slots=True)
class RunDetail:
    run_id: str
    run: Mapping[str, Any]
    """The ``run.json`` fields."""
    directory: str | None
    events: tuple[Event, ...] = ()
    lines: tuple[LogLine, ...] = ()
    captures: tuple[str, ...] = ()
    failures: tuple[SearchFailure, ...] = ()
    capture_searches: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    """Capture path to the ``image_search`` payload it was saved by."""

    @classmethod
    def from_get_run(cls, result: Mapping[str, Any]) -> RunDetail:
        run_id = str(result.get("run_id", ""))
        meta = result.get("run")
        events = tuple(parse_events(result.get("events"), run_id))
        log = RunLog()
        lines = tuple(log.apply(event) for event in events)
        captures = result.get("captures")
        directory = result.get("dir")
        searches: dict[str, Mapping[str, Any]] = {}
        for event in events:
            shot = event.payload.get("screenshot")
            if event.type == "image_search" and isinstance(shot, str):
                searches[shot] = event.payload
        return cls(run_id=run_id, run=dict(meta) if isinstance(meta, Mapping) else {},
                   directory=directory if isinstance(directory, str) else None,
                   events=events, lines=lines,
                   captures=tuple(str(c) for c in captures)
                   if isinstance(captures, list) else (),
                   failures=tuple(search_failures(events)),
                   capture_searches=searches)

    @property
    def status(self) -> str | None:
        status = self.run.get("status")
        return status if isinstance(status, str) else None

    @property
    def script_path(self) -> str | None:
        path = self.run.get("script_path")
        return path if isinstance(path, str) else None

    @property
    def warnings(self) -> int:
        finished = next((e for e in reversed(self.events) if e.type == "run_finished"), None)
        count = finished.payload.get("warnings") if finished is not None else None
        if not isinstance(count, int):
            count = self.run.get("warnings")
        return count if isinstance(count, int) else 0

    def capture_path(self, capture: str) -> str | None:
        """The absolute path of a capture."""
        if self.directory is None:
            return None
        return os.path.join(self.directory, *capture.split("/"))

    def headline(self) -> str:
        """``failed in 3.2 s (dry mode): message``."""
        status = self.status or "unfinished"
        duration = self.run.get("duration_ms")
        text = status
        if isinstance(duration, int):
            text += f" in {duration / 1000:.1f} s"
        mode = self.run.get("mode")
        if mode:
            text += f" ({mode} mode)"
        warnings = self.warnings
        if warnings:
            text += f", {warnings} warning{'s' if warnings != 1 else ''}"
        error = self.run.get("error")
        if isinstance(error, Mapping) and error.get("message"):
            where = f" at {error['step_id']}" if error.get("step_id") else ""
            text += f"{where}: {error['message']}"
        return text


def parse_events(raw: object, run_id: str | None = None) -> list[Event]:
    """``get_run``'s ``events`` (JSON objects) as events, in ``seq`` order; entries
    without a type or a ``seq`` are skipped."""
    events: list[Event] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        seq, type_ = item.get("seq"), item.get("type")
        if not isinstance(seq, int) or not isinstance(type_, str):
            continue
        payload = item.get("payload")
        item_run = item.get("run_id")
        t_ms = item.get("t_ms")
        events.append(Event(type=type_, seq=seq, ts=str(item.get("ts") or ""),
                            t_ms=t_ms if isinstance(t_ms, int) else 0,
                            run_id=item_run if isinstance(item_run, str) else run_id,
                            payload=dict(payload) if isinstance(payload, Mapping) else {}))
    events.sort(key=lambda event: event.seq)
    return events


def search_failures(events: Sequence[Event]) -> list[SearchFailure]:
    """One :class:`SearchFailure` per ``step_failed`` event with a ``screenshot``,
    paired with the ``image_search`` of that step that saved it (else the step's last
    search)."""
    failures: list[SearchFailure] = []
    stack: list[str] = []
    """The steps running, outermost first."""
    searches: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        payload = event.payload
        step_id = payload.get("step_id")
        if event.type == "step_started" and isinstance(step_id, str):
            stack.append(step_id)
            searches[step_id] = []
        elif event.type == "image_search" and stack:
            searches.setdefault(stack[-1], []).append(payload)
        elif event.type in ("step_finished", "step_failed", "step_skipped") \
                and isinstance(step_id, str):
            if step_id in stack:
                del stack[stack.index(step_id):]
            shot = payload.get("screenshot")
            if event.type != "step_failed" or not isinstance(shot, str):
                continue
            tried = searches.get(step_id, [])
            search = next((s for s in reversed(tried) if s.get("screenshot") == shot),
                          tried[-1] if tried else {})
            failures.append(_failure(step_id, shot, payload, search))
    return failures


def _failure(step_id: str, shot: str, failed: Mapping[str, Any],
             search: Mapping[str, Any]) -> SearchFailure:
    error = failed.get("error")
    error = error if isinstance(error, Mapping) else {}
    details = error.get("details")
    details = details if isinstance(details, Mapping) else {}
    template = search.get("template", details.get("template", details.get("image", "")))
    score = search.get("best_score", details.get("best_score", details.get("score")))
    threshold = search.get("threshold", details.get("threshold"))
    attempts = details.get("attempts")
    return SearchFailure(
        step_id=step_id, template=str(template or ""), capture=shot,
        message=str(error.get("message") or ""), code=str(error.get("code") or ""),
        best_score=float(score) if isinstance(score, int | float) else None,
        threshold=float(threshold) if isinstance(threshold, int | float) else None,
        bbox=_rect(search.get("bbox")), region=_rect(search.get("region")),
        attempts=attempts if isinstance(attempts, int) else None)


def _rect(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    if not all(isinstance(v, int) for v in value):
        return None
    return (value[0], value[1], value[2], value[3])
