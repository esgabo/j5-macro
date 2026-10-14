"""Showing images: templates (a file next to the script or an embedded ``#name``),
captures of failed searches and screenshots with a match drawn over them.

- :class:`ImageView` paints an image scaled to fit (small templates are enlarged, pixel
  for pixel), over a checkerboard so transparent pixels show, with rectangles
  (:class:`Mark`) drawn on top in image coordinates.
- :class:`TemplatePreview` shows the template an ``image`` parameter names, with its
  size and scale. Embedded images come from the engine (``get_image``): the GUI never
  reads the YAML itself.
"""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from j5_macro.gui.context import AppContext
from j5_macro.gui.engine_thread import describe_error
from j5_macro.gui.models.screen import embedded_name, resolve_file

FOUND = QColor("#1e9e3a")
MISSED = QColor("#d32f2f")
CHECKER = (QColor("#ffffff"), QColor("#d9d9d9"))
CHECKER_SIZE = 8
MAX_ZOOM = 4.0
"""Small templates are enlarged up to this factor."""


@dataclass(frozen=True, slots=True)
class Mark:
    """A rectangle drawn over an image, in the image's pixels."""

    rect: tuple[int, int, int, int]
    color: QColor
    label: str = ""


class ImageView(QWidget):
    """An image scaled to fit, over a checkerboard, with marks."""

    def __init__(self, parent: QWidget | None = None, *, placeholder: str = "",
                 minimum: QSize | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._marks: list[Mark] = []
        self._placeholder = placeholder
        self.setMinimumSize(minimum or QSize(120, 90))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    @property
    def image(self) -> QImage | None:
        return self._image

    @property
    def marks(self) -> list[Mark]:
        return list(self._marks)

    def set_image(self, image: QImage | None, placeholder: str | None = None) -> None:
        self._image = image if image is not None and not image.isNull() else None
        if placeholder is not None:
            self._placeholder = placeholder
        self._marks = []
        self.update()

    def set_marks(self, marks: Sequence[Mark]) -> None:
        self._marks = list(marks)
        self.update()

    def image_rect(self) -> QRectF:
        """Where the image is painted, in widget coordinates."""
        if self._image is None:
            return QRectF()
        width, height = self._image.width(), self._image.height()
        area = self.contentsRect().adjusted(4, 4, -4, -4)
        zoom = min(area.width() / width, area.height() / height, MAX_ZOOM)
        shown_w, shown_h = width * zoom, height * zoom
        return QRectF(area.x() + (area.width() - shown_w) / 2,
                      area.y() + (area.height() - shown_h) / 2, shown_w, shown_h)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        if self._image is None:
            painter.setPen(self.palette().placeholderText().color())
            painter.drawText(self.rect().adjusted(8, 8, -8, -8),
                             int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap),
                             self._placeholder)
            painter.end()
            return
        target = self.image_rect()
        _checkerboard(painter, target.toAlignedRect())
        zoom = target.width() / self._image.width()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, zoom < 1)
        painter.drawImage(target, self._image)
        painter.setPen(QPen(QColor("#9e9e9e"), 1))
        painter.drawRect(target.adjusted(-0.5, -0.5, 0.5, 0.5))
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        for mark in self._marks:
            x, y, w, h = mark.rect
            rect = QRectF(target.x() + x * zoom, target.y() + y * zoom, w * zoom, h * zoom)
            painter.setPen(QPen(mark.color, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            if mark.label:
                metrics = painter.fontMetrics()
                text_w = metrics.horizontalAdvance(mark.label) + 8
                text_h = metrics.height() + 2
                top = rect.top() - text_h if rect.top() - text_h >= 0 else rect.bottom()
                left = min(rect.left(), self.width() - text_w)
                box = QRectF(max(0.0, left), top, text_w, text_h)
                painter.fillRect(box, mark.color)
                painter.setPen(QColor("white"))
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, mark.label)
        painter.end()


def _checkerboard(painter: QPainter, rect: QRect) -> None:
    painter.save()
    painter.setClipRect(rect)
    for row, y in enumerate(range(rect.top(), rect.bottom() + 1, CHECKER_SIZE)):
        for column, x in enumerate(range(rect.left(), rect.right() + 1, CHECKER_SIZE)):
            painter.fillRect(x, y, CHECKER_SIZE, CHECKER_SIZE, CHECKER[(row + column) % 2])
    painter.restore()


def load_image(path: str) -> QImage | None:
    """A PNG (or any image Qt reads) from a file; None when it cannot be read."""
    if not os.path.isfile(path):
        return None
    image = QImage(path)
    return None if image.isNull() else image


def decode_image(data: str) -> QImage | None:
    """``get_image``'s base64 ``data`` as an image; None when it is not one."""
    try:
        raw = base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError):
        return None
    image = QImage()
    return image if image.loadFromData(raw) and not image.isNull() else None


class TemplatePreview(QWidget):
    """The template an ``image`` value names: a file (relative to the script) or an
    embedded ``#name`` of the open script, with its size and scale.

    By default the script is the open document (``ctx.document``); pass ``script_id``
    and ``script_path`` to :meth:`show_template` for another one (a run's)."""

    loaded = Signal(object)
    """The new :class:`QImage`, or None when there is none to show."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None, *,
                 minimum: QSize | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._token = 0
        self._image_value: str | None = None
        self.view = ImageView(placeholder="No template", minimum=minimum)
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        self.caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.caption)

    @property
    def image(self) -> QImage | None:
        return self.view.image

    @property
    def value(self) -> str | None:
        return self._image_value

    def show_template(self, image: str | None, *, script_id: str | None = None,
                      script_path: str | None = None,
                      capture_scale: float | None = None,
                      use_document: bool = True) -> None:
        """Show ``image`` (an ``image`` parameter's value). With ``use_document`` the
        script is the open document: its id (for ``#name``), its path (for relative
        files) and its ``capture_scale`` (for the scale shown); else the script given,
        if any."""
        self._token += 1
        token = self._token
        self._image_value = image
        state = self._ctx.document.state
        if use_document and script_id is None and state is not None:
            script_id = state.script_id
            script_path = script_path or state.path
            capture_scale = state.capture_scale if capture_scale is None else capture_scale
        if not image:
            self._show(None, "No template", "")
            return
        if "${" in image:
            self._show(None, "Computed at run time", f"{image}: computed at run time")
            return
        name = embedded_name(image)
        if name is not None:
            if script_id is None:
                where = f" ({script_path})" if script_path else ""
                self._show(None, f"{image}\nembedded in its script",
                           f"{image}: embedded in a script that is not open{where}")
                return
            self._show(None, "Loading…", image)
            self._ctx.screen.get_image(
                script_id, name,
                on_result=lambda result: self._embedded(token, image, result,
                                                        capture_scale),
                on_error=lambda exc: self._failed(token, image, exc))
            return
        path = resolve_file(image, script_path)
        loaded = load_image(path)
        if loaded is None:
            missing = "not found" if not os.path.exists(path) else "not a readable image"
            self._show(None, f"{image}\n{missing}", f"{image}: {missing} ({path})")
            return
        self._show(loaded, "", _caption(image, loaded, "file", capture_scale))

    def _embedded(self, token: int, image: str, result: Mapping[str, Any],
                  capture_scale: float | None) -> None:
        if token != self._token:
            return
        decoded = decode_image(str(result.get("data", "")))
        if decoded is None:
            self._show(None, "Not a readable image", f"{image}: not a readable image")
            return
        scale = result.get("scale")
        scale = float(scale) if isinstance(scale, int | float) else capture_scale
        self._show(decoded, "", _caption(image, decoded, "embedded", scale))

    def _failed(self, token: int, image: str, exc: BaseException) -> None:
        if token != self._token:
            return
        self._show(None, f"{image}\nnot available", f"{image}: {describe_error(exc)}")

    def _show(self, image: QImage | None, placeholder: str, caption: str) -> None:
        self.view.set_image(image, placeholder)
        self.caption.setText(caption)
        self.caption.setVisible(bool(caption))
        self.loaded.emit(image)


def _caption(image: str, loaded: QImage, kind: str, scale: float | None) -> str:
    text = f"{image} · {loaded.width()} x {loaded.height()} px · {kind}"
    if scale is not None and scale != 1:
        text += f" · captured at scale {scale:g}"
    return text
