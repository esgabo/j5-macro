"""The interpreter's explicit frame stack.

Each frame is a step list being executed with its own program counter (``index``). The
root frame holds the script's steps; a block step (``if``, ``loop``) pushes a frame for
the block it runs and stays the current step of its parent frame until that frame is
popped. A loop frame also carries the loop's state, which gives every step its
``iterations``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from j5_macro.script.model import Step


@dataclass(slots=True, kw_only=True)
class LoopState:
    """Where a ``loop`` is: its current iteration and what drives it."""

    index: int = 0
    total: int | None = None
    """``times`` or the number of ``for_each`` items; None for ``while``."""
    items: list[Any] | None = None
    var: str | None = None
    """Variable holding the current ``for_each`` item."""
    condition: Any = None
    """The ``while`` condition."""


@dataclass(slots=True, kw_only=True)
class Frame:
    steps: list[Step]
    owner: Step | None = None
    """The block step running this list; None for the script's top level."""
    index: int = 0
    loop: LoopState | None = None
    started: float = 0.0
    """Clock time when ``owner`` started, for its ``duration_ms``."""
    paused_before: float = 0.0
    """Paused seconds accumulated by the run before ``owner`` started."""
    result: dict[str, Any] = field(default_factory=dict)
    """``owner``'s ``step_finished`` result."""


def iterations(frames: Sequence[Frame]) -> list[int] | None:
    """Current iteration of each loop in ``frames``, outermost first (None if no loops)."""
    found = [frame.loop.index for frame in frames if frame.loop is not None]
    return found or None
