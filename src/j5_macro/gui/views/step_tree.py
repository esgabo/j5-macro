"""The step tree: every step with its id and type, ``if`` and ``loop`` blocks nested.

Read-only for now. Column 0 is the breakpoint gutter: clicking it emits
``breakpoint_toggled``; the markers come from :meth:`set_breakpoints` (the run
controller owns the set). The step being run is highlighted with
:meth:`set_current_step`.

Extension point (editor): drag-and-drop reordering, the step palette and context menus
(insert, delete) go here, each change sent through ``ScriptDocument.edit``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem, QWidget

from j5_macro.gui.models.steps import StepItem, step_summary

STEP_ID_ROLE = Qt.ItemDataRole.UserRole
"""Item data with the step id (None on block rows such as ``then``)."""
GUTTER, ID_COLUMN, TYPE_COLUMN, DETAILS_COLUMN = range(4)
BREAKPOINT_MARK = "●"
CURRENT_COLOR = QColor(255, 236, 140)
PAUSED_COLOR = QColor(255, 196, 120)
BREAKPOINT_COLOR = QColor(200, 30, 30)


class StepTreeView(QTreeWidget):
    step_selected = Signal(object)
    """The selected step id, or None."""
    breakpoint_toggled = Signal(str)
    """The user clicked a step's breakpoint gutter."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels(["", "Step", "Type", "Details"])
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        header = self.header()
        header.setSectionResizeMode(GUTTER, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(GUTTER, 24)
        header.setSectionResizeMode(ID_COLUMN, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(TYPE_COLUMN, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._script_breaks: set[str] = set()
        self._breakpoints: frozenset[str] = frozenset()
        self._current: str | None = None
        self._paused = False
        self.itemSelectionChanged.connect(self._selection_changed)
        self.itemClicked.connect(self._clicked)

    # -- content -------------------------------------------------------------------------

    def set_steps(self, steps: Sequence[StepItem]) -> None:
        """Show these steps, keeping the selection and the expanded state by id."""
        selected = self.selected_step_id()
        self.clear()
        self._items.clear()
        self._script_breaks.clear()
        for step in steps:
            self.addTopLevelItem(self._item(step))
        self.expandAll()
        self._paint_markers()
        if self._current is not None:
            self._paint_current(self._current, True)
        if selected is not None and selected in self._items:
            self.setCurrentItem(self._items[selected])

    def step_ids(self) -> list[str]:
        """Every step id shown, depth first."""
        return list(self._items)

    def item_for(self, step_id: str) -> QTreeWidgetItem | None:
        return self._items.get(step_id)

    def selected_step_id(self) -> str | None:
        items = self.selectedItems()
        if not items:
            return None
        step_id = items[0].data(0, STEP_ID_ROLE)
        return step_id if isinstance(step_id, str) else None

    def select_step(self, step_id: str) -> None:
        item = self._items.get(step_id)
        if item is not None:
            self.setCurrentItem(item)

    # -- run state -----------------------------------------------------------------------

    def set_breakpoints(self, step_ids: Iterable[str]) -> None:
        self._breakpoints = frozenset(step_ids)
        self._paint_markers()

    def set_current_step(self, step_id: str | None) -> None:
        """Highlight the step being run (None: no highlight)."""
        if self._current is not None:
            self._paint_current(self._current, False)
        self._current = step_id
        if step_id is not None:
            self._paint_current(step_id, True)
            item = self._items.get(step_id)
            if item is not None:
                self.scrollToItem(item)

    def current_step(self) -> str | None:
        return self._current

    def set_paused(self, paused: bool) -> None:
        """Show the current step as paused at (a stronger highlight) or running."""
        if paused != self._paused:
            self._paused = paused
            if self._current is not None:
                self._paint_current(self._current, True)

    def is_paused(self) -> bool:
        return self._paused

    # -- internals -----------------------------------------------------------------------

    def _item(self, step: StepItem) -> QTreeWidgetItem:
        item = QTreeWidgetItem(["", step.id, step.type, step_summary(step)])
        item.setData(0, STEP_ID_ROLE, step.id)
        if step.line is not None:
            item.setToolTip(ID_COLUMN, f"line {step.line}")
        self._items[step.id] = item
        if step.breakpoint:
            self._script_breaks.add(step.id)
        single = len(step.blocks) == 1
        for block, children in step.blocks.items():
            parent = item
            if not single:
                parent = QTreeWidgetItem(["", block])
                parent.setData(0, STEP_ID_ROLE, None)
                font = parent.font(ID_COLUMN)
                font.setItalic(True)
                parent.setFont(ID_COLUMN, font)
                parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.addChild(parent)
            for child in children:
                parent.addChild(self._item(child))
        return item

    def _paint_markers(self) -> None:
        for step_id, item in self._items.items():
            marked = step_id in self._breakpoints or step_id in self._script_breaks
            item.setText(GUTTER, BREAKPOINT_MARK if marked else "")
            item.setForeground(GUTTER, QBrush(BREAKPOINT_COLOR))
            tip = "break: true in the script" if step_id in self._script_breaks else \
                "breakpoint (click to remove)" if marked else "click to add a breakpoint"
            item.setToolTip(GUTTER, tip)

    def _paint_current(self, step_id: str, on: bool) -> None:
        item = self._items.get(step_id)
        if item is None:
            return
        color = PAUSED_COLOR if self._paused else CURRENT_COLOR
        brush = QBrush(color) if on else QBrush()
        for column in range(self.columnCount()):
            item.setBackground(column, brush)
            font = QFont(item.font(column))
            font.setBold(on)
            item.setFont(column, font)

    def _selection_changed(self) -> None:
        self.step_selected.emit(self.selected_step_id())

    def _clicked(self, item: QTreeWidgetItem, column: int) -> None:
        step_id = item.data(0, STEP_ID_ROLE)
        if column == GUTTER and isinstance(step_id, str):
            self.breakpoint_toggled.emit(step_id)
