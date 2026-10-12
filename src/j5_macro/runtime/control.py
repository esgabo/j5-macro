"""Thread-safe control state shared by the protocol thread and the run's worker thread.

The protocol thread requests things (pause, a command while paused, stop); the worker
thread polls and waits for them at checkpoints and inside waits. Everything is guarded
by one condition variable, which is also the lock for the run's variables.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Literal

from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.payloads.commands import RunState
from j5_macro.runtime.api import ControlError
from j5_macro.runtime.clock import Clock

Command = Literal["resume", "step_over", "step_into", "step_out"]
StopReason = Literal["user", "interference"]


class RunStopped(BaseException):
    """Raised on the worker thread when the run is stopped.

    A ``BaseException`` so executors catching ``Exception`` cannot swallow it.
    """

    def __init__(self, reason: StopReason) -> None:
        super().__init__(reason)
        self.reason: StopReason = reason


class RunControl:
    """Pause/command/stop flags and the run state, guarded by :attr:`lock`."""

    def __init__(self, clock: Clock, *, paused: bool, breakpoints: Iterable[str]) -> None:
        self.lock = threading.Condition()
        self._clock = clock
        self._state: RunState = "paused" if paused else "running"
        self._pause_requested = False
        self._command: Command | None = None
        self._stop_reason: StopReason | None = None
        self._breakpoints = frozenset(breakpoints)

    # --- protocol thread ------------------------------------------------------------

    @property
    def state(self) -> RunState:
        with self.lock:
            return self._state

    def request_pause(self) -> None:
        with self.lock:
            if self._state == "running":
                self._pause_requested = True
                self.lock.notify_all()

    def send(self, command: Command) -> None:
        with self.lock:
            self.require_paused(command)
            self._command = command
            self.lock.notify_all()

    def stop(self, reason: StopReason) -> None:
        with self.lock:
            if self._stop_reason is None:
                self._stop_reason = reason
                self._state = "stopping"
                self.lock.notify_all()

    def require_paused(self, what: str) -> None:
        """Raise ``invalid_state`` unless paused; call with :attr:`lock` held."""
        if self._state != "paused":
            raise ControlError(ErrorCode.INVALID_STATE,
                               f"'{what}' needs a paused run (the run is {self._state})")

    def set_breakpoints(self, step_ids: Iterable[str]) -> None:
        with self.lock:
            self._breakpoints = frozenset(step_ids)

    def is_breakpoint(self, step_id: str) -> bool:
        with self.lock:
            return step_id in self._breakpoints

    # --- worker thread --------------------------------------------------------------

    def check_stop(self) -> None:
        with self.lock:
            if self._stop_reason is not None:
                raise RunStopped(self._stop_reason)

    def pause_pending(self) -> bool:
        with self.lock:
            return self._pause_requested

    def enter_pause(self) -> bool:
        """Switch to ``paused``; returns True if it already was (no event needed)."""
        with self.lock:
            if self._stop_reason is not None:
                raise RunStopped(self._stop_reason)
            already = self._state == "paused"
            self._state = "paused"
            self._pause_requested = False
            return already

    def wait_command(self) -> Command:
        """Block until a command arrives (then ``running``) or the run is stopped."""
        with self.lock:
            while self._command is None and self._stop_reason is None:
                self._clock.wait(self.lock, None)
            if self._stop_reason is not None:
                raise RunStopped(self._stop_reason)
            command = self._command
            assert command is not None
            self._command = None
            self._state = "running"
            return command

    def sleep_unless_stopped(self, seconds: float) -> None:
        """Wait ``seconds``, ending early (with :class:`RunStopped`) only on stop.

        For the input masters (``AbortSignal.sleep``): a pause never cuts an action in
        half, so pause requests are ignored here and take effect at the next checkpoint.
        """
        deadline = self._clock.now() + seconds
        with self.lock:
            while True:
                if self._stop_reason is not None:
                    raise RunStopped(self._stop_reason)
                remaining = deadline - self._clock.now()
                if remaining <= 0:
                    return
                self._clock.wait(self.lock, remaining)

    def sleep(self, seconds: float) -> float:
        """Wait up to ``seconds``; returns the time left if a pause was requested
        (0 when the wait completed). Raises :class:`RunStopped` on stop."""
        deadline = self._clock.now() + seconds
        with self.lock:
            while True:
                if self._stop_reason is not None:
                    raise RunStopped(self._stop_reason)
                remaining = deadline - self._clock.now()
                if remaining <= 0:
                    return 0.0
                if self._pause_requested:
                    return remaining
                self._clock.wait(self.lock, remaining)
