"""The script list (left): the scripts of a folder (``list_scripts``), opening one and
creating a new one (``create_script``)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.context import AppContext
from j5_macro.gui.engine_thread import describe_error
from j5_macro.gui.models.script import ScriptEntry, ScriptState, parse_script_list
from j5_macro.protocol import Event

PATH_ROLE = Qt.ItemDataRole.UserRole
INVALID_COLOR = QColor(176, 0, 32)


class ScriptListPanel(QWidget):
    folder_changed = Signal(str)
    scripts_listed = Signal(object)
    """The new ``list[ScriptEntry]`` after each listing."""
    message = Signal(str)
    """Something for the status bar (a listing or creation failed)."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._folder: str | None = None
        self._entries: list[ScriptEntry] = []
        self.folder_label = QLabel("No folder")
        self.folder_label.setWordWrap(True)
        self.open_folder_button = QPushButton("Open folder…")
        self.new_button = QPushButton("New…")
        self.refresh_button = QPushButton("Refresh")
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Script", "File"])
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.open_folder_button)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.refresh_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.folder_label)
        layout.addLayout(buttons)
        layout.addWidget(self.list, 1)

        self.open_folder_button.clicked.connect(self.choose_folder)
        self.new_button.clicked.connect(self.ask_new_script)
        self.refresh_button.clicked.connect(self.refresh)
        self.list.itemActivated.connect(self._activated)
        ctx.document.state_changed.connect(self._document_changed)
        ctx.bridge.event_received.connect(self._on_event)
        self._update_buttons()

    @property
    def folder(self) -> str | None:
        return self._folder

    @property
    def entries(self) -> list[ScriptEntry]:
        return list(self._entries)

    def set_folder(self, folder: str) -> None:
        self._folder = os.path.abspath(folder)
        self.folder_label.setText(self._folder)
        self.folder_label.setToolTip(self._folder)
        self._update_buttons()
        self.folder_changed.emit(self._folder)
        self.refresh()

    def refresh(self) -> None:
        if self._folder is None:
            return
        folder = self._folder
        self._ctx.bridge.call("list_scripts", {"root": folder},
                              on_result=lambda result: self._listed(folder, result),
                              on_error=lambda exc: self.message.emit(
                                  f"cannot list {folder}: {describe_error(exc)}"))

    def open_path(self, path: str) -> None:
        self._ctx.document.open(path)

    def create_script(self, path: str, name: str) -> None:
        """Write a new empty script (the engine never overwrites a file), then open it."""
        def created(result: Mapping[str, Any]) -> None:
            self.refresh()
            self.open_path(path)

        self._ctx.bridge.call("create_script", {"path": path, "name": name},
                              on_result=created,
                              on_error=lambda exc: self.message.emit(
                                  f"cannot create {path}: {describe_error(exc)}"))

    # -- dialogs -------------------------------------------------------------------------

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open a folder of scripts",
                                                  self._folder or os.getcwd())
        if folder:
            self.set_folder(folder)

    def ask_new_script(self) -> None:
        if self._folder is None:
            return
        name, ok = QInputDialog.getText(self, "New script", "Script name:")
        if not ok or not name.strip():
            return
        default = os.path.join(self._folder, _file_name(name) + ".yaml")
        path, _ = QFileDialog.getSaveFileName(self, "Save the new script as", default,
                                              "Scripts (*.yaml *.yml)")
        if path:
            self.create_script(path, name.strip())

    # -- internals -----------------------------------------------------------------------

    def _listed(self, folder: str, result: Mapping[str, Any]) -> None:
        if folder != self._folder:
            return
        self._entries = parse_script_list(result)
        self.list.clear()
        for entry in self._entries:
            item = QTreeWidgetItem([entry.name, entry.relative_to(folder)])
            item.setData(0, PATH_ROLE, entry.path)
            item.setToolTip(0, entry.path)
            item.setToolTip(1, entry.path)
            if not entry.valid:
                for column in (0, 1):
                    item.setForeground(column, QBrush(INVALID_COLOR))
                item.setToolTip(0, f"{entry.path} (not valid)")
            self.list.addTopLevelItem(item)
        self.list.resizeColumnToContents(0)
        self._select(self._ctx.document.path)
        self.scripts_listed.emit(list(self._entries))

    def _activated(self, item: QTreeWidgetItem, column: int) -> None:
        path = item.data(0, PATH_ROLE)
        if isinstance(path, str):
            self.open_path(path)

    def _document_changed(self, state: ScriptState | None) -> None:
        self._select(state.path if state is not None else None)

    def _select(self, path: str | None) -> None:
        for index in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(index)
            if item is not None and path is not None and _same(item.data(0, PATH_ROLE), path):
                self.list.setCurrentItem(item)
                return
        self.list.clearSelection()

    def _on_event(self, event: Event) -> None:
        if event.type == "script_changed" and event.run_id is None:
            self.refresh()  # names and validity may have changed

    def _update_buttons(self) -> None:
        self.new_button.setEnabled(self._folder is not None)
        self.refresh_button.setEnabled(self._folder is not None)


def _file_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip().lower())
    return cleaned.strip("_") or "script"


def _same(a: object, b: str) -> bool:
    return isinstance(a, str) and os.path.normcase(os.path.abspath(a)) == \
        os.path.normcase(os.path.abspath(b))
