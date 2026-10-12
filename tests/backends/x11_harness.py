"""Helpers for X11 backend tests: real X windows on their own connection that observe input."""

from __future__ import annotations

import os
import select
import time
from collections.abc import Callable
from typing import Any

from Xlib import X, Xatom
from Xlib.display import Display
from Xlib.ext import xtest

TIMEOUT_S = 3.0

INPUT_EVENTS = (X.KeyPressMask | X.KeyReleaseMask | X.ButtonPressMask | X.ButtonReleaseMask
                | X.StructureNotifyMask)


def display_name() -> str | None:
    return os.environ.get("DISPLAY") or None


def wait_until(predicate: Callable[[], bool], timeout: float = TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class Observer:
    """A connection with top-level windows that records the input events they receive."""

    def __init__(self) -> None:
        self.display = Display(display_name())
        self.screen = self.display.screen()
        self.root = self.screen.root
        self.windows: list[Any] = []
        self.locks_off()

    def locks_off(self) -> None:
        """Turn CapsLock and NumLock off: lock state outlives the tests that toggle it."""
        for keysym, mask in ((0xFFE5, X.LockMask), (0xFF7F, X.Mod2Mask)):
            if self.modifier_state() & mask:
                self.tap_keysym(keysym)
        wait_until(lambda: self.modifier_state() & (X.LockMask | X.Mod2Mask) == 0)

    def close(self) -> None:
        self.locks_off()
        for window in self.windows:
            window.destroy()
        self.display.sync()
        self.display.close()

    def create_window(self, x: int, y: int, width: int, height: int, *,
                      wm_name: str | None = None, net_wm_name: str | None = None,
                      wm_state: bool = False, mapped: bool = True,
                      parent: Any = None, focus: bool = False) -> Any:
        parent = parent or self.root
        window = parent.create_window(
            x, y, width, height, 0, self.screen.root_depth, X.InputOutput,
            X.CopyFromParent, background_pixel=self.screen.white_pixel,
            event_mask=INPUT_EVENTS, override_redirect=parent == self.root)
        if wm_name is not None:
            window.change_property(Xatom.WM_NAME, Xatom.STRING, 8, wm_name.encode("latin-1"))
        if net_wm_name is not None:
            window.change_property(self.display.get_atom("_NET_WM_NAME"),
                                   self.display.get_atom("UTF8_STRING"), 8,
                                   net_wm_name.encode("utf-8"))
        if wm_state:
            atom = self.display.get_atom("WM_STATE")
            window.change_property(atom, atom, 32, [1, 0])  # NormalState
        if parent == self.root:
            self.windows.append(window)
        if mapped:
            window.map()
            self.wait_for(lambda e: e.type == X.MapNotify and e.window == window)
        if focus:
            window.set_input_focus(X.RevertToParent, X.CurrentTime)
        self.display.sync()
        return window

    def events(self, timeout: float = 0.2) -> list[Any]:
        """Every event that arrives within ``timeout`` seconds."""
        found: list[Any] = []
        deadline = time.monotonic() + timeout
        while True:
            while self.display.pending_events():
                found.append(self.display.next_event())
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return found
            select.select([self.display.fileno()], [], [], remaining)

    def wait_for(self, predicate: Callable[[Any], bool], timeout: float = TIMEOUT_S) -> Any:
        """The first event matching ``predicate``; earlier events are dropped."""
        deadline = time.monotonic() + timeout
        while True:
            while self.display.pending_events():
                event = self.display.next_event()
                if predicate(event):
                    return event
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("timed out waiting for an X event")
            select.select([self.display.fileno()], [], [], remaining)

    def drain(self) -> None:
        self.display.sync()
        self.events(0.05)

    def keyboard_mapping(self) -> list[list[int]]:
        info = self.display.display.info
        count = info.max_keycode - info.min_keycode + 1
        return [list(row) for row in
                self.display.get_keyboard_mapping(info.min_keycode, count)]

    def keysym_of(self, event: Any) -> int:
        """The keysym the event's keycode has right now (shift level from the state)."""
        row = self.display.get_keyboard_mapping(event.detail, 1)[0]
        level = 1 if event.state & X.ShiftMask else 0
        keysym = int(row[level])
        if level == 1 and keysym == 0:
            keysym = int(row[0])
        return keysym

    def tap_keysym(self, keysym: int) -> None:
        """Press and release a key from this connection (used to toggle NumLock)."""
        keycode = self.display.keysym_to_keycode(keysym)
        xtest.fake_input(self.display, X.KeyPress, keycode)
        xtest.fake_input(self.display, X.KeyRelease, keycode)
        self.display.sync()

    def modifier_state(self) -> int:
        return int(self.root.query_pointer().mask)
