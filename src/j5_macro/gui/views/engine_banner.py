"""A banner over the window when the engine could not start or died, with a restart
button. Not a modal dialog, so the window (and the log of what happened) stays usable."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from j5_macro.gui.bridge import EngineBridge


class EngineBanner(QFrame):
    def __init__(self, bridge: EngineBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("engineBanner")
        self.setStyleSheet("#engineBanner { background: #fde7e9; border: 1px solid #b00020; "
                           "border-radius: 4px; }")
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.restart_button = QPushButton("Restart engine")
        layout = QHBoxLayout(self)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.restart_button)
        self.restart_button.clicked.connect(bridge.restart)
        bridge.engine_died.connect(self.show_error)
        bridge.engine_failed.connect(
            lambda message: self.show_error(f"The engine could not start: {message}"))
        bridge.engine_started.connect(lambda _: self.hide())
        self.hide()

    def show_error(self, message: str) -> None:
        self.label.setText(message)
        self.show()
