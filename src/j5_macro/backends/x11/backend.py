"""The X11 backend: XTest input injection through python-xlib.

Characters that no key types at shift level 0 of the current layout (``A``, ``!``,
``ñ``, ``€``, ``日``...) are typed by temporarily mapping a spare keycode (one with no
keysyms) to that character's keysym, pressing it, and restoring the original mapping
once the character is released and a short grace period has passed. The grace period
lets applications fetch the mapping after the ``MappingNotify`` that precedes the key
event; restoring at once could make them translate the key with the old mapping.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from Xlib import XK, X
from Xlib import error as xerror
from Xlib.display import Display
from Xlib.ext import xtest

from j5_macro.backends.base import MODIFIER_KEYS, Button, Capabilities, Rect, UnknownKey
from j5_macro.backends.x11.hotkey import HotkeyWatcher, lock_variants
from j5_macro.backends.x11.keys import (
    NUM_LOCK_KEYSYM,
    SCROLL_LOCK_KEYSYM,
    Mapping,
    char_keysym,
    key_keysym,
    keycode_anywhere,
    keycode_at_level0,
    remap_row,
    spare_keycodes,
)
from j5_macro.backends.x11.windows import find_window

DEFAULT_REMAP_GRACE_SECONDS = 0.25

_BUTTONS = {Button.LEFT: 1, Button.MIDDLE: 2, Button.RIGHT: 3}
_SCROLL_UP, _SCROLL_DOWN, _SCROLL_LEFT, _SCROLL_RIGHT = 4, 5, 6, 7


@dataclass
class _Remap:
    """A spare keycode temporarily mapped to a keysym."""

    keycode: int
    keysym: int
    original: list[int]
    holds: int = 0
    generation: int = 0
    timer: threading.Timer | None = None


@dataclass
class _HeldChar:
    keycode: int
    remapped: bool


@dataclass
class _State:
    remaps: dict[int, _Remap] = field(default_factory=dict)
    held_chars: dict[str, list[_HeldChar]] = field(default_factory=dict)
    held_keys: dict[str, list[int]] = field(default_factory=dict)
    pressed: dict[int, int] = field(default_factory=dict)
    """How many held presses each keycode has (keys and characters can share one)."""
    held_buttons: set[int] = field(default_factory=set)
    watchers: list[HotkeyWatcher] = field(default_factory=list)


class X11Backend:
    """Input primitives for an X11 session (XTest), window lookup and global hotkeys.

    One display connection serves every call, serialized by a lock; each hotkey watcher
    has its own connection and thread.
    """

    name = "x11"
    capabilities = Capabilities(pointer_position=True, move_abs=True, move_rel=True,
                                char_input=True, screen_capture=False, windows=True,
                                hotkey=True)

    def __init__(self, display_name: str | None = None, *,
                 remap_grace_seconds: float = DEFAULT_REMAP_GRACE_SECONDS) -> None:
        self._display = Display(display_name)
        try:
            if not self._display.has_extension("XTEST"):
                raise RuntimeError("the X server does not provide the XTEST extension")
        except BaseException:
            self._display.close()
            raise
        self._display_name = self._display.get_display_name()
        self._root = self._display.screen().root
        self._min_keycode = int(self._display.display.info.min_keycode)
        self._max_keycode = int(self._display.display.info.max_keycode)
        self._grace = remap_grace_seconds
        self._lock = threading.RLock()
        self._state = _State()
        self._closed = False

    @property
    def display_name(self) -> str:
        return self._display_name

    # --- screen and pointer ----------------------------------------------------------

    def screen_bounds(self) -> Rect:
        with self._lock:
            self._check_open()
            geometry = self._root.get_geometry()
            return Rect(0, 0, int(geometry.width), int(geometry.height))

    def pointer_position(self) -> tuple[int, int] | None:
        with self._lock:
            self._check_open()
            pointer = self._root.query_pointer()
            return int(pointer.root_x), int(pointer.root_y)

    def move_abs(self, x: int, y: int) -> None:
        with self._lock:
            self._check_open()
            xtest.fake_input(self._display, X.MotionNotify, x=int(x), y=int(y))
            self._display.sync()

    def move_rel(self, dx: int, dy: int) -> None:
        with self._lock:
            self._check_open()
            xtest.fake_input(self._display, X.MotionNotify, detail=1, x=int(dx), y=int(dy))
            self._display.sync()

    def button_down(self, button: Button) -> None:
        code = _button_code(button)
        with self._lock:
            self._check_open()
            xtest.fake_input(self._display, X.ButtonPress, code)
            self._display.sync()
            self._state.held_buttons.add(code)

    def button_up(self, button: Button) -> None:
        code = _button_code(button)
        with self._lock:
            self._check_open()
            xtest.fake_input(self._display, X.ButtonRelease, code)
            self._display.sync()
            self._state.held_buttons.discard(code)

    def scroll(self, dx: int, dy: int) -> None:
        clicks = [(_SCROLL_DOWN if dy > 0 else _SCROLL_UP, abs(dy)),
                  (_SCROLL_RIGHT if dx > 0 else _SCROLL_LEFT, abs(dx))]
        with self._lock:
            self._check_open()
            for code, count in clicks:
                for _ in range(count):
                    xtest.fake_input(self._display, X.ButtonPress, code)
                    xtest.fake_input(self._display, X.ButtonRelease, code)
            self._display.sync()

    # --- keys --------------------------------------------------------------------------

    def key_down(self, key: str) -> None:
        keysym = key_keysym(key)
        with self._lock:
            self._check_open()
            keycode = keycode_anywhere(self._mapping(), self._min_keycode, keysym)
            if keycode is None:
                raise UnknownKey(f"no key produces {key!r} in the current keyboard mapping")
            self._press(keycode)
            self._state.held_keys.setdefault(key, []).append(keycode)

    def key_up(self, key: str) -> None:
        keysym = key_keysym(key)
        with self._lock:
            self._check_open()
            stack = self._state.held_keys.get(key)
            if stack:
                keycode = stack.pop()
                if not stack:
                    del self._state.held_keys[key]
            else:
                found = keycode_anywhere(self._mapping(), self._min_keycode, keysym)
                if found is None:
                    raise UnknownKey(
                        f"no key produces {key!r} in the current keyboard mapping")
                keycode = found
            self._release(keycode)

    def char_down(self, char: str) -> None:
        keysym = char_keysym(char)
        with self._lock:
            self._check_open()
            remap = self._state.remaps.get(keysym)
            if remap is not None:
                held = _HeldChar(remap.keycode, remapped=True)
            else:
                mapping = self._mapping()
                keycode = keycode_at_level0(mapping, self._min_keycode, keysym)
                if keycode is not None:
                    held = _HeldChar(keycode, remapped=False)
                else:
                    remap = self._remap(keysym, mapping, char)
                    held = _HeldChar(remap.keycode, remapped=True)
            if remap is not None:
                remap.holds += 1
                remap.generation += 1  # cancels a pending restore
            try:
                self._press(held.keycode)
            except BaseException:
                if remap is not None:
                    self._release_remap(remap)
                raise
            self._state.held_chars.setdefault(char, []).append(held)

    def char_up(self, char: str) -> None:
        keysym = char_keysym(char)
        with self._lock:
            self._check_open()
            stack = self._state.held_chars.get(char)
            if not stack:
                keycode = keycode_at_level0(self._mapping(), self._min_keycode, keysym)
                if keycode is not None:
                    self._release(keycode)
                return
            held = stack.pop()
            if not stack:
                del self._state.held_chars[char]
            self._release(held.keycode)
            if held.remapped:
                remap = self._state.remaps.get(keysym)
                if remap is not None:
                    self._release_remap(remap)

    def _press(self, keycode: int) -> None:
        """Press a key. A key is either down or up for the X server, so pressing a key
        that is already held (the same character twice) releases it first: the second
        press then produces a second character."""
        pressed = self._state.pressed
        if pressed.get(keycode, 0):
            xtest.fake_input(self._display, X.KeyRelease, keycode)
        xtest.fake_input(self._display, X.KeyPress, keycode)
        self._display.sync()
        pressed[keycode] = pressed.get(keycode, 0) + 1

    def _release(self, keycode: int) -> None:
        """Release a key once every press of it has been released."""
        pressed = self._state.pressed
        remaining = pressed.get(keycode, 0) - 1
        if remaining > 0:
            pressed[keycode] = remaining
            return
        pressed.pop(keycode, None)
        xtest.fake_input(self._display, X.KeyRelease, keycode)
        self._display.sync()

    def _mapping(self) -> Mapping:
        count = self._max_keycode - self._min_keycode + 1
        return [list(row) for row in self._display.get_keyboard_mapping(self._min_keycode,
                                                                         count)]

    def _remap(self, keysym: int, mapping: Mapping, char: str) -> _Remap:
        """Map a spare keycode (or one whose remap is idle) to ``keysym``."""
        in_use = {r.keycode for r in self._state.remaps.values()}
        candidates = [k for k in spare_keycodes(mapping, self._min_keycode) if k not in in_use]
        if candidates:
            keycode = candidates[0]
            original = list(mapping[keycode - self._min_keycode])
        else:
            idle = next((r for r in self._state.remaps.values() if r.holds == 0), None)
            if idle is None:
                raise UnknownKey(f"cannot type {char!r}: no spare keycode is left to map it")
            self._forget(idle)
            keycode, original = idle.keycode, idle.original
        width = len(mapping[0]) if mapping else 2
        self._display.change_keyboard_mapping(keycode, [remap_row(keysym, width)])
        self._display.sync()
        remap = _Remap(keycode=keycode, keysym=keysym, original=original)
        self._state.remaps[keysym] = remap
        return remap

    def _release_remap(self, remap: _Remap) -> None:
        remap.holds = max(0, remap.holds - 1)
        if remap.holds:
            return
        if self._grace <= 0:
            self._restore(remap)
            return
        generation = remap.generation
        timer = threading.Timer(self._grace, self._restore_if_idle, (remap.keysym, generation))
        timer.daemon = True
        remap.timer = timer
        timer.start()

    def _restore_if_idle(self, keysym: int, generation: int) -> None:
        with self._lock:
            if self._closed:
                return
            remap = self._state.remaps.get(keysym)
            if remap is None or remap.holds or remap.generation != generation:
                return
            with contextlib.suppress(xerror.XError, OSError):
                self._restore(remap)

    def _forget(self, remap: _Remap) -> None:
        if remap.timer is not None:
            remap.timer.cancel()
            remap.timer = None
        self._state.remaps.pop(remap.keysym, None)

    def _restore(self, remap: _Remap) -> None:
        self._forget(remap)
        self._display.change_keyboard_mapping(remap.keycode, [remap.original])
        self._display.sync()

    # --- windows and hotkeys ----------------------------------------------------------

    def find_window(self, title: str) -> Rect | None:
        with self._lock:
            self._check_open()
            return find_window(self._display, title)

    def watch_hotkey(self, keys: list[str], callback: Callable[[], None]) -> Callable[[], None]:
        if not keys:
            raise ValueError("a hotkey needs at least one key")
        trigger, modifiers = _split_hotkey(keys)
        with self._lock:
            self._check_open()
            keycode = keycode_anywhere(self._mapping(), self._min_keycode, key_keysym(trigger))
            if keycode is None:
                raise UnknownKey(f"no key produces {trigger!r} in the current keyboard mapping")
            masks = self._modifier_masks()
            required = 0
            for modifier in modifiers:
                required |= masks[modifier]
            locks = [X.LockMask, masks["numlock"], masks["scrolllock"]]
        description = "+".join(keys)
        watcher = HotkeyWatcher(self._display_name, keycode, lock_variants(required, locks),
                                callback, description)
        with self._lock:
            if self._closed:
                watcher.stop()
                raise RuntimeError("the X11 backend is closed")
            self._state.watchers.append(watcher)

        def unwatch() -> None:
            with self._lock:
                if watcher in self._state.watchers:
                    self._state.watchers.remove(watcher)
            watcher.stop()

        return unwatch

    def _modifier_masks(self) -> dict[str, int]:
        """Masks of each modifier name in the server's modifier mapping."""
        mapping = self._display.get_modifier_mapping()
        keymap = self._mapping()

        def mask_of(keysym_names: tuple[str, ...], default: int) -> int:
            for keysym_name in keysym_names:
                keysym = XK.string_to_keysym(keysym_name)
                keycode = keycode_anywhere(keymap, self._min_keycode, keysym)
                if keycode is None:
                    continue
                for index, keycodes in enumerate(mapping):
                    if keycode in keycodes:
                        return 1 << index
            return default

        return {
            "ctrl": X.ControlMask,
            "shift": X.ShiftMask,
            "alt": mask_of(("Alt_L", "Alt_R", "Meta_L"), X.Mod1Mask),
            "super": mask_of(("Super_L", "Super_R"), X.Mod4Mask),
            "numlock": _mask_for_keysym(mapping, keymap, self._min_keycode, NUM_LOCK_KEYSYM),
            "scrolllock": _mask_for_keysym(mapping, keymap, self._min_keycode,
                                           SCROLL_LOCK_KEYSYM),
        }

    # --- lifecycle --------------------------------------------------------------------

    def close(self) -> None:
        """Release held input, restore every key remap, stop hotkeys and disconnect."""
        with self._lock:
            if self._closed:
                return
            watchers = list(self._state.watchers)
            self._state.watchers.clear()
            with contextlib.suppress(xerror.XError, OSError):
                self._release_everything()
            self._closed = True
        for watcher in watchers:
            watcher.stop()
        with self._lock, contextlib.suppress(Exception):
            self._display.close()

    def _release_everything(self) -> None:
        state = self._state
        for keycode in state.pressed:
            xtest.fake_input(self._display, X.KeyRelease, keycode)
        state.pressed.clear()
        state.held_chars.clear()
        state.held_keys.clear()
        for code in state.held_buttons:
            xtest.fake_input(self._display, X.ButtonRelease, code)
        state.held_buttons.clear()
        self._display.sync()
        for remap in list(state.remaps.values()):
            self._restore(remap)

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("the X11 backend is closed")

    def __enter__(self) -> X11Backend:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _button_code(button: Button) -> int:
    try:
        return _BUTTONS[Button(button)]
    except (KeyError, ValueError):
        raise ValueError(f"unknown mouse button {button!r}") from None


def _split_hotkey(keys: list[str]) -> tuple[str, list[str]]:
    """The trigger key and the modifiers held with it: the only non-modifier key, or the
    last key when every key is a modifier."""
    others = [k for k in keys if k not in MODIFIER_KEYS]
    if len(others) > 1:
        raise UnknownKey(f"a hotkey has at most one non-modifier key: {'+'.join(keys)}")
    trigger = others[0] if others else keys[-1]
    key_keysym(trigger)  # validates the name
    modifiers = [k for k in keys if k in MODIFIER_KEYS and k != trigger]
    return trigger, modifiers


def _mask_for_keysym(modifier_mapping: Mapping, keymap: Mapping, min_keycode: int,
                     keysym: int) -> int:
    keycode = keycode_anywhere(keymap, min_keycode, keysym)
    if keycode is None:
        return 0
    for index, keycodes in enumerate(modifier_mapping):
        if keycode in keycodes:
            return 1 << index
    return 0
