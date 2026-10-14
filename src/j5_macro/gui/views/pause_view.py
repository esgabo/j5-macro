"""The pause box above the run log: why the run is paused, and what to do next.

Shown while the run is paused (:attr:`RunController.paused`). For a breakpoint, a step
or a ``pause``, it names the step the run is paused before; the debug toolbar goes on.
For a failed step (``on_error: pause``) it shows the error and, when the image search
saved one, the failure capture; for user interference, where the pointer was. Both
offer **Retry** (``resume``), **Skip** (``step_over``) and **Stop**, as the contract
defines (docs/contract.md, "Run states and debugging semantics").
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from j5_macro.gui.context import AppContext
from j5_macro.gui.models.debugger import PauseInfo

CAPTURE_SIZE = (360, 160)
"""The failure capture is scaled to fit this (width, height)."""
PAUSE_STYLE = ("QFrame#pauseBox { background: #fff6d5; border: 1px solid #e0c060; "
               "border-radius: 4px; }")
ERROR_PAUSE_STYLE = ("QFrame#pauseBox { background: #fde7ea; border: 1px solid #d08090; "
                     "border-radius: 4px; }")


class PauseView(QFrame):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.setObjectName("pauseBox")
        self.pause: PauseInfo | None = None
        self.title = QLabel()
        self.title.setObjectName("pauseTitle")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.detail = QLabel()
        self.detail.setObjectName("pauseDetail")
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #555;")
        self.capture = QLabel()
        self.capture.setObjectName("failureCapture")
        self.capture.hide()
        self.capture_path: Path | None = None
        self.retry_button = QPushButton("Retry")
        self.retry_button.setToolTip("Run the step again (resume)")
        self.skip_button = QPushButton("Skip")
        self.skip_button.setToolTip("Go on after the step without running it (step over)")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip("End the run")

        text = QVBoxLayout()
        text.addWidget(self.title)
        text.addWidget(self.detail)
        text.addWidget(self.hint)
        buttons = QHBoxLayout()
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self.skip_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        text.addLayout(buttons)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addLayout(text, 1)
        layout.addWidget(self.capture, 0, Qt.AlignmentFlag.AlignTop)

        self.retry_button.clicked.connect(ctx.run.retry)
        self.skip_button.clicked.connect(lambda: ctx.run.skip())
        self.stop_button.clicked.connect(ctx.run.stop)
        ctx.run.paused.connect(self.show_pause)
        ctx.run.actions_changed.connect(lambda actions: self._enable(actions.resume))
        self.show_pause(ctx.run.pause)

    def show_pause(self, pause: PauseInfo | None) -> None:
        self.pause = pause
        if pause is None:
            self.hide()
            self.capture.clear()
            self.capture.hide()
            self.capture_path = None
            return
        self.setStyleSheet(ERROR_PAUSE_STYLE if pause.recoverable else PAUSE_STYLE)
        self.title.setText(pause.title)
        detail = pause.detail
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        hint = pause.hint or _debug_hint()
        self.hint.setText(hint)
        for button in (self.retry_button, self.skip_button, self.stop_button):
            button.setVisible(pause.recoverable)
        self._show_capture(pause.screenshot)
        self._enable(self._ctx.run.actions.resume)
        self.show()

    def _show_capture(self, relative: str | None) -> None:
        path = self._ctx.run.capture_path(relative) if relative else None
        pixmap = QPixmap(str(path)) if path is not None and path.is_file() else QPixmap()
        self.capture_path = path if not pixmap.isNull() else None
        if pixmap.isNull():
            self.capture.clear()
            self.capture.hide()
            return
        width, height = CAPTURE_SIZE
        self.capture.setPixmap(pixmap.scaled(width, height,
                                             Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))
        self.capture.setToolTip(f"The screen when the search failed: {path}")
        self.capture.show()

    def _enable(self, enabled: bool) -> None:
        self.retry_button.setEnabled(enabled)
        self.skip_button.setEnabled(enabled)


def _debug_hint() -> str:
    return ("Continue (F5), Step over (F10), Step into (F11), Step out (Shift+F11), "
            "Stop (Shift+F5). Variables are on the right.")
