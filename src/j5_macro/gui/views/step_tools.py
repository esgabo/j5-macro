"""The screen tools of the selected step (the "Screen" tab and the Tools menu).

For the step selected in the tree, one :class:`ImageFieldTools` per image the step
searches for (its ``image``, a ``drag`` endpoint, an ``if``/``loop`` condition's
``image_visible``) and one :class:`PointFieldTools` per point it clicks or moves to.
Values are read from the open document (``ctx.document.state``) and written with
``ctx.document.edit`` (an ``update`` edit); the panel follows ``state_changed``.

The field tools are the same components the step forms use; this panel makes them
usable on any step until then, and gives the Tools menu something to act on.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from j5_macro.gui.context import AppContext
from j5_macro.gui.engine_thread import describe_error
from j5_macro.gui.models.screen import (
    ImageTarget,
    PointTarget,
    find_target,
    image_targets,
    image_values,
    point_targets,
    target_edit,
)
from j5_macro.gui.models.script import ScriptState
from j5_macro.gui.models.steps import StepItem, find_step
from j5_macro.gui.views.screen_dialogs import (
    ImageFieldTools,
    PointFieldTools,
    WindowChooser,
)


class StepToolsPanel(QWidget):
    message = Signal(str)
    """Something to show in the status bar."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._step_id: str | None = None
        self._shape: tuple[Any, ...] | None = None
        self._requests = 0
        self.image_tools: dict[str | None, ImageFieldTools] = {}
        self.point_tools: dict[str | None, PointFieldTools] = {}
        self.point_labels: dict[str | None, QLabel] = {}
        self.heading = QLabel()
        self.heading.setTextFormat(Qt.TextFormat.RichText)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.body)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.heading)
        layout.addWidget(scroll, 1)
        ctx.document.state_changed.connect(self._state_changed)
        self._apply(None)

    # -- the selected step ---------------------------------------------------------------

    @property
    def step_id(self) -> str | None:
        return self._step_id

    def step(self) -> StepItem | None:
        state = self._ctx.document.state
        if state is None or self._step_id is None:
            return None
        return find_step(state.steps, self._step_id)

    def set_step(self, step_id: str | None) -> None:
        """Show the tools of a step (the tree selects it again after every reload: the
        same step keeps its widgets and their messages)."""
        self._requests += 1
        if step_id is None:
            # The tree clears its selection while it reloads, then selects the step
            # again: only clear the panel when no step comes back.
            request = self._requests
            QTimer.singleShot(0, lambda: self._apply(None) if request == self._requests
                              else None)
            return
        self._apply(step_id)

    def _apply(self, step_id: str | None) -> None:
        same = step_id == self._step_id and self._shape is not None
        self._step_id = step_id
        self._rebuild(force=not same)

    def _state_changed(self, state: ScriptState | None) -> None:
        if state is None:
            self._step_id = None
        self._rebuild(force=False)

    def _rebuild(self, force: bool) -> None:
        step = self.step()
        images = image_targets(step) if step is not None else []
        points = point_targets(step) if step is not None else []
        shape = (step.id if step else None, tuple(t.param for t in images),
                 tuple(t.param for t in points))
        if not force and shape == self._shape:
            self._refresh_values()
            return
        self._shape = shape
        self._clear()
        if step is None:
            self.heading.setText("<i>Select a step in the tree to capture its template, "
                                 "test its search or pick its point.</i>")
            return
        self.heading.setText(f"<b>{_escape(step.id)}</b> <span style='color:gray'>"
                             f"({_escape(step.type)})</span>")
        if not images and not points:
            note = QLabel("This step neither searches for an image nor clicks a point.")
            note.setWordWrap(True)
            self.body_layout.addWidget(note)
        for target in images:
            self._add_image(target)
        for point in points:
            self._add_point(point)
        self.body_layout.addStretch(1)

    def _clear(self) -> None:
        self.image_tools.clear()
        self.point_tools.clear()
        self.point_labels.clear()
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _add_image(self, target: ImageTarget) -> None:
        param = target.param
        box = QGroupBox(target.label)
        tools = ImageFieldTools(
            self._ctx,
            get_value=lambda: self._image_value(param),
            set_value=lambda value: self.set_image(param, value),
            get_search=lambda: self._search_of(param),
            step_id=lambda: self._step_id)
        tools.message_changed.connect(self.message)
        layout = QVBoxLayout(box)
        layout.addWidget(tools)
        self.body_layout.addWidget(box)
        self.image_tools[param] = tools

    def _add_point(self, target: PointTarget) -> None:
        param = target.param
        box = QGroupBox(target.label)
        label = QLabel(_point_text(target))
        tools = PointFieldTools(self._ctx, get_point=lambda: self._point_values(param),
                                set_point=lambda values: self.set_point(param, values))
        tools.message_changed.connect(self.message)
        layout = QVBoxLayout(box)
        layout.addWidget(label)
        layout.addWidget(tools)
        self.body_layout.addWidget(box)
        self.point_tools[param] = tools
        self.point_labels[param] = label

    def _refresh_values(self) -> None:
        for tools in self.image_tools.values():
            tools.refresh()
        step = self.step()
        for target in point_targets(step) if step is not None else []:
            label = self.point_labels.get(target.param)
            if label is not None:
                label.setText(_point_text(target))

    # -- reading and writing targets -----------------------------------------------------

    def _image_target(self, param: str | None) -> ImageTarget | None:
        step = self.step()
        return find_target(image_targets(step), param) if step is not None else None

    def _image_value(self, param: str | None) -> str | None:
        target = self._image_target(param)
        return target.image if target is not None else None

    def _search_of(self, param: str | None) -> Mapping[str, Any]:
        target = self._image_target(param)
        if target is None:
            return {}
        search: dict[str, Any] = {}
        if target.window is not None:
            search["window"] = {"title": target.window}
        if target.region is not None:
            search["region"] = list(target.region)
        elif target.region_text is not None:
            search["region"] = target.region_text
        if target.threshold is not None:
            search["threshold"] = target.threshold
        if target.scale is not None:
            search["scale"] = target.scale
        return search

    def _point_values(self, param: str | None) -> Mapping[str, Any]:
        step = self.step()
        if step is None:
            return {}
        values = step.params if param is None else step.params.get(param)
        return values if isinstance(values, Mapping) else {}

    def set_image(self, param: str | None, value: str) -> None:
        """Make the step's target ``param`` search for ``value``."""
        step = self.step()
        if step is None:
            return
        self._edit(target_edit(step, param, image_values(self._image_target(param), value)))

    def set_point(self, param: str | None, values: Mapping[str, Any]) -> None:
        """Set the step's point target ``param`` (``pick_values`` and the like)."""
        step = self.step()
        if step is None:
            return
        self._edit(target_edit(step, param, values))

    def _edit(self, edit: Mapping[str, Any]) -> None:
        if self._ctx.document.state is None:
            return
        self._ctx.document.edit([edit], on_error=lambda exc: self.message.emit(
            f"cannot change the step: {describe_error(exc)}"))

    # -- the Tools menu ------------------------------------------------------------------

    def first_image_tools(self) -> ImageFieldTools | None:
        return next(iter(self.image_tools.values()), None)

    def first_point_tools(self) -> PointFieldTools | None:
        return next(iter(self.point_tools.values()), None)

    def capture(self) -> None:
        tools = self.first_image_tools()
        if tools is None:
            self.message.emit("Select a step that searches for an image (click, move, "
                              "wait_image_in...) to capture its template.")
            return
        tools.open_capture()

    def test(self) -> None:
        tools = self.first_image_tools()
        if tools is None:
            self.message.emit("Select a step that searches for an image to test it.")
            return
        tools.test()

    def pick(self) -> None:
        tools = self.first_point_tools()
        if tools is None:
            self.message.emit("Select a click, move or drag step to pick its point.")
            return
        tools.pick()

    def windows(self) -> WindowChooser:
        """The window list; with a point step selected, choosing sets its window."""
        tools = self.first_point_tools()
        if tools is not None:
            return tools.choose_window()
        chooser = WindowChooser(self._ctx, self.window())
        chooser.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        chooser.chosen.connect(lambda title: self.message.emit(f"Window: {title}"))
        chooser.show()
        chooser.refresh()
        return chooser


def _point_text(target: PointTarget) -> str:
    if target.x is None or target.y is None:
        where = "an image" if target.has_image else "not set"
    else:
        where = f"x={target.x}, y={target.y}"
    if target.window:
        where += f" in “{target.window}”"
    return where


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
