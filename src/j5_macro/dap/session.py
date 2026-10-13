"""Translates Debug Adapter Protocol requests into engine commands, and engine events
into DAP events (docs/contract.md, "Debug Adapter Protocol").

:class:`DebugSession` knows nothing about stdio: it receives decoded DAP messages through
:meth:`DebugSession.handle`, sends DAP messages through a callback and drives the engine
through the small :class:`Engine` interface, so it can be tested with a fake engine.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from j5_macro.cli.options import FAST_PROFILE
from j5_macro.client import ClientError, EngineDied, EngineError
from j5_macro.dap.script_map import ScriptMap, StepInfo, resolve_line
from j5_macro.protocol import ErrorInfo, Event

JsonObject = dict[str, Any]
Send = Callable[[JsonObject], None]
DeathHook = Callable[[list[str], int | None], Awaitable[None]]

THREAD_ID = 1
THREAD_NAME = "j5 run"
SCOPE_REF = 1
"""``variablesReference`` of the single "Variables" scope."""
STOP_WAIT = 5.0
"""Seconds ``disconnect`` waits for ``run_finished`` after sending ``stop``."""
CLOSE_TIMEOUT = 3.0
PREVIEW_LENGTH = 80
EXIT_CODES = {"completed": 0, "failed": 1, "stopped": 3}
MASK = "***"

CAPABILITIES: JsonObject = {
    "supportsConfigurationDoneRequest": True,
    "supportsEvaluateForHovers": True,
    "supportsSetVariable": True,
    "supportsTerminateRequest": True,
    "supportsRestartRequest": False,
    "supportsStepBack": False,
    "exceptionBreakpointFilters": [],
}

STEP_COMMANDS = {"continue": "resume", "next": "step_over", "stepIn": "step_into",
                 "stepOut": "step_out", "pause": "pause"}


class Engine(Protocol):
    """What the session needs from :class:`j5_macro.client.EngineClient`."""

    async def call(self, type: str, payload: Mapping[str, Any] | None = None) -> JsonObject:
        ...

    def events(self) -> AsyncIterator[Event]:
        ...

    async def close(self, timeout: float = ...) -> int | None:
        ...


EngineFactory = Callable[[DeathHook], Awaitable[Engine]]
"""Starts an engine (after its ``hello``); the hook runs if the engine dies."""


class DapError(Exception):
    """Fails the current request with this message, shown to the user."""


def problem_line(path: str, error: ErrorInfo | Mapping[str, Any], prefix: str = "") -> str:
    """``path:line: message`` for a load error or warning."""
    if isinstance(error, ErrorInfo):
        line, message, code = error.line, error.message, error.code
    else:
        line, message, code = error.get("line"), str(error.get("message", "")), None
    where = f"{path}:{line}" if isinstance(line, int) else path
    text = f"{where}: {prefix}{message}"
    return f"{text} [{code}]" if code else text


def load_problems(error: ErrorInfo) -> tuple[list[ErrorInfo], list[JsonObject]]:
    """Errors and warnings of a failed ``load``: ``details.errors`` or the error itself."""
    details = error.details or {}
    errors: list[ErrorInfo] = []
    raw = details.get("errors")
    for item in raw if isinstance(raw, list) else []:
        with contextlib.suppress(KeyError, TypeError, ValueError):
            errors.append(ErrorInfo.from_json(item))
    raw_warnings = details.get("warnings")
    warnings = ([w for w in raw_warnings if isinstance(w, dict)]
                if isinstance(raw_warnings, list) else [])
    return errors or [error], warnings


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "map"
    return type(value).__name__


def format_value(value: Any) -> str:
    """How a value shows in the Variables view: JSON, shortened for containers."""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    if isinstance(value, list | dict) and len(text) > PREVIEW_LENGTH:
        text = text[:PREVIEW_LENGTH - 3] + "..."
    return text


def parse_value(text: str) -> Any:
    """A value typed in the Variables view: JSON when it parses, else the text itself."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def child_expression(parent: str | None, key: str | int) -> str | None:
    if parent is None:
        return None
    return f"{parent}[{json.dumps(key, ensure_ascii=False)}]"


def _contains_mask(value: Any) -> bool:
    if isinstance(value, str):
        return MASK in value
    if isinstance(value, list):
        return any(_contains_mask(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_mask(item) for item in value.values())
    return False


class _Container:
    """A list or map shown in the Variables view, and where it lives."""

    def __init__(self, value: list[Any] | dict[str, Any], root: str | None,
                 keys: tuple[str | int, ...], expression: str | None) -> None:
        self.value = value
        self.root = root
        """Variable it belongs to (None for an ``evaluate`` result: read only)."""
        self.keys = keys
        self.expression = expression


class DebugSession:
    def __init__(self, send: Send, engine_factory: EngineFactory,
                 environ: Mapping[str, str] | None = None) -> None:
        self._send_raw = send
        self._engine_factory = engine_factory
        self._environ = os.environ if environ is None else environ
        self._seq = 0
        self._lock = asyncio.Lock()
        self._after: list[JsonObject] = []
        self.closed = asyncio.Event()

        self._lines_at_1 = True
        self._columns_at_1 = True
        self._engine: Engine | None = None
        self._pump: asyncio.Task[None] | None = None

        self._program: str | None = None
        self._source: list[str] | None = None
        self._script_id: str | None = None
        self._map = ScriptMap()
        self._mode = "normal"
        self._run_payload: JsonObject = {}
        self._no_debug = False
        self._stop_on_entry = False
        self._entry_breakpoint: str | None = None
        """Dry runs cannot start paused: a breakpoint on the first step stands in."""

        self._breakpoints: dict[str, list[tuple[int, int]]] = {}
        """Requested breakpoints per normalised source path: ``(id, engine line)``."""
        self._next_breakpoint_id = 1
        self._configured = False

        self._run_id: str | None = None
        self._run_ready = asyncio.Event()
        self._finished = asyncio.Event()
        self._run_over = False
        self._terminated = False
        self._entry_pending = False
        self._last_failure: JsonObject | None = None
        self._loop_index: dict[str, tuple[int, int | None]] = {}
        self._state: JsonObject | None = None
        self._containers: dict[int, _Container] = {}
        self._next_ref = SCOPE_REF + 1

    # -- sending -----------------------------------------------------------------------

    def _send(self, message: JsonObject) -> None:
        self._seq += 1
        self._send_raw({"seq": self._seq, **message})

    def _respond(self, request: Mapping[str, Any], body: JsonObject | None = None) -> None:
        message: JsonObject = {"type": "response", "request_seq": request.get("seq", 0),
                               "success": True, "command": request.get("command", "")}
        if body is not None:
            message["body"] = body
        self._send(message)

    def _respond_error(self, request: Mapping[str, Any], text: str) -> None:
        self._send({"type": "response", "request_seq": request.get("seq", 0),
                    "success": False, "command": request.get("command", ""), "message": text,
                    "body": {"error": {"id": 1, "format": text, "showUser": True}}})

    def event(self, name: str, body: JsonObject | None = None) -> None:
        message: JsonObject = {"type": "event", "event": name}
        if body is not None:
            message["body"] = body
        self._send(message)

    def _event_after(self, name: str, body: JsonObject | None = None) -> None:
        """Send an event right after the response of the request being handled."""
        self._after.append({"event": name, "body": body})

    def output(self, text: str, category: str = "console", line: int | None = None) -> None:
        body: JsonObject = {"category": category, "output": text + "\n"}
        if line is not None and self._program is not None:
            body["source"] = self._source_ref()
            body["line"] = self._to_client_line(line)
        self.event("output", body)

    # -- lines -------------------------------------------------------------------------

    def _to_client_line(self, line: int) -> int:
        return line if self._lines_at_1 else line - 1

    def _from_client_line(self, line: int) -> int:
        return line if self._lines_at_1 else line + 1

    def _client_column(self) -> int:
        return 1 if self._columns_at_1 else 0

    def _source_ref(self) -> JsonObject:
        assert self._program is not None
        return {"name": os.path.basename(self._program), "path": self._program}

    # -- requests ----------------------------------------------------------------------

    async def handle(self, message: Mapping[str, Any]) -> None:
        """Handle one message from the client; requests get exactly one response."""
        if message.get("type") != "request":
            return
        command = str(message.get("command", ""))
        raw_args = message.get("arguments")
        args: Mapping[str, Any] = raw_args if isinstance(raw_args, Mapping) else {}
        if command == "disconnect":
            await self._disconnect(message, args)
            return
        handler = self._handlers().get(command)
        async with self._lock:
            self._after = []
            try:
                if handler is None:
                    raise DapError(f"unsupported request '{command}'")
                body = await handler(args)
            except DapError as exc:
                self._respond_error(message, str(exc))
            except EngineError as exc:
                self._respond_error(message, exc.error.message)
            except EngineDied as exc:
                self._respond_error(message, f"the j5 engine died (exit code {exc.exit_code})")
            except (ClientError, TimeoutError) as exc:
                self._respond_error(message, f"the j5 engine did not answer: {exc}")
            else:
                self._respond(message, body)
            after, self._after = self._after, []
            for item in after:
                self.event(item["event"], item["body"])

    def _handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Awaitable[JsonObject | None]]]:
        return {
            "initialize": self._initialize,
            "launch": self._launch,
            "setBreakpoints": self._set_breakpoints,
            "setExceptionBreakpoints": self._set_exception_breakpoints,
            "configurationDone": self._configuration_done,
            "threads": self._threads,
            "stackTrace": self._stack_trace,
            "scopes": self._scopes,
            "variables": self._variables,
            "setVariable": self._set_variable,
            "evaluate": self._evaluate,
            "continue": self._control("continue"),
            "next": self._control("next"),
            "stepIn": self._control("stepIn"),
            "stepOut": self._control("stepOut"),
            "pause": self._control("pause"),
            "terminate": self._terminate,
        }

    async def _initialize(self, args: Mapping[str, Any]) -> JsonObject:
        self._lines_at_1 = args.get("linesStartAt1", True) is not False
        self._columns_at_1 = args.get("columnsStartAt1", True) is not False
        self._event_after("initialized")
        return dict(CAPABILITIES)

    async def _set_exception_breakpoints(self, args: Mapping[str, Any]) -> JsonObject:
        return {"breakpoints": []}

    # -- launch ------------------------------------------------------------------------

    async def _launch(self, args: Mapping[str, Any]) -> None:
        if self._engine is not None:
            raise DapError("this debug session already launched a script")
        program = args.get("program")
        if not isinstance(program, str) or not program.strip():
            raise DapError("launch needs 'program': the path of a j5 YAML script")
        cwd = args.get("cwd")
        base = cwd if isinstance(cwd, str) and cwd else os.getcwd()
        program = os.path.abspath(os.path.join(base, os.path.expanduser(program)))
        params = self._launch_params(args)
        seed = args.get("seed")
        if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
            raise DapError("'seed' must be an integer")
        self._no_debug = args.get("noDebug") is True
        dry = args.get("dryRun") is True
        self._stop_on_entry = args.get("stopOnEntry") is True and not self._no_debug
        self._mode = "dry" if dry else "step" if self._stop_on_entry else "normal"
        payload: JsonObject = {"mode": self._mode}
        if params:
            payload["params"] = params
        if seed is not None:
            payload["seed"] = seed
        if args.get("fast") is True:
            payload["input_profile"] = copy.deepcopy(FAST_PROFILE)
        if args.get("allowExec") is True:
            payload["allow_exec"] = True
        self._run_payload = payload
        self._program = program

        engine = await self._start_engine()
        result = await self._load(engine, program)
        self._script_id = str(result["script_id"])
        self._map = ScriptMap.from_tree(result.get("steps"))
        self._source = _read_lines(program)
        for warning in result.get("warnings") or []:
            if isinstance(warning, dict):
                self.output(problem_line(program, warning, "warning: "), "console")
        missing = [str(p.get("name")) for p in result.get("params") or []
                   if isinstance(p, dict) and p.get("required") and p.get("name") not in params]
        if missing:
            await self._close_engine()
            names = ", ".join(missing)
            raise DapError(f"{os.path.basename(program)} needs the param(s) {names}: set them "
                           "in the launch configuration's 'params' or 'paramsFromEnv'")
        if dry and self._stop_on_entry and self._map.first_step is not None:
            self._entry_breakpoint = self._map.first_step.id
        await self._sync_breakpoints(announce=True)
        if self._configured:
            await self._start_run()
        return None

    def _launch_params(self, args: Mapping[str, Any]) -> JsonObject:
        params = args.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise DapError("'params' must be an object of param names to values")
        values: JsonObject = dict(params)
        from_env = args.get("paramsFromEnv")
        if from_env is None:
            return values
        if not isinstance(from_env, dict):
            raise DapError("'paramsFromEnv' must be an object of param names to "
                           "environment variable names")
        for name, variable in from_env.items():
            if not isinstance(variable, str) or not variable:
                raise DapError(f"paramsFromEnv.{name}: expected an environment variable name")
            if name in values:
                raise DapError(f"param '{name}' is given in both 'params' and 'paramsFromEnv'")
            if variable not in self._environ:
                raise DapError(f"paramsFromEnv.{name}: the environment variable {variable} "
                               "is not set")
            values[name] = self._environ[variable]
        return values

    async def _start_engine(self) -> Engine:
        try:
            engine = await self._engine_factory(self._on_engine_died)
        except EngineDied as exc:
            tail = "; ".join(line for line in exc.stderr_tail[-5:] if line.strip())
            raise DapError(f"the j5 engine died while starting (exit code {exc.exit_code})"
                           + (f": {tail}" if tail else "")) from exc
        except TimeoutError as exc:
            raise DapError("the j5 engine did not answer in time") from exc
        except (OSError, ValueError, ClientError) as exc:
            raise DapError(f"cannot start the j5 engine: {exc}") from exc
        self._engine = engine
        self._pump = asyncio.create_task(self._pump_events(engine))
        return engine

    async def _load(self, engine: Engine, program: str) -> JsonObject:
        try:
            return await engine.call("load", {"path": program})
        except EngineError as exc:
            errors, warnings = load_problems(exc.error)
            for error in errors:
                self.output(problem_line(program, error), "stderr")
            for warning in warnings:
                self.output(problem_line(program, warning, "warning: "), "stderr")
            await self._close_engine()
            first = problem_line(program, errors[0])
            more = (f" (and {len(errors) - 1} more problem(s), see the Debug Console)"
                    if len(errors) > 1 else "")
            raise DapError(f"cannot load the script: {first}{more}") from exc

    # -- breakpoints -------------------------------------------------------------------

    def _program_key(self) -> str | None:
        return None if self._program is None else _path_key(self._program)

    async def _set_breakpoints(self, args: Mapping[str, Any]) -> JsonObject:
        source = args.get("source")
        path = source.get("path") if isinstance(source, Mapping) else None
        if not isinstance(path, str) or not path:
            raise DapError("setBreakpoints needs source.path")
        lines: list[int] = []
        requested = args.get("breakpoints")
        if isinstance(requested, list):
            lines = [int(b["line"]) for b in requested
                     if isinstance(b, Mapping) and isinstance(b.get("line"), int)]
        elif isinstance(args.get("lines"), list):
            lines = [int(n) for n in args["lines"] if isinstance(n, int)]
        entries = []
        for line in lines:
            entries.append((self._next_breakpoint_id, self._from_client_line(line)))
            self._next_breakpoint_id += 1
        key = _path_key(path)
        self._breakpoints[key] = entries
        results = [self._describe_breakpoint(key, bp_id, line) for bp_id, line in entries]
        if key == self._program_key():
            await self._sync_breakpoints(announce=False)
        return {"breakpoints": results}

    def _describe_breakpoint(self, key: str, bp_id: int, line: int) -> JsonObject:
        result: JsonObject = {"id": bp_id, "verified": False,
                              "line": self._to_client_line(line)}
        if self._script_id is None:
            result["message"] = "pending until the script is loaded"
            return result
        if key != self._program_key():
            result["message"] = (f"not the script being debugged "
                                 f"({os.path.basename(self._program or '')})")
            return result
        resolution = resolve_line(self._map, line, self._source)
        if resolution.step is None or resolution.step.line is None:
            result["message"] = resolution.message or "no step on this line"
            return result
        result.update(verified=True, line=self._to_client_line(resolution.step.line),
                      source=self._source_ref())
        return result

    def _breakpoint_step_ids(self) -> list[str]:
        key = self._program_key()
        if self._no_debug or key is None:
            return []
        ids: list[str] = []
        for _, line in self._breakpoints.get(key, []):
            step = resolve_line(self._map, line, self._source).step
            if step is not None and step.id not in ids:
                ids.append(step.id)
        if self._entry_breakpoint is not None and self._entry_breakpoint not in ids:
            ids.append(self._entry_breakpoint)
        return ids

    async def _sync_breakpoints(self, *, announce: bool) -> None:
        """Send the breakpoints of the launched script to the engine; with ``announce``,
        tell the client how the ones set before the launch resolved."""
        if self._engine is None or self._script_id is None:
            return
        await self._engine.call("set_breakpoints", {"script_id": self._script_id,
                                                    "step_ids": self._breakpoint_step_ids()})
        if not announce:
            return
        for key, entries in self._breakpoints.items():
            for bp_id, line in entries:
                self._event_after("breakpoint", {
                    "reason": "changed",
                    "breakpoint": self._describe_breakpoint(key, bp_id, line)})

    # -- running -----------------------------------------------------------------------

    async def _configuration_done(self, args: Mapping[str, Any]) -> None:
        self._configured = True
        if self._script_id is not None and self._run_id is None:
            await self._start_run()
        return None

    async def _start_run(self) -> None:
        assert self._engine is not None and self._script_id is not None
        self._entry_pending = self._stop_on_entry
        try:
            result = await self._engine.call("run", {"script_id": self._script_id,
                                                     **self._run_payload})
        except EngineError as exc:
            self.output(f"cannot start the run: [{exc.code}] {exc.error.message}", "stderr")
            self._run_ready.set()
            self._end(None, after=True)
            raise DapError(f"cannot start the run: {exc.error.message}") from exc
        except BaseException:
            self._run_ready.set()
            raise
        run_id = result.get("run_id")
        self._run_id = run_id if isinstance(run_id, str) else None
        self._run_ready.set()

    def _active_run(self) -> str:
        if self._run_id is None or self._run_over:
            raise DapError("no run is active")
        return self._run_id

    def _control(self, request: str) -> Callable[[Mapping[str, Any]],
                                                  Awaitable[JsonObject | None]]:
        command = STEP_COMMANDS[request]

        async def handler(args: Mapping[str, Any]) -> JsonObject | None:
            run_id = self._active_run()
            assert self._engine is not None
            await self._engine.call(command, {"run_id": run_id})
            if command != "pause":
                self._forget_state()
            return {"allThreadsContinued": True} if request == "continue" else None

        return handler

    async def _terminate(self, args: Mapping[str, Any]) -> None:
        if self._engine is None or self._run_id is None or self._run_over:
            self._end(None, after=True)
            return None
        await self._stop_run()
        return None

    async def _stop_run(self) -> None:
        if self._engine is None or self._run_id is None or self._run_over:
            return
        try:
            await self._engine.call("stop", {"run_id": self._run_id, "reason": "user"})
        except EngineError as exc:
            if exc.code != "not_running":
                raise

    async def _disconnect(self, request: Mapping[str, Any], args: Mapping[str, Any]) -> None:
        async with self._lock:
            with contextlib.suppress(ClientError, TimeoutError, DapError):
                await self._stop_run()
        if self._run_id is not None and not self._run_over:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._finished.wait(), STOP_WAIT)
        await self._close_engine()
        self._respond(request)
        self.closed.set()

    async def shutdown(self) -> None:
        """The client went away without ``disconnect``: stop the run and the engine."""
        async with self._lock:
            with contextlib.suppress(ClientError, TimeoutError, DapError):
                await self._stop_run()
        if self._run_id is not None and not self._run_over:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._finished.wait(), STOP_WAIT)
        await self._close_engine()
        self.closed.set()

    async def _close_engine(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None:
            with contextlib.suppress(ClientError, OSError):
                await engine.close(CLOSE_TIMEOUT)
        pump, self._pump = self._pump, None
        if pump is not None and pump is not asyncio.current_task():
            if not pump.done():
                pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump

    def _end(self, exit_code: int | None, *, after: bool = False) -> None:
        """Send ``exited`` (when known) and ``terminated``, once."""
        self._run_over = True
        self._finished.set()
        if self._terminated:
            return
        self._terminated = True
        send = self._event_after if after else self.event
        if exit_code is not None:
            send("exited", {"exitCode": exit_code})
        send("terminated", None)

    # -- inspection --------------------------------------------------------------------

    async def _threads(self, args: Mapping[str, Any]) -> JsonObject:
        return {"threads": [{"id": THREAD_ID, "name": THREAD_NAME}]}

    def _forget_state(self) -> None:
        self._state = None
        self._containers.clear()
        self._next_ref = SCOPE_REF + 1

    async def _get_state(self) -> JsonObject:
        if self._state is None:
            run_id = self._active_run()
            assert self._engine is not None
            self._state = await self._engine.call("get_state", {"run_id": run_id})
        return self._state

    async def _stack_trace(self, args: Mapping[str, Any]) -> JsonObject:
        if self._run_id is None or self._run_over:
            return {"stackFrames": [], "totalFrames": 0}
        state = await self._get_state()
        frames = self._frames(state)
        start = args.get("startFrame")
        levels = args.get("levels")
        first = start if isinstance(start, int) and start > 0 else 0
        selected = frames[first:]
        if isinstance(levels, int) and levels > 0:
            selected = selected[:levels]
        return {"stackFrames": selected, "totalFrames": len(frames)}

    def _frames(self, state: Mapping[str, Any]) -> list[JsonObject]:
        step_id = state.get("step_id")
        current = self._map.by_id.get(step_id) if isinstance(step_id, str) else None
        if current is None and step_id is None:
            current = self._map.first_step  # paused before the first step
        if current is None:
            return [{"id": 1, "name": str(step_id or THREAD_NAME),
                     "line": 0, "column": 0}]
        raw_path = state.get("path")
        path = (tuple(int(p) for p in raw_path) if isinstance(raw_path, list) and raw_path
                else current.path)
        chain = [current, *self._map.enclosing(path)]
        iterations = self._iterations(state, chain[1:])
        frames = []
        for index, step in enumerate(chain):
            frame: JsonObject = {"id": index + 1, "name": self._frame_name(step, iterations),
                                 "line": 0, "column": 0}
            if step.line is not None and self._program is not None:
                frame.update(line=self._to_client_line(step.line),
                             column=self._client_column(), source=self._source_ref())
            frames.append(frame)
        return frames

    def _iterations(self, state: Mapping[str, Any],
                    enclosing: Sequence[StepInfo]) -> dict[str, int]:
        """Current iteration index of each enclosing loop: ``get_state``'s ``iterations``
        when it reports them, else the latest ``loop_iteration`` of each loop."""
        loops = [step for step in reversed(enclosing) if step.type == "loop"]
        reported = state.get("iterations")
        if (isinstance(reported, list) and len(reported) == len(loops)
                and all(isinstance(i, int) for i in reported)):
            return {loop.id: int(index) for loop, index in zip(loops, reported, strict=True)}
        return {loop.id: self._loop_index[loop.id][0]
                for loop in loops if loop.id in self._loop_index}

    def _frame_name(self, step: StepInfo, iterations: Mapping[str, int]) -> str:
        name = f"{step.id} ({step.type})"
        if step.id in iterations:
            index = iterations[step.id]
            total = self._loop_index.get(step.id, (index, None))[1]
            name += f" iteration {index + 1}" + (f" of {total}" if total is not None else "")
        return name

    async def _scopes(self, args: Mapping[str, Any]) -> JsonObject:
        if self._run_id is None or self._run_over:
            return {"scopes": []}
        return {"scopes": [{"name": "Variables", "variablesReference": SCOPE_REF,
                            "expensive": False, "presentationHint": "locals"}]}

    def _reference(self, value: Any, root: str | None, keys: tuple[str | int, ...],
                   expression: str | None) -> int:
        if not isinstance(value, list | dict) or not value:
            return 0
        ref = self._next_ref
        self._next_ref += 1
        self._containers[ref] = _Container(value, root, keys, expression)
        return ref

    def _variable(self, name: str, value: Any, root: str | None,
                  keys: tuple[str | int, ...], expression: str | None) -> JsonObject:
        variable: JsonObject = {"name": name, "value": format_value(value),
                                "type": type_name(value),
                                "variablesReference": self._reference(value, root, keys,
                                                                      expression)}
        if expression is not None:
            variable["evaluateName"] = expression
        return variable

    async def _variables(self, args: Mapping[str, Any]) -> JsonObject:
        ref = args.get("variablesReference")
        if ref == SCOPE_REF:
            if self._run_id is None or self._run_over:
                return {"variables": []}
            raw = (await self._get_state()).get("variables")
            values = raw if isinstance(raw, dict) else {}
            return {"variables": [self._variable(str(name), value, str(name), (), str(name))
                                  for name, value in values.items()]}
        container = self._containers.get(ref) if isinstance(ref, int) else None
        if container is None:
            return {"variables": []}
        items: list[tuple[str | int, Any]] = (
            list(enumerate(container.value)) if isinstance(container.value, list)
            else list(container.value.items()))
        return {"variables": [
            self._variable(str(key), value, container.root, (*container.keys, key),
                           child_expression(container.expression, key))
            for key, value in items]}

    async def _set_variable(self, args: Mapping[str, Any]) -> JsonObject:
        run_id = self._active_run()
        assert self._engine is not None
        ref, name, text = (args.get("variablesReference"), args.get("name"),
                           args.get("value"))
        if not isinstance(name, str) or not isinstance(text, str):
            raise DapError("setVariable needs a name and a value")
        value = parse_value(text)
        if ref == SCOPE_REF:
            await self._engine.call("set_variable", {"run_id": run_id, "name": name,
                                                     "value": value})
            self._forget_state()
            return self._set_result(value, name, (), name)
        container = self._containers.get(ref) if isinstance(ref, int) else None
        if container is None or container.root is None:
            raise DapError("only variables and their items can be changed")
        variables = (await self._get_state()).get("variables")
        if not isinstance(variables, dict) or container.root not in variables:
            raise DapError(f"variable '{container.root}' no longer exists")
        root_value = copy.deepcopy(variables[container.root])
        if _contains_mask(root_value):
            raise DapError(f"'{container.root}' holds a masked secret ({MASK}); set the "
                           "whole variable instead")
        target: Any = root_value
        try:
            for step in container.keys:
                target = target[step]
            key: str | int = int(name) if isinstance(target, list) else name
            target[key] = value
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DapError(f"cannot change '{name}': the value changed") from exc
        await self._engine.call("set_variable", {"run_id": run_id, "name": container.root,
                                                 "value": root_value})
        expression = child_expression(container.expression, key)
        root, keys = container.root, (*container.keys, key)
        self._forget_state()
        return self._set_result(value, root, keys, expression)

    def _set_result(self, value: Any, root: str, keys: tuple[str | int, ...],
                    expression: str | None) -> JsonObject:
        return {"value": format_value(value), "type": type_name(value),
                "variablesReference": self._reference(value, root, keys, expression)}

    async def _evaluate(self, args: Mapping[str, Any]) -> JsonObject:
        expression = args.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise DapError("nothing to evaluate")
        if self._run_id is None or self._run_over:
            raise DapError("expressions can only be evaluated while the run is paused")
        assert self._engine is not None
        result = await self._engine.call("evaluate", {"run_id": self._run_id,
                                                      "expression": expression})
        value = result.get("value")
        return {"result": format_value(value), "type": type_name(value),
                "variablesReference": self._reference(value, None, (), None)}

    # -- engine events -----------------------------------------------------------------

    async def _on_engine_died(self, held: list[str], exit_code: int | None) -> None:
        if not held:
            return
        from j5_macro.cli.release import release_held

        report = await asyncio.to_thread(release_held, held)
        self.output(f"j5: {report.message()}", "stderr")

    async def _pump_events(self, engine: Engine) -> None:
        try:
            async for event in engine.events():
                if event.run_id is not None:
                    await self._run_ready.wait()
                async with self._lock:
                    await self._on_event(event)
        except EngineDied as exc:
            async with self._lock:
                tail = "".join(f"\n  {line}" for line in exc.stderr_tail[-10:] if line.strip())
                self.output(f"the j5 engine died (exit code {exc.exit_code}){tail}", "stderr")
                if not self._run_over:
                    self._end(1 if self._run_id is not None else None)
        except (ClientError, OSError) as exc:
            self.output(f"lost the j5 engine: {exc}", "stderr")

    async def _on_event(self, event: Event) -> None:
        payload = event.payload
        if event.run_id is None:
            if event.type == "log":
                self._log(payload)
            return
        if event.run_id != self._run_id or self._run_over:
            return
        handler = getattr(self, f"_event_{event.type}", None)
        if handler is not None:
            await handler(payload)

    def _log(self, payload: Mapping[str, Any]) -> None:
        level = str(payload.get("level", "info"))
        message = str(payload.get("message", ""))
        category = "stderr" if level in ("error", "warning") else "console"
        text = message if level == "info" else f"{level}: {message}"
        step = self._map.by_id.get(str(payload.get("step_id")))
        self.output(text, category, step.line if step is not None else None)

    async def _event_log(self, payload: JsonObject) -> None:
        self._log(payload)

    async def _event_run_started(self, payload: JsonObject) -> None:
        self.output(f"run {self._run_id} started (mode {payload.get('mode')}, seed "
                    f"{payload.get('seed')}, backend {payload.get('backend')})")

    async def _event_step_started(self, payload: JsonObject) -> None:
        self._last_failure = None
        line = payload.get("line")
        self.output(f"> {payload.get('step_id')} ({payload.get('type')})", "console",
                    line if isinstance(line, int) else None)

    async def _event_step_failed(self, payload: JsonObject) -> None:
        error = payload.get("error")
        error = error if isinstance(error, dict) else {}
        self._last_failure = {"step_id": payload.get("step_id"), **error}
        line = payload.get("line")
        details = error.get("details")
        attempt = details.get("attempt") if isinstance(details, dict) else None
        tries = f" (attempt {attempt})" if isinstance(attempt, int) and attempt > 1 else ""
        self.output(f"step '{payload.get('step_id')}' failed{tries}: "
                    f"[{error.get('code')}] {error.get('message')}", "stderr",
                    line if isinstance(line, int) else None)

    async def _event_step_skipped(self, payload: JsonObject) -> None:
        self.output(f"skipped step '{payload.get('step_id')}'")

    async def _event_user_interference(self, payload: JsonObject) -> None:
        step = payload.get("step_id")
        where = f" during step '{step}'" if step else ""
        self.output(f"you moved the mouse{where} (expected {payload.get('expected')}, "
                    f"found {payload.get('actual')}); the run will "
                    f"{payload.get('action', 'pause')}", "stderr")

    async def _event_loop_iteration(self, payload: JsonObject) -> None:
        step_id = payload.get("step_id")
        index, total = payload.get("index"), payload.get("total")
        if isinstance(step_id, str) and isinstance(index, int):
            self._loop_index[step_id] = (index, total if isinstance(total, int) else None)

    async def _event_state_changed(self, payload: JsonObject) -> None:
        state, reason = payload.get("state"), payload.get("reason")
        self._forget_state()
        if state == "running":
            if not self._no_debug:
                self.event("continued", {"threadId": THREAD_ID, "allThreadsContinued": True})
        elif state == "paused":
            await self._paused(reason if isinstance(reason, str) else None)

    async def _paused(self, reason: str | None) -> None:
        assert self._engine is not None and self._run_id is not None
        if self._no_debug:
            if reason in ("error", "interference"):
                self.output("the run paused for an error; stopping it (no debugger)", "stderr")
                await self._engine.call("stop", {"run_id": self._run_id, "reason": "user"})
            else:
                await self._engine.call("resume", {"run_id": self._run_id})
            return
        body: JsonObject = {"threadId": THREAD_ID, "allThreadsStopped": True}
        if self._entry_pending and reason in ("step", "breakpoint"):
            self._entry_pending = False
            body["reason"] = "entry"
            if self._entry_breakpoint is not None:
                self._entry_breakpoint = None
                await self._sync_breakpoints(announce=False)
        elif reason == "breakpoint":
            body["reason"] = "breakpoint"
        elif reason == "step":
            body["reason"] = "step"
        elif reason == "error":
            failure = self._last_failure or {}
            body.update(reason="exception",
                        description=f"step '{failure.get('step_id', '?')}' failed",
                        text=str(failure.get("message") or "the step failed"))
        elif reason == "interference":
            body.update(reason="pause", description="user moved the mouse")
        else:
            body["reason"] = "pause"
        self.event("stopped", body)

    async def _event_run_finished(self, payload: JsonObject) -> None:
        status = str(payload.get("status", "failed"))
        duration = payload.get("duration_ms")
        text = f"run {status}" + (f" in {duration} ms" if isinstance(duration, int) else "")
        reason = payload.get("reason")
        if reason:
            text += f" (reason: {reason})"
        error = payload.get("error")
        if isinstance(error, dict):
            text += f": [{error.get('code')}] {error.get('message')}"
        self.output(text, "stderr" if status == "failed" else "console")
        self._end(EXIT_CODES.get(status, 1))


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _read_lines(path: str) -> list[str] | None:
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return handle.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
