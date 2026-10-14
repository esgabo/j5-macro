"""Script params: values a script takes from outside (docs/contract.md, "Params" and
"Secrets").

:func:`check_value` and :func:`resolve` turn the values a ``run`` brings into variables,
following the declared types; the loader uses :data:`PARAM_TYPES` and :func:`check_value`
to validate defaults.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from j5_macro.script.model import ParamType, ScriptParam

PARAM_TYPES: tuple[ParamType, ...] = ("string", "integer", "number", "boolean")
PARAM_FIELDS = ("type", "description", "default", "secret")
MASK = "***"
"""What a secret value is replaced with in events and answers."""
MIN_SECRET_LENGTH = 4
"""Secrets shorter than this are masked only as whole values, never inside longer text:
replacing every ``x`` would garble unrelated output (``"backend": "x11"``)."""

_INTEGER = re.compile(r"^[+-]?\d+$")


@dataclass(frozen=True, slots=True)
class ParamProblem(Exception):
    """A ``run`` param or var that cannot be used: ``reason`` is ``missing``, ``unknown``
    or ``type``; ``kind`` is ``param`` or ``var``."""

    name: str
    reason: str
    message: str
    kind: str = "param"


def check_value(param: ScriptParam, value: Any) -> Any:
    """``value`` converted to the param's type; raises :class:`ParamProblem` (``type``).

    Native values must already have the type (``bool`` is not an integer; an integer is a
    valid number). Strings are converted for the other types only when they are exactly
    an integer, a finite number, or ``true``/``false`` (case-insensitive). String params
    accept strings only and keep them verbatim.
    """
    kind = param.type
    if kind == "string":
        if isinstance(value, str):
            return value
    elif kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
    elif kind == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and _INTEGER.match(value.strip()):
            return int(value.strip())
    elif kind == "number":
        if isinstance(value, int | float) and not isinstance(value, bool):
            if math.isfinite(value):
                return value
        elif isinstance(value, str):
            text = value.strip()
            if _INTEGER.match(text):
                return int(text)
            try:
                number = float(text)
            except ValueError:
                pass
            else:
                if math.isfinite(number):
                    return number
    shown = "a secret value" if param.secret else repr(value)
    raise ParamProblem(param.name, "type",
                       f"param '{param.name}' expects {_article(kind)} {kind}, got {shown}")


def resolve(declared: Mapping[str, ScriptParam], values: Mapping[str, Any] | None,
            vars_: Mapping[str, Any], overrides: Mapping[str, Any] | None
            ) -> dict[str, Any]:
    """The run's initial variables: ``vars_`` with ``overrides`` applied, then every
    declared param (its value, else its default).

    Raises :class:`ParamProblem` for the first unknown name, then for any value of the
    wrong type; for missing required params it raises one problem whose message lists
    them all (``missing_names`` gives the list).
    """
    values = dict(values or {})
    for name in values:
        if name not in declared:
            raise ParamProblem(name, "unknown", f"the script declares no param '{name}'")
    for name in overrides or {}:
        if name not in vars_:
            hint = " (it is a param: pass it as a param)" if name in declared else ""
            raise ParamProblem(name, "unknown",
                               f"the script declares no var '{name}'{hint}", "var")
    missing = [p.name for p in declared.values() if p.required and p.name not in values]
    if missing:
        listed = ", ".join(f"'{name}'" for name in missing)
        noun = "param" if len(missing) == 1 else "params"
        raise ParamProblem(missing[0], "missing", f"missing required {noun}: {listed}")
    variables = {**vars_, **(overrides or {})}
    for param in declared.values():
        variables[param.name] = (check_value(param, values[param.name])
                                 if param.name in values else param.default)
    return variables


def missing_names(declared: Mapping[str, ScriptParam],
                  values: Mapping[str, Any] | None) -> list[str]:
    """Required params without a value, in declaration order."""
    given = values or {}
    return [p.name for p in declared.values() if p.required and p.name not in given]


def secret_values(declared: Mapping[str, ScriptParam],
                  variables: Mapping[str, Any]) -> list[str]:
    """The text of every secret param's value, longest first (the masking order)."""
    found = {str(variables[p.name]) for p in declared.values()
             if p.secret and variables.get(p.name) not in (None, "")}
    return sorted(found, key=len, reverse=True)


def secret_texts(value: Any) -> list[str]:
    """The strings to mask for a result stored with ``as: {secret: true}``: every string
    in ``value`` (itself, or inside mappings and lists, at any depth; keys, numbers and
    booleans are not), and each one without its leading and trailing whitespace, so
    ``strip(token.stdout)`` is masked too. Empty and whitespace-only strings are left out
    (they would mask every space or line break)."""
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            if item.strip():
                found.update((item, item.strip()))
        elif isinstance(item, Mapping):
            for child in item.values():
                visit(child)
        elif isinstance(item, list | tuple):
            for child in item:
                visit(child)

    visit(value)
    return sorted(found, key=len, reverse=True)


def is_short_secret(text: str) -> bool:
    """Whether ``text`` is masked only as a whole value (see :data:`MIN_SECRET_LENGTH`)."""
    return len(text) < MIN_SECRET_LENGTH


def short_secret_params(declared: Mapping[str, ScriptParam],
                        variables: Mapping[str, Any]) -> list[str]:
    """The names of the secret params whose value is short (:func:`is_short_secret`), in
    declaration order."""
    return [p.name for p in declared.values()
            if p.secret and variables.get(p.name) not in (None, "")
            and is_short_secret(str(variables[p.name]))]


def short_secret_warning(what: str) -> str:
    """The warning for a short secret; ``what`` names it (``secret param 'pin'``), since
    the message must never show the value."""
    return (f"{what} has fewer than {MIN_SECRET_LENGTH} characters: it is masked only "
            "where a value is exactly that secret, not inside longer text")


def merge_secrets(secrets: list[str], more: list[str]) -> list[str]:
    """``secrets`` plus ``more``, without duplicates, longest first (the masking order)."""
    return sorted(set(secrets) | set(more), key=len, reverse=True)


POSITION_FIELDS = frozenset({"path", "iterations", "line", "index", "total", "duration_ms"})
"""Top-level payload fields that say where or how long, never what: engine-made integers
that :func:`mask_payload` leaves alone, so a numeric secret never turns ``path: [0]``
into ``["***"]``."""


def _is_position(value: Any) -> bool:
    def number(item: Any) -> bool:
        return isinstance(item, int) and not isinstance(item, bool)
    return number(value) or (isinstance(value, list) and all(number(v) for v in value))


def mask_payload(payload: Mapping[str, Any], secrets: list[str]) -> dict[str, Any]:
    """:func:`mask` over an event payload or a ``get_state`` answer, except for its
    :data:`POSITION_FIELDS` holding integers (or lists of them)."""
    return {k: v if k in POSITION_FIELDS and _is_position(v)
            else mask(v, secrets) for k, v in payload.items()}


def mask(value: Any, secrets: list[str]) -> Any:
    """``value`` (JSON-like) with its secrets replaced by ``***``, in strings and nested
    containers. A string equal to a secret becomes ``***``; inside longer strings only
    the secrets of at least :data:`MIN_SECRET_LENGTH` characters are replaced. Non-string
    scalars equal to a secret's text (a numeric secret) are masked too. Mapping keys are
    never masked: they are names (payload fields, variable names), not values."""
    if not secrets:
        return value
    if isinstance(value, str):
        if value in secrets:
            return MASK
        for secret in secrets:
            if not is_short_secret(secret) and secret in value:
                value = value.replace(secret, MASK)
        return value
    if isinstance(value, Mapping):
        return {k: mask(v, secrets) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [mask(v, secrets) for v in value]
    if (isinstance(value, int | float) and not isinstance(value, bool)
            and str(value) in secrets):
        return MASK
    return value


def _article(word: str) -> str:
    return "an" if word[0] in "aeiou" else "a"
