"""An in-memory backend that records every primitive: for tests and for dry tooling.

It keeps a pointer position, held keys and buttons, and a list of calls, so tests can
assert exactly what the input layer sent without a display.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from j5_macro.backends.base import (
    MODIFIER_KEYS,
    NAMED_KEYS,
    Button,
    Capabilities,
    Rect,
    UnknownKey,
)


@dataclass(frozen=True, slots=True)
class Call:
    name: str
    args: tuple[object, ...] = ()


@dataclass
class FakeBackend:
    """Records calls; ``capabilities`` and windows are configurable per test."""

    name: str = "fake"
    capabilities: Capabilities = field(default_factory=lambda: Capabilities(
        pointer_position=True, move_abs=True, move_rel=True, char_input=True,
        screen_capture=False, windows=True, hotkey=True))
    bounds: Rect = field(default_factory=lambda: Rect(0, 0, 1920, 1080))
    windows: dict[str, Rect] = field(default_factory=dict)
    pointer: tuple[int, int] = (0, 0)
    calls: list[Call] = field(default_factory=list)
    held_keys: list[str] = field(default_factory=list)
    held_chars: list[str] = field(default_factory=list)
    held_buttons: list[Button] = field(default_factory=list)
    hotkeys: list[tuple[list[str], Callable[[], None]]] = field(default_factory=list)
    closed: bool = False
    on_move: Callable[[FakeBackend], None] | None = None
    """Called after every ``move_abs`` and ``move_rel``, once the pointer has moved: tests
    use it to act as the user during a movement (e.g. set :attr:`pointer` elsewhere)."""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _record(self, name: str, *args: object) -> None:
        with self._lock:
            self.calls.append(Call(name, args))

    def calls_named(self, *names: str) -> list[Call]:
        with self._lock:
            return [c for c in self.calls if c.name in names]

    # --- Backend ---------------------------------------------------------------------

    def screen_bounds(self) -> Rect:
        return self.bounds

    def pointer_position(self) -> tuple[int, int] | None:
        return self.pointer if self.capabilities.pointer_position else None

    def move_abs(self, x: int, y: int) -> None:
        self._record("move_abs", x, y)
        self.pointer = (x, y)
        self._moved()

    def move_rel(self, dx: int, dy: int) -> None:
        self._record("move_rel", dx, dy)
        self.pointer = (self.pointer[0] + dx, self.pointer[1] + dy)
        self._moved()

    def _moved(self) -> None:
        if self.on_move is not None:
            self.on_move(self)

    def button_down(self, button: Button) -> None:
        self._record("button_down", button)
        self.held_buttons.append(button)

    def button_up(self, button: Button) -> None:
        self._record("button_up", button)
        if button in self.held_buttons:
            self.held_buttons.remove(button)

    def scroll(self, dx: int, dy: int) -> None:
        self._record("scroll", dx, dy)

    def key_down(self, key: str) -> None:
        _check_key(key)
        self._record("key_down", key)
        self.held_keys.append(key)

    def key_up(self, key: str) -> None:
        _check_key(key)
        self._record("key_up", key)
        if key in self.held_keys:
            self.held_keys.remove(key)

    def char_down(self, char: str) -> None:
        if len(char) != 1:
            raise UnknownKey(f"not a single character: {char!r}")
        self._record("char_down", char)
        self.held_chars.append(char)

    def char_up(self, char: str) -> None:
        self._record("char_up", char)
        if char in self.held_chars:
            self.held_chars.remove(char)

    def find_window(self, title: str) -> Rect | None:
        return next((rect for name, rect in self.windows.items() if title in name), None)

    def watch_hotkey(self, keys: list[str], callback: Callable[[], None]) -> Callable[[], None]:
        entry = (list(keys), callback)
        self.hotkeys.append(entry)
        return lambda: self.hotkeys.remove(entry) if entry in self.hotkeys else None

    def close(self) -> None:
        self.closed = True

    # --- test helpers ----------------------------------------------------------------

    def user_moves_pointer(self, x: int, y: int, *, after_moves: int) -> None:
        """Simulate the user putting the pointer at (x, y) right after the
        ``after_moves``-th pointer movement from now (once; replaces :attr:`on_move`)."""
        remaining = [after_moves]

        def hook(backend: FakeBackend) -> None:
            remaining[0] -= 1
            if remaining[0] == 0:
                backend.pointer = (x, y)

        self.on_move = hook

    def press_hotkey(self, keys: list[str]) -> None:
        """Simulate the user pressing a watched combination."""
        for watched, callback in list(self.hotkeys):
            if watched == keys:
                callback()

    def typed_text(self) -> str:
        """The characters sent with ``char_down``, in order."""
        return "".join(str(c.args[0]) for c in self.calls_named("char_down"))


def _check_key(key: str) -> None:
    if key in MODIFIER_KEYS or key in NAMED_KEYS:
        return
    if len(key) == 1 and key.isascii() and key.isprintable():
        return
    raise UnknownKey(f"unknown key {key!r}")
