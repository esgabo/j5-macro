"""Pure rendering of engine events and script problems as human-readable lines.

Nothing here does I/O: every function takes data and returns strings, so the
output can be unit-tested without an engine or a terminal. Colors and the
symbol set are chosen by the caller (see :func:`use_color` and :func:`symbols_for`).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TextIO

from j5_macro.protocol import ErrorInfo, Event

PARAMS_WIDTH = 80
"""Longest rendering of a step's params or result before it is shortened."""


# -- styling ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Symbols:
    start: str
    ok: str
    fail: str
    skip: str
    loop: str
    branch: str
    pause: str
    stop: str
    arrow: str


UNICODE_SYMBOLS = Symbols(start="▶", ok="✓", fail="✗", skip="↷", loop="↻", branch="⑂",
                          pause="⏸", stop="■", arrow="→")
ASCII_SYMBOLS = Symbols(start=">", ok="+", fail="x", skip="-", loop="@", branch="?",
                        pause="||", stop="#", arrow="->")


def symbols_for(stream: TextIO) -> Symbols:
    """Unicode symbols when ``stream`` can encode them (not a cp1252 pipe), else ASCII."""
    encoding = getattr(stream, "encoding", None) or "utf-8"
    sample = "".join(getattr(UNICODE_SYMBOLS, name) for name in Symbols.__slots__)
    try:
        sample.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return ASCII_SYMBOLS
    return UNICODE_SYMBOLS


def use_color(stream: TextIO, environ: Mapping[str, str] | None = None) -> bool:
    """Colors only on a terminal and only when ``NO_COLOR`` is unset or empty."""
    env = os.environ if environ is None else environ
    if env.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty is not None and isatty())


_CODES = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35",
          "cyan": "36", "dim": "2", "bold": "1"}


@dataclass(frozen=True, slots=True)
class Style:
    color: bool = False
    symbols: Symbols = UNICODE_SYMBOLS

    def paint(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        codes = ";".join(_CODES[name] for name in styles)
        return f"\x1b[{codes}m{text}\x1b[0m"


PLAIN = Style()


# -- helpers ------------------------------------------------------------------------------


def compact(value: Any, width: int = PARAMS_WIDTH) -> str:
    """One-line JSON for ``value``, shortened with an ellipsis past ``width`` characters."""
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= width else text[: width - 3] + "..."


def seconds(ms: Any) -> str:
    """``1234`` -> ``1.234s``; anything that is not a number -> ``?``."""
    if isinstance(ms, bool) or not isinstance(ms, int | float):
        return "?"
    return f"{ms / 1000:.3f}s"


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def depth_of(path: Any) -> int:
    """Nesting depth from a step ``path``: ``[3]`` -> 0, ``[3, 0, 1]`` -> 1, ...

    Paths alternate list positions and block slots, so every nesting level adds two
    items (docs/contract.md, "Script format and step registry").
    """
    if not isinstance(path, list) or not path:
        return 0
    return (len(path) - 1) // 2


def _point(value: Any) -> str:
    """``[x, y]`` -> ``(x, y)``; anything else as compact JSON."""
    if isinstance(value, list) and len(value) == 2:
        return f"({value[0]}, {value[1]})"
    return compact(value, 40)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


# -- script problems (load / validate) ----------------------------------------------------


def format_problem(path: str, error: ErrorInfo, style: Style = PLAIN) -> str:
    """Compiler style: ``path:line: error[code]: message (step id)``."""
    location = f"{path}:{error.line}" if error.line is not None else path
    label = style.paint(f"error[{error.code}]", "red", "bold")
    suffix = f" (step {error.step_id})" if error.step_id else ""
    return f"{location}: {label}: {error.message}{suffix}"


def format_warning(path: str, warning: Mapping[str, Any], style: Style = PLAIN) -> str:
    """Compiler style: ``path:line: warning: message``."""
    line = warning.get("line")
    location = f"{path}:{line}" if isinstance(line, int) else path
    label = style.paint("warning", "yellow", "bold")
    return f"{location}: {label}: {_text(warning.get('message'))}"


def validate_summary(path: str, errors: int, warnings: int, style: Style = PLAIN) -> str:
    if errors:
        verdict = style.paint(f"invalid ({plural(errors, 'error')}", "red")
        if warnings:
            verdict += style.paint(f", {plural(warnings, 'warning')}", "red")
        return f"{path}: {verdict}{style.paint(')', 'red')}"
    verdict = style.paint("valid", "green")
    if warnings:
        verdict += f" ({plural(warnings, 'warning')})"
    return f"{path}: {verdict}"


# -- step types ---------------------------------------------------------------------------


def format_step_types(step_types: Sequence[Mapping[str, Any]], style: Style = PLAIN) -> list[str]:
    """``list_step_types`` result as an indented listing."""
    lines: list[str] = []
    for info in step_types:
        name = style.paint(_text(info.get("name")), "bold")
        doc = _text(info.get("doc")).strip().splitlines()
        lines.append(f"{name}  {style.paint(doc[0], 'dim')}" if doc else name)
        params = info.get("params")
        for param in params if isinstance(params, list) else []:
            if not isinstance(param, Mapping):
                continue
            text = f"  {_text(param.get('name'))}: {_text(param.get('type'))}"
            if param.get("required"):
                text += style.paint(" (required)", "yellow")
            elif param.get("default") is not None:
                text += f" = {compact(param['default'], 40)}"
            param_doc = _text(param.get("doc")).strip().splitlines()
            if param_doc:
                text += f"  {style.paint(param_doc[0], 'dim')}"
            lines.append(text)
    return lines


# -- run events ---------------------------------------------------------------------------


@dataclass(slots=True)
class RunRenderer:
    """Turns run events into lines. Keeps a little state (step depths, the seed).

    ``render`` returns no lines for events that are not worth a line in the human
    output (``input_planned``, ``image_search``, ``state_changed`` to running, ...).
    """

    style: Style = PLAIN
    seed: int | None = None
    run_id: str | None = None
    _depths: dict[str, int] = field(default_factory=dict)

    def render(self, event: Event) -> list[str]:
        handler = getattr(self, f"_on_{event.type}", None)
        if handler is None:
            return []
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "run_started":
            self.run_id = event.run_id
        lines: list[str] = handler(payload)
        if event.type in ("run_started", "run_finished"):
            return lines
        stamp = self.style.paint(f"{event.t_ms / 1000:7.3f}s", "dim")
        return [f"{stamp}  {line}" for line in lines]

    # -- helpers --

    def _indent(self, payload: Mapping[str, Any]) -> str:
        if "path" in payload:
            depth = depth_of(payload.get("path"))
        else:
            depth = self._depths.get(_text(payload.get("step_id")), 0)
        return "  " * depth

    def _step(self, payload: Mapping[str, Any]) -> str:
        return self.style.paint(_text(payload.get("step_id")) or "?", "bold")

    def _error(self, error: Any) -> str:
        if not isinstance(error, Mapping):
            return "unknown error"
        code = self.style.paint(f"[{_text(error.get('code'))}]", "red")
        return f"{code} {_text(error.get('message'))}"

    # -- run lifecycle --

    def _on_run_started(self, p: Mapping[str, Any]) -> list[str]:
        """``run r-20261012-151051-d36f  Save report  (mode normal, seed 7, backend x11)``.

        Engines that predate ``script_name`` get the ``script_id`` instead."""
        seed = p.get("seed")
        self.seed = seed if isinstance(seed, int) else self.seed
        parts = [f"mode {_text(p.get('mode')) or '?'}"]
        if self.seed is not None:
            parts.append(f"seed {self.seed}")
        if p.get("backend"):
            parts.append(f"backend {p['backend']}")
        title = self.style.paint(f"run {self.run_id or '?'}", "bold")
        name = _text(p.get("script_name")) or _text(p.get("script_id"))
        head = f"{title}  {name}" if name else title
        return [f"{head}  ({', '.join(parts)})"]

    def _on_run_finished(self, p: Mapping[str, Any]) -> list[str]:
        sym = self.style.symbols
        status = _text(p.get("status")) or "finished"
        took = seconds(p.get("duration_ms"))
        seed = f"  (seed {self.seed})" if self.seed is not None else ""
        if status == "completed":
            head = self.style.paint(f"{sym.ok} completed", "green", "bold")
            return [f"{head} in {took}{seed}"]
        if status == "failed":
            head = self.style.paint(f"{sym.fail} failed", "red", "bold")
            line = f"{head} after {took}{seed}"
            if p.get("error") is not None:
                line += f": {self._error(p.get('error'))}"
            return [line]
        reason = f" ({p['reason']})" if p.get("reason") else ""
        head = self.style.paint(f"{sym.stop} {status}{reason}", "yellow", "bold")
        return [f"{head} after {took}{seed}"]

    def _on_state_changed(self, p: Mapping[str, Any]) -> list[str]:
        state = _text(p.get("state"))
        if state == "running":
            return []
        reason = f" ({p['reason']})" if p.get("reason") else ""
        sym = self.style.symbols.pause if state == "paused" else self.style.symbols.stop
        return [self.style.paint(f"{sym} {state}{reason}", "yellow")]

    # -- steps --

    def _on_step_started(self, p: Mapping[str, Any]) -> list[str]:
        step_id = _text(p.get("step_id"))
        self._depths[step_id] = depth_of(p.get("path"))
        params = p.get("params")
        line = (f"{self._indent(p)}{self.style.paint(self.style.symbols.start, 'cyan')} "
                f"{self._step(p)}  {_text(p.get('type'))}")
        if params:
            line += f"  {self.style.paint(compact(params), 'dim')}"
        return [line]

    def _on_step_finished(self, p: Mapping[str, Any]) -> list[str]:
        line = (f"{self._indent(p)}{self.style.paint(self.style.symbols.ok, 'green')} "
                f"{self._step(p)}")
        duration = p.get("duration_ms")
        if isinstance(duration, int) and not isinstance(duration, bool):
            line += f"  {self.style.paint(f'{duration}ms', 'dim')}"
        if p.get("result"):
            line += f"  {self.style.paint(compact(p.get('result')), 'dim')}"
        return [line]

    def _on_step_failed(self, p: Mapping[str, Any]) -> list[str]:
        indent = self._indent(p)
        lines = [f"{indent}{self.style.paint(self.style.symbols.fail, 'red')} {self._step(p)}  "
                 f"{self._error(p.get('error'))}"]
        if p.get("screenshot"):
            lines.append(f"{indent}  screenshot: {p['screenshot']}")
        return lines

    def _on_step_skipped(self, p: Mapping[str, Any]) -> list[str]:
        reason = f" ({p['reason']})" if p.get("reason") else ""
        return [f"{self._indent(p)}{self.style.paint(self.style.symbols.skip, 'dim')} "
                f"{self._step(p)}  skipped{reason}"]

    def _on_breakpoint_hit(self, p: Mapping[str, Any]) -> list[str]:
        return [f"{self._indent(p)}{self.style.paint('breakpoint', 'magenta')} {self._step(p)}"]

    def _on_branch_taken(self, p: Mapping[str, Any]) -> list[str]:
        condition = p.get("condition")
        cond = condition if isinstance(condition, str) else compact(condition, 60)
        branch = self.style.paint(_text(p.get("branch")), "bold")
        return [f"{self._indent(p)}{self.style.paint(self.style.symbols.branch, 'magenta')} "
                f"{self._step(p)}  if {cond} = {compact(p.get('value'), 40)} "
                f"{self.style.symbols.arrow} {branch}"]

    def _on_loop_iteration(self, p: Mapping[str, Any]) -> list[str]:
        index = p.get("index")
        total = p.get("total")
        number = index + 1 if isinstance(index, int) and not isinstance(index, bool) else "?"
        count = f"{number}/{total}" if isinstance(total, int) else f"{number}"
        return [f"{self._indent(p)}{self.style.paint(self.style.symbols.loop, 'magenta')} "
                f"{self._step(p)}  iteration {count}"]

    # -- the rest --

    def _on_variable_changed(self, p: Mapping[str, Any]) -> list[str]:
        indent = "  " * (self._depths.get(_text(p.get("step_id")), -1) + 1)
        name = self.style.paint(_text(p.get("name")), "blue")
        was = "(was " + compact(p.get("old"), 40) + ")"
        return [f"{indent}{name} = {compact(p.get('new'), 60)}  {self.style.paint(was, 'dim')}"]

    def _on_log(self, p: Mapping[str, Any]) -> list[str]:
        level = _text(p.get("level")) or "info"
        color = {"error": "red", "warning": "yellow", "debug": "dim"}.get(level, "cyan")
        indent = ("  " * (self._depths[_text(p.get("step_id"))] + 1)
                  if _text(p.get("step_id")) in self._depths else "")
        return [f"{indent}{self.style.paint(f'[{level}]', color)} {_text(p.get('message'))}"]

    def _on_user_interference(self, p: Mapping[str, Any]) -> list[str]:
        action = _text(p.get("action"))
        then = {"stop": "stopping the run", "pause": "pausing the run"}.get(
            action, f"action: {action or '?'}")
        return [self.style.paint(
            f"you moved the mouse: the pointer was at {_point(p.get('actual'))}, expected "
            f"near {_point(p.get('expected'))}; {then}", "yellow", "bold")]

    def _on_keys_released(self, p: Mapping[str, Any]) -> list[str]:
        keys = p.get("keys")
        names = ", ".join(str(k) for k in keys) if isinstance(keys, list) else "?"
        return [self.style.paint(f"released {names} ({_text(p.get('reason'))})", "dim")]
