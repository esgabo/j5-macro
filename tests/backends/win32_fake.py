"""A fake :class:`~j5_macro.backends.windows.win32.Win32Api`: records ``SendInput``
events and plays a small desktop, so the Windows backend is tested on any platform."""

from __future__ import annotations

import queue
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field

from j5_macro.backends.base import Rect
from j5_macro.backends.windows.win32 import (
    WM_HOTKEY,
    InputDenied,
    InputEvent,
)


@dataclass
class FakeWindow:
    title: str
    client: Rect
    shown: bool = True


@dataclass
class FakeWin32:
    screen: Rect = field(default_factory=lambda: Rect(-1920, 0, 3840, 1080))
    cursor: tuple[int, int] | None = (0, 0)
    windows_list: list[FakeWindow] = field(default_factory=list)
    """Topmost first; the handle of a window is its index plus one."""
    layout: dict[str, int] = field(default_factory=dict)
    """``vk_for_char`` answers: characters of the current layout."""
    sent: list[InputEvent] = field(default_factory=list)
    batches: list[list[InputEvent]] = field(default_factory=list)
    deny: bool = False
    taken_hotkeys: set[tuple[int, int]] = field(default_factory=set)
    registered: dict[int, tuple[int, int, int]] = field(default_factory=dict)
    """Hotkey id → (modifiers, vk, thread id)."""
    _queues: dict[int, queue.Queue[tuple[int, int] | None]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- input -----------------------------------------------------------------------

    def send_input(self, events: Sequence[InputEvent]) -> None:
        if self.deny:
            raise InputDenied("blocked")
        if events:
            self.batches.append(list(events))
            self.sent.extend(events)

    def cursor_pos(self) -> tuple[int, int] | None:
        return self.cursor

    def virtual_screen(self) -> Rect:
        return self.screen

    # --- windows ---------------------------------------------------------------------

    def windows(self) -> list[int]:
        return list(range(1, len(self.windows_list) + 1))

    def window_title(self, hwnd: int) -> str:
        return self.windows_list[hwnd - 1].title

    def window_shown(self, hwnd: int) -> bool:
        return self.windows_list[hwnd - 1].shown

    def client_rect(self, hwnd: int) -> Rect | None:
        return self.windows_list[hwnd - 1].client

    # --- keys ------------------------------------------------------------------------

    def vk_for_char(self, char: str) -> int | None:
        return self.layout.get(char)

    def scan_code(self, vk: int) -> int:
        return vk + 0x100  # recognizable in assertions

    # --- hotkeys ---------------------------------------------------------------------

    def thread_id(self) -> int:
        return threading.get_ident()

    def create_message_queue(self) -> None:
        with self._lock:
            self._queues.setdefault(threading.get_ident(), queue.Queue())

    def register_hotkey(self, hotkey_id: int, modifiers: int, vk: int) -> bool:
        if (modifiers, vk) in self.taken_hotkeys:
            return False
        with self._lock:
            self.registered[hotkey_id] = (modifiers, vk, threading.get_ident())
        return True

    def unregister_hotkey(self, hotkey_id: int) -> None:
        with self._lock:
            self.registered.pop(hotkey_id, None)

    def get_message(self) -> tuple[int, int] | None:
        with self._lock:
            messages = self._queues[threading.get_ident()]
        return messages.get()

    def post_quit(self, thread_id: int) -> bool:
        with self._lock:
            messages = self._queues.get(thread_id)
        if messages is None:
            return False
        messages.put(None)
        return True

    # --- test helpers ----------------------------------------------------------------

    def press_hotkey(self, modifiers: int, vk: int) -> None:
        """Post ``WM_HOTKEY`` for every registration of the combination."""
        with self._lock:
            targets = [(hotkey_id, thread) for hotkey_id, (m, v, thread)
                       in self.registered.items() if (m, v) == (modifiers, vk)]
            for hotkey_id, thread in targets:
                self._queues[thread].put((WM_HOTKEY, hotkey_id))
