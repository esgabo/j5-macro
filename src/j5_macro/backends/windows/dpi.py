"""Per-monitor DPI awareness for the engine process on Windows.

A process that does not declare DPI awareness gets virtualized coordinates on a scaled
display: Windows reports a 150 % 2560-pixel screen as 1707 pixels wide, scales captures
and moves the pointer to rounded positions. The engine declares per-monitor awareness
(v2) before it touches a window, the pointer or the screen, so every coordinate it sees
and sends, and every capture, is in physical pixels.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

from j5_macro.backends.windows.win32 import last_error

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
PROCESS_PER_MONITOR_DPI_AWARE = 2
ERROR_ACCESS_DENIED = 5
E_ACCESSDENIED = -2147024891  # HRESULT 0x80070005

_declared: str | None = None


def declare_dpi_awareness() -> str:
    """Declare the best DPI awareness the system offers and say which one is in effect.

    Windows 10 1703 and later get per-monitor v2, Windows 8.1 per-monitor (v1), older
    systems system awareness. Awareness can only be set once per process: when it is
    already set (by a manifest or an earlier call), that setting stays. Does nothing
    outside Windows. Never raises."""
    global _declared
    if _declared is not None:
        return _declared
    _declared = _declare() if sys.platform == "win32" else "not Windows"
    return _declared


def _declare() -> str:
    win_dll: Any = getattr(ctypes, "WinDLL", None)
    if win_dll is None:  # pragma: no cover
        return "unavailable"
    try:
        user32 = win_dll("user32", use_last_error=True)
    except OSError:  # pragma: no cover
        return "unavailable"
    setter = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if setter is not None:
        setter.argtypes = [ctypes.c_ssize_t]
        setter.restype = ctypes.c_int32
        if setter(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return "per-monitor v2"
        if last_error() == ERROR_ACCESS_DENIED:
            return "already set"
    try:
        shcore = win_dll("shcore")
        result = int(shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE))
    except (OSError, AttributeError):
        result = None
    if result == 0:
        return "per-monitor"
    if result == E_ACCESSDENIED:
        return "already set"
    legacy = getattr(user32, "SetProcessDPIAware", None)
    if legacy is not None and legacy():
        return "system"
    return "unavailable"
