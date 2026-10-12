"""Functions available to every expression, and the text form of values."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any


def to_text(value: Any) -> str:
    """Render a value as text: None is empty, booleans are ``true``/``false``."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _require_str(name: str, *values: Any) -> None:
    for value in values:
        if not isinstance(value, str):
            raise TypeError(f"{name}() expects text, got {type(value).__name__}")


def _str(value: Any) -> str:
    return to_text(value)


def _int(value: Any) -> int:
    if isinstance(value, str | int | float):
        return int(value)
    raise TypeError(f"int() expects a number or text, got {type(value).__name__}")


def _float(value: Any) -> float:
    if isinstance(value, str | int | float):
        return float(value)
    raise TypeError(f"float() expects a number or text, got {type(value).__name__}")


def _round(value: Any, ndigits: Any = None) -> Any:
    if ndigits is not None and (isinstance(ndigits, bool) or not isinstance(ndigits, int)):
        raise TypeError("round() expects an integer number of digits")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"round() expects a number, got {type(value).__name__}")
    return round(value, ndigits)


def _lower(text: Any) -> str:
    _require_str("lower", text)
    return str(text).lower()


def _upper(text: Any) -> str:
    _require_str("upper", text)
    return str(text).upper()


def _strip(text: Any, chars: Any = None) -> str:
    _require_str("strip", text)
    if chars is not None:
        _require_str("strip", chars)
    return str(text).strip(chars)


def _contains(container: Any, item: Any) -> bool:
    if isinstance(container, str):
        _require_str("contains", item)
    elif not isinstance(container, list | tuple | dict | set | frozenset):
        raise TypeError(f"contains() expects text or a collection, got {type(container).__name__}")
    return item in container


def _startswith(text: Any, prefix: Any) -> bool:
    _require_str("startswith", text, prefix)
    return str(text).startswith(str(prefix))


def _endswith(text: Any, suffix: Any) -> bool:
    _require_str("endswith", text, suffix)
    return str(text).endswith(str(suffix))


DEFAULT_FUNCTIONS: Mapping[str, Callable[..., Any]] = MappingProxyType(
    {
        "len": len,
        "str": _str,
        "int": _int,
        "float": _float,
        "bool": bool,
        "round": _round,
        "min": min,
        "max": max,
        "abs": abs,
        "lower": _lower,
        "upper": _upper,
        "strip": _strip,
        "contains": _contains,
        "startswith": _startswith,
        "endswith": _endswith,
    }
)
"""Functions every expression can call; caller functions with the same name win."""
