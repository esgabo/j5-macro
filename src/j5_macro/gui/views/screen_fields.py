"""The screen tools in the step forms: a field tools factory (see
:mod:`j5_macro.gui.views.fields`) that gives

- image fields :class:`ImageFieldTools` (**Capture…**, **Test** and the template's
  preview), searching as the step does (its window, region, threshold and scale);
- point fields (``x``/``y``) :class:`PointFieldTools` (**Pick…** and **Relative to its
  window**); picking a point aims the target at it (an image or ``at`` it had goes);
- window fields a **Window…** button that lists the visible windows.

:func:`install_screen_tools` registers it once per process; the main window calls it.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QWidget

from j5_macro.gui.views.fields import FieldContext, register_field_tools
from j5_macro.gui.views.screen_dialogs import ImageFieldTools, PointFieldTools, WindowChooser

_unregister: Callable[[], None] | None = None


class WindowButton(QPushButton):
    """**Window…**: lists the visible windows; choosing one sets the field."""

    def __init__(self, context: FieldContext) -> None:
        super().__init__("Window…")
        self.context = context
        self.chooser: WindowChooser | None = None
        self.setToolTip("Choose one of the visible windows")
        self.clicked.connect(self.choose)

    def choose(self) -> WindowChooser:
        assert self.context.ctx is not None
        chooser = WindowChooser(self.context.ctx, self.window())
        chooser.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        chooser.chosen.connect(lambda title: self.context.set_value({"title": title}))
        self.chooser = chooser
        chooser.show()
        chooser.refresh()
        return chooser


def screen_field_tools(context: FieldContext) -> list[QWidget]:
    """The screen tools for one field (none without an application context)."""
    ctx = context.ctx
    if ctx is None:
        return []
    if context.kind == "image":
        image = ImageFieldTools(
            ctx, get_value=context.get_value, set_value=context.set_value,
            get_search=context.search, step_id=lambda: context.step_id)
        image.setObjectName(f"imageTools:{context.param}")
        return [image]
    if context.kind == "point":
        point = PointFieldTools(ctx, get_point=context.get_value,
                                set_point=context.set_value)
        point.setObjectName(f"pointTools:{context.param}")
        point.window_button.hide()  # the window field has its own Window… button
        return [point]
    if context.kind == "window":
        button = WindowButton(context)
        button.setObjectName(f"windowTools:{context.param}")
        return [button]
    return []


def install_screen_tools() -> None:
    """Register :func:`screen_field_tools` (once; later calls do nothing)."""
    global _unregister
    if _unregister is None:
        _unregister = register_field_tools(screen_field_tools)


def uninstall_screen_tools() -> None:
    """Remove the screen tools again (for tests)."""
    global _unregister
    if _unregister is not None:
        _unregister()
        _unregister = None
