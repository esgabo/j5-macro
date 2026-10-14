"""The script header as a form: name, capture scale, input profile, params, vars and
the embedded images. Every change is a ``set_meta`` edit (or ``add_image``,
``rename_image``, ``delete_image`` for images), sent through the :class:`EditQueue`;
the form follows the script as it is reloaded, except in the part being edited."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.context import AppContext
from j5_macro.gui.controllers.edit_queue import EditQueue
from j5_macro.gui.models.fields import Parsed, format_scalar, parse_number
from j5_macro.gui.models.header import (
    PARAM_TYPES,
    ParamRow,
    image_summary,
    params_value,
    var_rows,
    vars_value,
)
from j5_macro.gui.models.script import ScriptState
from j5_macro.gui.models.steps import iter_steps
from j5_macro.gui.views.fields import ERROR_STYLE, ProfileEdit
from j5_macro.script import bundle
from j5_macro.script.images import REFERENCE_PREFIX, encode_base64

DEBOUNCE_MS = 600
P_NAME, P_TYPE, P_DESCRIPTION, P_SECRET, P_REQUIRED, P_DEFAULT = range(6)
IMAGE_NAME_ROLE = Qt.ItemDataRole.UserRole


class ScriptHeaderPanel(QWidget):
    def __init__(self, ctx: AppContext, queue: EditQueue,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._queue = queue
        self._state: ScriptState | None = None
        self._dirty: set[str] = set()
        self._loading = False
        self.choose_png: Callable[[QWidget, str], str | None] = _choose_png

        self.name = QLineEdit()
        self.capture_scale = QLineEdit()
        self.capture_scale.setPlaceholderText("1 (100 %)")
        self.capture_scale.setToolTip("The display scale the templates and pixel offsets "
                                      "were taken at (1.5 for 150 %)")
        self.capture_scale.setMaximumWidth(120)
        self.profile = ProfileEdit()

        self.params = QTableWidget(0, 6)
        self.params.setHorizontalHeaderLabels(
            ["Name", "Type", "Description", "Secret", "Required", "Default"])
        self.params.verticalHeader().hide()
        self.params.verticalHeader().setDefaultSectionSize(24)
        header = self.params.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(P_DESCRIPTION, QHeaderView.ResizeMode.Stretch)
        self.params.setMinimumHeight(110)
        self.vars = QTableWidget(0, 2)
        self.vars.setHorizontalHeaderLabels(["Name", "Value"])
        self.vars.verticalHeader().hide()
        self.vars.verticalHeader().setDefaultSectionSize(24)
        self.vars.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.vars.setMinimumHeight(100)
        self.vars.setToolTip('Values: text, numbers, true/false, or JSON such as [1, 2] '
                             'and {"a": 1}')
        self.images = QTableWidget(0, 2)
        self.images.setHorizontalHeaderLabels(["Name", "Image"])
        self.images.verticalHeader().hide()
        self.images.verticalHeader().setDefaultSectionSize(24)
        self.images.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.images.setMinimumHeight(100)
        self.images.setToolTip("Templates embedded in the script; steps refer to them as "
                               "#name. Double-click a name to rename it")
        self.problems = QLabel()
        self.problems.setObjectName("headerProblems")
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet(ERROR_STYLE)
        self.problems.hide()

        general = QGroupBox("Script")
        general_form = QFormLayout(general)
        general_form.addRow("name", self.name)
        general_form.addRow("capture scale", self.capture_scale)
        profile_box = QGroupBox("Input profile")
        profile_box.setToolTip("How the mouse moves and how text is typed, for the whole "
                               "script")
        QVBoxLayout(profile_box).addWidget(self.profile)
        params_box = QGroupBox("Params (values given when the script runs)")
        params_layout = QVBoxLayout(params_box)
        params_layout.addWidget(self.params)
        params_layout.addWidget(_buttons(("Add param", self.add_param),
                                         ("Remove", self.remove_param)))
        vars_box = QGroupBox("Vars (starting values of variables)")
        vars_layout = QVBoxLayout(vars_box)
        vars_layout.addWidget(self.vars)
        vars_layout.addWidget(_buttons(("Add var", self.add_var), ("Remove", self.remove_var)))
        images_box = QGroupBox("Embedded images")
        images_layout = QVBoxLayout(images_box)
        images_layout.addWidget(self.images)
        images_layout.addWidget(_buttons(("Add from file…", self.ask_add_image),
                                         ("Rename…", self.ask_rename_image),
                                         ("Delete", self.ask_delete_image)))

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        for box in (general, params_box, vars_box, images_box, profile_box):
            body_layout.addWidget(box)
        body_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.problems)
        layout.addWidget(scroll, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self.flush)
        self.name.textEdited.connect(lambda _: self._changed("name"))
        self.capture_scale.textEdited.connect(lambda _: self._changed("capture_scale"))
        self.profile.changed.connect(lambda now: self._changed("input", now))
        self.params.itemChanged.connect(lambda _: self._changed("params"))
        self.vars.itemChanged.connect(lambda _: self._changed("vars"))
        self.images.itemChanged.connect(self._image_renamed)
        self.show_script(ctx.document.state)

    # -- showing -------------------------------------------------------------------------

    def show_script(self, state: ScriptState | None) -> None:
        self._state = state
        self.setEnabled(state is not None)
        if state is None:
            return
        busy = self._busy_part()
        self._loading = True
        try:
            if "name" not in self._dirty and busy != "name":
                self.name.setText(state.name)
            if "capture_scale" not in self._dirty and busy != "capture_scale":
                self.capture_scale.setText(format_scalar(state.capture_scale))
            if "input" not in self._dirty and busy != "input":
                self.profile.set_value(state.input)
            if "params" not in self._dirty and busy != "params":
                self._fill_params([ParamRow.from_info(p) for p in state.params])
            if "vars" not in self._dirty and busy != "vars":
                self._fill_vars(var_rows(state.vars))
            self._fill_images(state.images)
        finally:
            self._loading = False

    def _busy_part(self) -> str | None:
        focus = self.focusWidget()
        if focus is None or not focus.hasFocus():
            return None
        for part, widget in (("name", self.name), ("capture_scale", self.capture_scale),
                             ("input", self.profile), ("params", self.params),
                             ("vars", self.vars)):
            if widget is focus or widget.isAncestorOf(focus):
                return part
        return None

    def _fill_params(self, rows: Sequence[ParamRow]) -> None:
        self.params.setRowCount(0)
        for row in rows:
            self._add_param_row(row)

    def _add_param_row(self, row: ParamRow) -> None:
        index = self.params.rowCount()
        self.params.insertRow(index)
        self.params.setItem(index, P_NAME, QTableWidgetItem(row.name))
        kind = QComboBox()
        kind.addItems(list(PARAM_TYPES))
        kind.setCurrentText(row.type)
        kind.currentTextChanged.connect(lambda _: self._changed("params", True))
        self.params.setCellWidget(index, P_TYPE, kind)
        self.params.setItem(index, P_DESCRIPTION, QTableWidgetItem(row.description))
        for column, checked in ((P_SECRET, row.secret), (P_REQUIRED, row.required)):
            box = QCheckBox()
            box.setChecked(checked)
            box.toggled.connect(lambda _: self._changed("params", True))
            self.params.setCellWidget(index, column, _centered(box))
        self.params.setItem(index, P_DEFAULT, QTableWidgetItem(row.default))

    def _fill_vars(self, rows: Sequence[tuple[str, str]]) -> None:
        self.vars.setRowCount(0)
        for name, value in rows:
            index = self.vars.rowCount()
            self.vars.insertRow(index)
            self.vars.setItem(index, 0, QTableWidgetItem(name))
            self.vars.setItem(index, 1, QTableWidgetItem(value))

    def _fill_images(self, images: Sequence[Mapping[str, Any]]) -> None:
        self.images.setRowCount(0)
        for info in images:
            name = str(info.get("name", ""))
            index = self.images.rowCount()
            self.images.insertRow(index)
            item = QTableWidgetItem(name)
            item.setData(IMAGE_NAME_ROLE, name)
            self.images.setItem(index, 0, item)
            summary = QTableWidgetItem(image_summary(info))
            summary.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.images.setItem(index, 1, summary)

    # -- reading -------------------------------------------------------------------------

    def param_rows(self) -> list[ParamRow]:
        rows: list[ParamRow] = []
        for index in range(self.params.rowCount()):
            kind = self.params.cellWidget(index, P_TYPE)
            rows.append(ParamRow(
                name=_text(self.params.item(index, P_NAME)),
                type=kind.currentText() if isinstance(kind, QComboBox) else "string",
                description=_text(self.params.item(index, P_DESCRIPTION)),
                secret=_checked(self.params.cellWidget(index, P_SECRET)),
                required=_checked(self.params.cellWidget(index, P_REQUIRED)),
                default=_text(self.params.item(index, P_DEFAULT)),
            ))
        return rows

    def param_checkbox(self, row: int, which: str) -> QCheckBox:
        """The ``secret`` or ``required`` check box of a param row."""
        holder = self.params.cellWidget(row, P_SECRET if which == "secret" else P_REQUIRED)
        box = holder.findChild(QCheckBox) if holder is not None else None
        if box is None:
            raise LookupError(f"no {which} check box in row {row}")
        return box

    def var_rows(self) -> list[tuple[str, str]]:
        return [(_text(self.vars.item(i, 0)), _text(self.vars.item(i, 1)))
                for i in range(self.vars.rowCount())]

    def image_names(self) -> list[str]:
        items = [self.images.item(i, 0) for i in range(self.images.rowCount())]
        return [str(item.data(IMAGE_NAME_ROLE)) for item in items if item is not None]

    # -- editing -------------------------------------------------------------------------

    def add_param(self) -> None:
        taken = {row.name for row in self.param_rows()}
        name = next(f"param{n}" if n else "param" for n in range(100)
                    if (f"param{n}" if n else "param") not in taken)
        self._add_param_row(ParamRow(name=name))
        self._changed("params", True)

    def remove_param(self) -> None:
        row = self.params.currentRow()
        if row >= 0:
            self.params.removeRow(row)
            self._changed("params", True)

    def add_var(self) -> None:
        taken = {name for name, _ in self.var_rows()}
        name = next(f"var{n}" if n else "var" for n in range(100)
                    if (f"var{n}" if n else "var") not in taken)
        index = self.vars.rowCount()
        self._loading = True
        self.vars.insertRow(index)
        self.vars.setItem(index, 0, QTableWidgetItem(name))
        self.vars.setItem(index, 1, QTableWidgetItem("0"))
        self._loading = False
        self._changed("vars", True)

    def remove_var(self) -> None:
        row = self.vars.currentRow()
        if row >= 0:
            self.vars.removeRow(row)
            self._changed("vars", True)

    def _changed(self, part: str, now: bool = False) -> None:
        if self._loading:
            return
        self._dirty.add(part)
        if now:
            self.flush()
        else:
            self._timer.start()

    def flush(self) -> None:
        """Send the pending header changes as one ``set_meta``."""
        self._timer.stop()
        if self._state is None or not self._dirty:
            self._dirty.clear()
            return
        meta: dict[str, Any] = {}
        problems: list[str] = []
        parts, self._dirty = self._dirty, set()
        if "name" in parts:
            name = self.name.text().strip()
            if name:
                meta["name"] = name
            else:
                problems.append("name: a script needs a name")
        if "capture_scale" in parts:
            scale = parse_number(self.capture_scale.text())
            if not scale.ok or isinstance(scale.value, str) or (
                    scale.value is not None and scale.value <= 0):
                problems.append("capture scale: expected a positive number")
            elif scale.value is not None:
                meta["capture_scale"] = scale.value
        if "input" in parts:
            profile = self.profile.read()
            if profile.ok:
                meta["input"] = profile.value or {}  # {} removes the section
            else:
                problems.append(f"input: {profile.error}")
        if "params" in parts:
            self._add(meta, problems, "params", params_value(self.param_rows()))
        if "vars" in parts:
            self._add(meta, problems, "vars", vars_value(self.var_rows()))
        self._show_problems(problems)
        if meta:
            self._queue.submit([{"op": "set_meta", **meta}],
                               on_error=lambda message: self._show_problems([message]))

    @staticmethod
    def _add(meta: dict[str, Any], problems: list[str], key: str, parsed: Parsed) -> None:
        if parsed.ok:
            meta[key] = parsed.value
        else:
            problems.append(f"{key}: {parsed.error}")

    def _show_problems(self, problems: Sequence[str]) -> None:
        self.problems.setText("\n".join(problems))
        self.problems.setVisible(bool(problems))

    # -- images --------------------------------------------------------------------------

    def ask_add_image(self) -> None:
        state = self._state
        start = os.path.dirname(state.path) if state and state.path else os.getcwd()
        path = self.choose_png(self, start)
        if path:
            self.add_image_file(path)

    def add_image_file(self, path: str, name: str | None = None) -> str | None:
        """Embed the PNG file at ``path`` (as ``j5 pack`` would) under ``name`` (by
        default from the file name); returns the name, or None when it cannot be
        embedded (the problem is shown)."""
        if self._state is None:
            return None
        try:
            data = bundle.read_png(path, Path(path).name)
        except bundle.BundleError as exc:
            self._show_problems([exc.message])
            return None
        entry = name or bundle.unique_name(Path(path).stem, set(self.image_names()))
        self._show_problems([])
        self._queue.submit([{"op": "add_image", "name": entry, "png": encode_base64(data)}],
                           on_error=lambda message: self._show_problems([message]))
        return entry

    def selected_image(self) -> str | None:
        row = self.images.currentRow()
        item = self.images.item(row, 0) if row >= 0 else None
        name = item.data(IMAGE_NAME_ROLE) if item is not None else None
        return name if isinstance(name, str) else None

    def ask_rename_image(self) -> None:
        name = self.selected_image()
        if name is None:
            return
        new_name, ok = QInputDialog.getText(self, "Rename image", "New name:", text=name)
        if ok and new_name.strip():
            self.rename_image(name, new_name.strip())

    def rename_image(self, name: str, new_name: str) -> None:
        """Rename an embedded image and the steps' ``#name`` references to it."""
        if name == new_name:
            return
        self._queue.submit([{"op": "rename_image", "name": name, "new_name": new_name}],
                           on_error=self._image_edit_failed)

    def _image_renamed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        old = item.data(IMAGE_NAME_ROLE)
        new = item.text().strip()
        if isinstance(old, str) and new and new != old:
            self.rename_image(old, new)

    def image_references(self, name: str) -> list[str]:
        """The ids of the steps that refer to ``#name``."""
        if self._state is None:
            return []
        reference = f"{REFERENCE_PREFIX}{name}"
        return [step.id for step in iter_steps(self._state.steps)
                if _mentions(step.params, reference)]

    def ask_delete_image(self) -> None:
        name = self.selected_image()
        if name is None:
            return
        users = self.image_references(name)
        text = f"Delete the embedded image #{name}?"
        if users:
            text += (f"\n\nThese steps refer to it and will need another image: "
                     f"{', '.join(users)}")
        answer = QMessageBox.question(self, "Delete image", text)
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_image(name)

    def delete_image(self, name: str) -> None:
        self._queue.submit([{"op": "delete_image", "name": name}],
                           on_error=self._image_edit_failed)

    def _image_edit_failed(self, message: str) -> None:
        self._show_problems([message])
        self.show_script(self._state)


def _mentions(value: Any, reference: str) -> bool:
    if isinstance(value, str):
        return value == reference
    if isinstance(value, Mapping):
        return any(_mentions(v, reference) for v in value.values())
    if isinstance(value, list | tuple):
        return any(_mentions(v, reference) for v in value)
    return False


def _choose_png(parent: QWidget, start: str) -> str | None:
    path, _ = QFileDialog.getOpenFileName(parent, "Embed an image", start,
                                          "PNG images (*.png)")
    return path or None


def _buttons(*specs: tuple[str, Callable[[], None]]) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    for text, slot in specs:
        button = QPushButton(text)
        button.clicked.connect(slot)
        layout.addWidget(button)
    layout.addStretch(1)
    return holder


def _centered(widget: QWidget) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(widget)
    return holder


def _checked(widget: QWidget | None) -> bool:
    box = widget.findChild(QCheckBox) if widget is not None else None
    return box.isChecked() if box is not None else False


def _text(item: QTableWidgetItem | None) -> str:
    return item.text() if item is not None else ""

