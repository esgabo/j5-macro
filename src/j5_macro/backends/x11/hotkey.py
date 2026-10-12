"""Global hotkeys on X11 with passive key grabs on the root window.

A grab only matches the exact modifier state, so the combination is grabbed once per
combination of the lock modifiers (CapsLock, NumLock, ScrollLock): the hotkey then works
whatever lights are on. Each watcher owns a display connection and a daemon thread that
reads the grabbed key events.
"""

from __future__ import annotations

import contextlib
import select
import threading
from collections.abc import Callable, Iterable
from itertools import combinations

from Xlib import X
from Xlib import error as xerror
from Xlib.display import Display

_POLL_SECONDS = 0.05
_JOIN_SECONDS = 2.0


class HotkeyConflict(RuntimeError):
    """Another client already grabbed the key combination."""


def lock_variants(required: int, lock_masks: Iterable[int]) -> list[int]:
    """Every ``required | locks`` for each subset of the lock masks not in ``required``."""
    locks = sorted({m for m in lock_masks if m and not m & required})
    variants = []
    for size in range(len(locks) + 1):
        for subset in combinations(locks, size):
            mask = required
            for m in subset:
                mask |= m
            variants.append(mask)
    return variants


class HotkeyWatcher:
    """Grabs ``keycode`` with each modifier mask and calls ``callback`` on every press."""

    def __init__(self, display_name: str | None, keycode: int, masks: list[int],
                 callback: Callable[[], None], description: str) -> None:
        self._keycode = keycode
        self._masks = masks
        self._callback = callback
        self._stop = threading.Event()
        self._display = Display(display_name)
        self._root = self._display.screen().root
        try:
            self._grab(description)
        except BaseException:
            self._display.close()
            raise
        self._thread = threading.Thread(target=self._run, name=f"j5-hotkey-{description}",
                                        daemon=True)
        self._thread.start()

    def _grab(self, description: str) -> None:
        catcher = xerror.CatchError(xerror.BadAccess)
        for mask in self._masks:
            self._root.grab_key(self._keycode, mask, False, X.GrabModeAsync, X.GrabModeAsync,
                                onerror=catcher)
        self._display.sync()
        if catcher.get_error() is not None:
            self._ungrab()
            raise HotkeyConflict(
                f"the hotkey {description} is already taken by another application")

    def _ungrab(self) -> None:
        for mask in self._masks:
            self._root.ungrab_key(self._keycode, mask)
        with contextlib.suppress(xerror.XError):
            self._display.sync()

    def _run(self) -> None:
        display = self._display
        try:
            fd = display.fileno()
            while not self._stop.is_set():
                readable, _, _ = select.select([fd], [], [], _POLL_SECONDS)
                if not readable and not display.pending_events():
                    continue
                while display.pending_events() and not self._stop.is_set():
                    event = display.next_event()
                    if event.type == X.KeyPress and event.detail == self._keycode:
                        # A failing callback must not stop the watcher.
                        with contextlib.suppress(Exception):
                            self._callback()
        except (OSError, xerror.XError, xerror.ConnectionClosedError):
            pass  # the connection is gone: nothing left to watch
        finally:
            with contextlib.suppress(Exception):
                self._ungrab()
            with contextlib.suppress(Exception):
                display.close()

    def stop(self) -> None:
        """Ungrab and stop the thread; safe to call more than once, from any thread."""
        self._stop.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(_JOIN_SECONDS)

    @property
    def running(self) -> bool:
        return self._thread.is_alive()
