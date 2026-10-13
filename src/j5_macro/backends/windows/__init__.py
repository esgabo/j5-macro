"""The Windows backend (SendInput through ctypes): ``pip install j5-macro[windows]``.

The backend classes are imported lazily so the pure helpers (``keys``, ``win32``'s
structures) load on any platform.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from j5_macro.backends.windows.backend import WindowsBackend
    from j5_macro.backends.windows.hotkey import HotkeyConflict

__all__ = ["HotkeyConflict", "WindowsBackend"]


def __getattr__(name: str) -> Any:
    if name == "WindowsBackend":
        from j5_macro.backends.windows.backend import WindowsBackend

        return WindowsBackend
    if name == "HotkeyConflict":
        from j5_macro.backends.windows.hotkey import HotkeyConflict

        return HotkeyConflict
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
