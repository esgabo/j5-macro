"""The interactive step debugger of ``j5 run --step`` and ``j5 run --break``.

Whenever the run pauses (a step command, a breakpoint, a ``pause:`` step, an
``on_error: pause`` failure or user interference), :class:`Debugger` reads commands,
like pdb or gdb, and turns them into the control commands of docs/contract.md
("Commands"): ``step_over``, ``step_into``, ``step_out``, ``resume``, ``evaluate``,
``get_state``, ``set_variable``, ``set_breakpoints`` and ``stop``.

Commands come from :class:`LineSource`: standard input read line by line on a thread,
so piped commands work (``printf 'n\\nc\\n' | j5 run --step x.yaml``). At end of input
the run continues to the end.

Parsing (:func:`parse_command`) and formatting (``format_*``) are pure functions.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from j5_macro.cli.render import PLAIN, Style, compact
from j5_macro.client import ClientError, EngineClient, EngineError
from j5_macro.protocol import Event

PROMPT = "(j5) "

STEPPING = {"next": "step_over", "step": "step_into", "out": "step_out",
            "continue": "resume"}
"""Debugger commands that let the run go on, and the control command each one sends."""

REPEATABLE = frozenset({"next", "step", "out"})
"""Commands an empty line repeats (like gdb)."""

ALIASES = {
    "n": "next", "next": "next",
    "s": "step", "step": "step",
    "o": "out", "out": "out", "finish": "out",
    "c": "continue", "cont": "continue", "continue": "continue",
    "p": "print", "print": "print",
    "v": "vars", "vars": "vars",
    "set": "set",
    "b": "break", "break": "break",
    "clear": "clear",
    "w": "where", "where": "where",
    "l": "list", "list": "list",
    "h": "help", "help": "help", "?": "help",
    "q": "quit", "quit": "quit", "exit": "quit",
}

NO_ARGUMENT = frozenset({"next", "step", "out", "continue", "vars", "where", "list", "help",
                         "quit"})

SOURCE_CONTEXT = (2, 3)
"""Source lines shown by ``where`` before and after the current step's line."""
LIST_CONTEXT = (3, 6)
"""Steps shown by ``list`` before and after the current one."""
SETTLE_S = 2.0
"""Longest wait after ``set`` for its ``variable_changed`` line, shown before the next
prompt."""
STATE_RETRIES = 10
"""``get_state`` attempts while the paused run reports no step yet (50 ms apart)."""

HELP = """\
commands, when the run is paused:
  n, next            run the current step (a whole loop or if) and pause at the next one
  s, step            pause at the very next step, entering loops and ifs
  o, out, finish     finish the current loop or if and pause after it
  c, continue        run until the next breakpoint or the end
  p, print EXPR      evaluate an expression over the variables, e.g. p attempts + 1
  v, vars            show the variables (secret values show as ***)
  set NAME = EXPR    set a variable to the value of an expression; JSON literals are
                     expressions too: set name = "bob", set n = 3, set xs = [1, 2]
  b, break [ID...]   add breakpoints on steps by id; alone, list the breakpoints
  clear [ID...]      remove breakpoints; alone, remove all of yours
  w, where           the current step and the script source around it
  l, list            the script steps around the current one
  h, help            this help
  q, quit            stop the run and exit
  an empty line      repeat the last n, s or o
after a failed step or mouse interference, c retries the step and n skips it;
Ctrl+C stops the run"""


class CommandSyntaxError(ValueError):
    """A debugger command that cannot be parsed; the message says why."""


@dataclass(frozen=True, slots=True)
class DebugCommand:
    name: str
    """Canonical name: one of the values of :data:`ALIASES`."""
    args: tuple[str, ...] = ()
    """``print``: the expression; ``set``: name and expression; ``break``/``clear``: ids."""


_SET = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)\s*(.+)$", re.DOTALL)


def parse_command(line: str) -> DebugCommand | None:
    """Parse one line; None for an empty line. Raises :class:`CommandSyntaxError`."""
    text = line.strip()
    if not text:
        return None
    word, _, rest = text.partition(" ")
    rest = rest.strip()
    name = ALIASES.get(word.lower())
    if name is None:
        raise CommandSyntaxError(f"unknown command '{word}' (h for help)")
    if name in NO_ARGUMENT:
        if rest:
            raise CommandSyntaxError(f"'{word}' takes no argument")
        return DebugCommand(name)
    if name == "print":
        if not rest:
            raise CommandSyntaxError("print what? usage: p EXPRESSION")
        return DebugCommand(name, (rest,))
    if name == "set":
        match = _SET.match(rest)
        if match is None:
            raise CommandSyntaxError("usage: set NAME = EXPRESSION")
        return DebugCommand(name, (match.group(1), match.group(2).strip()))
    # break and clear: step ids, separated by spaces or commas
    return DebugCommand(name, tuple(part for part in re.split(r"[\s,]+", rest) if part))


# -- the step tree ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StepInfo:
    id: str
    type: str
    path: tuple[int, ...]
    line: int | None
    breakpoint: bool
    """``break: true`` in the script."""

    @property
    def depth(self) -> int:
        """0 at the top level, +1 for each enclosing block."""
        return max(0, (len(self.path) - 1) // 2)


def flatten_steps(tree: Any) -> list[StepInfo]:
    """The step tree of ``load`` (``StepNode`` objects) in document order."""
    steps: list[StepInfo] = []

    def walk(nodes: Any) -> None:
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, Mapping) or not isinstance(node.get("id"), str):
                continue
            path = node.get("path")
            line = node.get("line")
            steps.append(StepInfo(
                id=node["id"], type=str(node.get("type", "?")),
                path=tuple(p for p in path if isinstance(p, int))
                if isinstance(path, list) else (),
                line=line if isinstance(line, int) and not isinstance(line, bool) else None,
                breakpoint=bool(node.get("break"))))
            blocks = node.get("blocks")
            if isinstance(blocks, Mapping):
                for children in blocks.values():
                    walk(children)

    walk(tree)
    return steps


# -- formatting ---------------------------------------------------------------------------


def _int_list(value: Any) -> list[int] | None:
    if isinstance(value, list) and all(isinstance(v, int) for v in value):
        return list(value)
    return None


def format_location(state: Mapping[str, Any], step: StepInfo | None, script: str,
                    style: Style = PLAIN, *, after: bool = False) -> str:
    """``-> count  set  (hello.yaml:18, iterations [0])``: the step the run is paused
    before; with ``after``, ``after pause_here  pause  (...)`` for a step that has
    already finished (a ``pause:`` step)."""
    step_id = state.get("step_id")
    if not isinstance(step_id, str) or not step_id:
        return "-> (no current step)"
    where: list[str] = []
    line = state.get("line")
    if isinstance(line, int) and not isinstance(line, bool):
        where.append(f"{script}:{line}")
    iterations = _int_list(state.get("iterations"))
    if iterations:
        where.append(f"iterations {iterations}")
    text = f"{'after' if after else '->'} {style.paint(step_id, 'bold')}"
    if step is not None:
        text += f"  {step.type}"
    return f"{text}  ({', '.join(where)})" if where else text


def format_where(state: Mapping[str, Any], step: StepInfo | None, script: str,
                 source: Sequence[str] | None, style: Style = PLAIN) -> list[str]:
    """``where``: the step, its path, iterations and line, then the source around it."""
    step_id = state.get("step_id")
    if not isinstance(step_id, str) or not step_id:
        return [f"no current step (state {state.get('state', '?')})"]
    head = f"step {style.paint(step_id, 'bold')}"
    if step is not None:
        head += f"  {step.type}"
    path = _int_list(state.get("path"))
    if path is not None:
        head += f"  path {path}"
    iterations = _int_list(state.get("iterations"))
    if iterations:
        head += f"  iterations {iterations}"
    line = state.get("line")
    if not isinstance(line, int) or isinstance(line, bool):
        return [head]
    lines = [f"{head}  {script}:{line}"]
    if not source:
        return lines
    before, after = SOURCE_CONTEXT
    first, last = max(1, line - before), min(len(source), line + after)
    width = len(str(last))
    for number in range(first, last + 1):
        marker = "->" if number == line else "  "
        text = f"{marker} {number:>{width}}  {source[number - 1]}".rstrip()
        lines.append(style.paint(text, "bold") if number == line else text)
    return lines


def format_list(steps: Sequence[StepInfo], current: str | None,
                breakpoints: frozenset[str] = frozenset(), style: Style = PLAIN) -> list[str]:
    """``list``: the steps around ``current``, indented by depth. ``->`` marks the
    current step and ``*`` a breakpoint (yours or the script's ``break: true``)."""
    if not steps:
        return ["the script has no steps"]
    index = next((i for i, s in enumerate(steps) if s.id == current), 0)
    before, after = LIST_CONTEXT
    first, last = max(0, index - before), min(len(steps), index + after + 1)
    shown = steps[first:last]
    width = max(len("  " * s.depth + s.id) for s in shown)
    type_width = max(len(s.type) for s in shown)
    lines: list[str] = []
    for step in shown:
        marker = "->" if step.id == current else "  "
        bp = "*" if step.breakpoint or step.id in breakpoints else " "
        label = ("  " * step.depth + step.id).ljust(width)
        text = f"{marker}{bp} {label}  {step.type.ljust(type_width)}"
        if step.line is not None:
            text += f"  (line {step.line})"
        lines.append(style.paint(text, "bold") if step.id == current else text)
    return lines


def format_variables(variables: Any) -> list[str]:
    """``vars``: one ``name = value`` line per variable, in the engine's order."""
    if not isinstance(variables, Mapping) or not variables:
        return ["no variables"]
    return [f"{name} = {compact(value)}" for name, value in variables.items()]


def format_breakpoints(client: Sequence[str], steps: Sequence[StepInfo]) -> list[str]:
    """``break`` alone: yours (in the order given) and the script's ``break: true``."""
    script = [s.id for s in steps if s.breakpoint and s.id not in client]
    if not client and not script:
        return ["no breakpoints"]
    lines = [f"breakpoint {step_id}" for step_id in client]
    lines += [f"breakpoint {step_id}  (break: true in the script)" for step_id in script]
    return lines


def unknown_ids(ids: Sequence[str], steps: Sequence[StepInfo]) -> list[str]:
    known = {s.id for s in steps}
    return [step_id for step_id in ids if step_id not in known]


# -- input --------------------------------------------------------------------------------


class LineSource:
    """Lines from a text stream, read on a daemon thread so the event loop never blocks.

    Lines typed while the run is running are kept for the next prompt (type-ahead, and
    piped commands). :meth:`get` returns None at end of input, then always None."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ended = False

    def _start(self) -> None:
        self._loop = asyncio.get_running_loop()
        stream = self._stream if self._stream is not None else sys.stdin
        loop, queue = self._loop, self._queue

        def put(item: str | None) -> None:
            with contextlib.suppress(RuntimeError):  # the loop is already closed
                loop.call_soon_threadsafe(queue.put_nowait, item)

        def read() -> None:
            try:
                while stream is not None:
                    line = stream.readline()
                    if not line:
                        break
                    put(line.rstrip("\r\n"))
            except (OSError, ValueError, RuntimeError):
                pass  # stdin closed
            put(None)

        self._thread = threading.Thread(target=read, name="j5-debugger-input", daemon=True)
        self._thread.start()

    async def get(self) -> str | None:
        if self._ended:
            return None
        if self._thread is None:
            self._start()
        line = await self._queue.get()
        if line is None:
            self._ended = True
        return line


# -- the debugger -------------------------------------------------------------------------


@dataclass(slots=True)
class Debugger:
    """Runs the prompt each time the run pauses; see the module docstring."""

    client: EngineClient
    run_id: str
    script_id: str
    script: str
    """The script path as the user wrote it, for messages and for reading its source."""
    steps: list[StepInfo]
    write: Callable[[str], None]
    """Writes one line of debugger output."""
    prompt: Callable[[str], None]
    """Writes the prompt, without a line break."""
    lines: LineSource
    echo: bool = False
    """Echo the commands read (stdin is not a terminal), so transcripts read well."""
    as_json: bool = False
    style: Style = PLAIN
    breakpoints: list[str] = field(default_factory=list)
    """Client breakpoints, in the order they were added."""
    at_prompt: bool = False
    """The prompt is shown and a command is awaited."""
    _changes: dict[str, asyncio.Event] = field(default_factory=dict)
    """``set`` commands waiting for their ``variable_changed`` event, by name."""
    _last: DebugCommand | None = None
    _source: list[str] | None = None
    _ended_noted: bool = False

    @property
    def ended(self) -> bool:
        """End of input was reached: every pause now resumes on its own."""
        return self._ended_noted

    def step(self, step_id: Any) -> StepInfo | None:
        return next((s for s in self.steps if s.id == step_id), None)

    async def paused(self, reason: Any, failed_step: str | None,
                     finished: Mapping[str, Any] | None = None) -> None:
        """Handle one pause: prompt until a command lets the run go on (or quits).

        ``failed_step`` is the step last started; ``finished`` the last ``step_finished``
        payload when no step started after it (the run paused right after that step, as
        a ``pause:`` step does)."""
        if self._ended_noted:
            await self._auto_resume(reason)
            return
        state = await self._state()
        if state is not None:
            step = self.step(state.get("step_id"))
            after = finished is not None and finished.get("step_id") == state.get("step_id")
            self.write(format_location(state, step, self.script, self.style, after=after))
            result = finished.get("result") if finished is not None else None
            if after and step is not None and step.type == "pause" and \
                    isinstance(result, Mapping) and result.get("message"):
                self.write(f"pause: {result['message']}")
        if reason in ("error", "interference"):
            subject = f"step '{failed_step}'" if failed_step else "the step"
            what = (f"you moved the mouse during {subject}" if reason == "interference"
                    else f"{subject} failed")
            self.write(f"{what}: c retries it, n skips it")
        while True:
            self.prompt(PROMPT)
            self.at_prompt = True
            try:
                line = await self.lines.get()
            finally:
                self.at_prompt = False
            if line is None:
                self.write("")
                self._ended_noted = True
                self.write("j5: end of debugger commands; continuing the run to the end")
                await self._send("resume")
                return
            if self.echo:
                self.write(line)
            try:
                command = parse_command(line)
            except CommandSyntaxError as exc:
                self.write(str(exc))
                continue
            if command is None:
                command = self._last
                if command is None:
                    continue
            if command.name in REPEATABLE:
                self._last = command
            if await self._execute(command):
                return

    async def _auto_resume(self, reason: Any) -> None:
        why = f" ({reason})" if isinstance(reason, str) and reason else ""
        self.write(f"j5: the run paused{why}; continuing (end of debugger commands)")
        await self._send("resume")

    async def _execute(self, command: DebugCommand) -> bool:
        """Run one command; True when the run goes on (or is being stopped)."""
        name = command.name
        if name in STEPPING:
            return await self._send(STEPPING[name])
        if name == "quit":
            self.write("j5: stopping the run")
            await self._send("stop", reason="user")
            return True
        if name == "help":
            for line in HELP.splitlines():
                self.write(line)
        elif name == "print":
            result = await self._call("evaluate", expression=command.args[0])
            if result is not None:
                self.write(compact(result.get("value"), 400))
        elif name == "vars":
            state = await self._state()
            if state is not None:
                for line in format_variables(state.get("variables")):
                    self.write(line)
        elif name == "set":
            await self._set(*command.args)
        elif name == "break":
            await self._break(command.args)
        elif name == "clear":
            await self._clear(command.args)
        elif name == "where":
            state = await self._state()
            if state is not None:
                step = self.step(state.get("step_id"))
                for line in format_where(state, step, self.script, self._read_source(),
                                         self.style):
                    self.write(line)
        elif name == "list":
            state = await self._state()
            current = state.get("step_id") if state is not None else None
            for line in format_list(self.steps, current if isinstance(current, str) else None,
                                    frozenset(self.breakpoints), self.style):
                self.write(line)
        return False

    async def _set(self, name: str, expression: str) -> None:
        """The right side is evaluated by the engine over the current variables (JSON
        literals are valid expressions), then assigned with ``set_variable``."""
        result = await self._call("evaluate", expression=expression)
        if result is None:
            return
        value = result.get("value")
        rendered = asyncio.Event()
        self._changes[name] = rendered
        try:
            if await self._call("set_variable", name=name, value=value) is None:
                return
            if self.as_json:
                self.write(f"{name} = {compact(value)}")
            else:
                # Text mode shows the variable_changed event instead; wait until it is
                # rendered so that it comes before the next prompt.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(rendered.wait(), SETTLE_S)
        finally:
            self._changes.pop(name, None)

    def observe(self, event: Event) -> None:
        """Called with every event of the run after it was rendered."""
        if event.type == "variable_changed" and "step_id" not in event.payload:
            waiter = self._changes.get(str(event.payload.get("name")))
            if waiter is not None:
                waiter.set()

    async def _break(self, ids: Sequence[str]) -> None:
        if not ids:
            for line in format_breakpoints(self.breakpoints, self.steps):
                self.write(line)
            return
        unknown = unknown_ids(ids, self.steps)
        if unknown:
            self.write(f"no step with id {', '.join(repr(i) for i in unknown)} "
                       "(l lists the steps)")
            return
        wanted = self.breakpoints + [i for i in dict.fromkeys(ids) if i not in self.breakpoints]
        if await self._set_breakpoints(wanted):
            self.write(f"breakpoints: {', '.join(self.breakpoints)}")

    async def _clear(self, ids: Sequence[str]) -> None:
        missing = [i for i in ids if i not in self.breakpoints]
        if missing:
            self.write(f"no breakpoint of yours on {', '.join(repr(i) for i in missing)}")
            return
        wanted = [i for i in self.breakpoints if ids and i not in ids]
        if await self._set_breakpoints(wanted):
            self.write(f"breakpoints: {', '.join(self.breakpoints)}" if self.breakpoints
                       else "no breakpoints of yours left")

    async def _set_breakpoints(self, step_ids: list[str]) -> bool:
        result = await self._call("set_breakpoints", run=False, script_id=self.script_id,
                                  step_ids=step_ids)
        if result is None:
            return False
        self.breakpoints = step_ids
        return True

    async def _state(self) -> dict[str, Any] | None:
        """``get_state``, retried briefly while a paused run has no current step yet."""
        state: dict[str, Any] | None = None
        for _ in range(STATE_RETRIES):
            state = await self._call("get_state")
            if state is None or state.get("step_id") or state.get("state") != "paused":
                return state
            await asyncio.sleep(0.05)
        return state

    def _read_source(self) -> list[str] | None:
        if self._source is None:
            try:
                text = Path(self.script).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                self.write(f"j5: cannot read {self.script}: {exc}")
                return None
            self._source = text.splitlines()
        return self._source

    async def _send(self, command: str, **payload: Any) -> bool:
        """A control command that lets the run go on; False (and re-prompt) on error."""
        return await self._call(command, **payload) is not None

    async def _call(self, command: str, *, run: bool = True,
                    **payload: Any) -> dict[str, Any] | None:
        """Send ``command``; on an engine error, say so and return None."""
        if run:
            payload = {"run_id": self.run_id, **payload}
        try:
            return dict(await self.client.call(command, payload))
        except EngineError as exc:
            self.write(f"{command}: [{exc.code}] {exc.error.message}")
        except (ClientError, TimeoutError):
            pass  # the engine died or is closing; the event stream reports it
        return None
