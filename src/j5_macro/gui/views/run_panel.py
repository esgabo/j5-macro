"""The run panel (bottom): Run, Dry run (optionally strict) and Stop, the run state, the
emergency stop key, and the live log with one line per event of the run.

Extension point (run and debugging): the params prompt (secret params without echo),
the debugging controls (continue, step over/into/out, pause), the variables view and
the on-error and interference pauses with retry/skip are added to this panel, driven by
:class:`~j5_macro.gui.controllers.RunController`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.context import AppContext
from j5_macro.gui.models.engine_info import EngineInfo
from j5_macro.gui.models.run_log import LogLine
from j5_macro.gui.models.script import ScriptState

LEVEL_COLORS = {
    "debug": QColor(128, 128, 128),
    "warning": QColor(160, 96, 0),
    "error": QColor(176, 0, 32),
}
MAX_LINES = 20000
"""Older lines are dropped beyond this, so a long run never slows the window down."""


class RunPanel(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.run_button = QPushButton("Run")
        self.dry_run_button = QPushButton("Dry run")
        self.strict_box = QCheckBox("Strict")
        self.strict_box.setToolTip("Dry run: fail where the real run would fail (an image "
                                   "not found, a missing window...)")
        self.stop_button = QPushButton("Stop")
        self.state_label = QLabel("Idle")
        self.stop_key_label = QLabel()
        self.log_view = QListWidget()
        self.log_view.setUniformItemSizes(True)
        self.log_view.setObjectName("runLog")

        bar = QHBoxLayout()
        for widget in (self.run_button, self.dry_run_button, self.strict_box,
                       self.stop_button):
            bar.addWidget(widget)
        bar.addSpacing(12)
        bar.addWidget(self.state_label)
        bar.addStretch(1)
        bar.addWidget(self.stop_key_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(self.log_view, 1)

        self.run_button.clicked.connect(lambda: self.start("normal"))
        self.dry_run_button.clicked.connect(lambda: self.start("dry"))
        self.stop_button.clicked.connect(ctx.run.stop)
        ctx.run.active_changed.connect(self._active_changed)
        ctx.run.event_logged.connect(self.append_line)
        ctx.run.state_changed.connect(self._state_changed)
        ctx.run.run_finished.connect(self._finished)
        ctx.run.error.connect(lambda message: self.append_text(message, "error"))
        ctx.document.state_changed.connect(lambda _: self._update_buttons())
        ctx.bridge.running_changed.connect(lambda _: self._update_buttons())
        ctx.bridge.engine_started.connect(self._engine_started)
        self._engine_started(ctx.bridge.info)
        self._update_buttons()

    def start(self, mode: str) -> None:
        strict = mode == "dry" and self.strict_box.isChecked()
        self._ctx.run.start(mode, strict=True if strict else None)

    # -- log -----------------------------------------------------------------------------

    def append_line(self, line: LogLine) -> None:
        self.append_text(line.text, line.level)

    def append_text(self, text: str, level: str = "info") -> None:
        item = QListWidgetItem(text)
        color = LEVEL_COLORS.get(level)
        if color is not None:
            item.setForeground(QBrush(color))
        self.log_view.addItem(item)
        while self.log_view.count() > MAX_LINES:
            self.log_view.takeItem(0)
        self.log_view.scrollToBottom()

    def log_lines(self) -> list[str]:
        """The text of every line, for tests and copying."""
        return [self.log_view.item(i).text() for i in range(self.log_view.count())]

    # -- state ---------------------------------------------------------------------------

    def _active_changed(self, active: bool) -> None:
        if active:
            self.log_view.clear()
            self.state_label.setText("Starting…")
        self._update_buttons()

    def _state_changed(self, state: str) -> None:
        reason = self._ctx.run.log.pause_reason
        self.state_label.setText(state.capitalize() + (f" ({reason})" if reason else ""))

    def _finished(self, payload: Mapping[str, Any]) -> None:
        self.state_label.setText(f"Finished: {payload.get('status')}")

    def _engine_started(self, info: EngineInfo | None) -> None:
        if info is None:
            self.stop_key_label.setText("")
        elif info.stop_key:
            self.stop_key_label.setText(f"Emergency stop: <b>{info.stop_key}</b>")
        else:
            self.stop_key_label.setText("<span style='color:#a06000'>No emergency stop "
                                        "key</span>")

    def _update_buttons(self) -> None:
        state: ScriptState | None = self._ctx.document.state
        can_run = (self._ctx.bridge.running and state is not None
                   and not self._ctx.run.active)
        self.run_button.setEnabled(can_run)
        self.dry_run_button.setEnabled(can_run)
        self.stop_button.setEnabled(self._ctx.run.active)
