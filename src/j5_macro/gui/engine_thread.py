"""The engine client on its own thread: an asyncio loop that the GUI never blocks on.

:class:`EngineThread` starts the engine with the existing asyncio
:class:`~j5_macro.client.EngineClient` (no second protocol implementation) on an event
loop that runs in a daemon thread. Commands are submitted from any thread and return a
:class:`concurrent.futures.Future`; events and the engine's death are reported through
callbacks that run **on the engine thread**. It imports no Qt: the Qt layer
(:mod:`j5_macro.gui.bridge`) turns those callbacks into signals on the GUI thread.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from j5_macro.cli.release import ReleaseReport, release_held
from j5_macro.client import ClientClosed, ClientError, EngineClient, EngineDied, EngineError
from j5_macro.client.engine_client import DEFAULT_REQUEST_TIMEOUT
from j5_macro.gui.models.engine_info import EngineInfo
from j5_macro.protocol import Event

JsonObject = dict[str, Any]
EventCallback = Callable[[Event], None]
DeathCallback = Callable[[EngineDied, str | None], None]
"""Called with the death and, when the engine held keys or buttons, a line saying what
the GUI released."""

CLIENT_NAME = "j5-gui"
THREAD_NAME = "j5-gui-engine"
CLOSE_TIMEOUT = 5.0
"""Seconds ``close`` waits for the engine at each step (EOF, terminate, kill)."""
STDERR_LINES = 10

log = logging.getLogger(__name__)


class EngineThread:
    """One engine process and the thread that talks to it. Start it once; to restart,
    close it and create another."""

    def __init__(
        self,
        cmd: Sequence[str] | None = None,
        *,
        on_event: EventCallback,
        on_died: DeathCallback,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        client_name: str = CLIENT_NAME,
        release: Callable[[list[str]], ReleaseReport] = release_held,
    ) -> None:
        self._cmd = list(cmd) if cmd is not None else None
        self._env = dict(env) if env is not None else None
        self._cwd = cwd
        self._client_name = client_name
        self._on_event = on_event
        self._on_died = on_died
        self._release = release
        self._lock = threading.Lock()
        self._loop_ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started: asyncio.Future[EngineClient] | None = None
        self._start_task: asyncio.Task[Any] | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._client: EngineClient | None = None
        self._release_message: str | None = None
        self._closed = False

    # -- lifecycle -----------------------------------------------------------------------

    def start(self) -> concurrent.futures.Future[EngineInfo]:
        """Launch the engine; the future resolves after ``hello`` (or fails with what
        :meth:`EngineClient.start` raised)."""
        with self._lock:
            if self._thread is not None or self._closed:
                raise RuntimeError("an EngineThread can only be started once")
            self._thread = threading.Thread(target=self._serve, name=THREAD_NAME, daemon=True)
            self._thread.start()
        self._loop_ready.wait()
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(self._start(), self._loop)

    def close(self, timeout: float = CLOSE_TIMEOUT) -> None:
        """Close the engine (stdin EOF stops an active run) and end the thread. Blocks;
        safe to call more than once and from any thread but the engine thread."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop
        if loop is None:
            return
        shutdown = asyncio.run_coroutine_threadsafe(self._shutdown(timeout), loop)
        try:
            shutdown.result(timeout * 3 + 5)
        except Exception:
            log.exception("closing the engine failed")
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def pid(self) -> int | None:
        """The engine's process id once it started."""
        client = self._client
        return client.pid if client is not None else None

    @property
    def alive(self) -> bool:
        """Started, not closed, and the process has not exited."""
        client = self._client
        return client is not None and not self._closed and client.returncode is None

    # -- commands ------------------------------------------------------------------------

    def call(self, type: str, payload: Mapping[str, Any] | None = None, *,
             timeout: float | None = DEFAULT_REQUEST_TIMEOUT
             ) -> concurrent.futures.Future[JsonObject]:
        """Send a command; the future gets ``result``, or :class:`EngineError` (the
        engine answered ``ok: false``), :class:`ClientError` (closed, dead) or
        ``TimeoutError``. Commands sent before the handshake wait for it."""
        with self._lock:
            loop = self._loop
            if loop is None or self._closed:
                failed: concurrent.futures.Future[JsonObject] = concurrent.futures.Future()
                failed.set_exception(ClientClosed("the engine is not running"))
                return failed
            return asyncio.run_coroutine_threadsafe(
                self._call(type, dict(payload or {}), timeout), loop)

    # -- engine thread -------------------------------------------------------------------

    def _serve(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._started = loop.create_future()
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                _cancel_all(loop)
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    async def _start(self) -> EngineInfo:
        assert self._started is not None
        self._start_task = asyncio.current_task()
        try:
            client = await EngineClient.start(
                self._cmd, client_name=self._client_name, env=self._env, cwd=self._cwd,
                on_engine_died=self._engine_died)
        except BaseException as exc:
            if not self._started.done():
                self._started.set_exception(
                    exc if isinstance(exc, Exception) else ClientClosed("start cancelled"))
                self._started.exception()  # retrieved: calls may never have waited
            raise
        self._client = client
        self._started.set_result(client)
        self._pump_task = asyncio.create_task(self._pump(client))
        return EngineInfo.from_hello(client.version, client.capabilities)

    async def _call(self, type: str, payload: JsonObject,
                    timeout: float | None) -> JsonObject:
        assert self._started is not None
        client = await asyncio.shield(self._started)
        return await client.call(type, payload, timeout=timeout)

    async def _pump(self, client: EngineClient) -> None:
        try:
            async for event in client.events():
                _guard(self._on_event, event)
        except EngineDied as exc:
            _guard(self._on_died, exc, self._release_message)

    async def _engine_died(self, held: list[str], exit_code: int | None) -> None:
        """``on_engine_died``: release what the engine held (contract: the client
        supervises the engine), before the death reaches :meth:`_pump`."""
        if not held:
            return
        try:
            report = await asyncio.to_thread(self._release, held)
            self._release_message = report.message()
        except Exception as exc:
            self._release_message = f"could not release {', '.join(held)}: {exc}"

    async def _shutdown(self, timeout: float) -> None:
        started = self._started
        if started is not None and not started.done():
            started.set_exception(ClientClosed("the engine was closed"))
            started.exception()
        task = self._start_task
        if self._client is None and task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        if self._client is not None:
            await self._client.close(timeout=timeout)
        if self._pump_task is not None:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(self._pump_task, timeout)


def describe_error(exc: BaseException) -> str:
    """A message for people about a failed command or start."""
    if isinstance(exc, EngineDied):
        return died_message(exc)
    if isinstance(exc, EngineError):
        return f"{exc.error.message} ({exc.code})"
    if isinstance(exc, TimeoutError):
        return "the engine did not answer in time"
    if isinstance(exc, ClientError):
        return str(exc)
    if isinstance(exc, OSError):
        return f"cannot launch the engine: {exc}"
    return f"{type(exc).__name__}: {exc}"


def died_message(exc: EngineDied, released: str | None = None) -> str:
    message = f"The engine stopped unexpectedly (exit code {exc.exit_code})."
    if released:
        message += f" {released[:1].upper()}{released[1:]}."
    tail = [line for line in exc.stderr_tail if line.strip()][-STDERR_LINES:]
    if tail:
        message += "\n" + "\n".join(tail)
    return message


def _guard(callback: Callable[..., None], *args: Any) -> None:
    """Run a client callback; its failure must never stop the event pump."""
    try:
        callback(*args)
    except Exception:
        log.exception("engine thread callback failed")


def _cancel_all(loop: asyncio.AbstractEventLoop) -> None:
    tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
