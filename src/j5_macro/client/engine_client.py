"""Asyncio client that launches the engine and speaks the JSON-lines protocol.

See docs/contract.md, "Transporte y formato de mensajes". One background task
reads stdout (responses and events), another drains stderr, and a monitor
notices when the process goes away and fails whatever is still pending.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import itertools
import json
import logging
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from types import TracebackType
from typing import Any, Self

from j5_macro.client.errors import ClientClosed, ClientError, EngineDied, EngineError
from j5_macro.client.sequencer import EventSequencer
from j5_macro.client.stderr import StderrTail
from j5_macro.protocol import Command, Event, ProtocolError, Response, decode, encode

JsonObject = dict[str, Any]
DeathCallback = Callable[[list[str], int | None], Awaitable[None] | None]

DEFAULT_COMMAND: tuple[str, ...] = (sys.executable, "-m", "j5_macro.engine")
SUPPORTED_VERSIONS = (1, 1)
DEFAULT_READY_TIMEOUT = 15.0
DEFAULT_REQUEST_TIMEOUT = 30.0
DEFAULT_CLOSE_TIMEOUT = 5.0
EXIT_GRACE = 2.0
"""Seconds to wait for stdout EOF after exit (or for exit after EOF) before forcing it."""
STREAM_LIMIT = 16 * 1024 * 1024
"""Longest accepted stdout line; ``get_events`` results can be large."""

log = logging.getLogger(__name__)


class EngineClient:
    """A running engine subprocess. Create it with :meth:`start`."""

    def __init__(
        self, process: asyncio.subprocess.Process, *, on_engine_died: DeathCallback | None = None
    ) -> None:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise ValueError("the engine process needs piped stdin, stdout and stderr")
        self._process = process
        self._stdin = process.stdin
        self._on_engine_died = on_engine_died
        self._ids = itertools.count(1)
        self._pending: dict[str, asyncio.Future[Response]] = {}
        self._events: asyncio.Queue[Event | None] = asyncio.Queue()
        self._sequencer = EventSequencer()
        self._stderr = StderrTail()
        self._held: list[str] = []
        self._write_lock = asyncio.Lock()
        self._ready: asyncio.Future[Event] = asyncio.get_running_loop().create_future()
        self._closing = False
        self._failure: ClientError | None = None
        self._death: EngineDied | None = None
        self.ready_event: Event | None = None
        self.version: int | None = None
        self.capabilities: JsonObject = {}
        self._stderr_task = asyncio.create_task(self._stderr.drain(process.stderr))
        self._reader_task = asyncio.create_task(self._read_stdout(process.stdout))
        self._monitor_task = asyncio.create_task(self._monitor())

    # -- lifecycle -----------------------------------------------------------

    @classmethod
    async def start(
        cls,
        cmd: Sequence[str] | None = None,
        *,
        client_name: str = "j5-cli",
        ready_timeout: float = DEFAULT_READY_TIMEOUT,
        hello_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        on_engine_died: DeathCallback | None = None,
        env: Mapping[str, str] | None = None,
        cwd: str | os.PathLike[str] | None = None,
    ) -> Self:
        """Launch the engine, wait for ``ready`` and negotiate the version with ``hello``."""
        child_env = dict(os.environ if env is None else env)
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        process = await asyncio.create_subprocess_exec(
            *(cmd or DEFAULT_COMMAND),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
            env=child_env,
            cwd=cwd,
        )
        client = cls(process, on_engine_died=on_engine_died)
        try:
            client.ready_event = await asyncio.wait_for(asyncio.shield(client._ready),
                                                        ready_timeout)
            await client._hello(client_name, hello_timeout)
        except BaseException:
            await client.close(timeout=1.0)
            raise
        return client

    async def _hello(self, client_name: str, timeout: float) -> None:
        v_min, v_max = SUPPORTED_VERSIONS
        result = await self.call("hello", {"v_min": v_min, "v_max": v_max, "client": client_name},
                                 timeout=timeout)
        version = result.get("v")
        if not isinstance(version, int) or not v_min <= version <= v_max:
            raise ClientError(f"engine chose unsupported protocol version {version!r}")
        capabilities = result.get("capabilities")
        self.version = version
        self.capabilities = capabilities if isinstance(capabilities, dict) else {}

    async def close(self, timeout: float = DEFAULT_CLOSE_TIMEOUT) -> int | None:
        """Close stdin and wait for exit; terminate, then kill, after ``timeout`` each."""
        self._closing = True
        if not self._stdin.is_closing():
            self._stdin.close()
        with contextlib.suppress(ConnectionError, OSError):
            await self._stdin.wait_closed()
        if not await self._wait_exit(timeout):
            log.warning("engine did not exit after stdin closed; terminating it")
            with contextlib.suppress(ProcessLookupError):
                self._process.terminate()
            if not await self._wait_exit(timeout):
                log.warning("engine ignored terminate; killing it")
                with contextlib.suppress(ProcessLookupError):
                    self._process.kill()
                await self._process.wait()
        await self._monitor_task
        return self._process.returncode

    async def _wait_exit(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._process.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # -- state ---------------------------------------------------------------

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    @property
    def held(self) -> list[str]:
        """Keys and buttons held according to the latest ``input_state`` event."""
        return list(self._held)

    @property
    def stderr_tail(self) -> list[str]:
        return self._stderr.lines

    # -- commands ------------------------------------------------------------

    async def request(
        self,
        type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = DEFAULT_REQUEST_TIMEOUT,
    ) -> Response:
        """Send a command and wait for the response with the same id."""
        if self._failure is not None:
            raise self._failure
        if self._closing:
            raise ClientClosed("the client is closed")
        command = Command(type=type, id=f"c{next(self._ids)}", payload=dict(payload or {}))
        future: asyncio.Future[Response] = asyncio.get_running_loop().create_future()
        self._pending[command.id] = future
        try:
            await self._send(command)
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(command.id, None)

    async def call(
        self,
        type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = DEFAULT_REQUEST_TIMEOUT,
    ) -> JsonObject:
        """Like :meth:`request`, but return ``result`` or raise :class:`EngineError`."""
        response = await self.request(type, payload, timeout=timeout)
        if not response.ok:
            assert response.error is not None
            raise EngineError(type, response.error)
        return response.result or {}

    async def _send(self, command: Command) -> None:
        data = (encode(command) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                self._stdin.write(data)
                await self._stdin.drain()
            except (ConnectionError, OSError) as exc:
                # The engine is gone; the monitor will fail the pending future.
                log.debug("write to engine failed: %s", exc)

    # -- events --------------------------------------------------------------

    async def events(self) -> AsyncIterator[Event]:
        """Yield events in ``seq`` order per stream, filling gaps with ``get_events``.

        Meant for a single consumer. Ends after :meth:`close`; raises
        :class:`EngineDied` (after the buffered events) if the engine died.
        """
        while True:
            event = await self._events.get()
            if event is None:
                self._events.put_nowait(None)  # keep later iterators finished too
                if self._death is not None:
                    raise self._death
                return
            if self._sequencer.is_duplicate(event):
                continue
            first_missing = self._sequencer.gap_start(event)
            if first_missing is not None:
                for missing in await self._fetch_missing(event.run_id, first_missing, event.seq):
                    if self._sequencer.accept(missing):
                        yield missing
            self._sequencer.accept(event)
            yield event

    async def _fetch_missing(self, run_id: str | None, from_seq: int, before: int) -> list[Event]:
        """Events of one stream with ``from_seq <= seq < before``, sorted; [] on failure."""
        try:
            result = await self.call("get_events", {"run_id": run_id, "from_seq": from_seq})
        except (ClientError, TimeoutError) as exc:
            log.warning("could not recover events %d..%d of stream %r: %s",
                        from_seq, before - 1, run_id, exc)
            return []
        raw_events = result.get("events")
        found: dict[int, Event] = {}
        for raw in raw_events if isinstance(raw_events, list) else []:
            event = _event_from_json(raw)
            if event is not None and event.run_id == run_id and from_seq <= event.seq < before:
                found.setdefault(event.seq, event)
        if len(found) < before - from_seq:
            log.warning("engine returned %d of %d missing events of stream %r",
                        len(found), before - from_seq, run_id)
        return [found[seq] for seq in sorted(found)]

    # -- background tasks ----------------------------------------------------

    async def _read_stdout(self, stream: asyncio.StreamReader) -> None:
        while line := await stream.readline():
            self._dispatch(line)

    def _dispatch(self, line: bytes) -> None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            message = decode(text)
        except ProtocolError as exc:
            log.warning("ignoring malformed line from engine: %s", exc)
            return
        if isinstance(message, Response):
            future = self._pending.get(message.id)
            if future is not None and not future.done():
                future.set_result(message)
            else:
                log.debug("ignoring response with unknown id %r", message.id)
        elif isinstance(message, Event):
            self._on_event(message)
        else:
            log.warning("ignoring %s %r sent by the engine", type(message).__name__, message.type)

    def _on_event(self, event: Event) -> None:
        if event.type == "input_state":
            held = event.payload.get("held")
            if isinstance(held, list):
                self._held = [str(item) for item in held]
        elif event.type == "ready" and event.run_id is None and not self._ready.done():
            self._ready.set_result(event)
        self._events.put_nowait(event)

    async def _monitor(self) -> None:
        """Wait until stdout closes or the process exits, then settle everything."""
        exited = asyncio.create_task(self._process.wait())
        await asyncio.wait({self._reader_task, exited}, return_when=asyncio.FIRST_COMPLETED)
        expected = self._closing
        if not exited.done() and not expected:
            # stdout closed but the process lingers: it can no longer talk to us.
            try:
                await asyncio.wait_for(asyncio.shield(exited), EXIT_GRACE)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self._process.kill()
        await exited  # when closing, close() is in charge of terminating it
        await _finish(self._reader_task, EXIT_GRACE)
        await _finish(self._stderr_task, EXIT_GRACE)
        if not self._reader_task.cancelled() and self._reader_task.exception() is not None:
            log.error("engine stdout reader failed", exc_info=self._reader_task.exception())

        exit_code = self._process.returncode
        if expected:
            self._failure = ClientClosed("the client was closed")
        else:
            self._death = EngineDied(exit_code, self._stderr.lines)
            self._failure = self._death
            log.error("%s", self._death)
            await self._notify_death(exit_code)
        waiters: list[asyncio.Future[Any]] = [*self._pending.values(), self._ready]
        for future in waiters:
            if not future.done():
                future.set_exception(self._failure)
        if self._ready.done() and not self._ready.cancelled():
            self._ready.exception()  # mark as retrieved; start() may not be waiting
        self._events.put_nowait(None)

    async def _notify_death(self, exit_code: int | None) -> None:
        if self._on_engine_died is None:
            return
        try:
            outcome = self._on_engine_died(list(self._held), exit_code)
            if inspect.isawaitable(outcome):
                await outcome
        except Exception:
            log.exception("on_engine_died callback failed")


async def _finish(task: asyncio.Task[Any], timeout: float) -> bool:
    """Wait up to ``timeout`` for ``task``; cancel it if still running. True if it finished."""
    if not task.done():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout)
    if task.done():
        return True
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    return False


def _event_from_json(raw: object) -> Event | None:
    """Parse one raw event object from a ``get_events`` result."""
    if not isinstance(raw, dict):
        return None
    data = {"v": 1, "kind": "event", **raw}
    try:
        message = decode(json.dumps(data, ensure_ascii=False))
    except ProtocolError:
        return None
    return message if isinstance(message, Event) else None
