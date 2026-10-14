"""The history panel (a tab next to the run panel): the saved runs of the open script
(``list_runs``, refreshed when a run ends) and, for the selected run (``get_run``), its
events, its captures and, for an image search that failed, the capture next to the
template it looked for, with the best location drawn on it. Runs can be deleted
(``delete_run``) and their folder opened.

The selected run id is :meth:`HistoryPanel.selected_run_id`; the run shown,
:attr:`HistoryPanel.detail` (a :class:`~j5_macro.gui.models.history.RunDetail`).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.context import AppContext
from j5_macro.gui.engine_thread import describe_error
from j5_macro.gui.models.history import (
    HISTORY_COLUMNS,
    RunDetail,
    SearchFailure,
    history_row,
)
from j5_macro.gui.models.runs import RunSummary, parse_run_list
from j5_macro.gui.models.script import ScriptState
from j5_macro.gui.views.template_preview import MISSED, ImageView, Mark, TemplatePreview, load_image

RUN_ID_ROLE = Qt.ItemDataRole.UserRole
LIMIT = 50
LEVEL_COLORS = {"error": QColor("#b00020"), "warning": QColor("#8a5a00"),
                "debug": QColor("#777777")}


class FailureView(QWidget):
    """A failed image search: the template and the capture side by side."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.choice = QComboBox()
        self.choice.hide()
        self.template_heading = QLabel()
        self.capture_heading = QLabel()
        self.template = TemplatePreview(ctx, minimum=QSize(100, 60))
        self.capture = ImageView(placeholder="No capture", minimum=QSize(160, 80))
        self.capture_caption = QLabel()
        self.capture_caption.setWordWrap(True)
        self.capture_caption.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        grid = QGridLayout()
        grid.setVerticalSpacing(2)
        grid.addWidget(self.template_heading, 0, 0)
        grid.addWidget(self.capture_heading, 0, 1)
        grid.addWidget(self.template, 1, 0)
        grid.addWidget(self.capture, 1, 1)
        grid.addWidget(self.capture_caption, 2, 1)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)
        grid.setRowStretch(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.choice)
        layout.addLayout(grid, 1)
        self._detail: RunDetail | None = None
        self.choice.currentIndexChanged.connect(self._chosen)

    @property
    def failure(self) -> SearchFailure | None:
        detail = self._detail
        index = self.choice.currentIndex()
        if detail is None or not 0 <= index < len(detail.failures):
            return None
        return detail.failures[index]

    def show_run(self, detail: RunDetail | None) -> None:
        self._detail = detail
        self.choice.blockSignals(True)
        self.choice.clear()
        failures = detail.failures if detail is not None else ()
        for failure in failures:
            self.choice.addItem(f"{failure.step_id}: {failure.template}")
        self.choice.blockSignals(False)
        self.choice.setVisible(len(failures) > 1)
        self._chosen()

    def _chosen(self) -> None:
        failure = self.failure
        detail = self._detail
        if failure is None or detail is None:
            self.template_heading.setText("<b>Template</b>")
            self.capture_heading.setText("<b>No image search failed in this run</b>")
            self.template.show_template(None)
            self.capture.set_image(None, "No capture")
            self.capture_caption.setText("")
            return
        self.template_heading.setText("<b>Template</b>")
        self.capture_heading.setText(f"<b>What {_escape(failure.step_id)} saw</b>")
        self._show_template(detail, failure)
        path = detail.capture_path(failure.capture)
        image = load_image(path) if path is not None else None
        self.capture.set_image(image, f"{failure.capture}\nnot found")
        rect = failure.bbox_in_capture
        if rect is not None and image is not None:
            score = "-" if failure.best_score is None else f"{failure.best_score:.3f}"
            self.capture.set_marks([Mark(rect, MISSED, f"best {score}")])
        caption = failure.score_text
        if failure.attempts:
            caption += f" · {failure.attempts} attempt{'s' if failure.attempts != 1 else ''}"
        self.capture_caption.setText(caption)
        where = f"searched {list(failure.region)} on the desktop\n" if failure.region else ""
        self.capture_caption.setToolTip(f"{where}{failure.capture}")
        self.capture.setToolTip(failure.message)

    def _show_template(self, detail: RunDetail, failure: SearchFailure) -> None:
        state = self._ctx.document.state
        same_script = (state is not None and state.path is not None
                       and detail.script_path is not None
                       and os.path.normcase(os.path.abspath(state.path))
                       == os.path.normcase(os.path.abspath(detail.script_path)))
        if same_script:
            self.template.show_template(failure.template)
        else:  # a script loaded from source, or another file
            self.template.show_template(failure.template, script_path=detail.script_path,
                                        use_document=False)


class HistoryPanel(QWidget):
    runs_listed = Signal(object)
    """The new ``list[RunSummary]`` after each listing."""
    run_selected = Signal(object)
    """The selected run id, or None."""
    run_loaded = Signal(object)
    """The :class:`RunDetail` of the selected run, once ``get_run`` answered."""
    run_deleted = Signal(str)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._runs: list[RunSummary] = []
        self._detail: RunDetail | None = None
        self._token = 0
        self.refresh_button = QPushButton("Refresh")
        self.delete_button = QPushButton("Delete…")
        self.open_button = QPushButton("Open folder")
        self.status = QLabel()
        self.list = QTreeWidget()
        self.list.setHeaderLabels(HISTORY_COLUMNS)
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.setAlternatingRowColors(True)

        self.headline = QLabel()
        self.headline.setWordWrap(True)
        self.headline.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.events = QListWidget()
        self.events.setUniformItemSizes(True)
        self.failure_view = FailureView(ctx)
        self.captures = QListWidget()
        self.capture_view = ImageView(placeholder="Select a capture")
        captures = QSplitter(Qt.Orientation.Horizontal)
        captures.addWidget(self.captures)
        captures.addWidget(self.capture_view)
        captures.setStretchFactor(0, 1)
        captures.setStretchFactor(1, 3)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.events, "Events")
        self.tabs.addTab(self.failure_view, "Failed search")
        self.tabs.addTab(captures, "Captures")
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self.headline)
        detail_layout.addWidget(self.tabs, 1)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.list)
        self.splitter.addWidget(detail)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)

        bar = QHBoxLayout()
        bar.addWidget(self.refresh_button)
        bar.addWidget(self.delete_button)
        bar.addWidget(self.open_button)
        bar.addWidget(self.status, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(self.splitter, 1)

        self.refresh_button.clicked.connect(self.refresh)
        self.delete_button.clicked.connect(self.ask_delete)
        self.open_button.clicked.connect(self.open_folder)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.captures.currentItemChanged.connect(self._capture_chosen)
        ctx.document.state_changed.connect(self._script_changed)
        ctx.run.run_finished.connect(lambda _: self.refresh())
        self._show_detail(None)
        self.refresh()

    @property
    def runs(self) -> list[RunSummary]:
        return list(self._runs)

    @property
    def detail(self) -> RunDetail | None:
        return self._detail

    def selected_run_id(self) -> str | None:
        items = self.list.selectedItems()
        run_id = items[0].data(0, RUN_ID_ROLE) if items else None
        return run_id if isinstance(run_id, str) else None

    def select_run(self, run_id: str) -> None:
        for index in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(index)
            if item is not None and item.data(0, RUN_ID_ROLE) == run_id:
                self.list.setCurrentItem(item)
                return

    def refresh(self) -> None:
        script_id = self._ctx.document.script_id
        if script_id is None:
            self._show([])
            return
        self._ctx.bridge.call("list_runs", {"script_id": script_id, "limit": LIMIT},
                              on_result=lambda result: self._listed(script_id, result),
                              on_error=lambda exc: self.status.setText(
                                  f"cannot list the runs: {describe_error(exc)}"))

    def load_run(self, run_id: str) -> None:
        """Read a run with ``get_run`` and show it (``run_loaded``)."""
        self._token += 1
        token = self._token
        self._ctx.bridge.call("get_run", {"run_id": run_id},
                              on_result=lambda result: self._loaded(token, result),
                              on_error=lambda exc: self._load_failed(token, exc))

    # -- deleting and opening ------------------------------------------------------------

    def ask_delete(self) -> None:
        run_id = self.selected_run_id()
        if run_id is None:
            return
        answer = QMessageBox.question(
            self, "Delete run", f"Delete the run {run_id}, its events and its captures?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_run(run_id)

    def delete_run(self, run_id: str) -> None:
        """``delete_run``, then list the runs again."""
        def deleted(_: Mapping[str, Any]) -> None:
            self.status.setText(f"Deleted {run_id}")
            self.run_deleted.emit(run_id)
            self.refresh()

        self._ctx.bridge.call("delete_run", {"run_id": run_id}, on_result=deleted,
                              on_error=lambda exc: self.status.setText(
                                  f"cannot delete {run_id}: {describe_error(exc)}"))

    def run_folder(self) -> str | None:
        detail = self._detail
        if detail is None or detail.run_id != self.selected_run_id():
            return None
        return detail.directory

    def open_folder(self) -> None:
        folder = self.run_folder()
        if folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # -- internals -----------------------------------------------------------------------

    def _script_changed(self, state: ScriptState | None) -> None:
        self.refresh()

    def _listed(self, script_id: str, result: Mapping[str, Any]) -> None:
        if script_id != self._ctx.document.script_id:
            return
        self._show(parse_run_list(result))

    def _show(self, runs: list[RunSummary]) -> None:
        selected = self.selected_run_id()
        self._runs = runs
        self.list.blockSignals(True)
        self.list.clear()
        for run in runs:
            row = history_row(run)
            item = QTreeWidgetItem(row.columns)
            item.setData(0, RUN_ID_ROLE, run.run_id)
            color = LEVEL_COLORS.get(row.level)
            if color is not None:
                item.setForeground(2, QBrush(color))
            self.list.addTopLevelItem(item)
        for column in range(self.list.columnCount()):
            self.list.resizeColumnToContents(column)
        self.list.blockSignals(False)
        if selected is not None:
            self.select_run(selected)
        self._selection_changed()  # read the selected run again: it may have changed
        self.status.setText(f"{len(runs)} saved run{'s' if len(runs) != 1 else ''}")
        self.runs_listed.emit(list(runs))

    def _selection_changed(self) -> None:
        run_id = self.selected_run_id()
        self._update_buttons()
        self.run_selected.emit(run_id)
        if run_id is None:
            self._token += 1
            self._show_detail(None)
            return
        if self._detail is None or self._detail.run_id != run_id:
            self.headline.setText("Loading…")
        self.load_run(run_id)

    def _loaded(self, token: int, result: Mapping[str, Any]) -> None:
        if token != self._token:
            return
        detail = RunDetail.from_get_run(result)
        if detail.run_id != self.selected_run_id():
            return
        self._show_detail(detail)
        self.run_loaded.emit(detail)

    def _load_failed(self, token: int, exc: BaseException) -> None:
        if token != self._token:
            return
        self._show_detail(None)
        self.headline.setText(f"cannot read the run: {describe_error(exc)}")

    def _show_detail(self, detail: RunDetail | None) -> None:
        previous = self._detail.run_id if self._detail is not None else None
        self._detail = detail
        self._update_buttons()
        self.events.clear()
        self.captures.clear()
        self.capture_view.set_image(None, "Select a capture")
        if detail is None:
            self.headline.setText("<i>Select a run to see its events and captures.</i>")
            self.failure_view.show_run(None)
            self.tabs.setTabEnabled(1, False)
            self.tabs.setTabEnabled(2, False)
            return
        self.headline.setText(f"<b>{_escape(detail.run_id)}</b>: "
                              f"{_escape(detail.headline())}")
        for line in detail.lines:
            item = QListWidgetItem(line.text)
            color = LEVEL_COLORS.get(line.level)
            if color is not None:
                item.setForeground(QBrush(color))
            self.events.addItem(item)
        for capture in detail.captures:
            self.captures.addItem(capture)
        self.failure_view.show_run(detail)
        self.tabs.setTabEnabled(1, bool(detail.failures))
        self.tabs.setTabEnabled(2, bool(detail.captures))
        self.tabs.setTabText(2, f"Captures ({len(detail.captures)})" if detail.captures
                             else "Captures")
        if detail.run_id != previous:
            self.tabs.setCurrentIndex(1 if detail.failures else 0)
            if not detail.failures:
                self.events.scrollToBottom()

    def _capture_chosen(self, item: QListWidgetItem | None, _: object = None) -> None:
        detail = self._detail
        if item is None or detail is None:
            self.capture_view.set_image(None, "Select a capture")
            return
        capture = item.text()
        path = detail.capture_path(capture)
        image = load_image(path) if path is not None else None
        self.capture_view.set_image(image, f"{capture}\nnot found")
        search = detail.capture_searches.get(capture)
        if image is None or search is None:
            return
        bbox, region = search.get("bbox"), search.get("region")
        if isinstance(bbox, list) and isinstance(region, list) and len(bbox) == 4 \
                and len(region) == 4:
            score = search.get("best_score")
            label = f"{score:.3f}" if isinstance(score, int | float) else ""
            color = QColor("#1e9e3a") if search.get("found") else MISSED
            self.capture_view.set_marks([Mark((bbox[0] - region[0], bbox[1] - region[1],
                                               bbox[2], bbox[3]), color, label)])

    def _update_buttons(self) -> None:
        selected = self.selected_run_id()
        self.delete_button.setEnabled(selected is not None)
        self.open_button.setEnabled(self.run_folder() is not None)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
