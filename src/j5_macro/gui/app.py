"""Starting the GUI: the Qt application, the engine and the main window."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from j5_macro.gui.context import AppContext
from j5_macro.gui.views.main_window import MainWindow

APP_NAME = "j5"
SETTINGS_ORG = "j5-macro"
"""Preferences (such as confirming runs that send real input) are saved under this."""


def run_gui(target: str | None = None, engine_cmd: Sequence[str] | None = None) -> int:
    """Show the main window on ``target`` (a folder of scripts or a script; the current
    directory by default) and run the event loop; returns the exit code."""
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    ctx = AppContext.create(engine_cmd)
    ctx.run.use_settings(QSettings(SETTINGS_ORG, APP_NAME))
    window = MainWindow(ctx)
    window.show()
    ctx.bridge.start()
    window.open_target(target or os.getcwd())
    try:
        return int(app.exec())
    finally:
        ctx.bridge.shutdown()
