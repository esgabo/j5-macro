"""The main window: it only lays out the panels and the menus and wires them together.

Areas (each in its own module, each built from the shared :class:`AppContext`):

- left: :class:`ScriptListPanel` (script_list.py)
- center: :class:`EditorPanel` (editor.py), with the step tree, the form and the YAML
- bottom tabs: :class:`RunPanel` (run_panel.py), :class:`HistoryPanel`
  (history_panel.py) and :class:`StepToolsPanel` (step_tools.py: the screen tools of
  the selected step, also in the Tools menu)
- top: :class:`EngineBanner` (engine_banner.py) when the engine died
- status bar: the engine's backend, vision and stop key
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from j5_macro import __version__
from j5_macro.gui.context import AppContext
from j5_macro.gui.models.engine_info import EngineInfo
from j5_macro.gui.models.script import ScriptState
from j5_macro.gui.views.editor import EditorPanel
from j5_macro.gui.views.engine_banner import EngineBanner
from j5_macro.gui.views.history_panel import HistoryPanel
from j5_macro.gui.views.run_panel import RunPanel
from j5_macro.gui.views.screen_dialogs import TopLevelHider
from j5_macro.gui.views.script_list import ScriptListPanel
from j5_macro.gui.views.step_tools import StepToolsPanel

TITLE = "j5"
STATUS_TIMEOUT_MS = 8000


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(TITLE)
        self.resize(1200, 800)

        self.banner = EngineBanner(ctx.bridge)
        self.script_list = ScriptListPanel(ctx)
        self.editor = EditorPanel(ctx)
        self.run_panel = RunPanel(ctx)
        self.history = HistoryPanel(ctx)
        self.step_tools = StepToolsPanel(ctx)
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.addTab(self.run_panel, "Run")
        self.bottom_tabs.addTab(self.history, "History")
        self.bottom_tabs.addTab(self.step_tools, "Screen")
        if ctx.screen.hider is None:
            ctx.screen.hider = TopLevelHider()

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.editor)
        right.addWidget(self.bottom_tabs)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.script_list)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 4)
        self.splitter.setSizes([280, 920])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.banner)
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)

        self.engine_label = QLabel("Starting the engine…")
        self.statusBar().addPermanentWidget(self.engine_label)
        self._build_menus()

        ctx.bridge.engine_started.connect(self._engine_started)
        ctx.bridge.engine_died.connect(lambda _: self.engine_label.setText("Engine stopped"))
        ctx.bridge.engine_failed.connect(lambda _: self.engine_label.setText("No engine"))
        ctx.document.state_changed.connect(self._document_changed)
        ctx.document.notice.connect(self.show_message)
        ctx.document.load_failed.connect(lambda message, _: self.show_message(message))
        self.script_list.message.connect(self.show_message)
        self.step_tools.message.connect(self.show_message)
        self.editor.tree.step_selected.connect(self.step_tools.set_step)
        ctx.run.error.connect(self.show_message)
        ctx.run.active_changed.connect(self._run_active)
        ctx.run.state_changed.connect(
            lambda state: self.editor.tree.set_paused(state == "paused"))
        ctx.run.paused.connect(self._run_paused)
        if ctx.bridge.info is not None:  # panels may be built after the engine started
            self._engine_started(ctx.bridge.info)
        self._document_changed(ctx.document.state)

    # -- actions -------------------------------------------------------------------------

    def open_target(self, path: str) -> None:
        """Open a folder in the script list, or a script and its folder."""
        path = os.path.abspath(path)
        if os.path.isdir(path):
            self.script_list.set_folder(path)
            return
        self.script_list.set_folder(os.path.dirname(path))
        self.ctx.document.open(path)

    def show_message(self, text: str) -> None:
        self.statusBar().showMessage(text, STATUS_TIMEOUT_MS)

    def choose_script(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a script", self.script_list.folder or os.getcwd(),
            "Scripts (*.yaml *.yml);;All files (*)")
        if path:
            self.open_target(path)

    # -- internals -----------------------------------------------------------------------

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.open_folder_action = self._action(file_menu, "Open &folder…",
                                               self.script_list.choose_folder,
                                               QKeySequence("Ctrl+Shift+O"))
        self.open_script_action = self._action(file_menu, "&Open script…", self.choose_script,
                                               QKeySequence.StandardKey.Open)
        self.new_script_action = self._action(file_menu, "&New script…",
                                              self.script_list.ask_new_script,
                                              QKeySequence.StandardKey.New)
        file_menu.addSeparator()
        self._action(file_menu, "&Quit", self.close, QKeySequence.StandardKey.Quit)

        run_menu = self.menuBar().addMenu("&Run")
        actions = self.run_panel.run_actions  # with their shortcuts (F5, F10, F11...)
        for name in ("run", "dry_run", "debug", "debug_dry", "options", None, "continue",
                     "pause", "step_over", "step_into", "step_out", "stop"):
            if name is None:
                run_menu.addSeparator()
            else:
                run_menu.addAction(actions[name])
        self.run_action = actions["run"]
        self.dry_run_action = actions["dry_run"]
        self.stop_action = actions["stop"]
        run_menu.addSeparator()
        self.toggle_breakpoint_action = self._action(
            run_menu, "Toggle &breakpoint", self._toggle_breakpoint, QKeySequence("F9"))
        self.confirm_action = QAction("&Confirm runs that send real input", self)
        self.confirm_action.setCheckable(True)
        self.confirm_action.setChecked(self.ctx.run.confirm_real_input)
        self.confirm_action.toggled.connect(self.ctx.run.set_confirm_real_input)
        run_menu.aboutToShow.connect(
            lambda: self.confirm_action.setChecked(self.ctx.run.confirm_real_input))
        run_menu.addAction(self.confirm_action)

        tools_menu = self.menuBar().addMenu("&Tools")
        self.capture_action = self._action(tools_menu, "&Capture template…",
                                           lambda: self._screen_tool("capture"),
                                           QKeySequence("Ctrl+Shift+C"))
        self.test_action = self._action(tools_menu, "&Test image search",
                                        lambda: self._screen_tool("test"),
                                        QKeySequence("Ctrl+T"))
        self.pick_action = self._action(tools_menu, "&Pick point…",
                                        lambda: self._screen_tool("pick"),
                                        QKeySequence("Ctrl+Shift+P"))
        self.windows_action = self._action(tools_menu, "&Windows…",
                                           lambda: self._screen_tool("windows"))

        engine_menu = self.menuBar().addMenu("&Engine")
        self._action(engine_menu, "&Restart engine", self.ctx.bridge.restart)

    def _action(self, menu: object, text: str, slot: object,
                shortcut: QKeySequence | QKeySequence.StandardKey | None = None) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)  # type: ignore[attr-defined]
        return action

    def _toggle_breakpoint(self) -> None:
        step_id = self.editor.tree.selected_step_id()
        if step_id is not None:
            self.ctx.run.toggle_breakpoint(step_id)

    def _screen_tool(self, name: str) -> None:
        """A Tools menu entry: the screen tool on the selected step."""
        self.bottom_tabs.setCurrentWidget(self.step_tools)
        getattr(self.step_tools, name)()

    def _engine_started(self, info: EngineInfo) -> None:
        self.engine_label.setText(f"j5 {__version__} · {info.summary()}")
        self.script_list.refresh()

    def _document_changed(self, state: ScriptState | None) -> None:
        self.setWindowTitle(f"{state.title} - {TITLE}" if state is not None else TITLE)

    def _run_paused(self, pause: object) -> None:
        if pause is not None:
            self.bottom_tabs.setCurrentWidget(self.run_panel)

    def _run_active(self, active: bool) -> None:
        if active:
            self.bottom_tabs.setCurrentWidget(self.run_panel)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.ctx.bridge.shutdown()
        super().closeEvent(event)
