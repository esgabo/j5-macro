"""The form of the selected step, generated from the step registry
(``list_step_types``): a field per parameter, chosen by its type
(:func:`~j5_macro.gui.models.fields.field_kind`), then the common fields (id, timeout,
on error, keep the result as, break, input profile).

Changes are checked as they are typed (a problem shows under the field and nothing is
sent), then sent through the :class:`EditQueue` as one ``update`` edit once typing
pauses (:data:`DEBOUNCE_MS`), or at once for a check box or a choice. The engine's
validation problems of the step (``get_script``'s ``errors``) show under their fields.
The form follows the script as it is reloaded, except in the fields being edited and
while the script is invalid (the engine then reports its last valid version, which
would undo what was just typed).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.context import AppContext
from j5_macro.gui.controllers.edit_queue import EditQueue
from j5_macro.gui.models.edits import update_edit
from j5_macro.gui.models.fields import (
    TARGET_EXTRAS,
    TARGET_PARAMS,
    Parsed,
    field_kind,
    has_target,
    parse_duration_text,
    step_problems,
)
from j5_macro.gui.models.header import image_names
from j5_macro.gui.models.registry import ParamInfo, StepRegistry, StepType
from j5_macro.gui.models.script import ScriptState
from j5_macro.gui.models.steps import StepItem
from j5_macro.gui.views.fields import (
    ERROR_STYLE,
    HINT_STYLE,
    BoolField,
    ChoiceField,
    Field,
    FieldEnv,
    LineField,
    ProfileField,
    TargetField,
    create_field,
)

DEBOUNCE_MS = 500
"""How long typing must pause before a change is sent."""
ON_ERROR_CHOICES = ("stop", "continue", "retry", "pause")


class AsField(Field):
    """The common ``as``: the variable that keeps the step's result, and whether it is a
    secret."""

    def __init__(self, env: FieldEnv | None = None) -> None:
        super().__init__(["as"], env)
        self.name = QLineEdit()
        self.name.setPlaceholderText("variable name (none)")
        self.secret = QCheckBox("secret")
        self.secret.setToolTip("Mask the result's text in events, logs and history")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.name, 1)
        layout.addWidget(self.secret)
        self.name.textEdited.connect(lambda _: self._emit())
        self.secret.toggled.connect(lambda _: self._emit(True))

    def _show(self, params: Mapping[str, Any]) -> None:
        value = params.get("as")
        if isinstance(value, Mapping):
            self.name.setText(str(value.get("name") or ""))
            self.secret.setChecked(value.get("secret") is True)
        else:
            self.name.setText("" if value is None else str(value))
            self.secret.setChecked(False)

    def _read(self) -> Parsed:
        name = self.name.text().strip()
        if not name:
            return Parsed({"as": None})
        if not name.isidentifier():
            return Parsed(error="a variable name: letters, digits and _, not starting "
                                "with a digit")
        return Parsed({"as": {"name": name, "secret": True} if self.secret.isChecked()
                       else name})


class _Row:
    """A field in the form with its label and problem label."""

    def __init__(self, field: Field, label: QLabel, problem: QLabel, common: bool) -> None:
        self.field = field
        self.label = label
        self.problem = problem
        self.common = common


class StepFormPanel(QWidget):
    renamed = Signal(str, str)
    """``(old id, new id)``: the step's id was changed from the form."""

    def __init__(self, ctx: AppContext | None = None, queue: EditQueue | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._queue = queue
        self._registry = StepRegistry()
        self._step: StepItem | None = None
        self._state: ScriptState | None = None
        self._key: tuple[Any, ...] | None = None
        self._rows: list[_Row] = []
        self._by_key: dict[tuple[bool, str], _Row] = {}
        self._dirty: set[int] = set()

        self._title = QLabel()
        self._title.setWordWrap(True)
        self._title.setTextFormat(Qt.TextFormat.RichText)
        self._doc = QLabel()
        self._doc.setWordWrap(True)
        self._doc.setStyleSheet(HINT_STYLE)
        self._problems = QLabel()
        self._problems.setObjectName("stepProblems")
        self._problems.setWordWrap(True)
        self._problems.setTextFormat(Qt.TextFormat.PlainText)
        self._problems.setStyleSheet(ERROR_STYLE)
        self._problems.hide()
        self._body = QWidget()
        self._form = QFormLayout(self._body)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self._form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._body)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._title)
        layout.addWidget(self._doc)
        layout.addWidget(self._problems)
        layout.addWidget(scroll, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self.flush)
        self.show_step(None)

    # -- public --------------------------------------------------------------------------

    @property
    def step(self) -> StepItem | None:
        return self._step

    def set_registry(self, registry: StepRegistry) -> None:
        self._registry = registry
        self._key = None
        self.show_step(self._step, self._state)

    def field(self, name: str, *, common: bool = False) -> Field | None:
        """The field that edits parameter ``name`` (or common field, with ``common``)."""
        row = self._by_key.get((common, name))
        return row.field if row is not None else None

    def problem_text(self, name: str, *, common: bool = False) -> str:
        """The problem shown under the field of ``name`` (empty when none)."""
        row = self._by_key.get((common, name))
        return row.problem.text() if row is not None else ""

    def general_problems(self) -> str:
        return self._problems.text()

    def pending(self) -> bool:
        """Changes are waiting to be sent."""
        return bool(self._dirty)

    def show_step(self, step: StepItem | None, state: ScriptState | None = None) -> None:
        """Show ``step`` of the script ``state``; the same step again only refreshes
        the fields that are not being edited."""
        if self._dirty and self._step is not None and (
                step is None or step.id != self._step.id):
            self.flush()
        self._step = step
        self._state = state
        if step is None:
            self._clear()
            self._key = None
            self._title.setText("Select a step to edit it, or insert one from the palette.")
            self._doc.setText("")
            self._show_problems([])
            return
        names = tuple(image_names(state.images)) if state is not None else ()
        key = (step.id, step.type, names, state.path if state else None, id(self._registry))
        if key != self._key:
            if self._dirty:
                self.flush()  # what was typed is sent before the form is rebuilt
            self._build(step, state)
            self._key = key
        elif state is None or state.valid:
            self._refresh(step)
        line = f", line {step.line}" if step.line is not None else ""
        self._title.setText(f"<b>{_escape(step.id)}</b> ({_escape(step.type)}{line})")
        self._show_engine_problems()

    def flush(self) -> None:
        """Send the pending changes now."""
        self._timer.stop()
        step = self._step
        if step is None or not self._dirty:
            self._dirty.clear()
            return
        params: dict[str, Any] = {}
        common: dict[str, Any] = {}
        sent: list[_Row] = []
        for row in self._rows:
            if id(row) not in self._dirty:
                continue
            changes = row.field.changes()
            if not changes.ok:
                self._set_problem(row, changes.error or "")
                continue
            self._set_problem(row, "")
            (common if row.common else params).update(changes.value)
            sent.append(row)
        self._dirty.clear()
        if not params and not common:
            return
        if "id" in common and not common["id"]:
            common.pop("id")
        for row in sent:
            row.field.accept()
        if self._ctx is None or self._ctx.document.state is None:
            return
        old_id = step.id
        new_id = common.get("id")
        edit = update_edit(old_id, params, common)

        def done(result: Mapping[str, Any]) -> None:
            if isinstance(new_id, str) and new_id != old_id:
                self.renamed.emit(old_id, new_id)

        if self._queue is not None:
            self._queue.submit([edit], on_result=done, on_error=self._edit_failed)
        else:
            self._ctx.document.edit([edit], on_result=done,
                                    on_error=lambda exc: self._edit_failed(str(exc)))

    # -- building ------------------------------------------------------------------------

    def _clear(self) -> None:
        self._timer.stop()
        self._dirty.clear()
        while self._form.rowCount():
            self._form.removeRow(0)
        self._rows.clear()
        self._by_key.clear()

    def _build(self, step: StepItem, state: ScriptState | None) -> None:
        self._clear()
        step_type = self._registry.get(step.type)
        self._doc.setText(step_type.doc if step_type is not None else
                          "This step type is not in the engine's registry.")
        env = FieldEnv(
            ctx=self._ctx, step_id=step.id, step_type=step.type,
            image_names=image_names(state.images) if state is not None else (),
            script_dir=os.path.dirname(state.path) if state and state.path else None,
            step_params=self.current_params, window=self._window)
        if step_type is not None:
            self._add_params(step_type, env)
        else:  # unknown type: show the parameters as they are, read-only
            for name, value in step.params.items():
                label = QLabel(str(value))
                label.setWordWrap(True)
                self._form.addRow(f"{name}:", label)
        self._add_heading("Step options")
        self._add(LineField("id", _parse_id, env=env, formatter=lambda v: str(v or "")),
                  "id", "The step's id: breakpoints and the run history refer to it",
                  common=True)
        self._add(LineField("timeout", parse_duration_text, env=env,
                            placeholder="none (e.g. 10s)"), "timeout",
                  "How long waits and image searches may take (replaces the step's own "
                  "timeout)", common=True)
        self._add(ChoiceField("on_error", ON_ERROR_CHOICES, default="stop", env=env),
                  "on error",
                  "What to do when the step fails: stop the run (the default), continue, "
                  "retry or pause", common=True)
        self._add(AsField(env), "keep result as",
                  "Keep the step's result in a variable (as)", common=True)
        self._add(BoolField("break", label="pause here when debugging", env=env), "break",
                  "break: true in the script: a breakpoint saved with it", common=True)
        profile = ProfileField("input", env=env)
        self._add(profile, "input", "Input profile settings for this step only",
                  common=True)
        self._load_all(step)
        self._collapse(profile, "input" in step.common)

    def _add_params(self, step_type: StepType, env: FieldEnv) -> None:
        names = [p.name for p in step_type.params]
        used: set[str] = set()
        if has_target(names):
            keys = [k for k in (*TARGET_PARAMS, *TARGET_EXTRAS) if k in names]
            defaults = {p.name: p.default for p in step_type.params}
            target = TargetField(keys, defaults=defaults, env=env)
            self._add(target, "target", "What the step aims at: an image, a position or "
                                        "a result kept with 'as'")
            for key in keys:
                self._by_key[(False, key)] = self._rows[-1]
            used.update(keys)
        for param in step_type.params:
            if param.name in used:
                continue
            kind = field_kind(step_type.name, param)
            self._add(create_field(step_type.name, param, kind, env), _label(param),
                      _tooltip(param))

    def _collapse(self, field: Field, expanded: bool) -> None:
        """Hide ``field`` behind a toggle button (shown expanded when it has a value)."""
        row = self._by_key[(True, field.keys[0])]
        toggle = QToolButton()
        toggle.setText("override for this step")
        toggle.setCheckable(True)
        toggle.setChecked(expanded)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        holder = field.parentWidget()
        assert holder is not None
        layout = holder.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.insertWidget(0, toggle)
        field.setVisible(expanded)

        def toggled(on: bool) -> None:
            toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
            field.setVisible(on)

        toggle.toggled.connect(toggled)
        row.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _add_heading(self, text: str) -> None:
        heading = QLabel(f"<b>{text}</b>")
        heading.setContentsMargins(0, 8, 0, 0)
        self._form.addRow(heading)

    def _add(self, field: Field, label: str, tooltip: str, *, common: bool = False) -> None:
        title = QLabel(label)
        title.setToolTip(tooltip)
        field.setToolTip(tooltip)
        problem = QLabel()
        problem.setWordWrap(True)
        problem.setStyleSheet(ERROR_STYLE)
        problem.setTextFormat(Qt.TextFormat.PlainText)
        problem.hide()
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(1)
        box.addWidget(field)
        box.addWidget(problem)
        self._form.addRow(title, holder)
        row = _Row(field, title, problem, common)
        self._rows.append(row)
        for key in field.keys:
            self._by_key[(common, key)] = row
        field.edited.connect(lambda now, r=row: self._edited(r, now))

    def _common_values(self, step: StepItem) -> dict[str, Any]:
        values: dict[str, Any] = {"id": step.id, **step.common}
        values["break"] = True if step.breakpoint else None
        return values

    def _load_all(self, step: StepItem) -> None:
        common = self._common_values(step)
        for row in self._rows:
            row.field.load(common if row.common else step.params)

    def _refresh(self, step: StepItem) -> None:
        common = self._common_values(step)
        for row in self._rows:
            if id(row) in self._dirty or row.field.busy():
                continue
            row.field.load(common if row.common else step.params)

    # -- editing -------------------------------------------------------------------------

    def _edited(self, row: _Row, now: bool) -> None:
        parsed = row.field.values()
        self._set_problem(row, "" if parsed.ok else (parsed.error or ""))
        self._dirty.add(id(row))
        if now:
            self.flush()
        else:
            self._timer.start()

    def _edit_failed(self, message: str) -> None:
        self._show_problems([message])

    def current_params(self) -> dict[str, Any]:
        """The step's parameters with what the form shows now."""
        params = dict(self._step.params) if self._step is not None else {}
        for row in self._rows:
            if row.common:
                continue
            parsed = row.field.values()
            if parsed.ok:
                params.update({k: v for k, v in parsed.value.items()})
        return {k: v for k, v in params.items() if v is not None}

    def _window(self) -> Mapping[str, Any] | None:
        window = self.current_params().get("window")
        return window if isinstance(window, Mapping) else None

    # -- problems ------------------------------------------------------------------------

    def _set_problem(self, row: _Row, text: str) -> None:
        row.problem.setText(text)
        row.problem.setVisible(bool(text))

    def _show_engine_problems(self) -> None:
        step, state = self._step, self._state
        if step is None:
            return
        by_field, general = step_problems(state.errors if state is not None else (), step.id)
        shown: set[int] = set()
        for name, messages in by_field.items():
            row = self._by_key.get((False, name)) or self._by_key.get((True, name))
            if row is None:
                general.extend(f"{name}: {m}" for m in messages)
                continue
            if id(row) in shown:
                row.problem.setText(row.problem.text() + "\n" + "\n".join(messages))
            else:
                self._set_problem(row, "\n".join(messages))
            shown.add(id(row))
        for row in self._rows:
            if id(row) not in shown and id(row) not in self._dirty:
                parsed = row.field.values()
                self._set_problem(row, "" if parsed.ok else (parsed.error or ""))
        self._show_problems(general)

    def _show_problems(self, messages: Sequence[str]) -> None:
        self._problems.setText("\n".join(messages))
        self._problems.setVisible(bool(messages))


def _parse_id(text: str) -> Parsed:
    text = text.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", text):
        return Parsed(error="letters, digits, '_' or '-', starting with a letter or '_'")
    return Parsed(text)


def _label(param: ParamInfo) -> str:
    return f"{param.name} *" if param.required else param.name


def _tooltip(param: ParamInfo) -> str:
    text = param.doc
    if param.required:
        text += " (required)"
    elif param.default is not None:
        text += f" (default: {param.default})"
    return text


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
