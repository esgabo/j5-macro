"""Backend interface: the only layer that changes per platform.

Backends expose primitives only. Every human-like behaviour lives in the mouse and
keyboard masters (``j5_macro.input``), which plan and then drive these primitives.
See docs/contract.md, "Mouse and keyboard masters".

Key names
---------
Physical keys are named in lowercase: the modifiers ``ctrl``, ``shift``, ``alt`` and
``super``; the named keys ``enter``, ``tab``, ``escape``, ``backspace``, ``delete``,
``insert``, ``space``, ``up``, ``down``, ``left``, ``right``, ``home``, ``end``,
``pageup``, ``pagedown``, ``capslock``, ``menu``, ``printscreen`` and ``f1`` to ``f24``;
and single printable ASCII characters (``a``, ``1``, ``/``) for the key that types that
character on a US layout. Aliases (``esc``, ``return``, ``control``, ``cmd``...) are
resolved by the input layer before they reach a backend. A backend raises
:class:`UnknownKey` for a name it cannot map.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

MODIFIER_KEYS = ("ctrl", "shift", "alt", "super")
NAMED_KEYS = (
    "enter", "tab", "escape", "backspace", "delete", "insert", "space", "up", "down",
    "left", "right", "home", "end", "pageup", "pagedown", "capslock", "menu", "printscreen",
    *(f"f{n}" for n in range(1, 25)),
)


class Button(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class UnknownKey(ValueError):
    """The backend cannot map a key name or character."""


@dataclass(frozen=True, slots=True)
class Rect:
    """A rectangle in physical pixels of the virtual desktop (``x``/``y`` may be negative)."""

    x: int
    y: int
    width: int
    height: int

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a backend can do; masters and steps adapt to it."""

    pointer_position: bool
    move_abs: bool
    move_rel: bool
    char_input: bool
    screen_capture: bool
    windows: bool = False
    """``find_window`` works (window-relative coordinates)."""
    hotkey: bool = False
    """``watch_hotkey`` works (global emergency stop key)."""
    display_scale: bool = False
    """``display_scale`` and ``display_scales`` report the display scale of monitors."""


MonitorScale = tuple[Rect, float]
"""A monitor's rectangle on the desktop and its display scale (1.0 = 100 %)."""


def scale_at(monitors: list[MonitorScale], x: int, y: int) -> float | None:
    """The display scale of the monitor containing (x, y), else of the nearest one;
    None without monitors."""
    best: tuple[int, float] | None = None
    for rect, scale in monitors:
        dx = max(rect.x - x, 0, x - (rect.x + rect.width - 1))
        dy = max(rect.y - y, 0, y - (rect.y + rect.height - 1))
        distance = dx * dx + dy * dy
        if best is None or distance < best[0]:
            best = (distance, scale)
    return None if best is None else best[1]


class Backend(Protocol):
    """Platform primitives. Coordinates are physical pixels on the virtual desktop.

    Calls may come from different threads (a run's worker, the engine's hotkey watcher);
    implementations serialize access to the display connection themselves.
    """

    name: str
    capabilities: Capabilities

    def screen_bounds(self) -> Rect:
        """The virtual desktop: the bounding box of every screen."""
        ...

    def pointer_position(self) -> tuple[int, int] | None:
        """Real cursor position, or None if the platform cannot report it."""
        ...

    def move_abs(self, x: int, y: int) -> None: ...

    def move_rel(self, dx: int, dy: int) -> None: ...

    def button_down(self, button: Button) -> None: ...

    def button_up(self, button: Button) -> None: ...

    def scroll(self, dx: int, dy: int) -> None:
        """Scroll by notches: positive ``dy`` scrolls down, positive ``dx`` right."""
        ...

    def key_down(self, key: str) -> None:
        """Press a physical key by name (see the module docstring)."""
        ...

    def key_up(self, key: str) -> None: ...

    def char_down(self, char: str) -> None:
        """Press the key for a character regardless of the keyboard layout."""
        ...

    def char_up(self, char: str) -> None: ...

    def display_scale(self, x: int, y: int) -> float | None:
        """The display scale (1.0 = 100 %) of the monitor at (x, y), or of the nearest
        one; None when the backend cannot tell (capability ``display_scale``)."""
        ...

    def display_scales(self) -> list[MonitorScale]:
        """Every monitor with its display scale; empty when the backend cannot tell."""
        ...

    def find_window(self, title: str) -> Rect | None:
        """Client area (the content, without the window manager's title bar and borders),
        in desktop coordinates, of the topmost visible window whose title contains
        ``title`` (case-sensitive), or None."""
        ...

    def watch_hotkey(self, keys: list[str], callback: Callable[[], None]) -> Callable[[], None]:
        """Call ``callback`` (from any thread) whenever the key combination is pressed,
        even when another application has the focus. Returns a function that stops
        watching."""
        ...

    def close(self) -> None:
        """Release the display connection and any temporary state (e.g. key remaps)."""
        ...
