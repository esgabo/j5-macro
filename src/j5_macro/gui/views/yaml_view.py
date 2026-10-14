"""The read-only YAML of the open script, exactly as ``get_script`` returns it.

It follows the selection: :meth:`highlight_line` marks the selected step's line, and a
click on a line emits ``line_clicked`` so the editor selects the step written there.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QFontDatabase, QMouseEvent, QTextCursor, QTextFormat
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

LINE_COLOR = QColor(220, 235, 255)


class YamlView(QPlainTextEdit):
    line_clicked = Signal(int)
    """The user clicked this 1-based line."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setPlaceholderText("No script open")
        self._line: int | None = None

    def set_source(self, text: str) -> None:
        """Replace the text, keeping the scroll position when it is the same script."""
        if text == self.toPlainText():
            return
        bar = self.verticalScrollBar()
        position = bar.value()
        self.setPlainText(text)
        bar.setValue(position)
        if self._line is not None:
            self.highlight_line(self._line, scroll=False)

    def highlighted_line(self) -> int | None:
        return self._line

    def highlight_line(self, line: int | None, *, scroll: bool = True) -> None:
        """Mark a 1-based line (a step's) and scroll to it; None clears the mark."""
        self._line = line
        if line is None or line < 1:
            self.setExtraSelections([])
            return
        block = self.document().findBlockByNumber(line - 1)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(LINE_COLOR)
        selection.format.setProperty(
            QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = cursor
        self.setExtraSelections([selection])
        if scroll:
            self.setTextCursor(cursor)
            self.centerCursor()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if self.textCursor().hasSelection():
            return  # selecting text to copy it
        line = self.cursorForPosition(event.position().toPoint()).blockNumber() + 1
        self.line_clicked.emit(line)
