"""The history panel (a tab next to the run panel): the saved runs of the open script
(``list_runs``), refreshed when a run ends.

Extension point (history): a run's events and captures (``get_run``), the failing
search's capture next to its template, and deleting runs (``delete_run``) are added
here; the selected run id is :meth:`HistoryPanel.selected_run_id`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.context import AppContext
from j5_macro.gui.engine_thread import describe_error
from j5_macro.gui.models.runs import RunSummary, parse_run_list
from j5_macro.gui.models.script import ScriptState

RUN_ID_ROLE = Qt.ItemDataRole.UserRole
LIMIT = 50


class HistoryPanel(QWidget):
    runs_listed = Signal(object)
    """The new ``list[RunSummary]`` after each listing."""
    run_selected = Signal(object)
    """The selected run id, or None."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._runs: list[RunSummary] = []
        self.refresh_button = QPushButton("Refresh")
        self.status = QLabel()
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Started", "Mode", "Status", "Duration", "Run"])
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        bar = QHBoxLayout()
        bar.addWidget(self.refresh_button)
        bar.addWidget(self.status, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(self.list, 1)
        self.refresh_button.clicked.connect(self.refresh)
        self.list.itemSelectionChanged.connect(
            lambda: self.run_selected.emit(self.selected_run_id()))
        ctx.document.state_changed.connect(self._script_changed)
        ctx.run.run_finished.connect(lambda _: self.refresh())

    @property
    def runs(self) -> list[RunSummary]:
        return list(self._runs)

    def selected_run_id(self) -> str | None:
        items = self.list.selectedItems()
        run_id = items[0].data(0, RUN_ID_ROLE) if items else None
        return run_id if isinstance(run_id, str) else None

    def refresh(self) -> None:
        script_id = self._ctx.document.script_id
        if script_id is None:
            self._show([])
            return
        self._ctx.bridge.call("list_runs", {"script_id": script_id, "limit": LIMIT},
                              on_result=lambda result: self._listed(script_id, result),
                              on_error=lambda exc: self.status.setText(
                                  f"cannot list the runs: {describe_error(exc)}"))

    def _script_changed(self, state: ScriptState | None) -> None:
        self.refresh()

    def _listed(self, script_id: str, result: Mapping[str, Any]) -> None:
        if script_id != self._ctx.document.script_id:
            return
        self._show(parse_run_list(result))

    def _show(self, runs: list[RunSummary]) -> None:
        self._runs = runs
        self.list.clear()
        for run in runs:
            item = QTreeWidgetItem([run.started, run.mode, run.status or "(unfinished)",
                                    run.duration_text, run.run_id])
            item.setData(0, RUN_ID_ROLE, run.run_id)
            self.list.addTopLevelItem(item)
        for column in range(self.list.columnCount()):
            self.list.resizeColumnToContents(column)
        self.status.setText(f"{len(runs)} saved run{'s' if len(runs) != 1 else ''}")
        self.runs_listed.emit(list(runs))
