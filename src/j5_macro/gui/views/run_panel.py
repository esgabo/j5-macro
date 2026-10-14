"""The run panel (bottom): starting runs, the debug toolbar, the pause box, the live
log and the variables.

- **Run**, **Dry run** (with **Strict**), **Debug** (pauses at the first step; its menu
  has **Debug a dry run**) and **Options…**. A script with params, and Options…, show
  the run dialog (:mod:`~j5_macro.gui.views.params_dialog`); a run that sends real
  input asks first, naming the emergency stop key (unless turned off).
- **Continue**, **Pause**, **Step over**, **Step into**, **Step out** and **Stop**, with
  VS Code's keys (F5, F6, F10, F11, Shift+F11, Shift+F5), enabled per state
  (:func:`~j5_macro.gui.models.debugger.debug_actions`). The actions are
  :attr:`RunPanel.run_actions`; the main window's Run menu shows the same ones.
- The pause box (:mod:`~j5_macro.gui.views.pause_view`), the log with a level filter,
  a summary line, and the variables (:mod:`~j5_macro.gui.views.variables`).

Dialogs are kept in small methods (:meth:`RunPanel.exec_dialog`,
:meth:`RunPanel.confirm_real_input`) so tests can replace them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.context import AppContext
from j5_macro.gui.models.debugger import DebugActions
from j5_macro.gui.models.engine_info import EngineInfo
from j5_macro.gui.models.params import initial_texts, parse_param_specs
from j5_macro.gui.models.run_log import LEVELS, LogLine, level_visible
from j5_macro.gui.models.run_options import (
    RunKind,
    RunOptions,
    confirmation_text,
    needs_confirmation,
    run_kind,
)
from j5_macro.gui.views.params_dialog import RunDialog
from j5_macro.gui.views.pause_view import PauseView
from j5_macro.gui.views.variables import VariablesPanel

LEVEL_COLORS = {
    "debug": QColor(128, 128, 128),
    "warning": QColor(160, 96, 0),
    "error": QColor(176, 0, 32),
}
MAX_LINES = 20000
"""Older lines are dropped beyond this, so a long run never slows the window down."""
FILTERS = (("All", "debug"), ("Info and above", "info"), ("Warnings and errors", "warning"),
           ("Errors", "error"))
LEVEL_ROLE = Qt.ItemDataRole.UserRole


class RunPanel(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.options = RunOptions()
        """The options of the next run (the run dialog changes them)."""
        self._build_actions()

        self.run_button = self._tool(self.run_action)
        self.dry_run_button = self._tool(self.dry_run_action)
        self.strict_box = QCheckBox("Strict")
        self.strict_box.setToolTip("Dry run: fail where the real run would fail (an image "
                                   "not found, a missing window...)")
        self.debug_button = self._tool(self.debug_action)
        debug_menu = QMenu(self.debug_button)
        debug_menu.addAction(self.debug_action)
        debug_menu.addAction(self.debug_dry_action)
        self.debug_button.setMenu(debug_menu)
        self.debug_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.options_button = self._tool(self.options_action)
        self.continue_button = self._tool(self.continue_action)
        self.pause_button = self._tool(self.pause_action)
        self.step_over_button = self._tool(self.step_over_action)
        self.step_into_button = self._tool(self.step_into_action)
        self.step_out_button = self._tool(self.step_out_action)
        self.stop_button = self._tool(self.stop_action)
        self.state_label = QLabel("Idle")
        self.state_label.setObjectName("runState")
        self.stop_key_label = QLabel()

        bar = QHBoxLayout()
        bar.setSpacing(4)
        for widget in (self.run_button, self.dry_run_button, self.strict_box,
                       self.debug_button, self.options_button):
            bar.addWidget(widget)
        bar.addWidget(_separator())
        for widget in (self.continue_button, self.pause_button, self.step_over_button,
                       self.step_into_button, self.step_out_button, self.stop_button):
            bar.addWidget(widget)
        bar.addSpacing(8)
        bar.addWidget(self.state_label)
        bar.addStretch(1)
        bar.addWidget(self.stop_key_label)

        self.pause_view = PauseView(ctx)
        self.log_view = QListWidget()
        self.log_view.setUniformItemSizes(True)
        self.log_view.setObjectName("runLog")
        self.filter_combo = QComboBox()
        for label, level in FILTERS:
            self.filter_combo.addItem(label, level)
        self.filter_combo.setToolTip("Which lines of the log to show")
        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Clear the log (the run's history keeps every event)")
        self.summary_label = QLabel()
        self.summary_label.setObjectName("runSummary")
        # Its text changes with every event: never let it resize the splitter.
        self.summary_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                         QSizePolicy.Policy.Preferred)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Show"))
        filter_row.addWidget(self.filter_combo)
        filter_row.addWidget(self.clear_button)
        filter_row.addStretch(1)
        log_column = QWidget()
        log_layout = QVBoxLayout(log_column)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addLayout(filter_row)
        log_layout.addWidget(self.log_view, 1)
        log_layout.addWidget(self.summary_label)
        self.variables = VariablesPanel(ctx)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(log_column)
        self.splitter.addWidget(self.variables)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(self.pause_view)
        layout.addWidget(self.splitter, 1)

        self.strict_box.toggled.connect(self._strict_toggled)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        self.clear_button.clicked.connect(self.clear_log)
        ctx.run.active_changed.connect(self._active_changed)
        ctx.run.event_logged.connect(self.append_line)
        ctx.run.state_changed.connect(self._state_changed)
        ctx.run.paused.connect(self._paused)
        ctx.run.run_finished.connect(self._finished)
        ctx.run.actions_changed.connect(self._update_actions)
        ctx.run.error.connect(lambda message: self.append_text(message, "error"))
        ctx.document.state_changed.connect(lambda _: self._refresh_actions())
        ctx.bridge.running_changed.connect(lambda _: self._refresh_actions())
        ctx.bridge.engine_started.connect(self._engine_started)
        self._engine_started(ctx.bridge.info)
        self._refresh_actions()

    # -- actions -------------------------------------------------------------------------

    def _build_actions(self) -> None:
        run = self._ctx.run
        self.run_action = self._action("Run", "Run the script, sending real input (F5)",
                                       lambda: self.request("normal"), "F5")
        self.dry_run_action = self._action(
            "Dry run", "Describe what the script would do without sending input (Ctrl+F5)",
            lambda: self.request("dry"), "Ctrl+F5")
        self.debug_action = self._action(
            "Debug", "Pause at the first step, then step through (F10 or F11)",
            lambda: self.request("debug"), "F11", "F10")
        self.debug_dry_action = self._action(
            "Debug a dry run", "Pause at the first step of a dry run (no input is sent)",
            lambda: self.request("debug_dry"), "Ctrl+F11")
        self.options_action = self._action("Options…", "Params and run options",
                                           lambda: self.request(None))
        self.continue_action = self._action("Continue", "Run to the next breakpoint or the "
                                            "end; after a failure, retry the step (F5)",
                                            run.resume, "F5")
        self.pause_action = self._action("Pause", "Pause at the next action (F6)",
                                         run.pause_run, "F6")
        self.step_over_action = self._action("Step over", "Run this step, a whole loop or "
                                             "if included, and pause (F10)",
                                             run.step_over, "F10")
        self.step_into_action = self._action("Step into", "Pause at the very next step, "
                                             "entering loops and ifs (F11)",
                                             run.step_into, "F11")
        self.step_out_action = self._action("Step out", "Finish the current loop or if and "
                                            "pause after it (Shift+F11)",
                                            run.step_out, "Shift+F11")
        self.stop_action = self._action("Stop", "Stop the run (Shift+F5)", run.stop,
                                        "Shift+F5")
        self.run_actions = {
            "run": self.run_action, "dry_run": self.dry_run_action,
            "debug": self.debug_action, "debug_dry": self.debug_dry_action,
            "options": self.options_action, "continue": self.continue_action,
            "pause": self.pause_action, "step_over": self.step_over_action,
            "step_into": self.step_into_action, "step_out": self.step_out_action,
            "stop": self.stop_action,
        }
        """Every action, by name, for the menus."""

    def _action(self, text: str, tip: str, slot: Any, *shortcuts: str) -> QAction:
        action = QAction(text, self)
        action.setToolTip(tip)
        if shortcuts:
            action.setShortcuts([QKeySequence(key) for key in shortcuts])
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.triggered.connect(lambda _checked=False: slot())
        self.addAction(action)
        return action

    def _tool(self, action: QAction) -> QToolButton:
        button = QToolButton()
        button.setDefaultAction(action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        return button

    # -- starting runs -------------------------------------------------------------------

    def start(self, mode: str) -> None:
        """Start a run of the open script now, with the current options and no params
        prompt (``mode``: ``normal``, ``dry``, ``debug``, ``debug_dry``, or ``step``)."""
        self._start(run_kind(mode), {}, self.options)

    def request(self, mode: str | None) -> None:
        """What the buttons do: the run dialog when the script has params (or for
        Options…, ``mode`` None), then the confirmation before real input, then the
        run."""
        state = self._ctx.document.state
        if state is None or self._ctx.run.active:
            return
        specs = parse_param_specs(state.params)
        kind = run_kind(mode) if mode is not None else None
        options = self.options
        params: dict[str, Any] = {}
        if specs or kind is None:
            dialog = self.run_dialog(kind.key if kind is not None else "dry")
            try:
                if not self.exec_dialog(dialog):
                    return
                outcome = dialog.outcome()
                if outcome is None:
                    return
                kind, params, options = outcome
                self._ctx.run.remember_params(dialog.remembered_texts(), state)
            finally:
                dialog.forget()
                dialog.deleteLater()
            self.options = options
            self.strict_box.setChecked(options.strict)
        info = self._ctx.bridge.info
        if needs_confirmation(kind, info.backend if info else None,
                              self._ctx.run.confirm_real_input) \
                and not self.confirm_real_input(kind):
            return
        self._start(kind, params, options)

    def run_dialog(self, kind: str) -> RunDialog:
        """The run dialog for the open script, filled with the last values."""
        state = self._ctx.document.state
        assert state is not None
        specs = parse_param_specs(state.params)
        texts = initial_texts(specs, self._ctx.run.remembered_params(state))
        return RunDialog(state.title, specs, texts, self.options, kind, parent=self)

    def exec_dialog(self, dialog: RunDialog) -> bool:
        """Show the run dialog; True when the user started the run."""
        return dialog.exec() == QDialog.DialogCode.Accepted

    def confirm_real_input(self, kind: RunKind) -> bool:
        """Ask before a run that sends real input; the box can turn the question off."""
        info = self._ctx.bridge.info
        box = QMessageBox(QMessageBox.Icon.Warning, f"{kind.label}: real input",
                          confirmation_text(info.stop_key if info else None),
                          QMessageBox.StandardButton.Cancel, self)
        start = box.addButton(kind.label, QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(start)
        box.setTextFormat(Qt.TextFormat.RichText)
        dont_ask = QCheckBox("Do not ask again")
        box.setCheckBox(dont_ask)
        box.exec()
        if box.clickedButton() is not start:
            return False
        if dont_ask.isChecked():
            self._ctx.run.set_confirm_real_input(False)
        return True

    def _start(self, kind: RunKind, params: Mapping[str, Any], options: RunOptions) -> None:
        if kind.mode == "dry" and self.strict_box.isChecked() != options.strict:
            options = replace(options, strict=self.strict_box.isChecked())
        self._ctx.run.start(kind.key, params=params, options=options)
        self.options = options.after_run()

    # -- log -----------------------------------------------------------------------------

    def append_line(self, line: LogLine) -> None:
        self.append_text(line.text, line.level)
        self._update_summary()

    def append_text(self, text: str, level: str = "info") -> None:
        item = QListWidgetItem(text)
        item.setData(LEVEL_ROLE, level)
        color = LEVEL_COLORS.get(level)
        if color is not None:
            item.setForeground(QBrush(color))
        self.log_view.addItem(item)
        item.setHidden(not level_visible(level, self.filter_level()))
        while self.log_view.count() > MAX_LINES:
            self.log_view.takeItem(0)
        self.log_view.scrollToBottom()

    def log_lines(self) -> list[str]:
        """The text of every line (also the hidden ones), for tests and copying."""
        return [self.log_view.item(i).text() for i in range(self.log_view.count())]

    def visible_lines(self) -> list[str]:
        return [self.log_view.item(i).text() for i in range(self.log_view.count())
                if not self.log_view.item(i).isHidden()]

    def filter_level(self) -> str:
        level = self.filter_combo.currentData()
        return level if level in LEVELS else "debug"

    def set_filter(self, level: str) -> None:
        self.filter_combo.setCurrentIndex(max(0, self.filter_combo.findData(level)))

    def clear_log(self) -> None:
        self.log_view.clear()

    def _apply_filter(self) -> None:
        minimum = self.filter_level()
        for index in range(self.log_view.count()):
            item = self.log_view.item(index)
            item.setHidden(not level_visible(str(item.data(LEVEL_ROLE)), minimum))
        self.log_view.scrollToBottom()

    def _update_summary(self) -> None:
        self.summary_label.setText(self._ctx.run.log.summary())

    # -- state ---------------------------------------------------------------------------

    def _active_changed(self, active: bool) -> None:
        if active:
            self.log_view.clear()
            self.summary_label.setText("")
            self.state_label.setText("Starting…")
        self._refresh_actions()

    def _state_changed(self, state: str) -> None:
        if state == "finished":
            return  # _finished says how
        pause = self._ctx.run.pause
        text = state.capitalize()
        if state == "paused" and pause is not None and pause.reason:
            text += f" ({pause.reason})"
        self.state_label.setText(text)
        self._update_summary()

    def _paused(self, _pause: object) -> None:
        self._state_changed(self._ctx.run.state)
        self.log_view.scrollToBottom()  # the pause box took some of its height

    def _finished(self, payload: Mapping[str, Any]) -> None:
        self.state_label.setText(f"Finished: {payload.get('status')}")
        self._update_summary()

    def _engine_started(self, info: EngineInfo | None) -> None:
        if info is None:
            self.stop_key_label.setText("")
        elif info.stop_key:
            self.stop_key_label.setText(f"Emergency stop: <b>{info.stop_key}</b>")
        else:
            self.stop_key_label.setText("<span style='color:#a06000'>No emergency stop "
                                        "key</span>")

    def _strict_toggled(self, checked: bool) -> None:
        if checked != self.options.strict:
            self.options = replace(self.options, strict=checked)

    def _refresh_actions(self) -> None:
        self._update_actions(self._ctx.run.actions)

    def _update_actions(self, actions: DebugActions) -> None:
        for action in (self.run_action, self.dry_run_action, self.debug_action,
                       self.debug_dry_action, self.options_action):
            action.setEnabled(actions.run)
        self.continue_action.setEnabled(actions.resume)
        self.pause_action.setEnabled(actions.pause)
        for action in (self.step_over_action, self.step_into_action, self.step_out_action):
            action.setEnabled(actions.step)
        self.stop_action.setEnabled(actions.stop)
        self.strict_box.setEnabled(actions.run)


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line
