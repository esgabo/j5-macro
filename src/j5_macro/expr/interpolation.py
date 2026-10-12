"""``${expr}`` interpolation in script values.

A string that is exactly one ``${...}`` (surrounding whitespace allowed) evaluates to the
raw value, keeping its type. Otherwise every ``${...}`` is replaced by the text form of
its value (see :func:`to_text`). ``$${`` writes a literal ``${``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from j5_macro.expr.errors import ExpressionError
from j5_macro.expr.evaluator import compile_expression, evaluate
from j5_macro.expr.functions import to_text


@dataclass(frozen=True, slots=True)
class _Slot:
    expression: str
    start: int
    """Index in the template of the first character of ``expression``."""


def _parse(text: str) -> list[str | _Slot]:
    """Split a template into literal text and ``${}`` slots."""
    parts: list[str | _Slot] = []
    literal: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith("$${", i):
            literal.append("${")
            i += 3
        elif text.startswith("${", i):
            end = _closing_brace(text, i + 2)
            if literal:
                parts.append("".join(literal))
                literal = []
            parts.append(_Slot(text[i + 2 : end], i + 2))
            i = end + 1
        else:
            literal.append(text[i])
            i += 1
    if literal:
        parts.append("".join(literal))
    return parts


def _closing_brace(text: str, start: int) -> int:
    """Index of the ``}`` closing a slot whose expression starts at ``start``."""
    depth = 1
    i = start
    while i < len(text):
        char = text[i]
        if char in "'\"":
            i = _skip_string(text, i)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ExpressionError("unterminated '${'", text, start - 1)


def _skip_string(text: str, start: int) -> int:
    quote_char = text[start]
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote_char:
            return i + 1
        i += 1
    return i


def _in_template(exc: ExpressionError, template: str, slot: _Slot) -> ExpressionError:
    """Re-raise an error from a slot with the column counted in the whole template."""
    column = None if exc.column is None else slot.start + exc.column
    return ExpressionError(exc.problem, exc.expression, column, template=template)


def find_interpolations(text: str) -> list[str]:
    """Return the inner expression of every ``${...}`` in ``text``, in order."""
    return [part.expression for part in _parse(text) if isinstance(part, _Slot)]


def validate_template(text: str) -> None:
    """Compile every ``${...}`` in ``text``; raises :class:`ExpressionError`."""
    for part in _parse(text):
        if isinstance(part, _Slot):
            try:
                compile_expression(part.expression)
            except ExpressionError as exc:
                raise _in_template(exc, text, part) from None


def interpolate(
    value: Any,
    variables: Mapping[str, Any],
    functions: Mapping[str, Callable[..., Any]] | None = None,
) -> Any:
    """Interpolate strings in ``value``, recursing into dict values and lists."""
    if isinstance(value, str):
        return _interpolate_text(value, variables, functions)
    if isinstance(value, dict):
        return {key: interpolate(item, variables, functions) for key, item in value.items()}
    if isinstance(value, list):
        return [interpolate(item, variables, functions) for item in value]
    return value


def _interpolate_text(
    text: str,
    variables: Mapping[str, Any],
    functions: Mapping[str, Callable[..., Any]] | None,
) -> Any:
    if "${" not in text:
        return text
    parts = _parse(text)
    slots = [part for part in parts if isinstance(part, _Slot)]
    whole = len(slots) == 1 and all(isinstance(part, _Slot) or not part.strip() for part in parts)
    values: list[Any] = []
    for slot in slots:
        try:
            values.append(evaluate(slot.expression, variables, functions))
        except ExpressionError as exc:
            raise _in_template(exc, text, slot) from None
    if whole:
        return values[0]
    rendered = iter(values)
    return "".join(to_text(next(rendered)) if isinstance(part, _Slot) else part for part in parts)
