"""The editor area (center): the step palette, the step tree, the selected step's form
(or the script header's), and the YAML.

Everything shown comes from the :class:`~j5_macro.gui.controllers.ScriptDocument`:
``state_changed`` refreshes the tree, the forms and the YAML, whoever changed the file.
Every change is an ``edit_script`` edit sent through the :class:`EditQueue` (one at a
time, each based on the version the previous one produced); the views never change
themselves, they show the reloaded script. The edits come from pure functions in
:mod:`j5_macro.gui.models.edits`.

"Save as self-contained file" and "Unpack images to files" are the client-side tools
of ``j5 pack`` and ``j5 unpack`` (:mod:`j5_macro.script.bundle`), run in this process.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMessageBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.context import AppContext
from j5_macro.gui.controllers.edit_queue import EditQueue
from j5_macro.gui.engine_thread import describe_error
from j5_macro.gui.models import edits
from j5_macro.gui.models.edits import Target
from j5_macro.gui.models.fields import problems_by_step
from j5_macro.gui.models.header import image_names
from j5_macro.gui.models.registry import StepRegistry, new_step
from j5_macro.gui.models.script import ScriptState, format_problem
from j5_macro.gui.models.steps import find_step, iter_steps
from j5_macro.gui.views.header_form import ScriptHeaderPanel
from j5_macro.gui.views.palette import StepPalette
from j5_macro.gui.views.step_form import StepFormPanel
from j5_macro.gui.views.step_tree import StepTreeView
from j5_macro.gui.views.yaml_view import YamlView
from j5_macro.script import bundle

MAX_PROBLEMS = 8


class EditorPanel(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.registry = StepRegistry()
        self.queue = EditQueue(ctx.document, self)
        self.title = QLabel()
        self.title.setObjectName("editorTitle")
        self.problems = QLabel()
        self.problems.setObjectName("editorProblems")
        self.problems.setWordWrap(True)
        self.problems.setTextFormat(Qt.TextFormat.PlainText)
        self.problems.setStyleSheet("color: #b00020;")
        self.problems.hide()
        self.step_palette = StepPalette()
        self.tree = StepTreeView()
        self.form = StepFormPanel(ctx, self.queue)
        self.header = ScriptHeaderPanel(ctx, self.queue)
        self.yaml_view = YamlView()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.form, "Step")
        self.tabs.addTab(self.header, "Script")

        self.toolbar = QToolBar()
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        style = self.style()
        pixmap = QStyle.StandardPixmap
        self.delete_action = self._action("Delete", self.delete_selected,
                                          QKeySequence.StandardKey.Delete,
                                          style.standardIcon(pixmap.SP_TrashIcon))
        self.duplicate_action = self._action("Duplicate", self.duplicate_selected,
                                             QKeySequence("Ctrl+D"))
        self.up_action = self._action("Up", lambda: self.move_selected(-1),
                                      QKeySequence("Alt+Up"),
                                      style.standardIcon(pixmap.SP_ArrowUp))
        self.down_action = self._action("Down", lambda: self.move_selected(1),
                                        QKeySequence("Alt+Down"),
                                        style.standardIcon(pixmap.SP_ArrowDown))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)

        steps_area = QWidget()
        steps_layout = QVBoxLayout(steps_area)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.setSpacing(2)
        steps_layout.addWidget(self.toolbar)
        steps_layout.addWidget(self.tree, 1)
        middle = QSplitter(Qt.Orientation.Vertical)
        middle.addWidget(steps_area)
        middle.addWidget(self.tabs)
        middle.setStretchFactor(0, 2)
        middle.setStretchFactor(1, 3)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.step_palette)
        self.splitter.addWidget(middle)
        self.splitter.addWidget(self.yaml_view)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 2)
        self.splitter.setSizes([150, 560, 380])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.title)
        layout.addWidget(self.problems)
        layout.addWidget(self.splitter, 1)

        self.last_message = ""
        """The last message shown in the status bar by the editor."""
        self._state: ScriptState | None = None
        self._select_after: set[str] | None = None
        """Ids before an insert: the new step is selected once the script reloads."""
        self._select_id: str | None = None
        self._syncing = False
        ctx.document.state_changed.connect(self.show_script)
        ctx.document.load_failed.connect(self._load_failed)
        ctx.run.current_step_changed.connect(self.tree.set_current_step)
        ctx.run.breakpoints_changed.connect(self.tree.set_breakpoints)
        ctx.bridge.engine_started.connect(lambda _: self.load_registry())
        self.tree.breakpoint_toggled.connect(ctx.run.toggle_breakpoint)
        self.tree.step_selected.connect(self._step_selected)
        self.tree.step_dropped.connect(self.drop_step)
        self.tree.type_dropped.connect(self.insert_step)
        self.step_palette.insert_requested.connect(lambda name: self.insert_step(name))
        self.form.renamed.connect(lambda _old, new: self._remember_selection(new))
        self.yaml_view.line_clicked.connect(self._line_clicked)
        self.queue.failed.connect(self._edit_failed)
        self.tree.set_breakpoints(ctx.run.breakpoints)
        self.show_script(ctx.document.state)
        if ctx.bridge.info is not None:  # built after the engine started
            self.load_registry()

    @property
    def state(self) -> ScriptState | None:
        return self._state

    # -- the registry --------------------------------------------------------------------

    def load_registry(self) -> None:
        """Ask the engine for its step types (``list_step_types``)."""
        self._ctx.bridge.call("list_step_types", {}, on_result=self._registry_loaded,
                              on_error=lambda exc: self._message(
                                  f"cannot list the step types: {describe_error(exc)}"))

    def _registry_loaded(self, result: Mapping[str, Any]) -> None:
        self.registry = StepRegistry.from_result(result)
        self.step_palette.set_registry(self.registry)
        self.tree.set_registry(self.registry)
        self.form.set_registry(self.registry)
        if self._state is not None:
            self.tree.set_steps(self._state.steps)

    # -- showing -------------------------------------------------------------------------

    def show_script(self, state: ScriptState | None) -> None:
        self._state = state
        self.form.setEnabled(state is not None)
        self.step_palette.setEnabled(state is not None)
        if state is None:
            self.title.setText("<i>No script open</i>")
            self.tree.set_steps([])
            self.form.show_step(None)
            self.header.show_script(None)
            self.yaml_view.set_source("")
            self._show_problems([])
            return
        validity = "" if state.valid else " <span style='color:#b00020'>(invalid)</span>"
        where = f" <span style='color:gray'>{_escape(state.path)}</span>" if state.path else ""
        self.title.setText(f"<b>{_escape(state.title)}</b>{validity}{where}")
        self.tree.set_steps(state.steps)
        self.tree.set_problems(problems_by_step(state.errors))
        self.yaml_view.set_source(state.source)
        self.header.show_script(state)
        self._show_problems(state.errors)
        self._select_pending(state)
        self._step_selected(self.tree.selected_step_id())

    def _select_pending(self, state: ScriptState) -> None:
        ids = [step.id for step in iter_steps(state.steps)]
        if self._select_after is not None:
            new = [step_id for step_id in ids if step_id not in self._select_after]
            if new:
                self._select_after = None
                self.tree.select_step(new[0])
        if self._select_id is not None and self._select_id in ids:
            self.tree.select_step(self._select_id)
            self._select_id = None

    def _remember_selection(self, step_id: str) -> None:
        self._select_id = step_id
        if self._state is not None and find_step(self._state.steps, step_id) is not None:
            self.tree.select_step(step_id)
            self._select_id = None

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
        self.form.show_step(step, self._state)
        if step is not None and self.tabs.currentWidget() is not self.form:
            self.tabs.setCurrentWidget(self.form)
        self._syncing = True
        try:
            self.yaml_view.highlight_line(step.line if step is not None else None)
        finally:
            self._syncing = False
        selected = step is not None
        for action in (self.delete_action, self.duplicate_action, self.up_action,
                       self.down_action):
            action.setEnabled(selected)

    def _line_clicked(self, line: int) -> None:
        if self._syncing or self._state is None:
            return
        step_id = edits.step_at_line(self._state.steps, line)
        if step_id is not None and step_id != self.tree.selected_step_id():
            self.tree.select_step(step_id)

    # -- editing -------------------------------------------------------------------------

    def submit(self, edit_list: Sequence[Mapping[str, Any]]) -> None:
        """Send edits after the pending changes of the forms."""
        self.form.flush()
        self.header.flush()
        self.queue.submit(edit_list)

    def insert_step(self, type_name: str, target: Target | None = None) -> None:
        """Insert a new step of ``type_name`` at ``target`` (by default after the
        selected step, or into the selected block)."""
        state = self._state
        step_type = self.registry.get(type_name)
        if state is None or step_type is None:
            return
        if target is None:
            target = edits.insert_target(self.tree.selected_step_id(),
                                         self.tree.selected_block())
        position = edits.position_for(state.steps, self.registry, target)
        if position is None:
            return
        self._select_after = {step.id for step in iter_steps(state.steps)}
        self.submit([edits.insert_edit(new_step(step_type, image_names(state.images)),
                                       position)])

    def delete_selected(self) -> None:
        step_id = self.tree.selected_step_id()
        if self._state is None or step_id is None:
            return
        neighbour = edits.neighbour_after_delete(self._state.steps, step_id)
        self._select_id = neighbour
        self.submit([edits.delete_edit(step_id)])

    def duplicate_selected(self) -> None:
        step_id = self.tree.selected_step_id()
        if self._state is None or step_id is None:
            return
        edit = edits.duplicate_edit(self._state.steps, self.registry, step_id)
        if edit is not None:
            self._select_after = {step.id for step in iter_steps(self._state.steps)}
            self.submit([edit])

    def move_selected(self, direction: int) -> None:
        step_id = self.tree.selected_step_id()
        if self._state is None or step_id is None:
            return
        mover = edits.move_up if direction < 0 else edits.move_down
        edit = mover(self._state.steps, step_id)
        if edit is not None:
            self._select_id = step_id
            self.submit([edit])

    def drop_step(self, step_id: str, target: Target) -> None:
        """Move ``step_id`` where it was dropped."""
        if self._state is None:
            return
        edit = edits.drop_edit(self._state.steps, self.registry, step_id, target)
        if edit is not None:
            self._select_id = step_id
            self.submit([edit])

    def _edit_failed(self, message: str) -> None:
        self._select_after = None
        self._message(f"edit failed: {message}")

    def _message(self, text: str) -> None:
        self.last_message = text
        self._ctx.document.notice.emit(text)

    def _action(self, text: str, slot: Any, shortcut: QKeySequence | QKeySequence.StandardKey,
                icon: Any = None) -> QAction:
        action = QAction(text, self)
        if icon is not None:
            action.setIcon(icon)
        action.setShortcut(shortcut)
        action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        action.setEnabled(False)
        action.triggered.connect(slot)
        self.toolbar.addAction(action)
        self.tree.addAction(action)
        return action

    # -- single files --------------------------------------------------------------------

    def ask_save_packed(self) -> None:
        """"Save as self-contained file…": choose where, then :meth:`save_packed`."""
        state = self._state
        if state is None or state.path is None:
            self._message("open a script file first")
            return
        stem = Path(state.path).stem
        suggestion = str(Path(state.path).with_name(f"{stem}-packed.yaml"))
        path, _ = QFileDialog.getSaveFileName(self, "Save as self-contained file",
                                              suggestion, "Scripts (*.yaml *.yml)")
        if path:
            self.save_packed(path)

    def save_packed(self, output: str) -> None:
        """Write the script with every image file it refers to embedded (``j5 pack``)
        to ``output``, once the pending edits are saved. The outcome is shown in the
        status bar and kept in :attr:`last_message`."""
        self.form.flush()
        self.header.flush()
        self.queue.when_idle(lambda: self._save_packed(output))

    def _save_packed(self, output: str) -> None:
        state = self._state
        if state is None or state.path is None:
            return
        registry = _local_registry()
        try:
            if os.path.abspath(output) == os.path.abspath(state.path):
                raise bundle.BundleError("choose another file: the script itself is "
                                         "the one being packed")
            packed = bundle.pack_script(state.path, registry, output=output)
            bundle.write_text(Path(output), packed.text)
        except bundle.BundleError as exc:
            details = [format_problem(e.to_json()) for e in exc.errors[:3]]
            self._message("; ".join([exc.message, *details]))
            return
        added = ", ".join(f"#{name}" for name in packed.plan.added)
        summary = (f"embedded {len(packed.plan.added)} image(s) ({added})"
                   if packed.plan.added else "no image file to embed")
        message = f"Saved {output}: {summary}"
        if packed.plan.skipped:
            message += f"; {len(packed.plan.skipped)} built at run time left as they are"
        self._message(message)

    def ask_unpack(self) -> None:
        """"Unpack images to files…": choose a folder, then :meth:`unpack_to`."""
        state = self._state
        if state is None or state.path is None:
            self._message("open a script file first")
            return
        if not state.images:
            self._message("the script has no embedded images")
            return
        directory = QFileDialog.getExistingDirectory(
            self, "Unpack images into a folder", os.path.dirname(state.path))
        if not directory:
            return
        target = Path(directory) / Path(state.path).name
        force = False
        if target.exists() or (Path(directory) / bundle.IMAGES_DIR).exists():
            answer = QMessageBox.question(
                self, "Unpack images",
                f"Files in {directory} may be replaced (the script {target.name} and the "
                f"images in {bundle.IMAGES_DIR}/). Continue?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            force = True
        self.unpack_to(directory, force=force)

    def unpack_to(self, directory: str, *, force: bool = False) -> None:
        """Write the embedded images to ``directory/img/<name>.png`` and the script,
        referring to them by path, to ``directory`` (``j5 unpack``), then open that
        script, once the pending edits are saved. Unpacking into the script's own folder
        rewrites the script in place. The outcome is shown in the status bar and kept
        in :attr:`last_message`."""
        self.form.flush()
        self.header.flush()
        self.queue.when_idle(lambda: self._unpack_to(directory, force))

    def _unpack_to(self, directory: str, force: bool) -> None:
        state = self._state
        if state is None or state.path is None:
            return
        try:
            result = bundle.unpack_script(state.path, directory, _local_registry(),
                                          force=force)
        except bundle.BundleError as exc:
            self._message(exc.message)
            return
        message = (f"Wrote {len(result.images)} image(s) to "
                   f"{Path(directory) / bundle.IMAGES_DIR} and the script to "
                   f"{result.script_path}")
        if result.warnings:
            message += f" ({'; '.join(result.warnings)})"
        if os.path.abspath(result.script_path) != os.path.abspath(state.path):
            self._ctx.document.open(str(result.script_path))
        self._message(message)


def _local_registry() -> Any:
    """The step registry in this process (plugins included), for packing."""
    from j5_macro.steps.builtins import registry_with_plugins

    registry, _ = registry_with_plugins()
    return registry


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
