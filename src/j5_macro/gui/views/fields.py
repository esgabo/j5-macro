"""The field widgets of the step form and the script header, and the hook that adds
screen tools (Capture, Pick, Test...) next to image and coordinate fields.

Every field edits one or more parameters of a step (``keys``): :meth:`Field.load` shows
them, :meth:`Field.values` reads them back as a :class:`Parsed` mapping (``None``
removes a parameter) or the problem to show, and :attr:`Field.edited` says the user
changed something (``True``: send it now, for a combo box or a check box; ``False``:
the form waits until typing pauses).

Field tools
-----------

Image fields (``image``), points (``point``: ``x``/``y``), regions (``region``) and
windows (``window``) call every registered :data:`FieldTools` factory with a
:class:`FieldContext` and put the widgets it returns (buttons, usually) next to the
field. Nothing is registered by default. A factory is registered once, at startup::

    from j5_macro.gui.views.fields import FieldContext, register_field_tools

    def capture_tools(context: FieldContext) -> list[QWidget]:
        if context.kind != "image":
            return []
        button = QPushButton("Capture")
        button.clicked.connect(lambda: ...)          # then context.set_value("#name")
        return [button]

    register_field_tools(capture_tools)

``context.set_value(value)`` shows the value in the field and sends the edit, as if
the user had typed it. Values: ``image`` a path or ``"#name"``; ``point`` ``{"x": int,
"y": int}``; ``region`` ``[x, y, w, h]`` (or an anchored mapping); ``window``
``{"title": str}`` or None.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.models.fields import (
    Parsed,
    format_lines,
    format_literal,
    format_offset,
    format_scalar,
    parse_anchored_region,
    parse_duration_text,
    parse_integer,
    parse_lines,
    parse_literal,
    parse_number,
    parse_offset,
    parse_rect,
    parse_scalar_text,
    parse_string,
    region_mode,
)
from j5_macro.gui.models.header import PROFILE_DEVICES, ProfileSetting, parse_setting
from j5_macro.gui.models.registry import ParamInfo
from j5_macro.script.images import REFERENCE_PREFIX

if TYPE_CHECKING:
    from j5_macro.gui.context import AppContext

ToolKind = Literal["image", "point", "region", "window"]
ERROR_STYLE = "color: #b00020;"
HINT_STYLE = "color: gray;"


# --- the field tools hook ---------------------------------------------------------------


@dataclass(slots=True)
class FieldContext:
    """What a field tools factory gets for one field."""

    kind: ToolKind
    """``image``, ``point``, ``region`` or ``window``."""
    param: str
    """The parameter as the engine names it: ``image``, ``from.image``, ``region``,
    ``x/y``, ``when.image_visible``..."""
    get_value: Callable[[], Any]
    """The field's current value (see the module docstring for the shapes)."""
    set_value: Callable[[Any], None]
    """Show a value in the field and send the edit."""
    ctx: AppContext | None = None
    """The application (``ctx.screen`` for the screen tools, ``ctx.document`` for the
    open script); None in a field built without one."""
    step_id: str | None = None
    step_type: str | None = None
    window: Callable[[], Mapping[str, Any] | None] = lambda: None
    """The window the field is relative to (the step's or the target's ``window``)."""
    step_params: Callable[[], Mapping[str, Any]] = lambda: {}
    """The step's parameters as the form shows them now (``threshold``, ``region``...)."""


class FieldTools(Protocol):
    def __call__(self, context: FieldContext) -> Sequence[QWidget]: ...


_FIELD_TOOLS: list[FieldTools] = []


def register_field_tools(factory: FieldTools) -> Callable[[], None]:
    """Add ``factory`` to every image, point, region and window field built from now on;
    returns a function that removes it again."""
    _FIELD_TOOLS.append(factory)

    def unregister() -> None:
        if factory in _FIELD_TOOLS:
            _FIELD_TOOLS.remove(factory)

    return unregister


def field_tools(context: FieldContext) -> list[QWidget]:
    """The widgets every registered factory makes for ``context``."""
    widgets: list[QWidget] = []
    for factory in list(_FIELD_TOOLS):
        widgets.extend(factory(context))
    return widgets


# --- environment ------------------------------------------------------------------------


@dataclass(slots=True)
class FieldEnv:
    """What fields need to know about where they are."""

    ctx: AppContext | None = None
    step_id: str | None = None
    step_type: str | None = None
    image_names: Sequence[str] = ()
    script_dir: str | None = None
    step_params: Callable[[], Mapping[str, Any]] = lambda: {}
    window: Callable[[], Mapping[str, Any] | None] = lambda: None
    choose_file: Callable[[QWidget, str], str | None] | None = None
    """Opens a file dialog (``(parent, start directory) -> path or None``); replaced
    in tests."""
    extra: dict[str, Any] = field(default_factory=dict)


def _choose_png(parent: QWidget, start: str) -> str | None:
    path, _ = QFileDialog.getOpenFileName(parent, "Choose an image", start,
                                          "PNG images (*.png);;All files (*)")
    return path or None


# --- base -------------------------------------------------------------------------------


class Field(QWidget):
    """A field of the form. Subclasses set :attr:`keys` and implement ``_show`` and
    ``_read``."""

    edited = Signal(bool)
    """The user changed the value; ``True`` to send it at once."""

    def __init__(self, keys: Sequence[str], env: FieldEnv | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.keys = tuple(keys)
        self.env = env or FieldEnv()
        self._loading = False
        self._baseline: dict[str, Any] = {}

    def load(self, params: Mapping[str, Any]) -> None:
        """Show these parameters (a step's ``params``)."""
        self._loading = True
        try:
            self._show(params)
        finally:
            self._loading = False
        parsed = self.values()
        self._baseline = dict(parsed.value) if parsed.ok else {}

    def values(self) -> Parsed:
        """``{key: value}`` for :attr:`keys`, or the problem."""
        return self._read()

    def changes(self) -> Parsed:
        """Only the keys whose value differs from what :meth:`load` showed."""
        parsed = self.values()
        if not parsed.ok:
            return parsed
        changed = {k: v for k, v in parsed.value.items()
                   if k not in self._baseline or not _same(self._baseline[k], v)}
        return Parsed(changed)

    def accept(self) -> None:
        """The changes were sent: they are the new baseline."""
        parsed = self.values()
        if parsed.ok:
            self._baseline.update(parsed.value)

    def busy(self) -> bool:
        """The user is typing in it (it has the keyboard focus)."""
        focus = self.focusWidget()
        return focus is not None and focus.hasFocus()

    def _emit(self, now: bool = False) -> None:
        if not self._loading:
            self.edited.emit(now)

    def _show(self, params: Mapping[str, Any]) -> None:
        raise NotImplementedError

    def _read(self) -> Parsed:
        raise NotImplementedError


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return bool(a == b)


def _line_edit(placeholder: str = "") -> QLineEdit:
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(False)
    return edit


def _row(*widgets: QWidget, stretch_last: bool = False) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    for widget in widgets:
        layout.addWidget(widget)
    if not stretch_last:
        layout.addStretch(1)
    return holder


def _labelled(label: str, widget: QWidget) -> QWidget:
    text = QLabel(label)
    text.setStyleSheet(HINT_STYLE)
    return _row(text, widget, stretch_last=True)


def _tools(kind: ToolKind, param: str, env: FieldEnv, get_value: Callable[[], Any],
           set_value: Callable[[Any], None],
           window: Callable[[], Mapping[str, Any] | None] | None = None) -> list[QWidget]:
    return field_tools(FieldContext(
        kind=kind, param=param, get_value=get_value, set_value=set_value, ctx=env.ctx,
        step_id=env.step_id, step_type=env.step_type, window=window or env.window,
        step_params=env.step_params))


# --- simple fields ----------------------------------------------------------------------


Parser = Callable[[str], Parsed]


class LineField(Field):
    """One line of text, parsed by ``parser`` (strings, numbers, durations,
    expressions)."""

    def __init__(self, key: str, parser: Parser, *, default: Any = None,
                 placeholder: str = "", env: FieldEnv | None = None,
                 formatter: Callable[[Any], str] = format_scalar) -> None:
        super().__init__([key], env)
        self.default = default
        self.parser = parser
        self.formatter = formatter
        self.edit = _line_edit(placeholder or (
            f"default: {formatter(default)}" if default is not None else ""))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit)
        self.edit.textEdited.connect(lambda _: self._emit())
        self.edit.editingFinished.connect(lambda: self._emit(True))

    def _show(self, params: Mapping[str, Any]) -> None:
        value = params.get(self.keys[0])
        if value is not None and self.default is not None and _same(value, self.default):
            value = None
        self.edit.setText(self.formatter(value))

    def _read(self) -> Parsed:
        parsed = self.parser(self.edit.text())
        return Parsed({self.keys[0]: parsed.value}) if parsed.ok else parsed


class TextField(Field):
    """Several lines of text (``type``'s ``text``)."""

    def __init__(self, key: str, *, required: bool = False,
                 env: FieldEnv | None = None) -> None:
        super().__init__([key], env)
        self.required = required
        self.edit = QPlainTextEdit()
        self.edit.setTabChangesFocus(True)
        self.edit.setFixedHeight(64)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit)
        self.edit.textChanged.connect(lambda: self._emit())

    def _show(self, params: Mapping[str, Any]) -> None:
        value = params.get(self.keys[0])
        self.edit.setPlainText("" if value is None else str(value))

    def _read(self) -> Parsed:
        parsed = parse_string(self.edit.toPlainText(), required=self.required)
        return Parsed({self.keys[0]: parsed.value})


class BoolField(Field):
    def __init__(self, key: str, *, default: Any = None, label: str = "",
                 env: FieldEnv | None = None) -> None:
        super().__init__([key], env)
        self.default = default if isinstance(default, bool) else False
        self.box = QCheckBox(label)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.box)
        layout.addStretch(1)
        self.box.toggled.connect(lambda _: self._emit(True))

    def _show(self, params: Mapping[str, Any]) -> None:
        value = params.get(self.keys[0])
        self.box.setChecked(value if isinstance(value, bool) else self.default)

    def _read(self) -> Parsed:
        checked = self.box.isChecked()
        return Parsed({self.keys[0]: None if checked == self.default else checked})


class ChoiceField(Field):
    """One of a fixed set; the first entry is the default (or nothing)."""

    def __init__(self, key: str, choices: Sequence[Any], *, default: Any = None,
                 env: FieldEnv | None = None) -> None:
        super().__init__([key], env)
        self.default = default
        self.combo = QComboBox()
        self.combo.addItem(f"(default: {format_scalar(default)})"
                           if default is not None else "(none)", None)
        for choice in choices:
            self.combo.addItem(format_scalar(choice), choice)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo)
        layout.addStretch(1)
        self.combo.activated.connect(lambda _: self._emit(True))

    def _show(self, params: Mapping[str, Any]) -> None:
        value = params.get(self.keys[0])
        if value is not None and self.default is not None and _same(value, self.default):
            value = None
        index = next((i for i in range(self.combo.count())
                      if _same(self.combo.itemData(i), value)), -1)
        if index < 0:  # an interpolated value or one the registry does not list
            self.combo.addItem(format_scalar(value), value)
            index = self.combo.count() - 1
        self.combo.setCurrentIndex(index)

    def _read(self) -> Parsed:
        return Parsed({self.keys[0]: self.combo.currentData()})


class AnyField(LineField):
    """Any value as JSON; plain text is a string."""

    def __init__(self, key: str, *, env: FieldEnv | None = None) -> None:
        super().__init__(key, parse_literal, env=env, formatter=format_literal,
                         placeholder='text, a number, true, [1, 2] or {"a": 1}')


# --- images and windows -----------------------------------------------------------------


class ImageEdit(QWidget):
    """An image: a ``#name`` of the script's embedded images (listed) or a file path,
    with "File…" and the registered field tools."""

    changed = Signal(bool)

    def __init__(self, param: str, env: FieldEnv,
                 window: Callable[[], Mapping[str, Any] | None] | None = None) -> None:
        super().__init__()
        self.env = env
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo.setMinimumContentsLength(10)
        self.combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        line = self.combo.lineEdit()
        assert line is not None
        line.setPlaceholderText("#name or img/file.png")
        for name in env.image_names:
            self.combo.addItem(f"{REFERENCE_PREFIX}{name}")
        self.browse = QPushButton("File…")
        self.browse.setToolTip("Choose a PNG file (saved relative to the script)")
        self.browse.clicked.connect(self.choose_file)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.combo, 1)
        layout.addWidget(self.browse)
        for widget in _tools("image", param, env, self.text, self.set_and_send, window):
            layout.addWidget(widget)
        line.textEdited.connect(lambda _: self.changed.emit(False))
        self.combo.activated.connect(lambda _: self.changed.emit(True))

    def text(self) -> str:
        return self.combo.currentText().strip()

    def set_text(self, text: str) -> None:
        self.combo.setEditText(text)

    def set_and_send(self, value: Any) -> None:
        self.set_text("" if value is None else str(value))
        self.changed.emit(True)

    def choose_file(self) -> None:
        start = self.env.script_dir or os.getcwd()
        chooser = self.env.choose_file or _choose_png
        path = chooser(self, start)
        if path:
            self.use_file(path)

    def use_file(self, path: str) -> None:
        """Refer to ``path``, relative to the script's folder when it is inside it."""
        value = path
        if self.env.script_dir:
            try:
                relative = os.path.relpath(path, self.env.script_dir)
            except ValueError:  # another drive on Windows
                relative = path
            if not relative.startswith(".."):
                value = relative.replace(os.sep, "/")
        self.set_and_send(value)


class ImageField(Field):
    def __init__(self, key: str, *, required: bool = False, env: FieldEnv | None = None,
                 param: str | None = None) -> None:
        super().__init__([key], env)
        self.required = required
        self.image = ImageEdit(param or key, self.env)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.image)
        self.image.changed.connect(self._emit)

    def _show(self, params: Mapping[str, Any]) -> None:
        value = params.get(self.keys[0])
        self.image.set_text("" if value is None else str(value))

    def _read(self) -> Parsed:
        text = self.image.text()
        if not text:
            if self.required:
                return Parsed(error="choose an image")
            return Parsed({self.keys[0]: None})
        return Parsed({self.keys[0]: text})


class WindowEdit(QWidget):
    """``{title}``: the first visible window whose title contains it."""

    changed = Signal(bool)

    def __init__(self, param: str, env: FieldEnv) -> None:
        super().__init__()
        self.edit = _line_edit("title contains… (empty: the whole desktop)")
        self.edit.setToolTip("Coordinates and regions become relative to the first visible "
                             "window whose title contains this text")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.edit, 1)
        for widget in _tools("window", param, env, self.value, self.set_and_send):
            layout.addWidget(widget)
        self.edit.textEdited.connect(lambda _: self.changed.emit(False))

    def value(self) -> dict[str, Any] | None:
        title = self.edit.text()
        return {"title": title} if title.strip() else None

    def set_value(self, value: Any) -> None:
        title = value.get("title") if isinstance(value, Mapping) else None
        self.edit.setText("" if title is None else str(title))

    def set_and_send(self, value: Any) -> None:
        self.set_value(value)
        self.changed.emit(True)


class WindowField(Field):
    def __init__(self, key: str, *, env: FieldEnv | None = None,
                 param: str | None = None) -> None:
        super().__init__([key], env)
        self.window_edit = WindowEdit(param or key, self.env)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.window_edit)
        self.window_edit.changed.connect(self._emit)

    def _show(self, params: Mapping[str, Any]) -> None:
        self.window_edit.set_value(params.get(self.keys[0]))

    def _read(self) -> Parsed:
        value = self.window_edit.value()
        if value is not None:
            current = self._baseline.get(self.keys[0])
            if isinstance(current, Mapping):  # keep keys this field does not show
                value = {**current, **value}
        return Parsed({self.keys[0]: value})


# --- points, offsets and regions --------------------------------------------------------


class PointEdit(QWidget):
    """``x`` and ``y`` (whole pixels, or ``${...}``), with the field tools."""

    changed = Signal(bool)

    def __init__(self, param: str, env: FieldEnv,
                 window: Callable[[], Mapping[str, Any] | None] | None = None) -> None:
        super().__init__()
        self.x_edit = _line_edit("x")
        self.y_edit = _line_edit("y")
        for edit in (self.x_edit, self.y_edit):
            edit.setMaximumWidth(90)
            edit.textEdited.connect(lambda _: self.changed.emit(False))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(_labelled("x", self.x_edit))
        layout.addWidget(_labelled("y", self.y_edit))
        for widget in _tools("point", param, env, self.value, self.set_and_send, window):
            layout.addWidget(widget)
        layout.addStretch(1)

    def value(self) -> dict[str, Any]:
        return {"x": parse_integer(self.x_edit.text()).value,
                "y": parse_integer(self.y_edit.text()).value}

    def read(self) -> Parsed:
        x, y = parse_integer(self.x_edit.text()), parse_integer(self.y_edit.text())
        for label, parsed in (("x", x), ("y", y)):
            if not parsed.ok or parsed.value is None:
                return Parsed(error=f"{label}: expected a whole number")
        return Parsed({"x": x.value, "y": y.value})

    def set_value(self, value: Any) -> None:
        mapping = value if isinstance(value, Mapping) else {}
        self.x_edit.setText(format_scalar(mapping.get("x")))
        self.y_edit.setText(format_scalar(mapping.get("y")))

    def set_and_send(self, value: Any) -> None:
        self.set_value(value)
        self.changed.emit(True)


class OffsetEdit(QWidget):
    """``[dx, dy]``: pixels, or multiples of the target's size (``0.5w``, ``-2h``)."""

    changed = Signal(bool)

    def __init__(self, first: str = "dx", second: str = "dy") -> None:
        super().__init__()
        self.dx = _line_edit("0")
        self.dy = _line_edit("0")
        for edit in (self.dx, self.dy):
            edit.setMaximumWidth(70)
            edit.setToolTip("Pixels, or a multiple of the target's width or height such "
                            "as 0.5w or -2h")
            edit.textEdited.connect(lambda _: self.changed.emit(False))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(_labelled(first, self.dx))
        layout.addWidget(_labelled(second, self.dy))
        layout.addStretch(1)

    def read(self) -> Parsed:
        return parse_offset(self.dx.text(), self.dy.text())

    def texts(self) -> tuple[str, str]:
        return self.dx.text(), self.dy.text()

    def set_value(self, value: Any) -> None:
        dx, dy = format_offset(value)
        self.dx.setText(dx)
        self.dy.setText(dy)


class OffsetField(Field):
    def __init__(self, key: str, *, env: FieldEnv | None = None) -> None:
        super().__init__([key], env)
        self.offset = OffsetEdit()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.offset)
        self.offset.changed.connect(self._emit)

    def _show(self, params: Mapping[str, Any]) -> None:
        self.offset.set_value(params.get(self.keys[0]))

    def _read(self) -> Parsed:
        parsed = self.offset.read()
        return Parsed({self.keys[0]: parsed.value}) if parsed.ok else parsed


class RegionEdit(QWidget):
    """Where to search: everywhere (the window, else the desktop), a rectangle, or a
    rectangle next to a stored result."""

    changed = Signal(bool)
    MODES = (("none", "Everywhere"), ("rect", "Rectangle"), ("at", "Next to a result"))

    def __init__(self, param: str, env: FieldEnv,
                 window: Callable[[], Mapping[str, Any] | None] | None = None) -> None:
        super().__init__()
        self.mode = QComboBox()
        for mode, label in self.MODES:
            self.mode.addItem(label, mode)
        self.rect_edits = [_line_edit(label) for label in ("x", "y", "w", "h")]
        rect_row = QWidget()
        rect_layout = QHBoxLayout(rect_row)
        rect_layout.setContentsMargins(0, 0, 0, 0)
        rect_layout.setSpacing(4)
        for edit in self.rect_edits:
            edit.setMaximumWidth(60)
            rect_layout.addWidget(edit)
            edit.textEdited.connect(lambda _: self.changed.emit(False))
        for widget in _tools("region", param, env, self.value, self.set_and_send, window):
            rect_layout.addWidget(widget)
        rect_layout.addStretch(1)
        self.at = _line_edit("${label}")
        self.at.textEdited.connect(lambda _: self.changed.emit(False))
        self.at_offset = OffsetEdit()
        self.at_size = OffsetEdit("w", "h")
        for edit in (self.at_size.dx, self.at_size.dy):
            edit.setPlaceholderText("1w" if edit is self.at_size.dx else "1h")
        self.at_offset.changed.connect(self.changed)
        self.at_size.changed.connect(self.changed)
        at_box = QWidget()
        at_layout = QFormLayout(at_box)
        at_layout.setContentsMargins(0, 0, 0, 0)
        at_layout.addRow("result", self.at)
        at_layout.addRow("offset", self.at_offset)
        at_layout.addRow("size", self.at_size)
        self.stack = QStackedWidget()
        self.stack.addWidget(QWidget())
        self.stack.addWidget(rect_row)
        self.stack.addWidget(at_box)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(_row(self.mode))
        layout.addWidget(self.stack)
        self.mode.activated.connect(self._mode_chosen)
        self._sync_stack()

    def _mode_chosen(self) -> None:
        self._sync_stack()
        self.changed.emit(self.current_mode() == "none")

    def _sync_stack(self) -> None:
        self.stack.setCurrentIndex(self.mode.currentIndex())
        self.stack.setVisible(self.current_mode() != "none")

    def current_mode(self) -> str:
        return str(self.mode.currentData())

    def read(self) -> Parsed:
        mode = self.current_mode()
        if mode == "rect":
            return parse_rect([edit.text() for edit in self.rect_edits])
        if mode == "at":
            return parse_anchored_region(self.at.text(), self.at_offset.texts(),
                                         self.at_size.texts())
        return Parsed(None)

    def value(self) -> Any:
        parsed = self.read()
        return parsed.value if parsed.ok else None

    def set_value(self, value: Any) -> None:
        mode = region_mode(value)
        self.mode.setCurrentIndex([m for m, _ in self.MODES].index(mode))
        if mode == "rect":
            for edit, item in zip(self.rect_edits, list(value) + [None] * 4, strict=False):
                edit.setText(format_scalar(item))
        elif mode == "at":
            self.at.setText(format_scalar(value.get("at")))
            self.at_offset.set_value(value.get("offset"))
            self.at_size.set_value(value.get("size"))
        self._sync_stack()

    def set_and_send(self, value: Any) -> None:
        self.set_value(value)
        self.changed.emit(True)


class RegionField(Field):
    def __init__(self, key: str, *, env: FieldEnv | None = None,
                 param: str | None = None) -> None:
        super().__init__([key], env)
        self.region = RegionEdit(param or key, self.env)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.region)
        self.region.changed.connect(self._emit)

    def _show(self, params: Mapping[str, Any]) -> None:
        self.region.set_value(params.get(self.keys[0]))

    def _read(self) -> Parsed:
        parsed = self.region.read()
        return Parsed({self.keys[0]: parsed.value}) if parsed.ok else parsed


# --- targets ----------------------------------------------------------------------------


class TargetEdit(QWidget):
    """What a ``click``, ``move`` or a ``drag`` point aims at: a position, an image or
    a stored result, plus its window and offset (and, for an image, its threshold, scale
    and region)."""

    changed = Signal(bool)
    MODES = (("image", "Image"), ("point", "Position"), ("at", "Stored result"))

    def __init__(self, prefix: str, env: FieldEnv, *, threshold_default: Any = 0.9,
                 scale_default: Any = 1.0, with_region: bool = True) -> None:
        super().__init__()
        self.prefix = prefix
        self.mode = QComboBox()
        for mode, label in self.MODES:
            self.mode.addItem(label, mode)
        self.window_edit = WindowEdit(f"{prefix}window", env)
        self.image = ImageEdit(f"{prefix}image", env, self.window_edit.value)
        self.point = PointEdit(f"{prefix}x/y", env, self.window_edit.value)
        self.at = _line_edit("${label}: a result kept with 'as'")
        self.threshold = _line_edit(f"default: {format_scalar(threshold_default)}")
        self.scale = _line_edit(f"default: {format_scalar(scale_default)}")
        self.threshold_default = threshold_default
        self.scale_default = scale_default
        for edit in (self.threshold, self.scale):
            edit.setMaximumWidth(110)
        self.region = RegionEdit(f"{prefix}region", env, self.window_edit.value) \
            if with_region else None
        self.offset = OffsetEdit()

        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.addRow("aim at", _row(self.mode))
        self.form.addRow("image", self.image)
        self.form.addRow("position", self.point)
        self.form.addRow("result", self.at)
        self.form.addRow("threshold", _row(self.threshold))
        self.form.addRow("scale", _row(self.scale))
        if self.region is not None:
            self.form.addRow("region", self.region)
        self.form.addRow("window", self.window_edit)
        self.form.addRow("offset", self.offset)

        for signal in (self.image.changed, self.point.changed, self.window_edit.changed,
                       self.offset.changed):
            signal.connect(self.changed)
        if self.region is not None:
            self.region.changed.connect(self.changed)
        for edit in (self.at, self.threshold, self.scale):
            edit.textEdited.connect(lambda _: self.changed.emit(False))
        self.mode.activated.connect(self._mode_chosen)
        self._sync()

    def current_mode(self) -> str:
        return str(self.mode.currentData())

    def _mode_chosen(self) -> None:
        self._sync()
        self.changed.emit(True)

    def _sync(self) -> None:
        mode = self.current_mode()
        image = mode == "image"
        rows: list[tuple[QWidget, bool]] = [
            (self.image, image), (self.point, mode == "point"), (self.at, mode == "at"),
            (self.threshold, image), (self.scale, image), (self.window_edit, mode != "at")]
        if self.region is not None:
            rows.append((self.region, image))
        for widget, visible in rows:
            self.form.setRowVisible(self._row_of(widget), visible)

    def _row_of(self, widget: QWidget) -> int:
        for row in range(self.form.rowCount()):
            item = self.form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            if item is None:
                continue
            found = item.widget()
            if found is widget or (found is not None and found.isAncestorOf(widget)):
                return row
        raise LookupError(widget)

    def set_value(self, value: Mapping[str, Any]) -> None:
        if value.get("at") is not None:
            mode = "at"
        elif value.get("image") is not None:
            mode = "image"
        else:
            mode = "point" if value.get("x") is not None or value.get("y") is not None \
                else "image"
        self.mode.setCurrentIndex([m for m, _ in self.MODES].index(mode))
        image = value.get("image")
        self.image.set_text("" if image is None else str(image))
        self.point.set_value(value)
        self.at.setText(format_scalar(value.get("at")))
        self.threshold.setText(_unless_default(value.get("threshold"),
                                               self.threshold_default))
        self.scale.setText(_unless_default(value.get("scale"), self.scale_default))
        if self.region is not None:
            self.region.set_value(value.get("region"))
        self.window_edit.set_value(value.get("window"))
        self.offset.set_value(value.get("offset"))
        self._sync()

    def read(self) -> Parsed:
        """Every key of a target (those of the other modes as None)."""
        mode = self.current_mode()
        value: dict[str, Any] = {"image": None, "x": None, "y": None, "at": None,
                                 "threshold": None, "scale": None, "window": None}
        if self.region is not None:
            value["region"] = None
        if mode == "image":
            image = self.image.text()
            if not image:
                return Parsed(error="image: choose an image")
            value["image"] = image
            for key, edit in (("threshold", self.threshold), ("scale", self.scale)):
                parsed = parse_number(edit.text())
                if not parsed.ok:
                    return Parsed(error=f"{key}: {parsed.error}")
                value[key] = parsed.value
            if self.region is not None:
                region = self.region.read()
                if not region.ok:
                    return Parsed(error=f"region: {region.error}")
                value["region"] = region.value
        elif mode == "point":
            point = self.point.read()
            if not point.ok:
                return point
            value.update(point.value)
        else:
            if not self.at.text().strip():
                return Parsed(error="result: give a stored result such as ${label}")
            value["at"] = self.at.text().strip()
        if mode != "at":
            value["window"] = self.window_edit.value()
        offset = self.offset.read()
        if not offset.ok:
            return Parsed(error=f"offset: {offset.error}")
        value["offset"] = offset.value
        return Parsed(value)


def _unless_default(value: Any, default: Any) -> str:
    if value is None or (default is not None and _same(value, default)):
        return ""
    return format_scalar(value)


class TargetField(Field):
    """The target of ``click`` and ``move``: their ``image``, ``x``, ``y``, ``at``,
    ``window``, ``offset``, ``threshold``, ``scale`` and ``region`` together."""

    def __init__(self, keys: Sequence[str], *, defaults: Mapping[str, Any],
                 env: FieldEnv | None = None) -> None:
        super().__init__(keys, env)
        self.target = TargetEdit("", self.env,
                                 threshold_default=defaults.get("threshold", 0.9),
                                 scale_default=defaults.get("scale", 1.0),
                                 with_region="region" in keys)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.target)
        self.target.changed.connect(self._emit)

    def _show(self, params: Mapping[str, Any]) -> None:
        self.target.set_value(params)

    def _read(self) -> Parsed:
        parsed = self.target.read()
        if not parsed.ok:
            return parsed
        return Parsed({k: v for k, v in parsed.value.items() if k in self.keys})


class PointTargetField(Field):
    """A point of ``drag`` (``from``, ``to``): a mapping edited like a target."""

    def __init__(self, key: str, *, env: FieldEnv | None = None) -> None:
        super().__init__([key], env)
        self.target = TargetEdit(f"{key}.", self.env)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.target)
        self.target.changed.connect(self._emit)

    def _show(self, params: Mapping[str, Any]) -> None:
        value = params.get(self.keys[0])
        self.target.set_value(value if isinstance(value, Mapping) else {})

    def _read(self) -> Parsed:
        parsed = self.target.read()
        if not parsed.ok:
            return parsed
        return Parsed({self.keys[0]: {k: v for k, v in parsed.value.items()
                                      if v is not None}})


# --- conditions -------------------------------------------------------------------------


class ConditionField(Field):
    """``when``/``while``: an expression, or ``{image_visible: ...}``."""

    MODES = (("expression", "Expression"), ("image", "Image is visible"))

    def __init__(self, key: str, *, required: bool = False,
                 env: FieldEnv | None = None) -> None:
        super().__init__([key], env)
        self.required = required
        self.mode = QComboBox()
        for mode, label in self.MODES:
            self.mode.addItem(label, mode)
        self.expression = _line_edit("an expression, e.g. n < 3 or found")
        self.image = ImageEdit(f"{key}.image_visible", self.env)
        self.threshold = _line_edit("default: 0.9")
        self.threshold.setMaximumWidth(110)
        self.window_edit = WindowEdit(f"{key}.image_visible.window", self.env)
        self.extra: dict[str, Any] = {}
        self.form = QFormLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.addRow("check", _row(self.mode))
        self.form.addRow("expression", self.expression)
        self.form.addRow("image", self.image)
        self.form.addRow("threshold", _row(self.threshold))
        self.form.addRow("window", self.window_edit)
        self.setLayout(self.form)
        self.mode.activated.connect(self._mode_chosen)
        self.expression.textEdited.connect(lambda _: self._emit())
        self.threshold.textEdited.connect(lambda _: self._emit())
        self.image.changed.connect(self._emit)
        self.window_edit.changed.connect(self._emit)
        self._sync()

    def _mode_chosen(self) -> None:
        self._sync()
        self._emit(True)

    def _sync(self) -> None:
        expression = self.mode.currentData() == "expression"
        self.form.setRowVisible(1, expression)
        for row in (2, 3, 4):
            self.form.setRowVisible(row, not expression)

    def _show(self, params: Mapping[str, Any]) -> None:
        value = params.get(self.keys[0])
        visible = value.get("image_visible") if isinstance(value, Mapping) else None
        self.extra = {}
        if visible is not None:
            self.mode.setCurrentIndex(1)
            if isinstance(visible, Mapping):
                self.image.set_text(str(visible.get("image") or ""))
                self.threshold.setText(_unless_default(visible.get("threshold"), 0.9))
                self.window_edit.set_value(visible.get("window"))
                self.extra = {k: v for k, v in visible.items()
                              if k not in ("image", "threshold", "window")}
            else:
                self.image.set_text(str(visible))
                self.threshold.setText("")
                self.window_edit.set_value(None)
        else:
            self.mode.setCurrentIndex(0)
            self.expression.setText("" if value is None else format_literal(value)
                                    if not isinstance(value, str) else value)
        self._sync()

    def _read(self) -> Parsed:
        if self.mode.currentData() == "expression":
            text = self.expression.text().strip()
            if not text:
                if self.required:
                    return Parsed(error="write an expression")
                return Parsed({self.keys[0]: None})
            return Parsed({self.keys[0]: text})
        image = self.image.text()
        if not image:
            return Parsed(error="choose an image")
        threshold = parse_number(self.threshold.text())
        if not threshold.ok:
            return Parsed(error=f"threshold: {threshold.error}")
        window = self.window_edit.value()
        if threshold.value is None and window is None and not self.extra:
            return Parsed({self.keys[0]: {"image_visible": image}})
        inner: dict[str, Any] = {"image": image, **self.extra}
        if threshold.value is not None:
            inner["threshold"] = threshold.value
        if window is not None:
            inner["window"] = window
        return Parsed({self.keys[0]: {"image_visible": inner}})


# --- programs and mappings --------------------------------------------------------------


class CommandField(Field):
    """``exec``'s ``command``: a program and its arguments (one per line, run without a
    shell), or a command line."""

    MODES = (("list", "Program and arguments"), ("line", "Command line"))

    def __init__(self, key: str, *, env: FieldEnv | None = None) -> None:
        super().__init__([key], env)
        self.mode = QComboBox()
        for mode, label in self.MODES:
            self.mode.addItem(label, mode)
        self.lines = QPlainTextEdit()
        self.lines.setPlaceholderText("program\nfirst argument\nsecond argument")
        self.lines.setTabChangesFocus(True)
        self.lines.setFixedHeight(72)
        self.line = _line_edit("program --option 'an argument'")
        hint = QLabel("One item per line: the program, then each argument as it is.")
        hint.setStyleSheet(HINT_STYLE)
        hint.setWordWrap(True)
        self.hint = hint
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(_row(self.mode))
        layout.addWidget(self.lines)
        layout.addWidget(self.line)
        layout.addWidget(hint)
        self.mode.activated.connect(self._mode_chosen)
        self.lines.textChanged.connect(lambda: self._emit())
        self.line.textEdited.connect(lambda _: self._emit())
        self._sync()

    def _mode_chosen(self) -> None:
        if self.mode.currentData() == "line" and not self.line.text():
            self.line.setText(" ".join(parse_lines(self.lines.toPlainText())))
        elif self.mode.currentData() == "list" and not self.lines.toPlainText():
            self.lines.setPlainText("\n".join(self.line.text().split()))
        self._sync()
        self._emit(True)

    def _sync(self) -> None:
        listed = self.mode.currentData() == "list"
        self.lines.setVisible(listed)
        self.hint.setVisible(listed)
        self.line.setVisible(not listed)

    def _show(self, params: Mapping[str, Any]) -> None:
        value = params.get(self.keys[0])
        if isinstance(value, str):
            self.mode.setCurrentIndex(1)
            self.line.setText(value)
            self.lines.setPlainText("")
        else:
            self.mode.setCurrentIndex(0)
            self.lines.setPlainText(format_lines(value))
            self.line.setText("")
        self._sync()

    def _read(self) -> Parsed:
        if self.mode.currentData() == "list":
            items = parse_lines(self.lines.toPlainText())
            if not items:
                return Parsed(error="give the program to run")
            return Parsed({self.keys[0]: items})
        text = self.line.text().strip()
        if not text:
            return Parsed(error="give the command line")
        return Parsed({self.keys[0]: text})


class MappingField(Field):
    """Names and values (``exec``'s ``env``): numbers and ``true``/``false`` keep their
    type, anything else is text."""

    def __init__(self, key: str, *, env: FieldEnv | None = None,
                 headers: tuple[str, str] = ("Name", "Value")) -> None:
        super().__init__([key], env)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(list(headers))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setFixedHeight(96)
        add = QPushButton("Add")
        remove = QPushButton("Remove")
        add.clicked.connect(self.add_row)
        remove.clicked.connect(self.remove_row)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.table)
        layout.addWidget(_row(add, remove))
        self.table.itemChanged.connect(lambda _: self._emit())

    def add_row(self, name: str = "", value: str = "") -> None:
        self._loading, loading = True, self._loading
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(name))
        self.table.setItem(row, 1, QTableWidgetItem(value))
        self._loading = loading
        item = self.table.item(row, 0)
        if not name and item is not None:
            self.table.editItem(item)

    def remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self._emit(True)

    def _show(self, params: Mapping[str, Any]) -> None:
        self.table.setRowCount(0)
        value = params.get(self.keys[0])
        for name, item in (value.items() if isinstance(value, Mapping) else []):
            self.add_row(str(name), format_scalar(item))

    def _read(self) -> Parsed:
        value: dict[str, Any] = {}
        for row in range(self.table.rowCount()):
            name_item, value_item = self.table.item(row, 0), self.table.item(row, 1)
            name = name_item.text().strip() if name_item is not None else ""
            if not name:
                continue
            value[name] = parse_scalar_text(value_item.text() if value_item else "")
        return Parsed({self.keys[0]: value or None})


# --- input profiles ---------------------------------------------------------------------


class ProfileEdit(QWidget):
    """Settings of an input profile, for the devices given (``mouse``, ``keyboard``);
    empty settings are left to the defaults."""

    changed = Signal(bool)

    def __init__(self, devices: Sequence[str] = ("mouse", "keyboard")) -> None:
        super().__init__()
        self.devices = tuple(devices)
        self.inputs: dict[tuple[str, str], QComboBox | QLineEdit] = {}
        self.settings: dict[tuple[str, str], ProfileSetting] = {}
        self.extra: dict[str, dict[str, Any]] = {}
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for device, settings in PROFILE_DEVICES:
            if device not in self.devices:
                continue
            for setting in settings:
                widget: QComboBox | QLineEdit
                if setting.kind in ("choice", "boolean"):
                    widget = QComboBox()
                    widget.addItem(f"(default: {format_scalar(setting.default)})", None)
                    options = setting.choices if setting.kind == "choice" else (
                        "true", "false")
                    for option in options:
                        widget.addItem(option, option)
                    widget.activated.connect(lambda _: self.changed.emit(True))
                else:
                    widget = _line_edit(f"default: {format_scalar(setting.default)}")
                    widget.setMaximumWidth(140)
                    widget.textEdited.connect(lambda _: self.changed.emit(False))
                widget.setToolTip(setting.doc)
                label = f"{device} {setting.name}" if len(self.devices) > 1 else setting.name
                layout.addRow(label, _row(widget))
                self.inputs[(device, setting.name)] = widget
                self.settings[(device, setting.name)] = setting

    def set_value(self, value: Any) -> None:
        profile = value if isinstance(value, Mapping) else {}
        self.extra = {}
        for device in self.devices:
            group = profile.get(device) if len(self.devices) > 1 else profile
            group = group if isinstance(group, Mapping) else {}
            self.extra[device] = {k: v for k, v in group.items()
                                  if (device, k) not in self.inputs}
            for (dev, name), widget in self.inputs.items():
                if dev != device:
                    continue
                current = group.get(name)
                if isinstance(widget, QComboBox):
                    text = format_scalar(current) if current is not None else None
                    index = widget.findData(text) if text is not None else 0
                    widget.setCurrentIndex(max(index, 0))
                else:
                    widget.setText(format_scalar(current))

    def read(self) -> Parsed:
        """The profile (``{mouse: {...}, keyboard: {...}}``, or the settings of the one
        device), None when nothing is set."""
        groups: dict[str, dict[str, Any]] = {}
        for (device, name), widget in self.inputs.items():
            text = (widget.currentData() or "") if isinstance(widget, QComboBox) \
                else widget.text()
            parsed = parse_setting(self.settings[(device, name)], str(text))
            if not parsed.ok:
                return Parsed(error=f"{device} {name}: {parsed.error}")
            if parsed.value is not None:
                groups.setdefault(device, {})[name] = parsed.value
        for device, extra in self.extra.items():
            if extra:
                groups[device] = {**extra, **groups.get(device, {})}
        if len(self.devices) == 1:
            return Parsed(groups.get(self.devices[0]) or None)
        return Parsed(groups or None)


class ProfileField(Field):
    """An input profile parameter or common field (``input``, ``type``'s
    ``keyboard``)."""

    def __init__(self, key: str, devices: Sequence[str] = ("mouse", "keyboard"), *,
                 env: FieldEnv | None = None) -> None:
        super().__init__([key], env)
        self.profile = ProfileEdit(devices)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.profile)
        self.profile.changed.connect(self._emit)

    def _show(self, params: Mapping[str, Any]) -> None:
        self.profile.set_value(params.get(self.keys[0]))

    def _read(self) -> Parsed:
        parsed = self.profile.read()
        return Parsed({self.keys[0]: parsed.value}) if parsed.ok else parsed


# --- the factory ------------------------------------------------------------------------


def create_field(step_type: str, param: ParamInfo, kind: str,
                 env: FieldEnv | None = None) -> Field:
    """The field for ``param`` of ``step_type``, edited as ``kind``
    (:func:`~j5_macro.gui.models.fields.field_kind`)."""
    name, default = param.name, param.default
    if kind == "choice":
        return ChoiceField(name, param.choices or (), default=default, env=env)
    if kind == "boolean":
        return BoolField(name, default=default, env=env)
    if kind == "text":
        return TextField(name, required=param.required, env=env)
    if kind == "image":
        return ImageField(name, required=param.required, env=env)
    if kind == "window":
        return WindowField(name, env=env)
    if kind == "offset":
        return OffsetField(name, env=env)
    if kind == "region":
        return RegionField(name, env=env)
    if kind == "target":
        return PointTargetField(name, env=env)
    if kind == "condition":
        return ConditionField(name, required=param.required, env=env)
    if kind == "command":
        return CommandField(name, env=env)
    if kind == "mapping":
        return MappingField(name, env=env)
    if kind == "keyboard":
        return ProfileField(name, ("keyboard",), env=env)
    if kind == "integer":
        return LineField(name, parse_integer, default=default, env=env)
    if kind == "number":
        return LineField(name, parse_number, default=default, env=env)
    if kind == "duration":
        return LineField(name, parse_duration_text, default=default, env=env,
                         placeholder="" if default is not None
                         else "e.g. 2s, 500ms or 2s±20%")
    if kind in ("string", "expression"):
        required = param.required
        return LineField(name, lambda text: parse_string(text, required=required),
                         default=default, env=env,
                         formatter=lambda v: "" if v is None else str(v))
    return AnyField(name, env=env)


__all__ = [
    "Field",
    "FieldContext",
    "FieldEnv",
    "FieldTools",
    "ToolKind",
    "create_field",
    "field_tools",
    "register_field_tools",
]
