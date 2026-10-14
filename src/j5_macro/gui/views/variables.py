"""The variables of the paused run, editing one, and evaluating expressions.

Fed by :class:`~j5_macro.gui.controllers.RunController`: ``variables_changed`` brings
``get_state``'s variables at every pause (secrets masked as ``***``). Lists and maps
expand. Selecting a variable puts its value in the editor; **Set** sends
``set_variable`` with the text as a value (JSON when it parses, else text) or, with
**Expression**, as an expression the engine evaluates. A value that holds a masked
secret cannot be edited. **Evaluate** sends ``evaluate``. Everything is disabled while
the run is not paused, since the engine only answers then.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.context import AppContext
from j5_macro.gui.models.debugger import DebugActions
from j5_macro.gui.models.variables import VariableRow, editable_text, format_value, variable_rows

MASKED_COLOR = QColor(128, 128, 128)
ERROR_STYLE = "color: #b00020;"


class VariablesPanel(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._rows: dict[str, VariableRow] = {}
        self._inspect = False

        title = QLabel("<b>Variables</b>")
        self.tree = QTreeWidget()
        self.tree.setObjectName("variables")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Name", "Value"])
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.placeholder = QLabel("Variables show while the run is paused.")
        self.placeholder.setStyleSheet("color: gray;")
        self.placeholder.setWordWrap(True)

        self.name_label = QLabel("Set")
        self.value_edit = QLineEdit()
        self.value_edit.setObjectName("variableValue")
        self.value_edit.setPlaceholderText("new value (JSON or text)")
        self.expression_box = QCheckBox("Expression")
        self.expression_box.setToolTip("Evaluate the text over the variables and assign "
                                       "the result")
        self.set_button = QPushButton("Set")
        self.evaluate_edit = QLineEdit()
        self.evaluate_edit.setObjectName("evaluateExpression")
        self.evaluate_edit.setPlaceholderText("expression, e.g. attempts + 1")
        self.evaluate_button = QPushButton("Evaluate")
        self.result_label = QLabel()
        self.result_label.setObjectName("evaluateResult")
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.TextFormat.PlainText)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        set_row = QHBoxLayout()
        set_row.addWidget(self.name_label)
        set_row.addWidget(self.value_edit, 1)
        set_row.addWidget(self.expression_box)
        set_row.addWidget(self.set_button)
        evaluate_row = QHBoxLayout()
        evaluate_row.addWidget(self.evaluate_edit, 1)
        evaluate_row.addWidget(self.evaluate_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(title)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.placeholder)
        layout.addLayout(set_row)
        layout.addLayout(evaluate_row)
        layout.addWidget(self.result_label)

        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self.value_edit.setFocus())
        self.set_button.clicked.connect(self.apply_edit)
        self.value_edit.returnPressed.connect(self.apply_edit)
        self.evaluate_button.clicked.connect(self.evaluate)
        self.evaluate_edit.returnPressed.connect(self.evaluate)
        ctx.run.variables_changed.connect(self.show_variables)
        ctx.run.run_started.connect(lambda _: self.reset())
        ctx.run.actions_changed.connect(self._actions_changed)
        self.show_variables(ctx.run.variables)
        self._actions_changed(ctx.run.actions)

    # -- content -------------------------------------------------------------------------

    def show_variables(self, variables: Mapping[str, Any] | None) -> None:
        selected = self.selected_name()
        self.tree.clear()
        self._rows.clear()
        rows = variable_rows(variables)
        for row in rows:
            item = self._item(row)
            self.tree.addTopLevelItem(item)
            self._rows[row.name] = row
            if selected == row.name:
                item.setSelected(True)
                self.tree.setCurrentItem(item)
        self.placeholder.setVisible(variables is None)
        self._selection_changed()

    def reset(self) -> None:
        """A new run: forget the last expression and its result."""
        self.evaluate_edit.clear()
        self.value_edit.clear()
        self._show_result("")

    def values(self) -> dict[str, str]:
        """Each variable's value as shown, by name (for tests)."""
        return {row.name: row.text for row in self._rows.values()}

    def selected_name(self) -> str | None:
        items = self.tree.selectedItems()
        if not items:
            return None
        item = items[0]
        parent = item.parent()
        while parent is not None:
            item, parent = parent, parent.parent()
        name = item.text(0)
        return name if name in self._rows else None

    def select(self, name: str) -> None:
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item is not None and item.text(0) == name:
                self.tree.setCurrentItem(item)
                return

    # -- actions -------------------------------------------------------------------------

    def apply_edit(self) -> None:
        name = self.selected_name()
        row = self._rows.get(name or "")
        if name is None or row is None:
            self._show_result("choose a variable first", error=True)
            return
        if row.masked:
            self._show_result("a secret cannot be edited here", error=True)
            return
        self.set_button.setEnabled(False)
        self._ctx.run.set_variable(name, self.value_edit.text(),
                                   expression=self.expression_box.isChecked(),
                                   on_done=self._set_done)

    def evaluate(self) -> None:
        expression = self.evaluate_edit.text()
        self.evaluate_button.setEnabled(False)

        def shown(value: Any) -> None:
            self.evaluate_button.setEnabled(self._inspect)
            self._show_result(f"{expression} = {format_value(value, 2000)}")

        def failed(message: str) -> None:
            self.evaluate_button.setEnabled(self._inspect)
            self._show_result(message, error=True)

        self._ctx.run.evaluate(expression, shown, failed)

    # -- internals -----------------------------------------------------------------------

    def _item(self, row: VariableRow) -> QTreeWidgetItem:
        item = QTreeWidgetItem([row.name, row.text])
        item.setToolTip(1, "holds a secret (masked)" if row.masked else row.text[:2000])
        if row.masked:
            item.setForeground(1, QBrush(MASKED_COLOR))
            font = QFont(item.font(1))
            font.setItalic(True)
            item.setFont(1, font)
        for child in row.children:
            item.addChild(self._item(child))
        return item

    def _selection_changed(self) -> None:
        row = self._rows.get(self.selected_name() or "")
        text = editable_text(row) if row is not None else None
        editable = self._inspect and row is not None and not row.masked
        self.name_label.setText(f"Set <b>{row.name}</b>" if row is not None else "Set")
        self.value_edit.setText(text or "")
        self.value_edit.setEnabled(editable)
        self.expression_box.setEnabled(editable)
        self.set_button.setEnabled(editable)
        if row is not None and row.masked:
            self.value_edit.setPlaceholderText("a secret: it cannot be edited")
        else:
            self.value_edit.setPlaceholderText("new value (JSON or text)")

    def _set_done(self, message: str | None) -> None:
        self._selection_changed()
        if message is not None:
            self._show_result(message, error=True)
        else:
            self._show_result("")

    def _show_result(self, text: str, *, error: bool = False) -> None:
        self.result_label.setStyleSheet(ERROR_STYLE if error else "")
        self.result_label.setText(text)

    def _actions_changed(self, actions: DebugActions) -> None:
        self._inspect = actions.inspect
        self.evaluate_edit.setEnabled(actions.inspect)
        self.evaluate_button.setEnabled(actions.inspect)
        self.tree.setEnabled(actions.inspect or self.tree.topLevelItemCount() > 0)
        self._selection_changed()
