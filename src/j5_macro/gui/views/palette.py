"""The step palette: every step type of the engine's registry (``list_step_types``,
plugin steps included), grouped, with its documentation as tooltip. Double-click (or
Enter, or **Insert**) inserts a step of that type at the selection of the step tree;
dragging a type onto the tree inserts it where it is dropped."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.models.registry import StepRegistry, palette_groups
from j5_macro.gui.views.step_tree import TYPE_MIME

TYPE_ROLE = Qt.ItemDataRole.UserRole


class _PaletteTree(QTreeWidget):
    def mimeTypes(self) -> list[str]:
        return [TYPE_MIME]

    def mimeData(self, items: Sequence[QTreeWidgetItem]) -> QMimeData:
        data = QMimeData()
        name = items[0].data(0, TYPE_ROLE) if items else None
        if isinstance(name, str):
            data.setData(TYPE_MIME, name.encode("utf-8"))
        return data


class StepPalette(QWidget):
    insert_requested = Signal(str)
    """A step type to insert at the selection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter steps")
        self.filter.setClearButtonEnabled(True)
        self.tree = _PaletteTree()
        self.tree.setHeaderHidden(True)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(10)
        self.insert_button = QPushButton("Insert")
        self.insert_button.setToolTip("Insert a step of the selected type after the selected "
                                      "step (or into the selected block)")
        self.insert_button.setEnabled(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.filter)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.insert_button)
        self._registry = StepRegistry()
        self.filter.textChanged.connect(lambda _: self._fill())
        self.tree.itemActivated.connect(self._activated)
        self.tree.itemSelectionChanged.connect(
            lambda: self.insert_button.setEnabled(self.selected_type() is not None))
        self.insert_button.clicked.connect(self._insert_selected)

    def set_registry(self, registry: StepRegistry) -> None:
        self._registry = registry
        self._fill()

    def type_names(self) -> list[str]:
        """The step types shown, in order."""
        names: list[str] = []
        for g in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g)
            if group is None:
                continue
            for i in range(group.childCount()):
                child = group.child(i)
                if child is not None:
                    names.append(str(child.data(0, TYPE_ROLE)))
        return names

    def item_for(self, name: str) -> QTreeWidgetItem | None:
        for g in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g)
            for i in range(group.childCount() if group is not None else 0):
                child = group.child(i) if group is not None else None
                if child is not None and child.data(0, TYPE_ROLE) == name:
                    return child
        return None

    def selected_type(self) -> str | None:
        items = self.tree.selectedItems()
        name = items[0].data(0, TYPE_ROLE) if items else None
        return name if isinstance(name, str) else None

    def _fill(self) -> None:
        self.tree.clear()
        for group, types in palette_groups(self._registry, self.filter.text()):
            header = QTreeWidgetItem([group])
            font = header.font(0)
            font.setBold(True)
            header.setFont(0, font)
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(header)
            for step_type in types:
                item = QTreeWidgetItem([step_type.name])
                item.setData(0, TYPE_ROLE, step_type.name)
                item.setToolTip(0, step_type.tooltip())
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                              | Qt.ItemFlag.ItemIsDragEnabled)
                header.addChild(item)
        self.tree.expandAll()

    def _activated(self, item: QTreeWidgetItem, _column: int) -> None:
        name = item.data(0, TYPE_ROLE)
        if isinstance(name, str):
            self.insert_requested.emit(name)

    def _insert_selected(self) -> None:
        name = self.selected_type()
        if name is not None:
            self.insert_requested.emit(name)
