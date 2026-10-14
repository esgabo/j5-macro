"""The run dialog: the script's params and the run's options, before a run starts.

Built from the params the script declares (:mod:`j5_macro.gui.models.params`): a field
per param with its type, default and description; secret params in password fields,
whose text is never shown, remembered or logged (the dialog clears them once it is
done). Below them, how to run (dry run, run, debug) and the options of ``run``: strict
(dry runs), allow ``exec`` (asks first, since it starts programs), seed, captures and
display scale.

The dialog only collects and checks; :class:`~j5_macro.gui.views.run_panel.RunPanel`
shows it and starts the run with :meth:`RunDialog.outcome`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.models.params import ParamSpec, collect_params, rememberable
from j5_macro.gui.models.run_options import (
    CAPTURES,
    KINDS,
    RunKind,
    RunOptions,
    parse_display_scale,
    parse_seed,
)

ERROR_STYLE = "color: #b00020;"
NOTE_STYLE = "color: gray;"
EXEC_WARNING = ("<b>exec</b> steps start programs on this computer with your permissions. "
                "Allow it only for scripts you trust.")


class RunDialog(QDialog):
    def __init__(self, title: str, specs: Sequence[ParamSpec], texts: Mapping[str, str],
                 options: RunOptions, kind: str = "normal",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Run {title}")
        self.setObjectName("runDialog")
        self._specs = list(specs)
        self._fields: dict[str, QLineEdit | QComboBox] = {}
        self._errors: dict[str, QLabel] = {}
        self._outcome: tuple[RunKind, dict[str, Any], RunOptions] | None = None
        self._texts: dict[str, str] = {}

        layout = QVBoxLayout(self)
        heading = QLabel(f"<b>{_escape(title)}</b>")
        layout.addWidget(heading)
        if self._specs:
            layout.addWidget(self._params_box(texts), 1)
        else:
            note = QLabel("This script declares no params.")
            note.setStyleSheet(NOTE_STYLE)
            layout.addWidget(note)
        layout.addWidget(self._options_box(options, kind))
        self.problem = QLabel()
        self.problem.setStyleSheet(ERROR_STYLE)
        self.problem.setWordWrap(True)
        self.problem.hide()
        layout.addWidget(self.problem)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.start_button = self.buttons.addButton(
            "Start", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start_button.setDefault(True)
        self.buttons.accepted.connect(self.try_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._kind_changed()
        self.resize(520, self.sizeHint().height())
        self._focus_first()

    # -- building ------------------------------------------------------------------------

    def _params_box(self, texts: Mapping[str, str]) -> QWidget:
        box = QGroupBox("Params")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for spec in self._specs:
            field = self._field(spec, texts.get(spec.name, ""))
            self._fields[spec.name] = field
            label = QLabel(_label(spec))
            label.setToolTip(spec.description or spec.name)
            column = QVBoxLayout()
            column.setSpacing(2)
            column.addWidget(field)
            if spec.description:
                note = QLabel(spec.description)
                note.setStyleSheet(NOTE_STYLE)
                note.setWordWrap(True)
                column.addWidget(note)
            error = QLabel()
            error.setStyleSheet(ERROR_STYLE)
            error.hide()
            self._errors[spec.name] = error
            column.addWidget(error)
            holder = QWidget()
            holder.setLayout(column)
            column.setContentsMargins(0, 0, 0, 0)
            form.addRow(label, holder)
        if len(self._specs) <= 6:
            return box
        scroll = QScrollArea()
        scroll.setWidget(box)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(320)
        return scroll

    def _field(self, spec: ParamSpec, text: str) -> QLineEdit | QComboBox:
        if spec.type == "boolean" and not spec.secret:
            combo = QComboBox()
            if not spec.required:
                combo.addItem(f"default ({spec.default_text or 'none'})", "")
            else:
                combo.addItem("choose…", "")
            combo.addItem("true", "true")
            combo.addItem("false", "false")
            index = combo.findData(text.strip().lower())
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.setObjectName(f"param_{spec.name}")
            return combo
        edit = QLineEdit()
        edit.setObjectName(f"param_{spec.name}")
        edit.setPlaceholderText(spec.hint + ("" if spec.type == "string" else
                                             f" ({spec.type})"))
        if spec.secret:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setToolTip("Secret: never shown, saved or logged")
        else:
            edit.setText(text)
        return edit

    def _options_box(self, options: RunOptions, kind: str) -> QWidget:
        box = QGroupBox("Run")
        form = QFormLayout(box)
        self.kind_combo = QComboBox()
        for key, item in KINDS.items():
            self.kind_combo.addItem(_kind_label(item), key)
        self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(kind)))
        self.kind_combo.currentIndexChanged.connect(self._kind_changed)
        form.addRow("Mode", self.kind_combo)
        self.strict_box = QCheckBox("Strict: fail where the real run would fail")
        self.strict_box.setChecked(options.strict)
        form.addRow("", self.strict_box)
        self.exec_box = QCheckBox("Allow exec steps to start programs")
        self.exec_box.setChecked(options.allow_exec)
        self.exec_box.clicked.connect(self._exec_clicked)
        self.exec_warning = QLabel(EXEC_WARNING)
        self.exec_warning.setWordWrap(True)
        self.exec_warning.setStyleSheet("color: #a06000;")
        self.exec_warning.setVisible(options.allow_exec)
        exec_column = QVBoxLayout()
        exec_column.setContentsMargins(0, 0, 0, 0)
        exec_column.addWidget(self.exec_box)
        exec_column.addWidget(self.exec_warning)
        exec_holder = QWidget()
        exec_holder.setLayout(exec_column)
        form.addRow("", exec_holder)
        self.seed_edit = QLineEdit("" if options.seed is None else str(options.seed))
        self.seed_edit.setPlaceholderText("random (reported in the log)")
        self.seed_edit.setToolTip("The same seed gives the same human-like movement and "
                                  "typing")
        form.addRow("Seed", self.seed_edit)
        self.captures_combo = QComboBox()
        for value in CAPTURES:
            self.captures_combo.addItem(
                "failed image searches only" if value == "on_failure"
                else "every image search attempt", value)
        self.captures_combo.setCurrentIndex(max(0, self.captures_combo.findData(
            options.captures)))
        form.addRow("Captures", self.captures_combo)
        self.scale_edit = QLineEdit("" if options.display_scale is None
                                    else f"{options.display_scale:g}")
        self.scale_edit.setPlaceholderText("detected (e.g. 1.5 or 150%)")
        form.addRow("Display scale", self.scale_edit)
        return box

    # -- use -----------------------------------------------------------------------------

    def field(self, name: str) -> QLineEdit | QComboBox:
        return self._fields[name]

    def set_text(self, name: str, text: str) -> None:
        """Fill a param's field (as the user would)."""
        widget = self._fields[name]
        if isinstance(widget, QComboBox):
            widget.setCurrentIndex(max(0, widget.findData(text)))
        else:
            widget.setText(text)

    def error_text(self, name: str) -> str:
        """The problem shown under a param's field ('' when none)."""
        label = self._errors[name]
        return label.text() if label.isVisibleTo(self) else ""

    def set_kind(self, kind: str) -> None:
        self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(kind)))

    def texts(self) -> dict[str, str]:
        texts: dict[str, str] = {}
        for name, widget in self._fields.items():
            if isinstance(widget, QComboBox):
                data = widget.currentData()
                texts[name] = data if isinstance(data, str) else ""
            else:
                texts[name] = widget.text()
        return texts

    def try_accept(self) -> bool:
        """Check the fields; accept the dialog when they are valid (else show why)."""
        texts = self.texts()
        params, errors = collect_params(self._specs, texts)
        problems: list[str] = []
        for name, label in self._errors.items():
            label.setText(errors.get(name, ""))
            label.setVisible(name in errors)
        if errors:
            problems.append("Check the params marked in red.")
        try:
            seed = parse_seed(self.seed_edit.text())
        except ValueError as exc:
            problems.append(str(exc))
            seed = None
        try:
            scale = parse_display_scale(self.scale_edit.text())
        except ValueError as exc:
            problems.append(str(exc))
            scale = None
        self.problem.setText(" ".join(problems))
        self.problem.setVisible(bool(problems))
        if problems:
            return False
        kind = KINDS[str(self.kind_combo.currentData())]
        captures = self.captures_combo.currentData()
        options = RunOptions(strict=self.strict_box.isChecked(),
                             allow_exec=self.exec_box.isChecked(), seed=seed,
                             captures=captures if isinstance(captures, str) else "on_failure",
                             display_scale=scale)
        self._outcome = (kind, params, options)
        self._texts = rememberable(self._specs, texts)
        self.accept()
        return True

    def outcome(self) -> tuple[RunKind, dict[str, Any], RunOptions] | None:
        """``(kind, params, options)`` once accepted, else None. The params include the
        secrets: pass them to the run and drop them."""
        return self._outcome

    def remembered_texts(self) -> dict[str, str]:
        """The non-secret texts to remember for the next run."""
        return dict(self._texts)

    def done(self, result: int) -> None:
        # Whatever happens, no secret stays in a widget once the dialog is closed.
        for spec in self._specs:
            widget = self._fields.get(spec.name)
            if spec.secret and isinstance(widget, QLineEdit):
                widget.clear()
        super().done(result)

    def forget(self) -> None:
        """Drop the outcome (its params may hold secrets)."""
        self._outcome = None

    # -- internals -----------------------------------------------------------------------

    def _kind_changed(self) -> None:
        kind = KINDS.get(str(self.kind_combo.currentData()))
        dry = kind is not None and kind.mode == "dry"
        self.strict_box.setEnabled(dry)
        self.start_button.setText(kind.label if kind is not None else "Start")

    def _exec_clicked(self, checked: bool) -> None:
        if checked and not self.confirm_exec():
            self.exec_box.setChecked(False)
        self.exec_warning.setVisible(self.exec_box.isChecked())

    def confirm_exec(self) -> bool:
        """Ask before allowing ``exec`` (a separate method, so tests can replace it)."""
        answer = QMessageBox.warning(
            self, "Allow exec?", EXEC_WARNING + "<br><br>Allow exec for this run?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _focus_first(self) -> None:
        for spec in self._specs:
            widget = self._fields[spec.name]
            if spec.required or spec.secret:
                widget.setFocus(Qt.FocusReason.OtherFocusReason)
                return


def _label(spec: ParamSpec) -> str:
    traits = [spec.type] if spec.type != "string" else []
    if spec.secret:
        traits.append("secret")
    text = spec.name + (" *" if spec.required else "")
    return f"{text} ({', '.join(traits)})" if traits else text


def _kind_label(kind: RunKind) -> str:
    if kind.key == "dry":
        return "Dry run: describe, send no input"
    if kind.key == "normal":
        return "Run: send real input"
    if kind.key == "debug":
        return "Debug: pause at the first step, send real input"
    return "Debug a dry run: pause at the first step, send no input"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
