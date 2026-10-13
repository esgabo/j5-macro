"""The ``j5`` subcommands that talk to the engine: ``validate``, ``run``, ``steps`` and
``runs``.

Each one launches the engine with :class:`EngineClient`, sends the commands of
docs/contract.md ("Commands") and renders what comes back. Output goes to the
streams passed in; failures that end the command raise :class:`CliError`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, TextIO

from j5_macro.cli.debugger import Debugger, LineSource, StepInfo, flatten_steps, unknown_ids
from j5_macro.cli.interrupt import Control, Controls
from j5_macro.cli.release import release_held
from j5_macro.cli.render import (
    RunRenderer,
    Style,
    format_problem,
    format_run_summary,
    format_runs,
    format_step_types,
    format_warning,
    validate_summary,
)
from j5_macro.client import ClientError, EngineClient, EngineDied, EngineError
from j5_macro.protocol import ErrorInfo, Event, encode
from j5_macro.protocol.messages import ProtocolError, decode

STOP_WAIT = 10.0
"""Seconds to wait for ``run_finished`` after the first Ctrl+C sent ``stop``."""
CLOSE_TIMEOUT = 5.0
"""Seconds ``close`` waits for the engine at each step (EOF, terminate, kill)."""
ABORT_CLOSE_TIMEOUT = 0.5
"""Same, after a second Ctrl+C: the user wants out now."""
STDERR_LINES = 10
"""Lines of the engine's stderr shown when it dies."""


class ExitCode(IntEnum):
    OK = 0
    FAILED = 1
    """``run``: the run failed. ``validate``: the script is invalid. Any: the engine died."""
    USAGE = 2
    """Bad arguments, or ``run`` could not load or start the script."""
    STOPPED = 3
    INTERRUPTED = 130


class CliError(Exception):
    """Ends a command with ``message`` on stderr and ``exit_code``."""

    def __init__(self, message: str, exit_code: int = ExitCode.FAILED) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(slots=True)
class Output:
    out: TextIO
    err: TextIO
    style: Style = field(default_factory=Style)
    err_style: Style = field(default_factory=Style)

    def line(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def note(self, text: str) -> None:
        print(text, file=self.err, flush=True)


def died_message(exc: EngineDied) -> str:
    message = f"the engine died (exit code {exc.exit_code})"
    tail = [line for line in exc.stderr_tail if line.strip()][-STDERR_LINES:]
    if tail:
        message += "; last lines of its stderr:\n" + "\n".join(f"  {line}" for line in tail)
    return message


def release_on_death(io: Output) -> Callable[[list[str], int | None], Awaitable[None]]:
    """The ``on_engine_died`` hook: release what the engine held and say so."""

    async def on_death(held: list[str], exit_code: int | None) -> None:
        if not held:
            return
        report = await asyncio.to_thread(release_held, held)
        io.note(f"j5: {report.message()}")

    return on_death


@contextlib.asynccontextmanager
async def engine(cmd: Sequence[str], io: Output | None = None) -> AsyncIterator[EngineClient]:
    """Launch the engine and close it on exit; startup failures become :class:`CliError`.

    With ``io``, keys and buttons the engine held when it died are released (and
    reported there)."""
    on_death = release_on_death(io) if io is not None else None
    try:
        client = await EngineClient.start(cmd, client_name="j5-cli", on_engine_died=on_death)
    except EngineDied as exc:
        raise CliError(died_message(exc)) from exc
    except TimeoutError as exc:
        raise CliError("the engine did not answer in time") from exc
    except OSError as exc:
        raise CliError(f"cannot launch the engine ({' '.join(cmd)}): {exc}") from exc
    except ClientError as exc:
        raise CliError(f"cannot talk to the engine: {exc}") from exc
    try:
        yield client
    except EngineDied as exc:
        raise CliError(died_message(exc)) from exc
    finally:
        await client.close(timeout=CLOSE_TIMEOUT)


# -- load ---------------------------------------------------------------------------------


@dataclass(slots=True)
class LoadOutcome:
    script_id: str | None
    errors: list[ErrorInfo]
    warnings: list[dict[str, Any]]
    params: list[dict[str, Any]] = field(default_factory=list)
    """The script's declared params, as ``load`` reports them."""
    steps: list[StepInfo] = field(default_factory=list)
    """The step tree of ``load``, flattened in document order."""


def _warnings(raw: Any) -> list[dict[str, Any]]:
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def problems_of(error: ErrorInfo) -> tuple[list[ErrorInfo], list[dict[str, Any]]]:
    """Errors and warnings of a failed ``load``: ``details.errors`` or the error itself."""
    details = error.details or {}
    errors: list[ErrorInfo] = []
    raw_errors = details.get("errors")
    for raw in raw_errors if isinstance(raw_errors, list) else []:
        with contextlib.suppress(KeyError, TypeError, ValueError):
            errors.append(ErrorInfo.from_json(raw))
    return errors or [error], _warnings(details.get("warnings"))


async def load(client: EngineClient, script: str) -> LoadOutcome:
    """``load`` the script by absolute path; script problems are returned, not raised."""
    try:
        result = await client.call("load", {"path": os.path.abspath(script)})
    except EngineError as exc:
        errors, warnings = problems_of(exc.error)
        return LoadOutcome(None, errors, warnings)
    script_id = result.get("script_id")
    if not isinstance(script_id, str):
        raise CliError("the engine answered load without a script_id")
    params = result.get("params")
    return LoadOutcome(script_id, [], _warnings(result.get("warnings")),
                       [p for p in params if isinstance(p, dict)]
                       if isinstance(params, list) else [],
                       flatten_steps(result.get("steps")))


# -- validate -----------------------------------------------------------------------------


async def validate(cmd: Sequence[str], script: str, *, as_json: bool, io: Output) -> int:
    async with engine(cmd, io) as client:
        outcome = await load(client, script)
    if as_json:
        io.line(json.dumps({
            "path": script,
            "valid": not outcome.errors,
            "script_id": outcome.script_id,
            "errors": [error.to_json() for error in outcome.errors],
            "warnings": outcome.warnings,
            "params": outcome.params,
        }, indent=2, ensure_ascii=False))
    else:
        for error in outcome.errors:
            io.line(format_problem(script, error, io.style))
        for warning in outcome.warnings:
            io.line(format_warning(script, warning, io.style))
        for param in outcome.params:
            io.line(describe_param(param))
        io.line(validate_summary(script, len(outcome.errors), len(outcome.warnings), io.style))
    return ExitCode.FAILED if outcome.errors else ExitCode.OK


# -- steps --------------------------------------------------------------------------------


async def steps(cmd: Sequence[str], *, as_json: bool, io: Output) -> int:
    async with engine(cmd, io) as client:
        try:
            result = await client.call("list_step_types")
        except EngineError as exc:
            raise CliError(f"list_step_types failed: [{exc.code}] {exc.error.message}") from exc
    step_types = result.get("step_types")
    if not isinstance(step_types, list):
        raise CliError("the engine answered list_step_types without step_types")
    if as_json:
        io.line(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for line in format_step_types([s for s in step_types if isinstance(s, dict)], io.style):
            io.line(line)
    return ExitCode.OK


# -- run ----------------------------------------------------------------------------------


def describe_param(param: Mapping[str, Any]) -> str:
    """``param customer (string, required): Name to type in the form``."""
    traits = [str(param.get("type", "string"))]
    if param.get("secret"):
        traits.append("secret")
    if param.get("required"):
        traits.append("required")
    elif "default" in param:
        traits.append(f"default {json.dumps(param['default'], ensure_ascii=False)}")
    text = f"param {param.get('name')} ({', '.join(traits)})"
    description = param.get("description")
    return f"{text}: {description}" if description else text


Ask = Callable[[str, bool], str]
"""``ask(prompt, secret) -> text``: reads a param from the user (no echo for secrets)."""


@dataclass(slots=True)
class RunOptions:
    mode: str = "normal"
    params: dict[str, Any] = field(default_factory=dict)
    inline_params: frozenset[str] = frozenset()
    """Params given with ``--param``, i.e. visible on the command line."""
    ask: Ask | None = None
    """Asks for missing required params; None when nobody can answer (no terminal)."""
    vars: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    input_profile: dict[str, Any] | None = None
    allow_exec: bool = False
    """Let ``exec`` steps run programs (``--allow-exec``)."""
    as_json: bool = False
    interactive: bool = False
    """Offer "press Enter to resume" when the run pauses (stdin is a terminal)."""
    debug: bool = False
    """Run the debugger prompt on every pause (``--step`` or ``--break``)."""
    breakpoints: list[str] = field(default_factory=list)
    """Client breakpoints to set before the run starts (``--break``)."""
    lines: LineSource | None = None
    """Where debugger commands come from; standard input when None."""
    echo_commands: bool = False
    """Echo debugger commands as they are read (standard input is not a terminal)."""

    def payload(self, script_id: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"script_id": script_id, "mode": self.mode}
        if self.params:
            payload["params"] = self.params
        if self.vars:
            payload["vars"] = self.vars
        if self.seed is not None:
            payload["seed"] = self.seed
        if self.input_profile is not None:
            payload["input_profile"] = self.input_profile
        if self.allow_exec:
            payload["allow_exec"] = True
        return payload


def warn_inline_secrets(declared: list[dict[str, Any]], inline: frozenset[str],
                        io: Output) -> None:
    """Warn about secret params given with ``--param``."""
    for param in declared:
        if param.get("secret") and param.get("name") in inline:
            io.note(f"j5: warning: secret param '{param['name']}' was given with --param, "
                    "so it is visible in the shell history and the process list; prefer "
                    "--param-env, --param-file or the prompt")


async def ask_missing(declared: list[dict[str, Any]], options: RunOptions,
                      io: Output) -> bool:
    """Ask for required params that were not given, when someone can answer. False if
    the user gave up (Ctrl+D or Ctrl+C at the prompt)."""
    if options.ask is None:
        return True  # the engine reports what is missing
    for param in declared:
        name = param.get("name")
        if not isinstance(name, str) or not param.get("required") or name in options.params:
            continue
        description = param.get("description")
        prompt = f"{name} ({description}): " if description else f"{name}: "
        try:
            value = await asyncio.to_thread(options.ask, prompt, bool(param.get("secret")))
        except (EOFError, KeyboardInterrupt):
            io.note("")
            io.note(f"j5: no value for param '{name}'")
            return False
        options.params[name] = value
    return True


async def set_initial_breakpoints(client: EngineClient, script: str, outcome: LoadOutcome,
                                  options: RunOptions, io: Output) -> bool:
    """Check the ``--break`` step ids against the loaded script and set them; False
    (after saying why) when one is unknown."""
    if not options.breakpoints:
        return True
    unknown = unknown_ids(options.breakpoints, outcome.steps)
    if unknown:
        known = ", ".join(step.id for step in outcome.steps) or "none"
        io.note(f"j5: --break: {script} has no step with id "
                f"{', '.join(repr(step_id) for step_id in unknown)} (its step ids: {known})")
        return False
    assert outcome.script_id is not None
    try:
        await client.call("set_breakpoints", {"script_id": outcome.script_id,
                                              "step_ids": list(dict.fromkeys(
                                                  options.breakpoints))})
    except EngineError as exc:
        io.note(f"j5: cannot set breakpoints: [{exc.code}] {exc.error.message}")
        return False
    return True


def make_debugger(client: EngineClient, run_id: str, script: str, outcome: LoadOutcome,
                  options: RunOptions, io: Output) -> Debugger:
    """The debugger of a ``--step`` or ``--break`` run. Its output goes to stdout, or to
    stderr with ``--json`` so that stdout keeps only events."""
    stream, style = (io.err, io.err_style) if options.as_json else (io.out, io.style)

    def write(text: str) -> None:
        print(text, file=stream, flush=True)

    def prompt(text: str) -> None:
        print(text, end="", file=stream, flush=True)

    assert outcome.script_id is not None
    return Debugger(client=client, run_id=run_id, script_id=outcome.script_id, script=script,
                    steps=outcome.steps, write=write, prompt=prompt,
                    lines=options.lines or LineSource(), echo=options.echo_commands,
                    as_json=options.as_json, style=style,
                    breakpoints=list(dict.fromkeys(options.breakpoints)))


UNATTENDED_RETRIES = 2
"""Without a terminal, a step paused by an error or interference is retried this many
times; then the run is stopped and ``j5 run`` exits with code 1."""

STATUS_CODES = {"completed": ExitCode.OK, "failed": ExitCode.FAILED,
                "stopped": ExitCode.STOPPED}


async def run(cmd: Sequence[str], script: str, options: RunOptions, *, io: Output) -> int:
    controls = Controls(asyncio.get_running_loop())
    interrupted = False
    status: str | None = None
    try:
        async with engine(cmd, io) as client:
            outcome = await load(client, script)
            for error in outcome.errors:
                io.note(format_problem(script, error, io.err_style))
            for warning in outcome.warnings:
                io.note(format_warning(script, warning, io.err_style))
            if outcome.script_id is None:
                return ExitCode.USAGE
            warn_inline_secrets(outcome.params, options.inline_params, io)
            if not await ask_missing(outcome.params, options, io):
                return ExitCode.USAGE
            if not await set_initial_breakpoints(client, script, outcome, options, io):
                return ExitCode.USAGE
            with controls.handle_sigint():
                try:
                    started = await client.call("run", options.payload(outcome.script_id))
                except EngineError as exc:
                    io.note(f"j5: cannot start the run: [{exc.code}] {exc.error.message}")
                    details = exc.error.details or {}
                    if "param" in details:
                        io.note("j5: pass params with --param NAME=VALUE, --param-file, "
                                "--param-env or --params-file (see j5 run --help)")
                    return ExitCode.USAGE
                run_id = started.get("run_id")
                if not isinstance(run_id, str):
                    raise CliError("the engine answered run without a run_id")
                debugger = (make_debugger(client, run_id, script, outcome, options, io)
                            if options.debug else None)
                follower = RunFollower(client, run_id, options, io, controls, debugger)
                try:
                    status = await follower.follow()
                finally:
                    interrupted = controls.interrupts > 0
                if follower.aborted:
                    await client.close(timeout=ABORT_CLOSE_TIMEOUT)
    except CliError as exc:
        if interrupted:
            io.note(f"j5: {exc}")
            return ExitCode.INTERRUPTED
        raise
    if interrupted:
        return ExitCode.INTERRUPTED
    if follower.gave_up:
        return ExitCode.FAILED
    return STATUS_CODES.get(status or "", ExitCode.FAILED)


class RunFollower:
    """Renders the events of one run until ``run_finished``, handling Ctrl+C and pauses."""

    def __init__(self, client: EngineClient, run_id: str, options: RunOptions, io: Output,
                 controls: Controls, debugger: Debugger | None = None) -> None:
        self.client = client
        self.debugger = debugger
        self._prompt: asyncio.Future[None] | None = None
        """The debugger prompt of the current pause, if any."""
        self.run_id = run_id
        self.options = options
        self.io = io
        self.controls = controls
        self.renderer = RunRenderer(style=io.style, seed=options.seed)
        self.aborted = False
        self._step: str | None = None
        """The step last started: the one a pause for an error or interference is about."""
        self._finished: dict[str, Any] | None = None
        """The last ``step_finished`` payload, until the next ``step_started``: a pause
        right after it (a ``pause:`` step) is after that step, not before it."""
        self._skipping = False
        """``step_over`` was sent to skip a step; the next ``step`` pause is resumed."""
        self._unattended_retries: dict[str, int] = {}
        """Retries sent without a terminal, per step, for error and interference pauses."""
        self.gave_up = False
        """The run was stopped because a step kept failing with nobody to ask."""
        self._background: set[asyncio.Future[Any]] = set()

    async def follow(self) -> str | None:
        """The final status, or None if the user gave up waiting for it."""
        events = self.client.events()
        next_event: asyncio.Future[Event] | None = None
        next_control: asyncio.Future[Control] | None = None
        deadline: float | None = None
        loop = asyncio.get_running_loop()
        try:
            while True:
                if next_event is None:
                    next_event = asyncio.ensure_future(anext(events))
                if next_control is None:
                    next_control = asyncio.ensure_future(self.controls.queue.get())
                timeout = None if deadline is None else max(0.0, deadline - loop.time())
                waiters: set[asyncio.Future[Any]] = {next_event, next_control}
                done, _ = await asyncio.wait(waiters, timeout=timeout,
                                             return_when=asyncio.FIRST_COMPLETED)
                if next_control in done:
                    control = next_control.result()
                    next_control = None
                    if control is Control.ABORT:
                        self.io.note("j5: interrupted again; not waiting for the engine")
                        self.aborted = True
                        return None
                    if control is Control.STOP and deadline is None:
                        self._end_prompt()
                        self.io.note("j5: stopping the run (Ctrl+C again to quit now)")
                        self._send("stop", {"run_id": self.run_id, "reason": "user"})
                        deadline = loop.time() + STOP_WAIT
                    elif control is Control.RESUME:
                        self._send("resume", {"run_id": self.run_id})
                    elif control is Control.SKIP:
                        self._skipping = True
                        self._send("step_over", {"run_id": self.run_id})
                if next_event in done:
                    try:
                        event = next_event.result()
                    except StopAsyncIteration:
                        raise CliError("the engine closed the event stream") from None
                    next_event = None
                    status = self._handle(event)
                    if status is not None:
                        return status
                if not done:
                    self.io.note(f"j5: the run did not finish {STOP_WAIT:g}s after stop; "
                                 "giving up")
                    self.aborted = True
                    return None
        finally:
            for task in (next_event, next_control, *self._background):
                if task is not None and not task.done():
                    task.cancel()
            if next_event is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await next_event
            if isinstance(events, AsyncGenerator):
                with contextlib.suppress(Exception):
                    await events.aclose()

    def _handle(self, event: Event) -> str | None:
        """Render ``event``; return the status when it is our ``run_finished``."""
        if event.type == "ready" or event.run_id not in (self.run_id, None):
            return None
        if self.options.as_json:
            self.io.line(encode(event))
        elif event.run_id is not None or event.type == "log":
            for line in self.renderer.render(event):
                self.io.line(line)
        if event.run_id != self.run_id:
            return None
        if self.debugger is not None:
            self.debugger.observe(event)
        if event.type == "step_started":
            self._step = str(event.payload.get("step_id") or "") or None
            self._finished = None
        elif event.type == "step_finished":
            self._finished = dict(event.payload)
        if event.type == "state_changed" and event.payload.get("state") == "paused":
            self._paused(event.payload.get("reason"))
        if event.type == "run_finished":
            self._end_prompt()
            status = event.payload.get("status")
            return status if isinstance(status, str) else "unknown"
        return None

    def _paused(self, reason: Any) -> None:
        if self.controls.interrupts:
            return
        if self.debugger is not None:
            if self._prompt is None or self._prompt.done():
                self._prompt = asyncio.ensure_future(
                    self.debugger.paused(reason, self._step, self._finished))
                self._background.add(self._prompt)
                self._prompt.add_done_callback(self._background.discard)
            return
        if self._skipping and reason == "step":
            # The pause that follows skipping a step: carry on without asking again.
            self._skipping = False
            self._send("resume", {"run_id": self.run_id})
            return
        self._skipping = False
        interactive = self.options.interactive and not self.options.as_json
        step = f" '{self._step}'" if self._step else ""
        if reason in ("interference", "error"):
            what = (f"you moved the mouse during step{step}" if reason == "interference"
                    else f"step{step} failed")
            if interactive:
                self.io.note(f"j5: {what}; press Enter to retry the step, or s + Enter to "
                             "skip it (Ctrl+C stops the run)")
                self.controls.wait_for_enter(skippable=True)
            else:
                key = self._step or ""
                retries = self._unattended_retries.get(key, 0)
                if retries >= UNATTENDED_RETRIES:
                    self.io.note(f"j5: {what} again; stopping the run after "
                                 f"{retries} retries (no terminal to ask)")
                    self.gave_up = True
                    self._send("stop", {"run_id": self.run_id, "reason": "user"})
                else:
                    self._unattended_retries[key] = retries + 1
                    self.io.note(f"j5: {what}; retrying it (no terminal to ask)")
                    self._send("resume", {"run_id": self.run_id})
            return
        if interactive:
            self.io.note("j5: the run is paused; press Enter to resume or Ctrl+C to stop "
                         "(for the debugger, run with --step or --break)")
            self.controls.wait_for_enter()
        else:
            self.io.note("j5: the run paused; resuming it (no terminal to ask)")
            self._send("resume", {"run_id": self.run_id})

    def _end_prompt(self) -> None:
        """Cancel a debugger prompt still waiting for a command (the run is ending)."""
        if self._prompt is not None and not self._prompt.done():
            self._prompt.cancel()
            if self.debugger is not None and self.debugger.at_prompt:
                self.debugger.write("")  # end the prompt's line

    def _send(self, command: str, payload: Mapping[str, Any]) -> None:
        """Send a control command without blocking the event loop of :meth:`follow`."""
        task = asyncio.ensure_future(self._call(command, payload))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _call(self, command: str, payload: Mapping[str, Any]) -> None:
        try:
            await self.client.call(command, payload)
        except EngineError as exc:
            if exc.code != "not_running":
                self.io.note(f"j5: {command} failed: [{exc.code}] {exc.error.message}")
        except (ClientError, TimeoutError):
            pass  # the engine died or is closing; the event stream reports it


# -- runs (the run history) ----------------------------------------------------------------


def history_error(exc: EngineError, run_id: str) -> tuple[str, int]:
    """The message and exit code of a failed ``get_run`` or ``delete_run``."""
    if exc.code == "not_running":
        return f"no saved run {run_id}", ExitCode.FAILED
    if exc.code == "validation_failed":
        return (f"{run_id!r} is not a run id (r-YYYYMMDD-HHMMSS-xxxx; see j5 runs)",
                ExitCode.USAGE)
    return f"{run_id}: [{exc.code}] {exc.error.message}", ExitCode.FAILED


async def runs_list(cmd: Sequence[str], *, limit: int, as_json: bool, runs_dir: str,
                    io: Output) -> int:
    async with engine(cmd, io) as client:
        try:
            result = await client.call("list_runs", {"limit": limit})
        except EngineError as exc:
            raise CliError(f"list_runs failed: [{exc.code}] {exc.error.message}") from exc
    runs = result.get("runs")
    if not isinstance(runs, list):
        raise CliError("the engine answered list_runs without runs")
    if as_json:
        io.line(json.dumps(result, indent=2, ensure_ascii=False))
    elif not runs:
        io.note(f"j5: no saved runs in {runs_dir}")
    else:
        for line in format_runs([r for r in runs if isinstance(r, dict)], io.style):
            io.line(line)
    return ExitCode.OK


async def runs_show(cmd: Sequence[str], run_id: str, *, as_json: bool, runs_dir: str,
                    io: Output) -> int:
    async with engine(cmd, io) as client:
        try:
            result = await client.call("get_run", {"run_id": run_id})
        except EngineError as exc:
            message, code = history_error(exc, run_id)
            io.note(f"j5: {message}")
            return code
    raw_events = result.get("events")
    events = [e for e in raw_events if isinstance(e, dict)] if isinstance(raw_events, list) else []
    if as_json:
        for raw in events:
            io.line(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
        return ExitCode.OK
    run = result.get("run")
    meta: dict[str, Any] = run if isinstance(run, dict) else {}
    captures = [str(c) for c in result.get("captures") or []]
    for line in format_run_summary(run_id, meta, captures, os.path.join(runs_dir, run_id),
                                   io.style):
        io.line(line)
    seed = meta.get("seed")
    renderer = RunRenderer(style=io.style, seed=seed if isinstance(seed, int) else None)
    finished = False
    for raw in events:
        with contextlib.suppress(ProtocolError):
            event = decode(json.dumps(raw))
            if isinstance(event, Event):
                finished = finished or event.type == "run_finished"
                for line in renderer.render(event):
                    io.line(line)
    if not finished:
        io.line(io.style.paint("(no run_finished: the run is still active, or the engine "
                               "ended before it finished)", "yellow"))
    return ExitCode.OK


async def runs_delete(cmd: Sequence[str], run_ids: Sequence[str], *, io: Output) -> int:
    exit_code: int = ExitCode.OK
    async with engine(cmd, io) as client:
        for run_id in run_ids:
            try:
                await client.call("delete_run", {"run_id": run_id})
            except EngineError as exc:
                message, code = history_error(exc, run_id)
                io.note(f"j5: {message}")
                exit_code = max(exit_code, code)
            else:
                io.line(f"deleted {run_id}")
    return exit_code


def run_sync(coro: Coroutine[Any, Any, int]) -> int:
    """``asyncio.run`` that maps Ctrl+C outside a run to exit code 130."""
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        return ExitCode.INTERRUPTED
