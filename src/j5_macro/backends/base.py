"""Backend interface: the only layer that changes per platform.

Backends expose primitives only. Every "human-like" behaviour lives in the
mouse and keyboard masters (``j5_macro.input``), which plan and then drive
these primitives. See docs/contract.md, "Mouse and keyboard masters".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Button(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a backend can do; masters adapt to it."""

    pointer_position: bool
    move_abs: bool
    move_rel: bool
    char_input: bool
    screen_capture: bool


class Backend(Protocol):
    """Platform primitives. Coordinates are physical pixels on the virtual desktop."""

    name: str
    capabilities: Capabilities

    def pointer_position(self) -> tuple[int, int] | None:
        """Real cursor position, or None if the platform cannot report it."""
        ...

    def move_abs(self, x: int, y: int) -> None: ...

    def move_rel(self, dx: int, dy: int) -> None: ...

    def button_down(self, button: Button) -> None: ...

    def button_up(self, button: Button) -> None: ...

    def scroll(self, dx: int, dy: int) -> None: ...

    def key_down(self, key: str) -> None:
        """Press a physical key (for shortcuts and modifiers)."""
        ...

    def key_up(self, key: str) -> None: ...

    def char_down(self, char: str) -> None:
        """Press a character independently of the keyboard layout."""
        ...

    def char_up(self, char: str) -> None: ...
