"""The step tree: every step with its id and type, ``if`` and ``loop`` blocks nested.

Column 0 is the breakpoint gutter: clicking it emits ``breakpoint_toggled``; the markers
come from :meth:`set_breakpoints` (the run controller owns the set). The step being run
is highlighted with :meth:`set_current_step`. Steps with problems (the engine's
validation errors) are drawn in red, with the problems as tooltip.

Every block slot of a block step (``then``, ``else``, ``do``) is a row of its own, even
when empty, so steps can be dropped and inserted into it. The tree never changes
itself: dragging a step (or a step type from the palette) onto it emits
``step_dropped`` (or ``type_dropped``) with a :class:`~j5_macro.gui.models.edits.Target`,
the editor sends the ``move`` (or ``insert``), and the tree is rebuilt from the
reloaded script.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QDragEnterEvent, QDragMoveEvent, QDropEvent, QFont
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem, QWidget

from j5_macro.gui.models.edits import Target
from j5_macro.gui.models.registry import StepRegistry
from j5_macro.gui.models.steps import StepItem, step_summary

STEP_ID_ROLE = Qt.ItemDataRole.UserRole
"""Item data with the step id (None on block rows such as ``then``)."""
BLOCK_ROLE = Qt.ItemDataRole.UserRole + 1
"""Item data of a block row: ``(step id, block)``."""
GUTTER, ID_COLUMN, TYPE_COLUMN, DETAILS_COLUMN = range(4)
BREAKPOINT_MARK = "●"
PROBLEM_MARK = "⚠"
CURRENT_COLOR = QColor(255, 236, 140)
PAUSED_COLOR = QColor(255, 196, 120)
BREAKPOINT_COLOR = QColor(200, 30, 30)
PROBLEM_COLOR = QColor(176, 0, 32)
STEP_MIME = "application/x-j5-step-id"
"""Drag data of a step of the tree: its id (UTF-8)."""
TYPE_MIME = "application/x-j5-step-type"
"""Drag data of a step type from the palette: its name (UTF-8)."""
EDGE = 0.25
"""The top and bottom quarter of a row drop before and after it; the middle, into it."""


class StepTreeView(QTreeWidget):
    step_selected = Signal(object)
    """The selected step id, or None (also for a block row)."""
    breakpoint_toggled = Signal(str)
    """The user clicked a step's breakpoint gutter."""
    step_dropped = Signal(str, object)
    """``(step id, Target)``: a step was dragged there."""
    type_dropped = Signal(str, object)
    """``(step type, Target)``: a step type was dragged there from the palette."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels(["", "Step", "Type", "Details"])
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        header = self.header()
        header.setSectionResizeMode(GUTTER, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(GUTTER, 24)
        header.setSectionResizeMode(ID_COLUMN, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(TYPE_COLUMN, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._blocks: dict[tuple[str, str], QTreeWidgetItem] = {}
        self._script_breaks: set[str] = set()
        self._breakpoints: frozenset[str] = frozenset()
        self._problems: dict[str, list[str]] = {}
        self._current: str | None = None
        self._registry = StepRegistry()
        self._paused = False
        self.itemSelectionChanged.connect(self._selection_changed)
        self.itemClicked.connect(self._clicked)

    # -- content -------------------------------------------------------------------------

    def set_registry(self, registry: StepRegistry) -> None:
        """The step types, for the block slots of each block step."""
        self._registry = registry

    def set_steps(self, steps: Sequence[StepItem]) -> None:
        """Show these steps, keeping the selection by id."""
        selected = self.selected_step_id()
        block = self.selected_block()
        self.blockSignals(True)
        try:
            self.clear()
            self._items.clear()
            self._blocks.clear()
            self._script_breaks.clear()
            for step in steps:
                self.addTopLevelItem(self._item(step))
            self.expandAll()
            self._paint_markers()
            self._paint_problems()
            if self._current is not None:
                self._paint_current(self._current, True)
            if selected is not None and selected in self._items:
                self.setCurrentItem(self._items[selected])
            elif block is not None and block in self._blocks:
                self.setCurrentItem(self._blocks[block])
        finally:
            self.blockSignals(False)
        if self.selected_step_id() != selected:
            self.step_selected.emit(self.selected_step_id())

    def step_ids(self) -> list[str]:
        """Every step id shown, depth first."""
        return list(self._items)

    def item_for(self, step_id: str) -> QTreeWidgetItem | None:
        return self._items.get(step_id)

    def block_item(self, step_id: str, block: str) -> QTreeWidgetItem | None:
        return self._blocks.get((step_id, block))

    def selected_step_id(self) -> str | None:
        items = self.selectedItems()
        if not items:
            return None
        step_id = items[0].data(0, STEP_ID_ROLE)
        return step_id if isinstance(step_id, str) else None

    def selected_block(self) -> tuple[str, str] | None:
        """``(step id, block)`` when a block row is selected."""
        items = self.selectedItems()
        if not items:
            return None
        data = items[0].data(0, BLOCK_ROLE)
        if isinstance(data, list | tuple) and len(data) == 2:
            return str(data[0]), str(data[1])
        return None

    def select_step(self, step_id: str) -> None:
        item = self._items.get(step_id)
        if item is not None:
            self.setCurrentItem(item)

    def select_block(self, step_id: str, block: str) -> None:
        item = self._blocks.get((step_id, block))
        if item is not None:
            self.setCurrentItem(item)

    def set_problems(self, problems: Mapping[str, Sequence[str]]) -> None:
        """Mark the steps with problems: ``{step id: [messages]}``."""
        self._problems = {k: list(v) for k, v in problems.items()}
        self._paint_problems()

    def problem_steps(self) -> list[str]:
        """The ids of the steps marked with problems."""
        return [step_id for step_id in self._items if step_id in self._problems]

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

    # -- drag and drop -------------------------------------------------------------------

    def mimeTypes(self) -> list[str]:
        return [STEP_MIME, TYPE_MIME]

    def mimeData(self, items: Sequence[QTreeWidgetItem]) -> QMimeData:
        data = QMimeData()
        step_id = items[0].data(0, STEP_ID_ROLE) if items else None
        if isinstance(step_id, str):
            data.setData(STEP_MIME, step_id.encode("utf-8"))
        return data

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction | Qt.DropAction.CopyAction

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if _payload(event.mimeData()) is not None:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if _payload(event.mimeData()) is None:
            event.ignore()
            return
        super().dragMoveEvent(event)
        event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        payload = _payload(event.mimeData())
        if payload is None:
            event.ignore()
            return
        target = self.target_at(event.position().toPoint())
        kind, name = payload
        # The tree never moves its own items: the script is edited and reloaded.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        if kind == STEP_MIME:
            self.step_dropped.emit(name, target)
        else:
            self.type_dropped.emit(name, target)

    def target_at(self, point: QPoint) -> Target:
        """Where a drop at ``point`` (viewport coordinates) goes."""
        item = self.itemAt(point)
        if item is None:
            return Target()
        block = item.data(0, BLOCK_ROLE)
        if isinstance(block, list | tuple) and len(block) == 2:
            return Target(str(block[0]), "into", str(block[1]))
        step_id = item.data(0, STEP_ID_ROLE)
        if not isinstance(step_id, str):
            return Target()
        rect = self.visualItemRect(item)
        offset = (point.y() - rect.top()) / max(rect.height(), 1)
        has_blocks = bool(self._registry.blocks_of(item.text(TYPE_COLUMN)))
        if offset < EDGE:
            return Target(step_id, "before")
        if offset > 1 - EDGE or not has_blocks:
            return Target(step_id, "after")
        return Target(step_id, "into")

    # -- internals -----------------------------------------------------------------------

    def _item(self, step: StepItem) -> QTreeWidgetItem:
        step_type = self._registry.get(step.type)
        defaults = {p.name: p.default for p in step_type.params
                    if p.default is not None} if step_type is not None else None
        item = QTreeWidgetItem(["", step.id, step.type, step_summary(step, defaults=defaults)])
        item.setData(0, STEP_ID_ROLE, step.id)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                      | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
        if step.line is not None:
            item.setToolTip(ID_COLUMN, f"line {step.line}")
        self._items[step.id] = item
        if step.breakpoint:
            self._script_breaks.add(step.id)
        slots = list(self._registry.blocks_of(step.type))
        slots += [name for name in step.blocks if name not in slots]
        for block in slots:
            children = step.blocks.get(block, ())
            row = QTreeWidgetItem(["", block if children else f"{block} (empty)"])
            row.setData(0, STEP_ID_ROLE, None)
            row.setData(0, BLOCK_ROLE, (step.id, block))
            font = row.font(ID_COLUMN)
            font.setItalic(True)
            row.setFont(ID_COLUMN, font)
            row.setForeground(ID_COLUMN, QBrush(QColor(110, 110, 110)))
            row.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                         | Qt.ItemFlag.ItemIsDropEnabled)
            row.setToolTip(ID_COLUMN, f"the '{block}' steps of {step.id}; drop or insert "
                                      "steps here")
            item.addChild(row)
            self._blocks[(step.id, block)] = row
            for child in children:
                row.addChild(self._item(child))
        return item

    def _paint_markers(self) -> None:
        for step_id, item in self._items.items():
            marked = step_id in self._breakpoints or step_id in self._script_breaks
            item.setText(GUTTER, BREAKPOINT_MARK if marked else "")
            item.setForeground(GUTTER, QBrush(BREAKPOINT_COLOR))
            tip = "break: true in the script" if step_id in self._script_breaks else \
                "breakpoint (click to remove)" if marked else "click to add a breakpoint"
            item.setToolTip(GUTTER, tip)

    def _paint_problems(self) -> None:
        for step_id, item in self._items.items():
            problems = self._problems.get(step_id)
            details = item.text(DETAILS_COLUMN).removeprefix(f"{PROBLEM_MARK} ")
            if problems:
                item.setForeground(ID_COLUMN, QBrush(PROBLEM_COLOR))
                item.setText(DETAILS_COLUMN, f"{PROBLEM_MARK} {details}")
                item.setForeground(DETAILS_COLUMN, QBrush(PROBLEM_COLOR))
                item.setToolTip(DETAILS_COLUMN, "\n".join(problems))
            else:
                item.setForeground(ID_COLUMN, QBrush())
                item.setText(DETAILS_COLUMN, details)
                item.setForeground(DETAILS_COLUMN, QBrush())
                item.setToolTip(DETAILS_COLUMN, "")

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


def _payload(data: QMimeData | None) -> tuple[str, str] | None:
    """``(mime type, name)`` of a step or step type being dragged."""
    if data is None:
        return None
    for kind in (STEP_MIME, TYPE_MIME):
        if data.hasFormat(kind):
            name = bytes(data.data(kind).data()).decode("utf-8")
            if name:
                return kind, name
    return None
