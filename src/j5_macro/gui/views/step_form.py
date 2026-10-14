"""The form of the selected step: for now its parameters and common fields, read-only.

Extension point (editor): replace :class:`StepFormPanel`'s body with forms generated
from ``list_step_types`` (parameters, types, defaults, documentation), whose changes go
through ``ScriptDocument.edit`` as ``update`` edits; the script header (name, params,
vars, input profile) gets a form too (``set_meta``). Image and coordinate fields use
:class:`~j5_macro.gui.screen_tools.ScreenTools` for "Capture", "Pick" and "Test".
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from j5_macro.gui.models.steps import StepItem


class StepFormPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = QLabel()
        self._title.setWordWrap(True)
        self._body = QWidget()
        self._form = QFormLayout(self._body)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._body)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._title)
        layout.addWidget(scroll, 1)
        self._step: StepItem | None = None
        self.show_step(None)

    @property
    def step(self) -> StepItem | None:
        return self._step

    def show_step(self, step: StepItem | None) -> None:
        self._step = step
        while self._form.rowCount():
            self._form.removeRow(0)
        if step is None:
            self._title.setText("Select a step to see its parameters.")
            return
        line = f", line {step.line}" if step.line is not None else ""
        self._title.setText(f"<b>{step.id}</b> ({step.type}{line})")
        for name, value in step.params.items():
            self._form.addRow(f"{name}:", _value_label(value))
        for name, value in step.common.items():
            self._form.addRow(f"{name}:", _value_label(value))
        if step.breakpoint:
            self._form.addRow("break:", _value_label(True))


def _value_label(value: Any) -> QLabel:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label
