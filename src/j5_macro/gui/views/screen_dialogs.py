"""Screen tools as reusable widgets: Capture, Test and the template preview for an image
field, Pick and the window list for a coordinate field.

Field tools (the component contract; see gui/README.md, "Field tools"):

- :class:`ImageFieldTools` ``(ctx, get_value, set_value, *, get_search=None,
  step_id=None)``: **Capture…**, **Test** and a :class:`TemplatePreview` for an
  ``image`` value. ``get_value()`` returns the field's current value (a path relative to
  the script or ``#name``); ``set_value(value)`` is called with the new value after a
  capture. ``get_search()`` returns the other parameters of the search in the step's
  own shape (``window: {title}``, ``region``, ``threshold``, ``scale``), so **Test**
  searches exactly as the step would. ``step_id`` (a string or a callable) names new
  templates. Call :meth:`ImageFieldTools.refresh` when the value changes.
- :class:`PointFieldTools` ``(ctx, get_point, set_point)``: **Pick…** and
  **Window…** for an ``x``/``y``/``window`` field. ``get_point()`` returns the target's
  parameters (``x``, ``y``, ``window``, ``image``); ``set_point(values)`` receives the
  parameters to change, in that shape: ``x``, ``y``, ``window`` (``{title}``, or None to
  remove it), and None for ``image`` and ``at`` when a picked point replaces them.

Both report what they did, and what went wrong, in their own ``message`` label and the
``message_changed`` signal; Escape on the overlay (``cancelled``) is quiet. The dialogs
they open (:class:`CaptureDialog`, :class:`TestResultDialog`, :class:`WindowChooser`)
are modeless, so the windows can hide while the overlay is open (see
:class:`TopLevelHider`). Each dialog keeps its dialog code (``open_*``) apart from the
work (``capture``, ``test``...), which tests call directly.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from j5_macro.client import EngineError
from j5_macro.gui.context import AppContext
from j5_macro.gui.engine_thread import describe_error
from j5_macro.gui.models.screen import (
    FAILED,
    FindArgs,
    FindOutcome,
    ToolProblem,
    capture_file,
    capture_name,
    describe_pick,
    file_value,
    find_args,
    image_reference,
    image_target,
    pick_values,
    point_target,
    rect_within,
    tool_problem,
    valid_name,
)
from j5_macro.gui.views.template_preview import (
    FOUND,
    MISSED,
    ImageView,
    Mark,
    TemplatePreview,
    load_image,
)

HIDE_DELAY_MS = 400
"""How long to wait after hiding the windows before the engine looks at the screen
(window managers unmap and repaint asynchronously)."""
NO_DELAY_PLATFORMS = ("offscreen", "minimal")
ERROR_STYLE = "color: #b00020;"
WARNING_STYLE = "color: #8a5a00;"


def problem_of(exc: BaseException) -> ToolProblem:
    """A failed screen tool's error, read for people."""
    if isinstance(exc, EngineError):
        return tool_problem(exc.code, exc.error.message, exc.error.details)
    return ToolProblem(FAILED, describe_error(exc))


VERSION_WAIT_MS = 5000


def when_document_at(ctx: AppContext, version: object, callback: Callable[[], None]) -> None:
    """Call ``callback`` once the open document shows ``version`` (a capture embedded
    in the script changed it; an edit based on the version before would answer
    ``conflict``). Right away when it already does or ``version`` is not a string;
    after :data:`VERSION_WAIT_MS` at the latest."""
    state = ctx.document.state
    if not isinstance(version, str) or state is None or state.version == version:
        callback()
        return
    called = False
    document = ctx.document

    def fire() -> None:
        nonlocal called
        if called:
            return
        called = True
        with contextlib.suppress(RuntimeError, TypeError):
            document.state_changed.disconnect(changed)
        callback()

    def changed(new_state: object) -> None:
        if getattr(new_state, "version", None) == version:
            fire()

    document.state_changed.connect(changed)
    QTimer.singleShot(VERSION_WAIT_MS, fire)


class TopLevelHider:
    """A :class:`~j5_macro.gui.screen_tools.WindowHider` that hides every visible
    window of the application (the main window and its dialogs) and shows them again.
    Nested uses are counted: the windows come back when the last one restores."""

    def __init__(self, delay_ms: int | None = None) -> None:
        self._delay_ms = delay_ms
        self._depth = 0
        self._hidden: list[QWidget] = []

    @property
    def hidden(self) -> bool:
        return self._depth > 0

    def hide(self, then: Callable[[], None]) -> None:
        self._depth += 1
        if self._depth == 1:
            self._hidden = [widget for widget in QApplication.topLevelWidgets()
                            if widget.isVisible() and widget.windowType() in
                            (Qt.WindowType.Window, Qt.WindowType.Dialog)]
            for widget in self._hidden:
                widget.hide()
        QTimer.singleShot(self.delay_ms(), then)

    def restore(self) -> None:
        if self._depth == 0:
            return
        self._depth -= 1
        if self._depth:
            return
        for widget in self._hidden:
            with contextlib.suppress(RuntimeError):  # deleted meanwhile
                widget.show()
                widget.raise_()
        if self._hidden:
            with contextlib.suppress(RuntimeError):
                self._hidden[-1].activateWindow()
        self._hidden = []

    def delay_ms(self) -> int:
        if self._delay_ms is not None:
            return self._delay_ms
        return 0 if QGuiApplication.platformName() in NO_DELAY_PLATFORMS else HIDE_DELAY_MS


class _Messages(QWidget):
    """A widget with a ``message`` label for what its tools did."""

    message_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message.hide()

    def say(self, text: str, style: str = "") -> None:
        self.message.setStyleSheet(style)
        self.message.setText(text)
        self.message.setVisible(bool(text))
        self.message_changed.emit(text)

    def report(self, problem: ToolProblem) -> None:
        if problem.quiet:
            self.say("Cancelled.")
            return
        self.say(problem.text, ERROR_STYLE)


# --- capture ----------------------------------------------------------------------------


class CaptureDialog(QDialog):
    """Capture a template: embedded in the open script's ``images`` under a name, or
    saved as a PNG file next to it; padding and a transparent background as options.

    **Capture…** opens the engine's overlay (the windows hide meanwhile);
    :meth:`capture` with a ``bbox`` saves that rectangle without it."""

    captured = Signal(str, object)
    """``(value, result)``: the ``image`` value to use (``#name`` or a path relative to
    the script) and the engine's ``capture_region`` result."""
    problem = Signal(object)
    """A :class:`ToolProblem` (cancelled included)."""

    def __init__(self, ctx: AppContext, *, step_id: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.setWindowTitle("Capture a template")
        self.setModal(False)
        state = ctx.document.state
        self._script_id = state.script_id if state is not None else None
        self._script_path = state.path if state is not None else None
        names = [str(image.get("name")) for image in state.images] if state else []
        folder = self.folder()
        files = os.listdir(folder) if folder and os.path.isdir(folder) else []

        self.embed_option = QRadioButton("Embed in the script as")
        self.file_option = QRadioButton("Save as a file next to the script")
        group = QButtonGroup(self)
        group.addButton(self.embed_option)
        group.addButton(self.file_option)
        self.name_edit = QLineEdit(capture_name(step_id, names))
        self.file_edit = QLineEdit(capture_file(step_id, files))
        self.padding = QSpinBox()
        self.padding.setRange(0, 200)
        self.padding.setSuffix(" px")
        self.transparent = QCheckBox("Transparent background (the border's colour)")
        self.overwrite = QCheckBox("Replace an existing image of that name")
        self.info = QLabel("Drag a rectangle over what the step should find. "
                           "Escape cancels.")
        self.info.setWordWrap(True)
        self.capture_button = QPushButton("Capture…")
        self.capture_button.setDefault(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(self.capture_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        self.capture_button.clicked.connect(lambda: self.capture())

        form = QFormLayout()
        form.addRow(self.embed_option, self.name_edit)
        form.addRow(self.file_option, self.file_edit)
        form.addRow("Padding", self.padding)
        form.addRow("", self.transparent)
        form.addRow("", self.overwrite)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.info)
        layout.addWidget(buttons)

        self.embed_option.setChecked(True)
        self.embed_option.setEnabled(self._script_id is not None)
        self.file_option.setEnabled(folder is not None)
        if self._script_id is None and folder is None:
            self.capture_button.setEnabled(False)
            self.info.setText("Open a script first.")
        self.embed_option.toggled.connect(self._option_changed)
        self._option_changed()
        self.result_: dict[str, Any] | None = None

    def folder(self) -> str | None:
        """The script's directory; None for a script without a file."""
        if self._script_path is None:
            return None
        return os.path.dirname(os.path.abspath(self._script_path))

    @property
    def embedding(self) -> bool:
        return self.embed_option.isChecked()

    def capture(self, bbox: Sequence[int] | None = None, window: str | None = None) -> None:
        """Send ``capture_region``: interactive without ``bbox``."""
        payload: dict[str, Any] = {"bbox": bbox, "window": window,
                                   "padding": self.padding.value() or None,
                                   "transparent": self.transparent.isChecked() or None,
                                   "overwrite": self.overwrite.isChecked() or None}
        if self.embedding:
            name = self.name_edit.text().strip()
            if not valid_name(name) or self._script_id is None:
                self._say("A name starts with a letter or _ and has only letters, digits, "
                          "_ and -.", ERROR_STYLE)
                return
            payload.update(script_id=self._script_id, name=name)
            value = image_reference(name)
        else:
            folder = self.folder()
            file_name = self.file_edit.text().strip()
            if folder is None or not file_name:
                self._say("Give a file name.", ERROR_STYLE)
                return
            if not file_name.lower().endswith(".png"):
                file_name += ".png"
            path = os.path.join(folder, file_name)
            payload["path"] = path
            value = file_value(path, self._script_path)
        self.capture_button.setEnabled(False)
        self._say("Capturing…" if bbox is not None else
                  "Drag a rectangle over what the step should find; Escape cancels.")
        self._ctx.screen.capture_region(
            on_result=lambda result: self._captured(value, result),
            on_error=self._failed, **payload)

    def _captured(self, value: str, result: Mapping[str, Any]) -> None:
        self.capture_button.setEnabled(True)
        self.result_ = dict(result)
        lines = [f"Saved {value} ({_size(result.get('bbox'))})."]
        warnings = [w for w in result.get("warnings") or [] if isinstance(w, Mapping)]
        for warning in warnings:
            lines.append(f"Warning: {warning.get('message')}")
        scale = result.get("display_scale")
        if isinstance(scale, int | float) and scale != 1:
            if self.embedding:
                lines.append(f"Captured at display scale {scale:g}; the entry records it, "
                             "so the script finds it at any scale.")
            else:
                lines.append(f"Captured at display scale {scale:g}. A file cannot carry its "
                             "scale: set the script's capture_scale, or embed it instead.")
        self._say("\n".join(lines), WARNING_STYLE if len(lines) > 1 else "")
        self.captured.emit(value, dict(result))
        if len(lines) == 1:
            self.accept()

    def _failed(self, exc: BaseException) -> None:
        self.capture_button.setEnabled(True)
        problem = problem_of(exc)
        self._say("Cancelled." if problem.quiet else problem.text,
                  "" if problem.quiet else ERROR_STYLE)
        self.problem.emit(problem)

    def _say(self, text: str, style: str = "") -> None:
        self.info.setStyleSheet(style)
        self.info.setText(text)

    def _option_changed(self) -> None:
        self.name_edit.setEnabled(self.embedding)
        self.overwrite.setText("Replace an existing image of that name" if self.embedding
                               else "Replace an existing file")
        self.file_edit.setEnabled(not self.embedding and self.folder() is not None)


def _size(bbox: object) -> str:
    if isinstance(bbox, list) and len(bbox) == 4:
        return f"{bbox[2]} x {bbox[3]} px at ({bbox[0]}, {bbox[1]})"
    return "?"


# --- test -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TestResult:
    """A "Test": the search's outcome and a screenshot of the area it searched."""

    __test__ = False  # not a pytest class

    args: FindArgs
    outcome: FindOutcome
    shot: QImage | None = None
    shot_bbox: tuple[int, int, int, int] | None = None

    @property
    def mark(self) -> tuple[int, int, int, int] | None:
        """The best location in the screenshot."""
        if self.outcome.bbox is None or self.shot_bbox is None:
            return None
        return rect_within(self.outcome.bbox, self.shot_bbox)


class ImageTester(QObject):
    """One search exactly as a step makes it (``find_image``), then a screenshot of the
    searched area to show the match on. The windows hide meanwhile."""

    finished = Signal(object)
    """A :class:`TestResult`."""
    failed = Signal(object)
    """A :class:`ToolProblem`."""

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.busy = False

    def test(self, args: FindArgs, script_id: str | None) -> None:
        if self.busy:
            return
        self.busy = True
        screen = self._ctx.screen

        def work(done: Callable[[], None]) -> None:
            def found(result: Mapping[str, Any]) -> None:
                outcome = FindOutcome.from_result(result, args.threshold)
                screen.screenshot(
                    region=outcome.region,
                    on_result=lambda shot: self._shot(args, outcome, shot, done),
                    on_error=lambda _exc: self._finish(done, TestResult(args, outcome)))

            def failed(exc: BaseException) -> None:
                self.busy = False
                done()
                self.failed.emit(problem_of(exc))

            screen.find_image(args.image, script_id=script_id, on_result=found,
                              on_error=failed, **args.kwargs())

        screen.while_hidden(work)

    def _shot(self, args: FindArgs, outcome: FindOutcome, result: Mapping[str, Any],
              done: Callable[[], None]) -> None:
        path = result.get("path")
        image = load_image(path) if isinstance(path, str) else None
        if isinstance(path, str):
            with contextlib.suppress(OSError):
                os.remove(path)  # a temporary file of the engine's
        bbox = result.get("bbox")
        shot_bbox = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])) \
            if isinstance(bbox, list) and len(bbox) == 4 else None
        self._finish(done, TestResult(args, outcome, image, shot_bbox))

    def _finish(self, done: Callable[[], None], result: TestResult) -> None:
        self.busy = False
        done()
        self.finished.emit(result)


class TestResultDialog(QDialog):
    """The result of a Test: found or not, the score, and the best location drawn on a
    screenshot of the searched area."""

    __test__ = False

    again = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Test image search")
        self.setModal(False)
        self.summary = QLabel()
        font = self.summary.font()
        font.setPointSizeF(font.pointSizeF() * 1.2)
        font.setBold(True)
        self.summary.setFont(font)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.view = ImageView(placeholder="No screenshot", minimum=QSize(360, 220))
        self.again_button = QPushButton("Test again")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(self.again_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        self.again_button.clicked.connect(self.again)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.details)
        layout.addWidget(self.view, 1)
        layout.addWidget(buttons)
        self.resize(640, 480)
        self.result_: TestResult | None = None

    def show_result(self, result: TestResult) -> None:
        self.result_ = result
        outcome = result.outcome
        color = FOUND if outcome.found else MISSED
        self.summary.setText(outcome.summary)
        self.summary.setStyleSheet(f"color: {color.name()};")
        self.details.setText(f"Template {result.args.image}\n" + "\n".join(outcome.details()))
        self.view.set_image(result.shot, "No screenshot of the searched area")
        mark = result.mark
        if mark is not None:
            score = "-" if outcome.score is None else f"{outcome.score:.3f}"
            self.view.set_marks([Mark(mark, color, score)])


# --- windows ----------------------------------------------------------------------------


class WindowChooser(QDialog):
    """The visible windows (``list_windows``), topmost first, to choose a title."""

    listed = Signal(object)
    """The ``windows`` list, after each listing."""
    chosen = Signal(str)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.setWindowTitle("Choose a window")
        self.setModal(False)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter")
        self.filter.setClearButtonEnabled(True)
        self.list = QListWidget()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Title (a part of it is enough)")
        refresh = QPushButton("Refresh")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(refresh, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        refresh.clicked.connect(self.refresh)
        self.filter.textChanged.connect(self._filter)
        self.list.currentItemChanged.connect(
            lambda item, _: self.title_edit.setText(item.data(Qt.ItemDataRole.UserRole))
            if item is not None else None)
        self.list.itemDoubleClicked.connect(lambda _: self._accept())
        layout = QVBoxLayout(self)
        layout.addWidget(self.filter)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.title_edit)
        layout.addWidget(buttons)
        self.resize(520, 420)

    def titles(self) -> list[str]:
        return [str(self.list.item(i).data(Qt.ItemDataRole.UserRole))
                for i in range(self.list.count())]

    def refresh(self) -> None:
        self.status.setText("Listing the windows…")
        self._ctx.screen.list_windows(on_result=self._listed, on_error=self._failed)

    def _listed(self, result: Mapping[str, Any]) -> None:
        windows = [w for w in result.get("windows") or [] if isinstance(w, Mapping)]
        self.list.clear()
        for window in windows:
            title = str(window.get("title", ""))
            bbox = window.get("bbox")
            size = f"{bbox[2]} x {bbox[3]} at ({bbox[0]}, {bbox[1]})" \
                if isinstance(bbox, list) and len(bbox) == 4 else ""
            item = QListWidgetItem(f"{title}    {size}".rstrip())
            item.setData(Qt.ItemDataRole.UserRole, title)
            self.list.addItem(item)
        self.status.setText(f"{len(windows)} window{'s' if len(windows) != 1 else ''}"
                            if windows else "No visible window with a title.")
        self._filter(self.filter.text())
        self.listed.emit(windows)

    def _failed(self, exc: BaseException) -> None:
        self.status.setText(problem_of(exc).text)
        self.status.setStyleSheet(ERROR_STYLE)

    def _filter(self, text: str) -> None:
        needle = text.casefold()
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(needle not in item.text().casefold())

    def _accept(self) -> None:
        title = self.title_edit.text().strip()
        if title:
            self.chosen.emit(title)
        self.accept()


# --- field tools ------------------------------------------------------------------------


class ImageFieldTools(_Messages):
    """**Capture…**, **Test** and a preview for an ``image`` field (see the module
    docstring for the contract)."""

    tested = Signal(object)
    """The :class:`TestResult` of each Test."""
    captured = Signal(str)
    """The new value after a capture (already given to ``set_value``)."""

    def __init__(self, ctx: AppContext, get_value: Callable[[], str | None],
                 set_value: Callable[[str], None], *,
                 get_search: Callable[[], Mapping[str, Any]] | None = None,
                 step_id: str | Callable[[], str | None] | None = None,
                 preview: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._get_value = get_value
        self._set_value = set_value
        self._get_search = get_search
        self._step_id = step_id
        self.capture_button = QPushButton("Capture…")
        self.capture_button.setToolTip("Capture a template from the screen")
        self.test_button = QPushButton("Test")
        self.test_button.setToolTip("Search the screen for the template now, as the step "
                                    "would")
        self.preview = TemplatePreview(ctx, minimum=QSize(120, 72)) if preview else None
        self.tester = ImageTester(ctx, self)
        self.capture_dialog: CaptureDialog | None = None
        self.result_dialog: TestResultDialog | None = None
        self.last_result: TestResult | None = None
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(self.capture_button)
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(buttons)
        if self.preview is not None:
            layout.addWidget(self.preview, 1)
        layout.addWidget(self.message)
        self.capture_button.clicked.connect(self.open_capture)
        self.test_button.clicked.connect(self.test)
        self.tester.finished.connect(self._tested)
        self.tester.failed.connect(self._test_failed)
        self.refresh()

    def value(self) -> str | None:
        return self._get_value()

    def refresh(self) -> None:
        """Show the field's current value (call it when the value changes)."""
        value = self._get_value()
        if self.preview is not None:
            self.preview.show_template(value)
        self.test_button.setEnabled(bool(value) and not self.tester.busy)

    def step_id(self) -> str | None:
        return self._step_id() if callable(self._step_id) else self._step_id

    def open_capture(self) -> CaptureDialog:
        """The capture dialog (modeless); its capture sets the field's value."""
        dialog = CaptureDialog(self._ctx, step_id=self.step_id(), parent=self.window())
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.captured.connect(self._captured)
        self.capture_dialog = dialog
        dialog.show()
        return dialog

    def test(self) -> None:
        """Search for the template now; the result opens in a dialog."""
        value = self._get_value()
        search = dict(self._get_search() if self._get_search is not None else {})
        search["image"] = value
        try:
            args = find_args(image_target(search))
        except ValueError as exc:
            self.say(str(exc), ERROR_STYLE)
            return
        state = self._ctx.document.state
        self.test_button.setEnabled(False)
        self.say("Searching…")
        self.tester.test(args, state.script_id if state is not None else None)

    def _captured(self, value: str, result: object) -> None:
        version = result.get("version") if isinstance(result, Mapping) else None

        def apply() -> None:
            self._set_value(value)
            self.say(f"Captured {value}.")
            self.captured.emit(value)

        when_document_at(self._ctx, version, apply)

    def _tested(self, result: TestResult) -> None:
        self.last_result = result
        self.test_button.setEnabled(True)
        self.say(result.outcome.summary,
                 f"color: {(FOUND if result.outcome.found else MISSED).name()};")
        dialog = self.result_dialog
        if dialog is None:
            dialog = TestResultDialog(self.window())
            dialog.again.connect(self.test)
            self.result_dialog = dialog
        dialog.show_result(result)
        dialog.show()
        dialog.raise_()
        self.tested.emit(result)

    def _test_failed(self, problem: ToolProblem) -> None:
        self.test_button.setEnabled(True)
        self.report(problem)


class PointFieldTools(_Messages):
    """**Pick…** and **Window…** for an ``x``/``y``/``window`` field (see the module
    docstring for the contract)."""

    picked = Signal(object)
    """The values given to ``set_point`` after a pick or a window choice."""

    def __init__(self, ctx: AppContext, get_point: Callable[[], Mapping[str, Any]],
                 set_point: Callable[[Mapping[str, Any]], None], *,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._get_point = get_point
        self._set_point = set_point
        self.pick_button = QPushButton("Pick…")
        self.pick_button.setToolTip("Click a point on the screen")
        self.window_button = QPushButton("Window…")
        self.window_button.setToolTip("Choose the window the point is relative to")
        self.relative = QCheckBox("Relative to its window")
        self.relative.setChecked(True)
        self.relative.setToolTip("Keep the point relative to the window it is in, so the "
                                 "step still works when the window moves")
        self.chooser: WindowChooser | None = None
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.pick_button)
        row.addWidget(self.window_button)
        row.addWidget(self.relative)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        layout.addWidget(self.message)
        self.pick_button.clicked.connect(self.pick)
        self.window_button.clicked.connect(self.choose_window)

    def pick(self) -> None:
        """Open the overlay; the clicked point becomes the field's value."""
        self.pick_button.setEnabled(False)
        self.say("Click the point; Escape cancels.")
        self._ctx.screen.pick_point(on_result=self.apply_pick, on_error=self._failed)

    def apply_pick(self, result: Mapping[str, Any]) -> None:
        """Set the field from a ``pick_point`` result."""
        self.pick_button.setEnabled(True)
        use_window = self.relative.isChecked()
        values = pick_values(result, point_target(self._get_point()), use_window=use_window)
        self._set_point(values)
        self.say(f"Picked {describe_pick(result, use_window=use_window)}.")
        self.picked.emit(values)

    def choose_window(self) -> WindowChooser:
        chooser = WindowChooser(self._ctx, self.window())
        chooser.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        chooser.chosen.connect(self.apply_window)
        self.chooser = chooser
        chooser.show()
        chooser.refresh()
        return chooser

    def apply_window(self, title: str) -> None:
        values = {"window": {"title": title}}
        self._set_point(values)
        self.say(f"Relative to “{title}”.")
        self.picked.emit(values)

    def _failed(self, exc: BaseException) -> None:
        self.pick_button.setEnabled(True)
        self.report(problem_of(exc))
