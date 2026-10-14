"""The desktop GUI (``j5 gui``): a PySide6 client of the engine.

The GUI is optional and needs the ``gui`` extra (``pip install "j5-macro[gui]"``). This
module imports no Qt, so the CLI can check for PySide6 before importing the rest. The
architecture (the engine bridge, the threading rules and which module each area of the
window lives in) is described in README.md next to this file; the user guide is
docs/gui.md.
"""

from __future__ import annotations

import importlib.util

INSTALL_HINT = 'pip install "j5-macro[gui]"'
"""How to get PySide6, for messages."""


def qt_missing() -> str | None:
    """Why the GUI cannot start (PySide6 is not installed), or None when it can."""
    try:
        found = importlib.util.find_spec("PySide6") is not None
    except (ImportError, ValueError):
        found = False
    if found:
        return None
    return f"the GUI needs PySide6, which is not installed: {INSTALL_HINT}"
