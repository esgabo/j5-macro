"""Global hotkeys on Windows with ``RegisterHotKey``.

A hotkey registered without a window posts ``WM_HOTKEY`` to the queue of the thread that
registered it, so each watcher owns a daemon thread that registers the combination and
then waits for messages; stopping posts ``WM_QUIT`` to that thread. Windows matches
hotkeys whatever the lock keys (CapsLock, NumLock) are, and ``MOD_NOREPEAT`` keeps a
held combination from firing again and again.
"""

from __future__ import annotations

import contextlib
import itertools
import threading
from collections.abc import Callable

from j5_macro.backends.windows.keys import MOD_NOREPEAT
from j5_macro.backends.windows.win32 import WM_HOTKEY, Win32Api

_JOIN_SECONDS = 2.0
_ids = itertools.count(1)


class HotkeyConflict(RuntimeError):
    """Another program already registered the key combination."""


class HotkeyWatcher:
    """Registers ``modifiers`` + ``vk`` and calls ``callback`` on every press."""

    def __init__(self, api: Win32Api, modifiers: int, vk: int,
                 callback: Callable[[], None], description: str) -> None:
        self._api = api
        self._modifiers = modifiers | MOD_NOREPEAT
        self._vk = vk
        self._callback = callback
        self._id = next(_ids)
        self._thread_id: int | None = None
        self._registered = threading.Event()
        self._ok = False
        self._stopped = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name=f"j5-hotkey-{description}",
                                        daemon=True)
        self._thread.start()
        self._registered.wait()
        if not self._ok:
            self._thread.join(_JOIN_SECONDS)
            raise HotkeyConflict(
                f"the hotkey {description} is already taken by another application")

    def _run(self) -> None:
        api = self._api
        try:
            api.create_message_queue()
            with self._lock:
                self._thread_id = api.thread_id()
            self._ok = api.register_hotkey(self._id, self._modifiers, self._vk)
        except Exception:
            self._ok = False
        finally:
            self._registered.set()
        if not self._ok:
            return
        try:
            while True:
                message = api.get_message()
                if message is None:
                    break
                kind, wparam = message
                if kind == WM_HOTKEY and wparam == self._id:
                    # A failing callback must not stop the watcher.
                    with contextlib.suppress(Exception):
                        self._callback()
        finally:
            with contextlib.suppress(Exception):
                api.unregister_hotkey(self._id)

    def stop(self) -> None:
        """Unregister and stop the thread; safe to call more than once, from any thread."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            thread_id = self._thread_id
        if thread_id is not None and self._thread.is_alive():
            self._api.post_quit(thread_id)
        if threading.current_thread() is not self._thread:
            self._thread.join(_JOIN_SECONDS)

    @property
    def running(self) -> bool:
        return self._thread.is_alive()
