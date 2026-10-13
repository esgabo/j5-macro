"""Safe expression evaluator.

Expressions are parsed with :func:`ast.parse` and evaluated by walking the tree here;
nothing is passed to Python's ``eval``. Every node type, operator and operand type is
whitelisted, and every value the expression builds is size-limited, so scripts cannot
reach Python internals, the filesystem, or exhaust memory or CPU. Attribute syntax only
reads the keys of mappings (``label.bbox`` is ``label["bbox"]``): no Python attribute is
ever looked up.
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, NoReturn

from j5_macro.expr.errors import ExpressionError
from j5_macro.expr.functions import DEFAULT_FUNCTIONS

MAX_LENGTH = 2000
"""Maximum expression length in characters."""
MAX_DEPTH = 100
"""Maximum nesting depth of the syntax tree."""
MAX_NODES = 1000
"""Maximum number of syntax tree nodes."""
MAX_SIZE = 100_000
"""Maximum size of a built value: characters of a string, or items of a list/tuple/dict
counted recursively (nested strings count their characters)."""
MAX_INT = 2**63 - 1
"""Maximum magnitude of an integer result."""

_ALIASES: dict[str, Any] = {"true": True, "false": False, "null": None}

_BINARY: dict[type[ast.operator], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%",
}
_REJECTED_BINARY: dict[type[ast.operator], str] = {
    ast.Pow: "**",
    ast.MatMult: "@",
    ast.BitAnd: "&",
    ast.BitOr: "|",
    ast.BitXor: "^",
    ast.LShift: "<<",
    ast.RShift: ">>",
}
_UNARY: dict[type[ast.unaryop], str] = {ast.UAdd: "+", ast.USub: "-", ast.Not: "not"}
_COMPARE: dict[type[ast.cmpop], str] = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.In: "in",
    ast.NotIn: "not in",
}
_REJECTED_NODES: dict[type[ast.AST], str] = {
    ast.Lambda: "lambda is not allowed",
    ast.ListComp: "comprehensions are not allowed",
    ast.SetComp: "comprehensions are not allowed",
    ast.DictComp: "comprehensions are not allowed",
    ast.GeneratorExp: "comprehensions are not allowed",
    ast.JoinedStr: "f-strings are not allowed",
    ast.FormattedValue: "f-strings are not allowed",
    ast.Starred: "unpacking with '*' is not allowed",
    ast.NamedExpr: "assignment ':=' is not allowed",
    ast.Slice: "slices are not allowed",
    ast.Set: "set displays are not supported",
    ast.Await: "await is not allowed",
    ast.Yield: "yield is not allowed",
    ast.YieldFrom: "yield is not allowed",
}
_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Subscript,
    ast.Attribute,
    ast.UnaryOp,
    ast.BinOp,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    *_BINARY,
    *_UNARY,
    *_COMPARE,
)

_Number = (int, float)
_Sequence = (str, list, tuple)


@dataclass(frozen=True, slots=True)
class CompiledExpression:
    """A parsed and validated expression, ready for :func:`evaluate`."""

    text: str
    tree: ast.Expression = field(repr=False, compare=False)
    lead: int = field(default=0, repr=False, compare=False)
    """Characters of leading whitespace removed before parsing."""


def compile_expression(text: str) -> CompiledExpression:
    """Parse and validate ``text``; raises :class:`ExpressionError` on any problem."""
    if not isinstance(text, str):
        raise ExpressionError(f"expected expression text, got {type(text).__name__}", repr(text))
    return _compile_cached(text)


@functools.lru_cache(maxsize=512)
def _compile_cached(text: str) -> CompiledExpression:
    if len(text) > MAX_LENGTH:
        raise ExpressionError(
            f"expression is too long ({len(text)} characters, limit {MAX_LENGTH})", text
        )
    source = text.lstrip()
    lead = len(text) - len(source)
    if not source.strip():
        raise ExpressionError("empty expression", text)
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        column = None
        if exc.lineno is not None and exc.offset is not None:
            column = lead + _line_start(source, exc.lineno) + exc.offset
            column = len(text) + 1 if exc.offset < 1 else min(column, len(text) + 1)
        raise ExpressionError(f"syntax error: {exc.msg}", text, column) from None
    except (RecursionError, MemoryError, ValueError):
        raise ExpressionError("expression is too complex or invalid", text) from None
    compiled = CompiledExpression(text, tree, lead)
    _validate(compiled)
    return compiled


def evaluate(
    expr: str | CompiledExpression,
    variables: Mapping[str, Any],
    functions: Mapping[str, Callable[..., Any]] | None = None,
) -> Any:
    """Evaluate an expression against ``variables``.

    ``functions`` adds callable names to :data:`DEFAULT_FUNCTIONS`, overriding them.
    """
    compiled = expr if isinstance(expr, CompiledExpression) else compile_expression(expr)
    table = {**DEFAULT_FUNCTIONS, **functions} if functions else DEFAULT_FUNCTIONS
    return _Evaluator(compiled, variables, table).visit(compiled.tree.body)


# --- validation -------------------------------------------------------------------


def _validate(compiled: CompiledExpression) -> None:
    count = 0
    stack: list[tuple[ast.AST, int, ast.AST]] = [(compiled.tree, 0, compiled.tree)]
    while stack:
        node, depth, located = stack.pop()
        if hasattr(node, "lineno"):
            located = node  # operators and contexts report their parent's position
        count += 1
        if count > MAX_NODES:
            _fail(compiled, f"expression is too complex (more than {MAX_NODES} nodes)", located)
        if depth > MAX_DEPTH:
            _fail(compiled, f"expression is nested too deeply (limit {MAX_DEPTH})", located)
        _check_node(compiled, node)
        stack.extend(
            (child, depth + 1, located)
            for child in ast.iter_child_nodes(node)
            if not isinstance(child, ast.expr_context)
        )


def _check_node(compiled: CompiledExpression, node: ast.AST) -> None:
    kind = type(node)
    if kind in _REJECTED_NODES:
        _fail(compiled, _REJECTED_NODES[kind], node)
    if kind in _REJECTED_BINARY:
        return  # reported with its position by the BinOp
    if isinstance(node, ast.BinOp) and type(node.op) in _REJECTED_BINARY:
        _fail(compiled, f"operator '{_REJECTED_BINARY[type(node.op)]}' is not allowed", node)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
        _fail(compiled, "operator '~' is not allowed", node)
    if isinstance(node, ast.Compare):
        for op in node.ops:
            if isinstance(op, ast.Is | ast.IsNot):
                _fail(compiled, "'is' is not allowed; use '==' or '!='", node)
    if isinstance(node, ast.Constant):
        _check_constant(compiled, node)
    if isinstance(node, ast.Dict) and any(key is None for key in node.keys):
        _fail(compiled, "unpacking with '**' is not allowed", node)
    if isinstance(node, ast.Call):
        if node.keywords:
            _fail(compiled, "keyword arguments are not allowed", node.keywords[0])
        func = node.func
        if not isinstance(func, ast.Name) and type(func) not in _REJECTED_NODES:
            _fail(compiled, "only functions called by name are allowed", node)
    if isinstance(node, ast.Invert):
        return
    if not isinstance(node, _ALLOWED_NODES):
        _fail(compiled, f"'{kind.__name__}' is not allowed", node)


def _check_constant(compiled: CompiledExpression, node: ast.Constant) -> None:
    value = node.value
    if value is None or isinstance(value, bool | str | float):
        return
    if isinstance(value, int):
        if abs(value) > MAX_INT:
            _fail(compiled, f"integer literal is too large (limit {MAX_INT})", node)
        return
    _fail(compiled, f"literal of type '{type(value).__name__}' is not allowed", node)


# --- evaluation -------------------------------------------------------------------


class _Evaluator:
    def __init__(
        self,
        compiled: CompiledExpression,
        variables: Mapping[str, Any],
        functions: Mapping[str, Callable[..., Any]],
    ) -> None:
        self.compiled = compiled
        self.variables = variables
        self.functions = functions

    def visit(self, node: ast.expr) -> Any:
        match node:
            case ast.Constant(value=value):
                return value
            case ast.Name(id=name):
                return self._name(node, name)
            case ast.List(elts=elts):
                return self._built(node, [self.visit(e) for e in elts])
            case ast.Tuple(elts=elts):
                return self._built(node, tuple(self.visit(e) for e in elts))
            case ast.Dict(keys=keys, values=values):
                return self._dict(node, keys, values)
            case ast.Subscript(value=target, slice=index):
                return self._subscript(node, self.visit(target), self.visit(index))
            case ast.Attribute(value=target, attr=attr):
                return self._attribute(node, self.visit(target), attr)
            case ast.UnaryOp(op=op, operand=operand):
                return self._unary(node, op, self.visit(operand))
            case ast.BinOp(left=left, op=op, right=right):
                return self._binary(node, op, self.visit(left), self.visit(right))
            case ast.BoolOp(op=op, values=values):
                return self._bool(op, values)
            case ast.Compare(left=left, ops=ops, comparators=comparators):
                return self._compare(node, left, ops, comparators)
            case ast.IfExp(test=test, body=body, orelse=orelse):
                return self.visit(body) if self.visit(test) else self.visit(orelse)
            case ast.Call(func=ast.Name(id=name), args=args):
                return self._call(node, name, [self.visit(a) for a in args])
        _fail(self.compiled, f"'{type(node).__name__}' is not allowed", node)

    def _name(self, node: ast.expr, name: str) -> Any:
        if name in _ALIASES:
            return _ALIASES[name]
        try:
            return self.variables[name]
        except KeyError:
            _fail(self.compiled, f"unknown variable '{name}'", node)

    def _dict(self, node: ast.expr, keys: list[ast.expr | None], values: list[ast.expr]) -> Any:
        result: dict[Any, Any] = {}
        for key_node, value_node in zip(keys, values, strict=True):
            assert key_node is not None  # rejected by validation
            key = self.visit(key_node)
            value = self.visit(value_node)
            try:
                result[key] = value
            except TypeError:
                _fail(self.compiled, f"{_type(key)} cannot be a dict key", key_node)
        return self._built(node, result)

    def _subscript(self, node: ast.expr, target: Any, index: Any) -> Any:
        if isinstance(target, dict):
            try:
                return target[index]
            except KeyError:
                _fail(self.compiled, f"key {index!r} not found", node)
            except TypeError:
                _fail(self.compiled, f"{_type(index)} cannot be a dict key", node)
        if isinstance(target, _Sequence):
            if isinstance(index, bool) or not isinstance(index, int):
                _fail(self.compiled, f"index must be an integer, got {_type(index)}", node)
            try:
                return target[index]
            except IndexError:
                _fail(self.compiled, f"index {index} out of range", node)
        _fail(self.compiled, f"cannot index {_type(target)}", node)

    def _attribute(self, node: ast.expr, target: Any, attr: str) -> Any:
        """``mapping.key`` reads the key ``key``; nothing else has attributes (no Python
        attribute is ever looked up)."""
        if not isinstance(target, dict):
            _fail(self.compiled, f"cannot read '.{attr}' of {_type(target)}: only "
                  "mappings have attributes", node)
        try:
            return target[attr]
        except KeyError:
            _fail(self.compiled, f"key {attr!r} not found", node)

    def _unary(self, node: ast.expr, op: ast.unaryop, operand: Any) -> Any:
        if isinstance(op, ast.Not):
            return not operand
        if not isinstance(operand, _Number):
            _fail(
                self.compiled,
                f"unary '{_UNARY[type(op)]}' needs a number, got {_type(operand)}",
                node,
            )
        result = -operand if isinstance(op, ast.USub) else +operand
        return self._number(node, result)

    def _binary(self, node: ast.expr, op: ast.operator, left: Any, right: Any) -> Any:
        symbol = _BINARY[type(op)]
        if isinstance(left, _Number) and isinstance(right, _Number):
            try:
                result = _ARITHMETIC[symbol](left, right)
            except ZeroDivisionError:
                _fail(self.compiled, "division by zero", node)
            except (ArithmeticError, ValueError) as exc:
                _fail(self.compiled, f"arithmetic error: {exc}", node)
            return self._number(node, result)
        if symbol == "+" and type(left) is type(right) and isinstance(left, _Sequence):
            self._limit(node, _header(left) + _content(left) + _content(right))
            return left + right
        if symbol == "*":
            seq, times = (left, right) if isinstance(left, _Sequence) else (right, left)
            if isinstance(seq, _Sequence) and isinstance(times, int):
                self._limit(node, _header(seq) + _content(seq) * max(times, 0))
                return seq * times
        _fail(
            self.compiled,
            f"unsupported operand types for '{symbol}': {_type(left)} and {_type(right)}",
            node,
        )

    def _bool(self, op: ast.boolop, values: list[ast.expr]) -> Any:
        result: Any = None
        for value in values:
            result = self.visit(value)
            if bool(result) == isinstance(op, ast.Or):
                return result
        return result

    def _compare(
        self, node: ast.expr, left: ast.expr, ops: list[ast.cmpop], comparators: list[ast.expr]
    ) -> bool:
        current = self.visit(left)
        for op, right_node in zip(ops, comparators, strict=True):
            right = self.visit(right_node)
            symbol = _COMPARE[type(op)]
            if symbol in ("in", "not in") and not isinstance(
                right, str | list | tuple | dict | set | frozenset
            ):
                _fail(
                    self.compiled,
                    f"'{symbol}' needs text or a collection on the right, got {_type(right)}",
                    node,
                )
            try:
                ok = _COMPARISONS[symbol](current, right)
            except TypeError:
                _fail(
                    self.compiled,
                    f"cannot compare {_type(current)} and {_type(right)} with '{symbol}'",
                    node,
                )
            if not ok:
                return False
            current = right
        return True

    def _call(self, node: ast.expr, name: str, args: list[Any]) -> Any:
        function = self.functions.get(name)
        if function is None:
            _fail(self.compiled, f"unknown function '{name}'", node)
        try:
            result = function(*args)
        except ExpressionError:
            raise
        except (TypeError, ValueError, ArithmeticError, LookupError) as exc:
            _fail(self.compiled, f"{name}() failed: {exc}", node)
        if name in DEFAULT_FUNCTIONS and function is DEFAULT_FUNCTIONS[name]:
            if isinstance(result, int):
                self._number(node, result)
            elif isinstance(result, str):
                self._limit(node, len(result))
        return result

    def _built(self, node: ast.expr, value: Any) -> Any:
        self._limit(node, _size(value))
        return value

    def _number(self, node: ast.expr, value: Any) -> Any:
        if isinstance(value, int) and abs(value) > MAX_INT:
            _fail(self.compiled, f"integer result is too large (limit {MAX_INT})", node)
        return value

    def _limit(self, node: ast.expr, size: int) -> None:
        if size > MAX_SIZE:
            _fail(
                self.compiled, f"result is too large (limit {MAX_SIZE} items or characters)", node
            )


_ARITHMETIC: dict[str, Callable[[Any, Any], Any]] = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a / b,
    "//": lambda a, b: a // b,
    "%": lambda a, b: a % b,
}
_COMPARISONS: dict[str, Callable[[Any, Any], bool]] = {
    "==": lambda a, b: bool(a == b),
    "!=": lambda a, b: bool(a != b),
    "<": lambda a, b: bool(a < b),
    "<=": lambda a, b: bool(a <= b),
    ">": lambda a, b: bool(a > b),
    ">=": lambda a, b: bool(a >= b),
    "in": lambda a, b: a in b,
    "not in": lambda a, b: a not in b,
}


def _size(value: Any) -> int:
    """Recursive size of a value, stopping early once it passes :data:`MAX_SIZE`."""
    total = 0
    stack = [value]
    while stack and total <= MAX_SIZE:
        item = stack.pop()
        if isinstance(item, str):
            total += max(len(item), 1)
        elif isinstance(item, list | tuple):
            total += 1
            stack.extend(item)
        elif isinstance(item, dict):
            total += 1
            stack.extend(item.keys())
            stack.extend(item.values())
        else:
            total += 1
    return total


def _header(seq: str | list[Any] | tuple[Any, ...]) -> int:
    """What :func:`_size` counts for the container itself."""
    return 0 if isinstance(seq, str) else 1


def _content(seq: str | list[Any] | tuple[Any, ...]) -> int:
    """Size of a sequence's contents, without the container itself."""
    return len(seq) if isinstance(seq, str) else _size(seq) - 1


def _type(value: Any) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def _line_start(source: str, lineno: int) -> int:
    """Character index where 1-based line ``lineno`` starts in ``source``."""
    lines = source.split("\n")
    return sum(len(line) + 1 for line in lines[: lineno - 1])


def _column(compiled: CompiledExpression, node: ast.AST) -> int | None:
    lineno = getattr(node, "lineno", None)
    offset = getattr(node, "col_offset", None)
    if lineno is None or offset is None:
        return None
    source = compiled.text[compiled.lead :]
    line = source.split("\n")[lineno - 1]
    chars = len(line.encode()[:offset].decode(errors="ignore"))
    return compiled.lead + _line_start(source, lineno) + chars + 1


def _fail(compiled: CompiledExpression, problem: str, node: ast.AST | None = None) -> NoReturn:
    column = _column(compiled, node) if node is not None else None
    raise ExpressionError(problem, compiled.text, column)
