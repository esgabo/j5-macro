"""Safe expressions and ``${}`` interpolation for scripts (docs/contract.md, "Formato de
script").

Expressions support literals (including ``true``, ``false``, ``null``), variables,
list/tuple/dict displays, indexing, arithmetic, comparisons, ``in``, ``and``/``or``/``not``,
``a if c else b`` and calls to whitelisted functions. They never reach Python's ``eval``.
"""

from j5_macro.expr.errors import ExpressionError
from j5_macro.expr.evaluator import (
    MAX_DEPTH,
    MAX_INT,
    MAX_LENGTH,
    MAX_NODES,
    MAX_SIZE,
    CompiledExpression,
    compile_expression,
    evaluate,
)
from j5_macro.expr.functions import DEFAULT_FUNCTIONS, to_text
from j5_macro.expr.interpolation import find_interpolations, interpolate, validate_template

__all__ = [
    "DEFAULT_FUNCTIONS",
    "MAX_DEPTH",
    "MAX_INT",
    "MAX_LENGTH",
    "MAX_NODES",
    "MAX_SIZE",
    "CompiledExpression",
    "ExpressionError",
    "compile_expression",
    "evaluate",
    "find_interpolations",
    "interpolate",
    "to_text",
    "validate_template",
]
