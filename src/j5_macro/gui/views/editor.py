"""The editor area (center): the step tree, the selected step's form and the YAML.

Everything shown comes from the :class:`~j5_macro.gui.controllers.ScriptDocument`:
``state_changed`` refreshes the tree, the form and the YAML, whoever changed the file.

Extension point (editor): editing (palette, drag-and-drop, forms, the header form,
"save as a single file") is built inside this panel and its parts (step_tree.py,
step_form.py), sending every change through ``ctx.document.edit``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSplitter, QVBoxLayout, QWidget

from j5_macro.gui.context import AppContext
from j5_macro.gui.models.script import ScriptState, format_problem
from j5_macro.gui.models.steps import find_step
from j5_macro.gui.views.step_form import StepFormPanel
from j5_macro.gui.views.step_tree import StepTreeView
from j5_macro.gui.views.yaml_view import YamlView

MAX_PROBLEMS = 8


class EditorPanel(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.title = QLabel()
        self.title.setObjectName("editorTitle")
        self.problems = QLabel()
        self.problems.setObjectName("editorProblems")
        self.problems.setWordWrap(True)
        self.problems.setTextFormat(Qt.TextFormat.PlainText)
        self.problems.setStyleSheet("color: #b00020;")
        self.problems.hide()
        self.tree = StepTreeView()
        self.form = StepFormPanel()
        self.yaml_view = YamlView()

        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(self.tree)
        left.addWidget(self.form)
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 1)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.yaml_view)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.title)
        layout.addWidget(self.problems)
        layout.addWidget(self.splitter, 1)

        self._state: ScriptState | None = None
        ctx.document.state_changed.connect(self.show_script)
        ctx.document.load_failed.connect(self._load_failed)
        ctx.run.current_step_changed.connect(self.tree.set_current_step)
        ctx.run.breakpoints_changed.connect(self.tree.set_breakpoints)
        self.tree.breakpoint_toggled.connect(ctx.run.toggle_breakpoint)
        self.tree.step_selected.connect(self._step_selected)
        self.tree.set_breakpoints(ctx.run.breakpoints)
        self.show_script(ctx.document.state)

    @property
    def state(self) -> ScriptState | None:
        return self._state

    def show_script(self, state: ScriptState | None) -> None:
        self._state = state
        if state is None:
            self.title.setText("<i>No script open</i>")
            self.tree.set_steps([])
            self.form.show_step(None)
            self.yaml_view.set_source("")
            self._show_problems([])
            return
        validity = "" if state.valid else " <span style='color:#b00020'>(invalid)</span>"
        where = f" <span style='color:gray'>{_escape(state.path)}</span>" if state.path else ""
        self.title.setText(f"<b>{_escape(state.title)}</b>{validity}{where}")
        self.tree.set_steps(state.steps)
        self.yaml_view.set_source(state.source)
        self._show_problems(state.errors)
        self._step_selected(self.tree.selected_step_id())

    def _load_failed(self, message: str, errors: Sequence[Mapping[str, Any]]) -> None:
        path = self._ctx.document.path or ""
        self.title.setText(f"<b>Cannot open</b> <span style='color:gray'>{_escape(path)}"
                           "</span>")
        self._show_problems(errors or [{"message": message}])

    def _show_problems(self, problems: Sequence[Mapping[str, Any]]) -> None:
        lines = [format_problem(problem) for problem in problems[:MAX_PROBLEMS]]
        if len(problems) > MAX_PROBLEMS:
            lines.append(f"... and {len(problems) - MAX_PROBLEMS} more")
        self.problems.setText("\n".join(lines))
        self.problems.setVisible(bool(lines))

    def _step_selected(self, step_id: str | None) -> None:
        step = find_step(self._state.steps, step_id) if self._state and step_id else None
        self.form.show_step(step)
        self.yaml_view.highlight_line(step.line if step is not None else None)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
