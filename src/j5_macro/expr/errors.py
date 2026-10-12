"""The single error type raised by expressions and interpolation."""

from __future__ import annotations

from typing import Any

from j5_macro.protocol import ErrorCode, ErrorInfo

_MAX_QUOTED = 80


def quote(text: str) -> str:
    """Quote an expression for an error message, shortening very long ones."""
    if len(text) > _MAX_QUOTED:
        text = text[: _MAX_QUOTED - 3] + "..."
    return repr(text)


class ExpressionError(ValueError):
    """An expression or ``${}`` template could not be compiled or evaluated.

    ``info`` is the protocol error (code ``expression_error``). Its ``details`` hold
    ``expression`` and, when known, ``column`` (1-based). For an error inside a ``${}``
    template they also hold ``template``, and ``column`` counts within the template.
    """

    def __init__(
        self,
        problem: str,
        expression: str,
        column: int | None = None,
        template: str | None = None,
    ) -> None:
        self.problem = problem
        self.expression = expression
        self.column = column
        self.template = template
        details: dict[str, Any] = {"expression": expression}
        if column is not None:
            details["column"] = column
        message = f"{problem} in expression {quote(expression)}"
        if template is not None:
            details["template"] = template
            message += f" (in {quote(template)})"
        self.info = ErrorInfo(
            code=ErrorCode.EXPRESSION_ERROR.value, message=message, details=details
        )
        super().__init__(message)
