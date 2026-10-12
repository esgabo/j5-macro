"""The ``j5`` subcommands that talk to the engine: ``validate``, ``run`` and ``steps``.

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

from j5_macro.cli.interrupt import Control, Controls
from j5_macro.cli.release import release_held
from j5_macro.cli.render import (
    RunRenderer,
    Style,
    format_problem,
    format_step_types,
    format_warning,
    validate_summary,
)
from j5_macro.client import ClientError, EngineClient, EngineDied, EngineError
from j5_macro.protocol import ErrorInfo, Event, encode

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
    return LoadOutcome(script_id, [], _warnings(result.get("warnings")))


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
        }, indent=2, ensure_ascii=False))
    else:
        for error in outcome.errors:
            io.line(format_problem(script, error, io.style))
        for warning in outcome.warnings:
            io.line(format_warning(script, warning, io.style))
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


@dataclass(slots=True)
class RunOptions:
    mode: str = "normal"
    vars: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    input_profile: dict[str, Any] | None = None
    as_json: bool = False
    interactive: bool = False
    """Offer "press Enter to resume" when the run pauses (stdin is a terminal)."""

    def payload(self, script_id: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"script_id": script_id, "mode": self.mode}
        if self.vars:
            payload["vars"] = self.vars
        if self.seed is not None:
            payload["seed"] = self.seed
        if self.input_profile is not None:
            payload["input_profile"] = self.input_profile
        return payload


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
            with controls.handle_sigint():
                try:
                    started = await client.call("run", options.payload(outcome.script_id))
                except EngineError as exc:
                    io.note(f"j5: cannot start the run: [{exc.code}] {exc.error.message}")
                    return ExitCode.USAGE
                run_id = started.get("run_id")
                if not isinstance(run_id, str):
                    raise CliError("the engine answered run without a run_id")
                follower = RunFollower(client, run_id, options, io, controls)
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
    return STATUS_CODES.get(status or "", ExitCode.FAILED)


class RunFollower:
    """Renders the events of one run until ``run_finished``, handling Ctrl+C and pauses."""

    def __init__(self, client: EngineClient, run_id: str, options: RunOptions, io: Output,
                 controls: Controls) -> None:
        self.client = client
        self.run_id = run_id
        self.options = options
        self.io = io
        self.controls = controls
        self.renderer = RunRenderer(style=io.style, seed=options.seed)
        self.aborted = False
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
                        self.io.note("j5: stopping the run (Ctrl+C again to quit now)")
                        self._send("stop", {"run_id": self.run_id, "reason": "user"})
                        deadline = loop.time() + STOP_WAIT
                    elif control is Control.RESUME:
                        self._send("resume", {"run_id": self.run_id})
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
        if event.type == "state_changed" and event.payload.get("state") == "paused":
            self._paused()
        if event.type == "run_finished":
            status = event.payload.get("status")
            return status if isinstance(status, str) else "unknown"
        return None

    def _paused(self) -> None:
        if self.controls.interrupts:
            return
        if self.options.interactive and not self.options.as_json:
            self.io.note("j5: the run is paused; press Enter to resume or Ctrl+C to stop "
                         "(the interactive debugger arrives with `j5 run --step`)")
            self.controls.wait_for_enter()
        else:
            self.io.note("j5: the run paused; resuming it (no terminal to ask)")
            self._send("resume", {"run_id": self.run_id})

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


def run_sync(coro: Coroutine[Any, Any, int]) -> int:
    """``asyncio.run`` that maps Ctrl+C outside a run to exit code 130."""
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        return ExitCode.INTERRUPTED
