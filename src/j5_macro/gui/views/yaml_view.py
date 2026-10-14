"""The read-only YAML of the open script, exactly as ``get_script`` returns it."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFontDatabase, QTextCursor, QTextFormat
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

LINE_COLOR = QColor(220, 235, 255)


class YamlView(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setPlaceholderText("No script open")

    def set_source(self, text: str) -> None:
        """Replace the text, keeping the scroll position when it is the same script."""
        if text == self.toPlainText():
            return
        bar = self.verticalScrollBar()
        position = bar.value()
        self.setPlainText(text)
        bar.setValue(position)

    def highlight_line(self, line: int | None) -> None:
        """Mark a 1-based line (a step's) and scroll to it; None clears the mark."""
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
        self.setTextCursor(cursor)
        self.centerCursor()
