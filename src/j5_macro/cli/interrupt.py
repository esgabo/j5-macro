"""Ctrl+C handling while a run is in progress.

The first Ctrl+C asks the engine to ``stop`` the run (it releases held keys and
buttons and ends with ``run_finished``); the second one gives up waiting. Both
are delivered to the event loop as items of a queue, together with other
asynchronous requests such as "the user pressed Enter, resume".
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import threading
from collections.abc import Iterator
from enum import StrEnum
from types import FrameType


class Control(StrEnum):
    STOP = "stop"
    """First Ctrl+C: stop the run and wait for ``run_finished``."""
    ABORT = "abort"
    """Second Ctrl+C: stop waiting and exit."""
    RESUME = "resume"
    """The user asked to resume a paused run (Enter)."""
    SKIP = "skip"
    """The user asked to skip the step the run paused on (``s`` then Enter)."""

SKIP_ANSWERS = frozenset({"s", "skip"})
"""Lines that mean :attr:`Control.SKIP` when the CLI offers to skip a step."""


class Controls:
    """A thread-safe inbox of :class:`Control` requests for the running event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.queue: asyncio.Queue[Control] = asyncio.Queue()
        self.interrupts = 0
        self._reader: threading.Thread | None = None

    def put(self, control: Control) -> None:
        """Queue ``control``; safe from signal handlers and other threads."""
        self._loop.call_soon_threadsafe(self.queue.put_nowait, control)

    def interrupt(self) -> None:
        """What Ctrl+C does: :attr:`Control.STOP` the first time, then ABORT."""
        self.interrupts += 1
        self.put(Control.STOP if self.interrupts == 1 else Control.ABORT)

    @contextlib.contextmanager
    def handle_sigint(self) -> Iterator[None]:
        """Route SIGINT to :meth:`interrupt` inside the block (main thread only).

        On POSIX the loop's own signal handling is used: it wakes the loop through a
        wakeup fd even when the signal lands on another thread. The Windows proactor
        loop has no ``add_signal_handler`` but always installs a wakeup fd, so a plain
        handler is enough there.
        """
        if threading.current_thread() is not threading.main_thread():
            yield
            return

        def handler(signum: int, frame: FrameType | None) -> None:
            self.interrupt()

        previous = signal.getsignal(signal.SIGINT)
        try:
            self._loop.add_signal_handler(signal.SIGINT, self.interrupt)
            uses_loop = True
        except (NotImplementedError, RuntimeError):
            signal.signal(signal.SIGINT, handler)
            uses_loop = False
        try:
            yield
        finally:
            if uses_loop:
                self._loop.remove_signal_handler(signal.SIGINT)
            signal.signal(signal.SIGINT, previous)

    def wait_for_enter(self, *, skippable: bool = False) -> None:
        """Queue RESUME when a line arrives on stdin (daemon thread, never joined). With
        ``skippable``, a line saying ``s`` or ``skip`` queues SKIP instead."""
        if self._reader is not None and self._reader.is_alive():
            return

        def read() -> None:
            try:
                line = sys.stdin.readline()
                if line:
                    skip = skippable and line.strip().lower() in SKIP_ANSWERS
                    self.put(Control.SKIP if skip else Control.RESUME)
            except (OSError, ValueError, RuntimeError):
                pass  # stdin closed, or the event loop is already gone

        self._reader = threading.Thread(target=read, name="j5-resume", daemon=True)
        self._reader.start()
