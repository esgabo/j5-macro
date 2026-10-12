"""Tests for the safe expression evaluator and ``${}`` interpolation."""

from __future__ import annotations

import time
from typing import Any

import pytest

from j5_macro.expr import (
    DEFAULT_FUNCTIONS,
    MAX_DEPTH,
    MAX_INT,
    MAX_LENGTH,
    MAX_SIZE,
    CompiledExpression,
    ExpressionError,
    compile_expression,
    evaluate,
    find_interpolations,
    interpolate,
    to_text,
    validate_template,
)
from j5_macro.protocol import ErrorCode

VARS: dict[str, Any] = {
    "x": 10,
    "y": 3,
    "f": 2.5,
    "name": "Ana",
    "items": [1, 2, 3],
    "pos": {"x": 100, "y": 200},
    "nested": {"list": [{"v": "deep"}]},
    "nothing": None,
    "flag": True,
}


def ev(text: str, **extra: Any) -> Any:
    return evaluate(text, {**VARS, **extra})


def error_of(text: str, variables: dict[str, Any] | None = None) -> ExpressionError:
    with pytest.raises(ExpressionError) as info:
        evaluate(text, VARS if variables is None else variables)
    return info.value


# --- grammar ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("10 - 4 - 3", 3),
        ("7 / 2", 3.5),
        ("7 // 2", 3),
        ("-7 // 2", -4),
        ("7 % 3", 1),
        ("-x", -10),
        ("+y", 3),
        ("-(-x)", 10),
        ("x * f", 25.0),
        ("2 * -3", -6),
        ("1 + 2 * 3 - 4 / 2", 5.0),
        ("x % y + x // y", 4),
    ],
)
def test_arithmetic_and_precedence(text: str, expected: Any) -> None:
    assert ev(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 < 2", True),
        ("x == 10", True),
        ("x != 10", False),
        ("x >= 10", True),
        ("x <= 9", False),
        ("x > y", True),
        ("1 < y < x", True),
        ("1 < x < y", False),
        ("1 < 2 < 3 < 4", True),
        ("3 > 2 > 2", False),
        ("x == 10 == 10.0", True),
        ("name == 'Ana'", True),
        ("'a' < 'b'", True),
    ],
)
def test_comparisons(text: str, expected: bool) -> None:
    assert ev(text) is expected


def test_chained_comparison_short_circuits() -> None:
    assert ev("1 > 2 > undefined") is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("true and false", False),
        ("true or false", True),
        ("not true", False),
        ("not nothing", True),
        ("0 or 'fallback'", "fallback"),
        ("1 and 'last'", "last"),
        ("x > 5 and y < 5", True),
        ("not x > 5 or flag", True),
        ("not (x > 5 and y > 5)", True),
    ],
)
def test_boolean_operators(text: str, expected: Any) -> None:
    assert ev(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "false and undefined_var",
        "true or undefined_var",
        "false and undefined_fn()",
        "flag or 1 / 0",
        "x if true else undefined_var",
        "undefined_var if false else x",
    ],
)
def test_short_circuit_does_not_evaluate_the_other_side(text: str) -> None:
    ev(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2 in items", True),
        ("5 in items", False),
        ("5 not in items", True),
        ("'x' in pos", True),
        ("'z' not in pos", True),
        ("'An' in name", True),
        ("'b' in ('a', 'b')", True),
    ],
)
def test_membership(text: str, expected: bool) -> None:
    assert ev(text) is expected


def test_ternary() -> None:
    assert ev("'big' if x > 5 else 'small'") == "big"
    assert ev("'big' if x > 50 else 'small'") == "small"
    assert ev("1 if false else 2 if false else 3") == 3


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("items[0]", 1),
        ("items[-1]", 3),
        ("pos['x']", 100),
        ('pos["y"] + 1', 201),
        ("nested['list'][0]['v']", "deep"),
        ("name[0]", "A"),
        ("[10, 20, 30][1]", 20),
        ("{'a': 1, 'b': 2}['b']", 2),
        ("(1, 2)[1]", 2),
        ("{1: 'one'}[1]", "one"),
    ],
)
def test_indexing(text: str, expected: Any) -> None:
    assert ev(text) == expected


def test_displays() -> None:
    assert ev("[1, x, name]") == [1, 10, "Ana"]
    assert ev("(1, 2)") == (1, 2)
    assert ev("()") == ()
    assert ev("{'k': x, 'l': [y]}") == {"k": 10, "l": [3]}
    assert ev("[]") == []
    assert ev("{}") == {}


def test_aliases_and_python_constants() -> None:
    assert ev("true") is True
    assert ev("false") is False
    assert ev("null") is None
    assert ev("True") is True
    assert ev("False") is False
    assert ev("None") is None
    assert ev("nothing == null") is True


def test_literals() -> None:
    assert ev("42") == 42
    assert ev("1.5e3") == 1500.0
    assert ev("'single' + \"double\"") == "singledouble"
    assert ev("1_000") == 1000


def test_concatenation_and_repetition() -> None:
    assert ev("name + '!'") == "Ana!"
    assert ev("items + [4]") == [1, 2, 3, 4]
    assert ev("(1,) + (2,)") == (1, 2)
    assert ev("'ab' * 3") == "ababab"
    assert ev("3 * 'ab'") == "ababab"
    assert ev("[0] * 3") == [0, 0, 0]
    assert ev("'ab' * -1") == ""


def test_unicode() -> None:
    assert ev("'héllo ' + nombre", nombre="Señor 日本") == "héllo Señor 日本"
    assert ev("len('日本語')") == 3
    assert ev("upper('ñandú')") == "ÑANDÚ"
    assert ev("año + 1", año=2025) == 2026
    assert ev("'é' in 'café'") is True


def test_whitespace_and_multiline() -> None:
    assert ev("   x + 1  ") == 11
    assert ev("(x +\n y)") == 13


# --- functions --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("len(items)", 3),
        ("len(name)", 3),
        ("len(pos)", 2),
        ("str(x)", "10"),
        ("str(f)", "2.5"),
        ("str(true)", "true"),
        ("str(null)", ""),
        ("int('42')", 42),
        ("int(' 7 ')", 7),
        ("int(3.9)", 3),
        ("float('2.5')", 2.5),
        ("float(2)", 2.0),
        ("bool(0)", False),
        ("bool('x')", True),
        ("round(2.567, 2)", 2.57),
        ("round(2.5)", 2),
        ("round(3.5)", 4),
        ("min(3, 1, 2)", 1),
        ("min(items)", 1),
        ("max(3, 1, 2)", 3),
        ("max(items)", 3),
        ("abs(-4)", 4),
        ("abs(-1.5)", 1.5),
        ("lower('AbC')", "abc"),
        ("upper('AbC')", "ABC"),
        ("strip('  hi  ')", "hi"),
        ("strip('--hi--', '-')", "hi"),
        ("contains(items, 2)", True),
        ("contains(name, 'z')", False),
        ("contains(pos, 'x')", True),
        ("startswith(name, 'An')", True),
        ("startswith(name, 'na')", False),
        ("endswith(name, 'na')", True),
        ("endswith(name, 'An')", False),
    ],
)
def test_default_functions(text: str, expected: Any) -> None:
    result = ev(text)
    assert result == expected
    assert type(result) is type(expected)


def test_default_function_names() -> None:
    assert set(DEFAULT_FUNCTIONS) == {
        "len", "str", "int", "float", "bool", "round", "min", "max", "abs",
        "lower", "upper", "strip", "contains", "startswith", "endswith",
    }  # fmt: skip


@pytest.mark.parametrize(
    "text",
    [
        "len(5)",
        "int('abc')",
        "int([1])",
        "float('x')",
        "min()",
        "max([])",
        "abs('a')",
        "lower(5)",
        "upper(null)",
        "strip(1)",
        "contains(5, 1)",
        "contains('abc', 1)",
        "startswith(1, 'a')",
        "endswith('a', 1)",
        "round('a')",
        "round(1.5, 'a')",
        "int(1e300)",
        "int(float('inf'))",
    ],
)
def test_default_function_errors(text: str) -> None:
    error = error_of(text)
    assert error.info.code == ErrorCode.EXPRESSION_ERROR


def test_caller_functions() -> None:
    calls: list[tuple[Any, ...]] = []

    def image_visible(path: str, threshold: float = 0.9) -> bool:
        calls.append((path, threshold))
        return path == "ok.png"

    functions = {"image_visible": image_visible}
    assert evaluate("image_visible('ok.png')", {}, functions) is True
    assert evaluate("not image_visible('no.png', 0.5)", {}, functions) is True
    assert calls == [("ok.png", 0.9), ("no.png", 0.5)]


def test_caller_functions_override_defaults() -> None:
    assert evaluate("len('abc')", {}, {"len": lambda value: 99}) == 99
    assert evaluate("upper('a')", {}, {"len": lambda value: 99}) == "A"


def test_caller_function_errors_are_wrapped() -> None:
    def broken(value: Any) -> Any:
        raise ValueError("bad value")

    with pytest.raises(ExpressionError, match=r"broken\(\) failed: bad value"):
        evaluate("broken(1)", {}, {"broken": broken})


def test_unknown_function() -> None:
    error = error_of("nope(1)")
    assert "unknown function 'nope'" in str(error)


def test_variables_are_not_callable() -> None:
    error = error_of("fn(1)", {"fn": len})
    assert "unknown function 'fn'" in str(error)


def test_functions_are_not_variables() -> None:
    assert "unknown variable 'len'" in str(error_of("len"))


# --- errors -----------------------------------------------------------------------


def test_unknown_variable() -> None:
    error = error_of("x + missing")
    assert error.info.code == "expression_error"
    assert error.info.message == "unknown variable 'missing' in expression 'x + missing'"
    assert error.info.details == {"expression": "x + missing", "column": 5}


def test_unknown_variable_does_not_fall_back_to_none() -> None:
    with pytest.raises(ExpressionError):
        evaluate("missing == null", {})


def test_error_is_value_error_with_error_info_json() -> None:
    error = error_of("1 / 0")
    assert isinstance(error, ValueError)
    assert error.info.to_json() == {
        "code": "expression_error",
        "message": "division by zero in expression '1 / 0'",
        "details": {"expression": "1 / 0", "column": 1},
    }


@pytest.mark.parametrize("text", ["1 / 0", "1 // 0", "1 % 0", "1.5 / 0", "x / (y - 3)", "1 % 0.0"])
def test_division_by_zero(text: str) -> None:
    assert "division by zero" in str(error_of(text))


def test_columns() -> None:
    assert error_of("  x + missing").info.details == {"expression": "  x + missing", "column": 7}
    assert error_of("'é' + missing").column == 7
    assert error_of("x +").column == 4  # end of input
    assert error_of("(x\n+ missing)").column == 6


def test_long_expression_is_shortened_in_message() -> None:
    text = "missing + " + " + ".join(["1"] * 40)
    message = error_of(text).info.message
    assert "..." in message
    assert error_of(text).info.details is not None
    assert error_of(text).expression == text


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("().__class__", "attribute access"),
        ("().__class__.__bases__[0].__subclasses__()", "attribute access"),
        ("name.upper()", "attribute access"),
        ("'abc'.join(items)", "attribute access"),
        ("x.real", "attribute access"),
        ("2 ** 10", "'**'"),
        ("9 ** 9 ** 9", "'**'"),
        ("lambda: 1", "lambda"),
        ("(lambda a: a)(1)", "lambda"),
        ("[i for i in items]", "comprehension"),
        ("{i: i for i in items}", "comprehension"),
        ("{i for i in items}", "comprehension"),
        ("len(i for i in items)", "comprehension"),
        ("f'{x}'", "f-string"),
        ("[*items]", "'*'"),
        ("len(*items)", "'*'"),
        ("{**pos}", "'**'"),
        ("(z := 1)", "':='"),
        ("len(items, key=1)", "keyword arguments"),
        ("x is None", "'is'"),
        ("x is not None", "'is'"),
        ("x & 1", "'&'"),
        ("x | 1", "'|'"),
        ("x ^ 1", "'^'"),
        ("x << 1", "'<<'"),
        ("x >> 1", "'>>'"),
        ("~x", "'~'"),
        ("x @ y", "'@'"),
        ("items[0:2]", "slices"),
        ("items[::-1]", "slices"),
        ("{1, 2}", "set"),
        ("b'abc'", "bytes"),
        ("1j", "complex"),
        ("...", "ellipsis"),
        ("items[0](1)", "by name"),
        ("x = 1", "syntax error"),
        ("import os", "syntax error"),
        ("x; y", "syntax error"),
        ("await x", "await"),
    ],
)
def test_rejected_constructs(text: str, problem: str) -> None:
    with pytest.raises(ExpressionError) as info:
        compile_expression(text)
    assert problem in str(info.value)
    assert info.value.info.code == "expression_error"


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os')",
        "eval('1')",
        "exec('x = 1')",
        "open('/etc/passwd')",
        "getattr(name, 'upper')",
        "globals()",
        "type(x)",
        "vars()",
        "compile('1', 'f', 'eval')",
    ],
)
def test_dangerous_builtins_are_unknown_functions(text: str) -> None:
    assert "unknown function" in str(error_of(text))


def test_dunder_names_are_plain_variables() -> None:
    assert "unknown variable '__builtins__'" in str(error_of("__builtins__"))


@pytest.mark.parametrize(
    "text",
    [
        "'a' + 1",
        "1 + 'a'",
        "items + 1",
        "items + (1,)",
        "'a' - 'b'",
        "'%s' % x",
        "'%0999999999d' % 1",
        "items * 1.5",
        "items * items",
        "-name",
        "+name",
        "pos + pos",
        "null + 1",
    ],
)
def test_unsupported_operand_types(text: str) -> None:
    with pytest.raises(ExpressionError):
        ev(text)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("items[5]", "out of range"),
        ("items['a']", "integer"),
        ("items[true]", "integer"),
        ("items[1.0]", "integer"),
        ("pos['z']", "not found"),
        ("pos[[1]]", "dict key"),
        ("x[0]", "cannot index"),
        ("{[1]: 2}", "dict key"),
        ("1 < 'a'", "cannot compare"),
        ("1 in 5", "'in'"),
        ("[1] in pos", "cannot compare"),
    ],
)
def test_runtime_errors(text: str, problem: str) -> None:
    assert problem in str(error_of(text))


def test_compile_requires_text() -> None:
    with pytest.raises(ExpressionError):
        compile_expression(5)  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_empty_expression(text: str) -> None:
    with pytest.raises(ExpressionError, match="empty expression"):
        compile_expression(text)


# --- compiled expressions ---------------------------------------------------------


def test_compile_once_evaluate_many() -> None:
    compiled = compile_expression("x * 2")
    assert isinstance(compiled, CompiledExpression)
    assert compiled.text == "x * 2"
    assert [evaluate(compiled, {"x": n}) for n in range(3)] == [0, 2, 4]


def test_compile_does_not_check_names() -> None:
    compiled = compile_expression("later_fn(later_var)")
    assert evaluate(compiled, {"later_var": 2}, {"later_fn": lambda v: v + 1}) == 3


def test_evaluate_does_not_mutate_variables() -> None:
    variables = {"items": [1, 2]}
    assert evaluate("items + [3]", variables) == [1, 2, 3]
    assert variables == {"items": [1, 2]}


# --- resource limits --------------------------------------------------------------


def test_expression_length_limit() -> None:
    text = "1" + " + 1" * MAX_LENGTH
    with pytest.raises(ExpressionError, match="too long"):
        compile_expression(text)
    with pytest.raises(ExpressionError, match="too long"):
        compile_expression("'" + "a" * MAX_LENGTH + "'")


@pytest.mark.parametrize(
    "text",
    [
        "-" * (MAX_DEPTH + 5) + "1",
        "[" * 150 + "]" * 150,
        "(" * 300 + "1" + ")" * 300,
        "not " * 300 + "x",
        " + ".join(["1"] * (MAX_DEPTH + 5)),
        "len(" * 150 + "'a'" + ")" * 150,
    ],
)
def test_deep_nesting_is_rejected(text: str) -> None:
    with pytest.raises(ExpressionError):
        compile_expression(text)


def test_moderate_nesting_is_fine() -> None:
    assert ev(" + ".join(["1"] * 50)) == 50
    assert ev("-" * 50 + "1") == 1
    assert ev("[" * 30 + "]" * 30) is not None


def test_node_count_limit() -> None:
    text = "[" + "-1," * 600 + "]"
    assert len(text) <= MAX_LENGTH
    with pytest.raises(ExpressionError, match="too complex"):
        compile_expression(text)


@pytest.mark.parametrize(
    "text",
    [
        "'a' * 1000000000",
        "1000000000 * 'a'",
        "[0] * 1000000000",
        "[0] * (MAX + 1)",
        "'ab' * (MAX // 2 + 1)",
        "(0,) * 999999999999",
        "[[0] * 1000] * 1000",
        "[[[0] * 100] * 100] * 100",
        "['x' * 1000] * 1000",
        "[big, big]",
        "{'a': big, 'b': big}",
        "(big,) + (big,)",
    ],
)
def test_huge_repetition_is_rejected(text: str) -> None:
    variables = {"MAX": MAX_SIZE, "big": "x" * (MAX_SIZE // 2 + 1)}
    started = time.monotonic()
    with pytest.raises(ExpressionError, match="too large"):
        evaluate(text, variables)
    assert time.monotonic() - started < 1


def test_repetition_up_to_the_limit_is_fine() -> None:
    assert len(evaluate("'a' * MAX", {"MAX": MAX_SIZE})) == MAX_SIZE
    assert len(evaluate("[0] * (MAX - 1)", {"MAX": MAX_SIZE})) == MAX_SIZE - 1


def test_huge_concatenation_in_a_chain_of_plus() -> None:
    chunk = "x" * 30_000
    with pytest.raises(ExpressionError, match="too large"):
        evaluate("s + s + s + s", {"s": chunk})
    with pytest.raises(ExpressionError, match="too large"):
        evaluate("(s * 30000) + (s * 30000)", {"s": "ab"})
    with pytest.raises(ExpressionError, match="too large"):
        evaluate("l + l + l + l", {"l": [0] * 30_000})
    assert len(evaluate("s + s + s", {"s": chunk})) == 90_000


def test_doubling_through_repetition_and_plus() -> None:
    text = "a" + "".join(f" + a * {n}" for n in range(1, 60))
    with pytest.raises(ExpressionError, match="too large"):
        evaluate(text, {"a": "x" * 100})


def test_integer_magnitude_limit() -> None:
    with pytest.raises(ExpressionError, match="too large"):
        evaluate("n * n", {"n": 2**40})
    with pytest.raises(ExpressionError, match="too large"):
        evaluate("n + 1", {"n": MAX_INT})
    with pytest.raises(ExpressionError, match="too large"):
        evaluate("-n - 2", {"n": MAX_INT})
    with pytest.raises(ExpressionError, match="too large"):
        compile_expression("99999999999999999999999")
    with pytest.raises(ExpressionError, match="too large"):
        evaluate("int('9' * 30)", {})
    with pytest.raises(ExpressionError, match="too large"):
        evaluate("round(1e30)", {})
    assert evaluate("n", {"n": MAX_INT}) == MAX_INT
    assert evaluate("n * 1", {"n": MAX_INT}) == MAX_INT


def test_repeated_squaring_cannot_blow_up() -> None:
    text = "n" + " * n" * 40
    with pytest.raises(ExpressionError, match="too large"):
        evaluate(text, {"n": 1000})


def test_floats_overflow_to_infinity() -> None:
    assert evaluate("1e308 * 10", {}) == float("inf")


def test_default_string_functions_are_size_limited() -> None:
    with pytest.raises(ExpressionError, match="too large"):
        evaluate("upper(s)", {"s": "ß" * 60_000})


# --- interpolation ----------------------------------------------------------------


def test_whole_string_keeps_the_type() -> None:
    assert interpolate("${x}", VARS) == 10
    assert interpolate("  ${ x }  ", VARS) == 10
    assert interpolate("${f}", VARS) == 2.5
    assert interpolate("${items}", VARS) == [1, 2, 3]
    assert interpolate("${pos}", VARS) == {"x": 100, "y": 200}
    assert interpolate("${nothing}", VARS) is None
    assert interpolate("${flag}", VARS) is True
    assert interpolate("${x + 1}", VARS) == 11


def test_mixed_text_renders_values() -> None:
    assert interpolate("Hola ${name}!", VARS) == "Hola Ana!"
    assert interpolate("${x},${y}", VARS) == "10,3"
    assert interpolate("x=${x} f=${f}", VARS) == "x=10 f=2.5"
    assert interpolate("${items} ${pos}", VARS) == "[1, 2, 3] {'x': 100, 'y': 200}"
    assert interpolate("${name}${name}", VARS) == "AnaAna"


def test_none_and_bool_rendering() -> None:
    assert interpolate("[${nothing}]", VARS) == "[]"
    assert interpolate("is ${flag}", VARS) == "is true"
    assert interpolate("is ${not flag}", VARS) == "is false"
    assert to_text(None) == ""
    assert to_text(True) == "true"
    assert to_text(False) == "false"
    assert to_text(0) == "0"
    assert to_text("s") == "s"


def test_escape() -> None:
    assert interpolate("$${x}", VARS) == "${x}"
    assert interpolate("cost: $${x} = ${x}", VARS) == "cost: ${x} = 10"
    assert interpolate("$x and $ { x }", VARS) == "$x and $ { x }"
    assert interpolate("$${", VARS) == "${"
    assert interpolate("  $${x}  ", VARS) == "  ${x}  "


def test_braces_and_quotes_inside_expressions() -> None:
    assert interpolate("${ {'a': '}'}['a'] }", VARS) == "}"
    assert interpolate("v=${ {'k': x}['k'] }!", VARS) == "v=10!"
    assert interpolate("${'it\\'s'}", VARS) == "it's"
    assert interpolate("${'${x}'}", VARS) == "${x}"


def test_strings_without_interpolation_are_unchanged() -> None:
    assert interpolate("plain text", VARS) == "plain text"
    assert interpolate("", VARS) == ""
    assert interpolate("100$ {}", VARS) == "100$ {}"


def test_nested_structures() -> None:
    value = {
        "click": {"x": "${pos['x']}", "y": "${pos['y'] + 5}"},
        "text": ["${name}", "id ${x}", 7, None, True],
        "${x}": "keys are untouched",
        "count": 3,
    }
    assert interpolate(value, VARS) == {
        "click": {"x": 100, "y": 205},
        "text": ["Ana", "id 10", 7, None, True],
        "${x}": "keys are untouched",
        "count": 3,
    }


def test_other_types_pass_through() -> None:
    marker = object()
    assert interpolate(marker, VARS) is marker
    assert interpolate(5, VARS) == 5
    assert interpolate(("${x}",), VARS) == ("${x}",)
    assert interpolate(None, VARS) is None


def test_interpolate_does_not_mutate_input() -> None:
    value = {"a": ["${x}"]}
    interpolate(value, VARS)
    assert value == {"a": ["${x}"]}


def test_interpolate_with_caller_functions() -> None:
    assert interpolate("${double(x)}", VARS, {"double": lambda v: v * 2}) == 20


def test_interpolate_unicode() -> None:
    assert interpolate("¡Hola ${nombre}! ✓", {"nombre": "Señora 日本"}) == "¡Hola Señora 日本! ✓"


@pytest.mark.parametrize("text", ["${x", "a ${x", "${ {'a': 1}", "${'}", "${x} ${"])
def test_unterminated(text: str) -> None:
    with pytest.raises(ExpressionError, match="unterminated") as info:
        interpolate(text, VARS)
    assert info.value.info.code == "expression_error"
    with pytest.raises(ExpressionError, match="unterminated"):
        find_interpolations(text)


def test_unterminated_column() -> None:
    with pytest.raises(ExpressionError) as info:
        interpolate("ab ${x", VARS)
    assert info.value.column == 4


def test_interpolation_errors_point_into_the_template() -> None:
    with pytest.raises(ExpressionError) as info:
        interpolate("Hi ${name} ${x + missing}", VARS)
    error = info.value
    assert error.info.code == "expression_error"
    assert error.info.details == {
        "expression": "x + missing",
        "column": 18,
        "template": "Hi ${name} ${x + missing}",
    }
    assert "unknown variable 'missing'" in error.info.message
    assert "Hi ${name}" in error.info.message
    assert "Hi ${name} ${x + missing}"[18 - 1 :].startswith("missing")


def test_empty_interpolation() -> None:
    with pytest.raises(ExpressionError, match="empty expression"):
        interpolate("a ${} b", VARS)


def test_find_interpolations() -> None:
    assert find_interpolations("Hi ${name}, ${ x + 1 }") == ["name", " x + 1 "]
    assert find_interpolations("$${x} ${y}") == ["y"]
    assert find_interpolations("nothing here") == []
    assert find_interpolations("${ {'a': '}'}['a'] }") == [" {'a': '}'}['a'] "]


def test_validate_template() -> None:
    validate_template("Hi ${name} and ${undefined_is_fine_at_load_time}")
    validate_template("no interpolation")
    with pytest.raises(ExpressionError) as info:
        validate_template("a ${x.y}")
    assert info.value.info.details == {"expression": "x.y", "column": 5, "template": "a ${x.y}"}
    with pytest.raises(ExpressionError, match="syntax error"):
        validate_template("${x +}")
    with pytest.raises(ExpressionError, match="unterminated"):
        validate_template("${x")
