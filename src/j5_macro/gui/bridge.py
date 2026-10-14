"""The Qt side of the engine connection: signals and callbacks on the GUI thread.

:class:`EngineBridge` owns an :class:`~j5_macro.gui.engine_thread.EngineThread` (the
asyncio :class:`~j5_macro.client.EngineClient` on its own thread) and is the only way the
GUI talks to the engine:

- ``call(type, payload, on_result=..., on_error=...)`` sends a command and returns at
  once; the callbacks run later **on the GUI thread**, never inside ``call``.
- ``event_received`` delivers every event (engine and run events, in ``seq`` order per
  stream) on the GUI thread.
- ``engine_started``, ``engine_failed`` and ``engine_died`` report the engine's life;
  ``restart`` starts a new one.

Threading rules: widgets and models are only touched on the GUI thread; nothing on the
GUI thread waits on a future (no ``future.result()``); everything that reaches the GUI
from the engine thread goes through a queued signal.
"""

from __future__ import annotations

import concurrent.futures
import logging
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot

from j5_macro.cli.release import ReleaseReport, release_held
from j5_macro.client import ClientClosed, EngineDied
from j5_macro.client.engine_client import DEFAULT_REQUEST_TIMEOUT
from j5_macro.gui.engine_thread import (
    CLOSE_TIMEOUT,
    EngineThread,
    JsonObject,
    describe_error,
    died_message,
)
from j5_macro.gui.models.engine_info import EngineInfo
from j5_macro.protocol import Event

ResultCallback = Callable[[JsonObject], None]
ErrorCallback = Callable[[BaseException], None]

log = logging.getLogger(__name__)


class EngineBridge(QObject):
    """The engine for the GUI. Create it on the GUI thread, then :meth:`start` it."""

    engine_started = Signal(object)
    """:class:`EngineInfo`, after the handshake."""
    engine_failed = Signal(str)
    """The engine could not be started: a message for people."""
    engine_died = Signal(str)
    """The engine exited without being asked to: a message for people (what was
    released, the last lines of its stderr)."""
    running_changed = Signal(bool)
    event_received = Signal(object)
    """Every :class:`~j5_macro.protocol.Event`, engine and run events alike."""

    _deliver = Signal(object)
    """Internal: a callable to run on the GUI thread."""

    def __init__(
        self,
        cmd: Sequence[str] | None = None,
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        release: Callable[[list[str]], ReleaseReport] = release_held,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cmd = list(cmd) if cmd is not None else None
        self._env = dict(env) if env is not None else None
        self._cwd = cwd
        self._release = release
        self._engine: EngineThread | None = None
        self._generation = 0
        self._info: EngineInfo | None = None
        self._running = False
        # Always queued, so a callback never runs inside call(), even for a future that
        # is already done.
        self._deliver.connect(self._run_delivered, Qt.ConnectionType.QueuedConnection)

    # -- lifecycle -----------------------------------------------------------------------

    @property
    def info(self) -> EngineInfo | None:
        """What the engine reported in ``hello``; None until it started."""
        return self._info

    @property
    def running(self) -> bool:
        return self._running

    @property
    def pid(self) -> int | None:
        return self._engine.pid if self._engine is not None else None

    def start(self) -> None:
        """Launch the engine (a no-op while one is running or starting)."""
        if self._engine is not None and not self._engine.closed:
            return
        self._generation += 1
        generation = self._generation
        self._engine = EngineThread(
            self._cmd, env=self._env, cwd=self._cwd, release=self._release,
            on_event=partial(self._on_event, generation),
            on_died=partial(self._on_died, generation))
        future = self._engine.start()
        future.add_done_callback(partial(self._post, partial(self._started, generation)))

    def restart(self) -> None:
        """Close the current engine, if any, and start a new one."""
        self.shutdown()
        self.start()

    def shutdown(self, timeout: float = CLOSE_TIMEOUT) -> None:
        """Close the engine and wait for its thread (blocks up to a few seconds)."""
        engine, self._engine = self._engine, None
        self._generation += 1  # late signals of the old engine are ignored
        self._info = None
        if engine is not None:
            engine.close(timeout)
        self._set_running(False)

    # -- commands ------------------------------------------------------------------------

    def call(
        self,
        type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        on_result: ResultCallback | None = None,
        on_error: ErrorCallback | None = None,
        timeout: float | None = DEFAULT_REQUEST_TIMEOUT,
    ) -> concurrent.futures.Future[JsonObject]:
        """Send a command (docs/contract.md). ``on_result`` gets ``result``; ``on_error``
        gets the exception (:class:`~j5_macro.client.EngineError` carries the engine's
        ``code`` and ``error.details``; :func:`describe_error` makes a message of any).
        Both run on the GUI thread. Without ``on_error`` failures are logged. Interactive
        authoring commands need ``timeout=None``: they answer when the user finishes."""
        if self._engine is None:
            future: concurrent.futures.Future[JsonObject] = concurrent.futures.Future()
            future.set_exception(ClientClosed("the engine is not running"))
        else:
            future = self._engine.call(type, payload, timeout=timeout)
        generation = self._generation
        future.add_done_callback(partial(
            self._post, partial(self._settle, generation, type, on_result, on_error)))
        return future

    # -- engine thread -> GUI thread -----------------------------------------------------

    def _post(self, callback: Callable[[concurrent.futures.Future[Any]], None],
              future: concurrent.futures.Future[Any]) -> None:
        """Runs on whichever thread completed the future: hand over to the GUI thread."""
        self._deliver.emit(partial(callback, future))

    @Slot(object)
    def _run_delivered(self, callback: Callable[[], None]) -> None:
        callback()

    def _started(self, generation: int, future: concurrent.futures.Future[EngineInfo]) -> None:
        if generation != self._generation or future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            if self._engine is not None:
                self._engine.close(timeout=1.0)
            self._engine = None
            self.engine_failed.emit(describe_error(exc))
            return
        self._info = future.result()
        self._set_running(True)
        self.engine_started.emit(self._info)

    def _settle(self, generation: int, type: str, on_result: ResultCallback | None,
                on_error: ErrorCallback | None,
                future: concurrent.futures.Future[JsonObject]) -> None:
        if future.cancelled():
            return
        exc = future.exception()
        if exc is None:
            if on_result is not None and generation == self._generation:
                on_result(future.result())
        elif on_error is not None:
            on_error(exc)
        else:
            log.warning("%s failed: %s", type, describe_error(exc))

    def _on_event(self, generation: int, event: Event) -> None:
        """Engine thread: queue the event for the GUI thread (order is kept)."""
        if generation == self._generation:
            self._deliver.emit(partial(self._emit_event, generation, event))

    def _emit_event(self, generation: int, event: Event) -> None:
        if generation == self._generation:
            self.event_received.emit(event)

    def _on_died(self, generation: int, exc: EngineDied, released: str | None) -> None:
        """Engine thread: the engine died (what it held was already released)."""
        self._deliver.emit(partial(self._died, generation, died_message(exc, released)))

    def _died(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        engine, self._engine = self._engine, None
        if engine is not None:
            engine.close(timeout=1.0)  # the process is gone: only ends the thread
        self._info = None
        self._set_running(False)
        self.engine_died.emit(message)

    def _set_running(self, running: bool) -> None:
        if running != self._running:
            self._running = running
            self.running_changed.emit(running)
