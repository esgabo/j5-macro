"""The X11 backend (python-xlib and XTest): ``pip install j5-macro[x11]``.

The backend classes are imported lazily so the pure helpers (``keys``) load without
python-xlib.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from j5_macro.backends.x11.backend import X11Backend
    from j5_macro.backends.x11.hotkey import HotkeyConflict

__all__ = ["HotkeyConflict", "X11Backend"]


def __getattr__(name: str) -> Any:
    if name == "X11Backend":
        from j5_macro.backends.x11.backend import X11Backend

        return X11Backend
    if name == "HotkeyConflict":
        from j5_macro.backends.x11.hotkey import HotkeyConflict

        return HotkeyConflict
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
