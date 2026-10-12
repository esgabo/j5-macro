"""Time source for runs, injectable so tests do not really sleep."""

from __future__ import annotations

import threading
import time
from typing import Protocol


class Clock(Protocol):
    """Monotonic time and interruptible waiting on a condition variable."""

    def now(self) -> float:
        """Monotonic seconds."""
        ...

    def wait(self, cond: threading.Condition, timeout: float | None) -> None:
        """Wait on ``cond`` (held by the caller) until notified or ``timeout`` seconds
        of this clock's time pass (``None``: until notified)."""
        ...


class MonotonicClock:
    """The real clock."""

    def now(self) -> float:
        return time.monotonic()

    def wait(self, cond: threading.Condition, timeout: float | None) -> None:
        cond.wait(timeout)
