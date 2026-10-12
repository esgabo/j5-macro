"""Per-stream ``seq`` bookkeeping: gap detection and duplicate suppression.

Each run has its own ``seq`` stream and engine-level events (``run_id`` null)
have another one. Every stream starts at 1 and has no gaps (docs/contract.md).
"""

from __future__ import annotations

from j5_macro.protocol import Event


class EventSequencer:
    """Tracks the last ``seq`` delivered for each stream key (``run_id``)."""

    def __init__(self) -> None:
        self._last: dict[str | None, int] = {}

    def last(self, run_id: str | None) -> int:
        """Last delivered seq for the stream, 0 if nothing was delivered yet."""
        return self._last.get(run_id, 0)

    def is_duplicate(self, event: Event) -> bool:
        return event.seq <= self.last(event.run_id)

    def gap_start(self, event: Event) -> int | None:
        """First missing seq before ``event``, or None when it is the next one."""
        expected = self.last(event.run_id) + 1
        return expected if event.seq > expected else None

    def accept(self, event: Event) -> bool:
        """Record ``event`` as delivered; False (and no change) if it is a duplicate."""
        if self.is_duplicate(event):
            return False
        self._last[event.run_id] = event.seq
        return True
