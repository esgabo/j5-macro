"""The Win32 calls the Windows backend makes, behind a small interface.

:class:`Win32Api` is everything the backend needs from the system; :class:`User32` is
its ctypes implementation, which only loads on Windows. The structures and the encoding
of input events are plain ctypes with fixed-size fields (``DWORD`` is ``c_uint32``, not
``c_ulong``, which is 8 bytes on Linux), so they have their Windows layout on any
platform and are tested everywhere; tests drive the backend with a fake ``Win32Api``.
"""

from __future__ import annotations

import ctypes
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from j5_macro.backends.base import Rect

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
WHEEL_DELTA = 120

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

WM_QUIT = 0x0012
WM_HOTKEY = 0x0312

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

DWMWA_CLOAKED = 14

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32),
                ("mouseData", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_uint16),
                ("wParamH", ctypes.c_uint16)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("u", _INPUTUNION)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int32), ("top", ctypes.c_int32),
                ("right", ctypes.c_int32), ("bottom", ctypes.c_int32)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint32),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint32), ("pt", POINT), ("lPrivate", ctypes.c_uint32)]


@dataclass(frozen=True, slots=True)
class MouseEvent:
    """One ``MOUSEINPUT``: ``data`` is signed (a wheel notch down is ``-120``)."""

    flags: int
    dx: int = 0
    dy: int = 0
    data: int = 0


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """One ``KEYBDINPUT``: a virtual key with its scan code, or a UTF-16 code unit in
    ``scan`` with ``KEYEVENTF_UNICODE`` (and ``vk`` 0)."""

    vk: int
    scan: int
    flags: int


InputEvent = MouseEvent | KeyEvent


def encode_inputs(events: Sequence[InputEvent]) -> ctypes.Array[INPUT]:
    """The ``INPUT`` array ``SendInput`` takes."""
    array = (INPUT * len(events))()
    for record, event in zip(array, events, strict=True):
        if isinstance(event, MouseEvent):
            record.type = INPUT_MOUSE
            record.u.mi = MOUSEINPUT(event.dx, event.dy, event.data & 0xFFFFFFFF,
                                     event.flags, 0, 0)
        else:
            record.type = INPUT_KEYBOARD
            record.u.ki = KEYBDINPUT(event.vk, event.scan, event.flags, 0, 0)
    return array


def normalize(value: int, origin: int, size: int) -> int:
    """The absolute ``MOUSEINPUT`` coordinate (0 to 65535 across the virtual desktop)
    that lands exactly on pixel ``value``.

    Windows maps a normalized ``n`` to pixel ``origin + n * size // 65536``, so the
    smallest ``n`` reaching pixel offset ``p`` is ``ceil(p * 65536 / size)``. Pixels
    outside the desktop are clamped to its edge."""
    if size <= 0:
        return 0
    offset = min(max(value - origin, 0), size - 1)
    return min(0xFFFF, (offset * 0x10000 + size - 1) // size)


class Win32Api(Protocol):
    """The system calls of the Windows backend (see :class:`User32`)."""

    def send_input(self, events: Sequence[InputEvent]) -> None:
        """Send every event, or raise :class:`InputDenied` if Windows refuses any."""
        ...

    def cursor_pos(self) -> tuple[int, int] | None:
        """The cursor in physical pixels; None when Windows will not say (the secure
        desktop: a UAC prompt or the lock screen)."""
        ...

    def virtual_screen(self) -> Rect: ...

    def windows(self) -> list[int]:
        """Top-level window handles, topmost first."""
        ...

    def window_title(self, hwnd: int) -> str: ...

    def window_shown(self, hwnd: int) -> bool:
        """Visible, not minimized and not cloaked (a hidden UWP or other-desktop window)."""
        ...

    def client_rect(self, hwnd: int) -> Rect | None:
        """The client area in desktop coordinates; None if the window went away."""
        ...

    def vk_for_char(self, char: str) -> int | None:
        """The virtual key that types ``char`` in the current layout (any shift level)."""
        ...

    def scan_code(self, vk: int) -> int: ...

    def thread_id(self) -> int: ...

    def create_message_queue(self) -> None:
        """Make sure the calling thread has a message queue."""
        ...

    def register_hotkey(self, hotkey_id: int, modifiers: int, vk: int) -> bool: ...

    def unregister_hotkey(self, hotkey_id: int) -> None: ...

    def get_message(self) -> tuple[int, int] | None:
        """Wait for the calling thread's next message: ``(message, wParam)``, or None on
        ``WM_QUIT`` or an error."""
        ...

    def post_quit(self, thread_id: int) -> bool:
        """Post ``WM_QUIT`` to a thread's queue."""
        ...


class InputDenied(PermissionError):
    """Windows did not inject the input: the target window belongs to a process with
    higher integrity (an elevated program, UAC) or the secure desktop is active."""


class User32:
    """:class:`Win32Api` through ctypes. Only constructible on Windows."""

    def __init__(self) -> None:
        win_dll: Any = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise OSError("the Windows backend only runs on Windows")
        user32 = win_dll("user32", use_last_error=True)
        self._user32 = user32
        try:
            self._dwmapi: Any = win_dll("dwmapi")
        except OSError:  # pragma: no cover - dwmapi exists since Vista
            self._dwmapi = None
        self._kernel32 = win_dll("kernel32", use_last_error=True)
        winfunctype: Any = getattr(ctypes, "WINFUNCTYPE")  # noqa: B009 - Windows only
        # BOOL CALLBACK EnumWindowsProc(HWND, LPARAM)
        self._enum_proc: Any = winfunctype(ctypes.c_int32, ctypes.c_void_p, ctypes.c_ssize_t)
        _declare(user32, self._dwmapi, self._kernel32, self._enum_proc)

    def send_input(self, events: Sequence[InputEvent]) -> None:
        if not events:
            return
        array = encode_inputs(events)
        sent = self._user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
        if sent != len(events):
            error = last_error()
            raise InputDenied(
                f"Windows blocked the input ({sent} of {len(events)} events sent, error "
                f"{error}): the focused window may belong to a program running as "
                "administrator, or a UAC prompt or the lock screen is showing")

    def cursor_pos(self) -> tuple[int, int] | None:
        point = POINT()
        if not self._user32.GetCursorPos(ctypes.byref(point)):
            return None
        return int(point.x), int(point.y)

    def virtual_screen(self) -> Rect:
        metric = self._user32.GetSystemMetrics
        return Rect(int(metric(SM_XVIRTUALSCREEN)), int(metric(SM_YVIRTUALSCREEN)),
                    int(metric(SM_CXVIRTUALSCREEN)), int(metric(SM_CYVIRTUALSCREEN)))

    def windows(self) -> list[int]:
        found: list[int] = []

        def collect(hwnd: Any, _: Any) -> int:
            found.append(int(hwnd or 0))
            return 1

        self._user32.EnumWindows(self._enum_proc(collect), 0)
        return found

    def window_title(self, hwnd: int) -> str:
        length = int(self._user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def window_shown(self, hwnd: int) -> bool:
        if not self._user32.IsWindowVisible(hwnd) or self._user32.IsIconic(hwnd):
            return False
        if self._dwmapi is None:  # pragma: no cover
            return True
        cloaked = ctypes.c_uint32(0)
        result = self._dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        return result != 0 or cloaked.value == 0

    def client_rect(self, hwnd: int) -> Rect | None:
        rect = RECT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        origin = POINT(0, 0)
        if not self._user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        return Rect(int(origin.x), int(origin.y), int(rect.right - rect.left),
                    int(rect.bottom - rect.top))

    def vk_for_char(self, char: str) -> int | None:
        result = int(self._user32.VkKeyScanW(char))
        if result == -1 or result & 0xFF == 0xFF:
            return None
        return result & 0xFF

    def scan_code(self, vk: int) -> int:
        return int(self._user32.MapVirtualKeyW(vk, 0)) & 0xFFFF  # MAPVK_VK_TO_VSC

    def thread_id(self) -> int:
        return int(self._kernel32.GetCurrentThreadId())

    def create_message_queue(self) -> None:
        msg = MSG()
        # Any message function creates the queue; PM_NOREMOVE (0) leaves it untouched.
        self._user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

    def register_hotkey(self, hotkey_id: int, modifiers: int, vk: int) -> bool:
        return bool(self._user32.RegisterHotKey(None, hotkey_id, modifiers, vk))

    def unregister_hotkey(self, hotkey_id: int) -> None:
        self._user32.UnregisterHotKey(None, hotkey_id)

    def get_message(self) -> tuple[int, int] | None:
        msg = MSG()
        result = int(self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0))
        if result <= 0:  # 0: WM_QUIT, -1: error
            return None
        return int(msg.message), int(msg.wParam)

    def post_quit(self, thread_id: int) -> bool:
        return bool(self._user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0))


def last_error() -> int:
    """``GetLastError`` of the last call through a library loaded with
    ``use_last_error`` (``ctypes.get_last_error`` only exists on Windows)."""
    getter: Any = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None else 0


def _declare(user32: Any, dwmapi: Any, kernel32: Any, enum_proc: Any) -> None:
    """Argument and result types, so handles and pointers keep their full width."""
    c = ctypes
    handle = c.c_void_p
    p_point, p_rect, p_msg = c.POINTER(POINT), c.POINTER(RECT), c.POINTER(MSG)
    signatures: list[tuple[Any, str, list[Any], Any]] = [
        (user32, "SendInput", [c.c_uint32, c.POINTER(INPUT), c.c_int32], c.c_uint32),
        (user32, "GetCursorPos", [p_point], c.c_int32),
        (user32, "GetSystemMetrics", [c.c_int32], c.c_int32),
        (user32, "EnumWindows", [enum_proc, c.c_ssize_t], c.c_int32),
        (user32, "GetWindowTextLengthW", [handle], c.c_int32),
        (user32, "GetWindowTextW", [handle, c.c_wchar_p, c.c_int32], c.c_int32),
        (user32, "IsWindowVisible", [handle], c.c_int32),
        (user32, "IsIconic", [handle], c.c_int32),
        (user32, "GetClientRect", [handle, p_rect], c.c_int32),
        (user32, "ClientToScreen", [handle, p_point], c.c_int32),
        (user32, "VkKeyScanW", [c.c_wchar], c.c_int16),
        (user32, "MapVirtualKeyW", [c.c_uint32, c.c_uint32], c.c_uint32),
        (user32, "PeekMessageW", [p_msg, handle, c.c_uint32, c.c_uint32, c.c_uint32],
         c.c_int32),
        (user32, "RegisterHotKey", [handle, c.c_int32, c.c_uint32, c.c_uint32], c.c_int32),
        (user32, "UnregisterHotKey", [handle, c.c_int32], c.c_int32),
        (user32, "GetMessageW", [p_msg, handle, c.c_uint32, c.c_uint32], c.c_int32),
        (user32, "PostThreadMessageW", [c.c_uint32, c.c_uint32, c.c_size_t, c.c_ssize_t],
         c.c_int32),
        (kernel32, "GetCurrentThreadId", [], c.c_uint32),
    ]
    if dwmapi is not None:
        signatures.append((dwmapi, "DwmGetWindowAttribute",
                           [handle, c.c_uint32, c.c_void_p, c.c_uint32], c.c_int32))
    for library, name, argtypes, restype in signatures:
        function = getattr(library, name)
        function.argtypes = argtypes
        function.restype = restype
