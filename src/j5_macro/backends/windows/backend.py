"""The Windows backend: input injection with ``SendInput``.

The pointer moves with absolute ``MOUSEINPUT`` events normalized over the virtual
desktop (every monitor; coordinates may be negative), so no pointer acceleration
applies and a move lands on the exact pixel. Physical keys are virtual keys with their
scan codes; characters are Unicode packets, which type any character whatever the
keyboard layout, dead keys included, without touching the layout.

Every coordinate is in physical pixels because the engine declares per-monitor DPI
awareness (``j5_macro.backends.windows.dpi``) before it creates the backend; the
backend declares it too, in case it is used outside the engine.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable

from j5_macro.backends.base import MODIFIER_KEYS, Button, Capabilities, Rect, UnknownKey
from j5_macro.backends.windows.dpi import declare_dpi_awareness
from j5_macro.backends.windows.hotkey import HotkeyWatcher
from j5_macro.backends.windows.keys import (
    CONTROL_CHAR_VKS,
    EXTENDED_VKS,
    HOTKEY_MODIFIERS,
    char_units,
    check_key,
    named_vk,
    us_vk,
)
from j5_macro.backends.windows.win32 import (
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_HWHEEL,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MIDDLEDOWN,
    MOUSEEVENTF_MIDDLEUP,
    MOUSEEVENTF_MOVE,
    MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP,
    MOUSEEVENTF_VIRTUALDESK,
    MOUSEEVENTF_WHEEL,
    WHEEL_DELTA,
    InputEvent,
    KeyEvent,
    MouseEvent,
    Win32Api,
    normalize,
)

_BUTTONS = {
    Button.LEFT: (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    Button.RIGHT: (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    Button.MIDDLE: (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


class WindowsBackend:
    """Input primitives for a Windows desktop session, window lookup and global hotkeys.

    ``api`` replaces the system calls (tests use a fake); by default they go through
    ctypes, which only works on Windows. Calls are serialized by a lock.
    """

    name = "windows"
    capabilities = Capabilities(pointer_position=True, move_abs=True, move_rel=True,
                                char_input=True, screen_capture=False, windows=True,
                                hotkey=True)

    def __init__(self, api: Win32Api | None = None) -> None:
        if api is None:
            from j5_macro.backends.windows.win32 import User32

            declare_dpi_awareness()
            api = User32()
        self._api = api
        self._lock = threading.RLock()
        self._held_keys: dict[str, list[int]] = {}
        self._held_chars: dict[str, list[list[InputEvent]]] = {}
        """The events that release each held character, per press."""
        self._held_buttons: list[Button] = []
        self._watchers: list[HotkeyWatcher] = []
        self._closed = False

    # --- screen and pointer ----------------------------------------------------------

    def screen_bounds(self) -> Rect:
        with self._lock:
            self._check_open()
            return self._api.virtual_screen()

    def pointer_position(self) -> tuple[int, int] | None:
        with self._lock:
            self._check_open()
            return self._api.cursor_pos()

    def move_abs(self, x: int, y: int) -> None:
        with self._lock:
            self._check_open()
            self._move_to(int(x), int(y))

    def move_rel(self, dx: int, dy: int) -> None:
        """Move by (dx, dy) from the real cursor position, as an absolute move: relative
        ``MOUSEINPUT`` events go through pointer acceleration and would land elsewhere."""
        with self._lock:
            self._check_open()
            current = self._api.cursor_pos()
            if current is None:
                raise RuntimeError("Windows does not report the cursor position "
                                   "(is the secure desktop showing?)")
            self._move_to(current[0] + int(dx), current[1] + int(dy))

    def _move_to(self, x: int, y: int) -> None:
        bounds = self._api.virtual_screen()
        self._api.send_input([MouseEvent(
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            dx=normalize(x, bounds.x, bounds.width), dy=normalize(y, bounds.y, bounds.height))])

    def button_down(self, button: Button) -> None:
        button = _button(button)
        with self._lock:
            self._check_open()
            self._api.send_input([MouseEvent(_BUTTONS[button][0])])
            self._held_buttons.append(button)

    def button_up(self, button: Button) -> None:
        button = _button(button)
        with self._lock:
            self._check_open()
            self._api.send_input([MouseEvent(_BUTTONS[button][1])])
            if button in self._held_buttons:
                self._held_buttons.remove(button)

    def scroll(self, dx: int, dy: int) -> None:
        """One wheel event per notch, like a real wheel: positive ``dy`` scrolls down
        (a negative wheel delta), positive ``dx`` right."""
        events: list[InputEvent] = []
        events += [MouseEvent(MOUSEEVENTF_WHEEL, data=-WHEEL_DELTA if dy > 0 else WHEEL_DELTA)
                   for _ in range(abs(int(dy)))]
        events += [MouseEvent(MOUSEEVENTF_HWHEEL,
                              data=WHEEL_DELTA if dx > 0 else -WHEEL_DELTA)
                   for _ in range(abs(int(dx)))]
        with self._lock:
            self._check_open()
            self._api.send_input(events)

    # --- keys --------------------------------------------------------------------------

    def key_down(self, key: str) -> None:
        with self._lock:
            self._check_open()
            vk = self._vk(key)
            self._api.send_input([self._key_event(vk, up=False)])
            self._held_keys.setdefault(key, []).append(vk)

    def key_up(self, key: str) -> None:
        with self._lock:
            self._check_open()
            stack = self._held_keys.get(key)
            if stack:
                vk = stack.pop()
                if not stack:
                    del self._held_keys[key]
            else:
                vk = self._vk(key)
            self._api.send_input([self._key_event(vk, up=True)])

    def char_down(self, char: str) -> None:
        with self._lock:
            self._check_open()
            self._api.send_input(self._char_events(char, up=False))
            self._held_chars.setdefault(char, []).append(self._char_events(char, up=True))

    def char_up(self, char: str) -> None:
        with self._lock:
            self._check_open()
            stack = self._held_chars.get(char)
            if stack:
                events = stack.pop()
                if not stack:
                    del self._held_chars[char]
            else:
                events = self._char_events(char, up=True)
            self._api.send_input(events)

    def _char_events(self, char: str, *, up: bool) -> list[InputEvent]:
        """Enter, Tab and Backspace for their control characters, else one Unicode
        packet per UTF-16 code unit."""
        vk = CONTROL_CHAR_VKS.get(char)
        if vk is not None:
            return [self._key_event(vk, up=up)]
        flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
        return [KeyEvent(0, unit, flags) for unit in char_units(char)]

    def _vk(self, key: str) -> int:
        check_key(key)
        vk = named_vk(key)
        if vk is not None:
            return vk
        found = self._api.vk_for_char(key)
        return found if found is not None else us_vk(key)

    def _key_event(self, vk: int, *, up: bool) -> KeyEvent:
        flags = KEYEVENTF_KEYUP if up else 0
        if vk in EXTENDED_VKS:
            flags |= KEYEVENTF_EXTENDEDKEY
        return KeyEvent(vk, self._api.scan_code(vk), flags)

    # --- windows and hotkeys ----------------------------------------------------------

    def find_window(self, title: str) -> Rect | None:
        """The client area of the topmost visible (not minimized, not cloaked) top-level
        window whose title contains ``title``."""
        with self._lock:
            self._check_open()
            api = self._api
            for hwnd in api.windows():
                if not hwnd or title not in api.window_title(hwnd):
                    continue
                if not api.window_shown(hwnd):
                    continue
                rect = api.client_rect(hwnd)
                if rect is not None:
                    return rect
            return None

    def watch_hotkey(self, keys: list[str], callback: Callable[[], None]) -> Callable[[], None]:
        if not keys:
            raise ValueError("a hotkey needs at least one key")
        others = [k for k in keys if k not in MODIFIER_KEYS]
        if len(others) > 1:
            raise UnknownKey(f"a hotkey has at most one non-modifier key: {'+'.join(keys)}")
        trigger = others[0] if others else keys[-1]
        modifiers = 0
        for key in keys:
            if key in MODIFIER_KEYS and key != trigger:
                modifiers |= HOTKEY_MODIFIERS[key]
        with self._lock:
            self._check_open()
            vk = self._vk(trigger)
        watcher = HotkeyWatcher(self._api, modifiers, vk, callback, "+".join(keys))
        with self._lock:
            if self._closed:
                watcher.stop()
                raise RuntimeError("the Windows backend is closed")
            self._watchers.append(watcher)

        def unwatch() -> None:
            with self._lock:
                if watcher in self._watchers:
                    self._watchers.remove(watcher)
            watcher.stop()

        return unwatch

    # --- lifecycle --------------------------------------------------------------------

    def close(self) -> None:
        """Release held keys, characters and buttons (newest first) and stop hotkeys."""
        with self._lock:
            if self._closed:
                return
            watchers = list(self._watchers)
            self._watchers.clear()
            with contextlib.suppress(Exception):
                self._release_everything()
            self._closed = True
        for watcher in watchers:
            watcher.stop()

    def _release_everything(self) -> None:
        events: list[InputEvent] = []
        for stack in self._held_chars.values():
            for release in reversed(stack):
                events += release
        for vks in self._held_keys.values():
            events += [self._key_event(vk, up=True) for vk in reversed(vks)]
        events += [MouseEvent(_BUTTONS[b][1]) for b in reversed(self._held_buttons)]
        self._held_chars.clear()
        self._held_keys.clear()
        self._held_buttons.clear()
        self._api.send_input(events)

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("the Windows backend is closed")

    def __enter__(self) -> WindowsBackend:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _button(button: Button) -> Button:
    try:
        return Button(button)
    except ValueError:
        raise ValueError(f"unknown mouse button {button!r}") from None
