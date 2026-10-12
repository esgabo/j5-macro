"""Event sequencing: gap-free ``seq``, wall-clock ``ts`` and monotonic ``t_ms``.

The engine owns one :class:`EventSequencer` for engine-level events
(``run_id: null``); each run will get its own instance with its own ``run_id``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from j5_macro.protocol.messages import Event, JsonObject

MonotonicClock = Callable[[], int]
"""Returns nanoseconds from an arbitrary, never-decreasing origin."""
WallClock = Callable[[], datetime]
"""Returns the current time as an aware UTC datetime."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_ts(moment: datetime) -> str:
    """Format as UTC ISO 8601 with milliseconds and a ``Z`` suffix."""
    text = moment.astimezone(UTC).isoformat(timespec="milliseconds")
    return text.removesuffix("+00:00") + "Z"


class EventSequencer:
    """Stamps events of one stream: ``seq`` starts at 1, ``t_ms`` at stream start.

    Thread-safe: ``seq`` and ``t_ms`` are assigned under one lock, so they are
    consistent with each other even when several threads emit.
    """

    def __init__(
        self,
        run_id: str | None = None,
        *,
        monotonic_ns: MonotonicClock = time.monotonic_ns,
        wall_clock: WallClock = utc_now,
    ) -> None:
        self.run_id = run_id
        self._monotonic_ns = monotonic_ns
        self._wall_clock = wall_clock
        self._start_ns = monotonic_ns()
        self._last_seq = 0
        self._lock = threading.Lock()

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def next(self, type: str, payload: JsonObject | None = None) -> Event:
        """Build the next event of this stream."""
        with self._lock:
            self._last_seq += 1
            elapsed_ms = (self._monotonic_ns() - self._start_ns) // 1_000_000
            return Event(
                type=type,
                seq=self._last_seq,
                ts=format_ts(self._wall_clock()),
                t_ms=max(0, elapsed_ms),
                run_id=self.run_id,
                payload=payload if payload is not None else {},
            )
