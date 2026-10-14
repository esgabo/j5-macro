"""The variables of a paused run, as ``get_state`` reports them, and editing them.

Secret values arrive masked as ``***`` (docs/contract.md, "Secrets"), also inside
longer strings, lists and maps. They stay masked here, and the GUI never edits a
variable that holds a mask (:func:`editable_text` is None): writing the mask back would
replace the secret with the text ``***``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

MASK = "***"


@dataclass(frozen=True, slots=True)
class VariableRow:
    name: str
    """The variable's name, or the key or index inside its parent."""
    value: Any
    text: str
    """The value as JSON, on one line."""
    masked: bool
    """The value holds a masked secret (somewhere inside, for lists and maps)."""
    children: tuple[VariableRow, ...] = ()


def contains_mask(value: Any) -> bool:
    if isinstance(value, str):
        return MASK in value
    if isinstance(value, list):
        return any(contains_mask(item) for item in value)
    if isinstance(value, dict):
        return any(contains_mask(item) for item in value.values())
    return False


def format_value(value: Any, limit: int = 200) -> str:
    """The value as compact JSON (strings quoted), cut at ``limit`` characters."""
    text = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return text if len(text) <= limit else text[:limit - 1] + "…"


def variable_rows(variables: Mapping[str, Any] | None) -> list[VariableRow]:
    """One row per variable, in the engine's order; lists and maps have child rows."""
    return [_row(str(name), value) for name, value in (variables or {}).items()]


def _row(name: str, value: Any) -> VariableRow:
    children: tuple[VariableRow, ...] = ()
    if isinstance(value, dict):
        children = tuple(_row(str(key), item) for key, item in value.items())
    elif isinstance(value, list):
        children = tuple(_row(f"[{index}]", item) for index, item in enumerate(value))
    return VariableRow(name=name, value=value, text=format_value(value),
                       masked=contains_mask(value), children=children)


def parse_value_text(text: str) -> Any:
    """A value typed by the user: JSON when it parses (``3``, ``true``, ``"a"``,
    ``[1, 2]``), else the text itself (as the DAP adapter does)."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def set_variable_payload(run_id: str, name: str, text: str, *,
                         expression: bool) -> dict[str, Any]:
    """``set_variable``'s payload for what the user typed: an expression the engine
    evaluates, or a value."""
    if not name:
        raise ValueError("choose a variable")
    if expression:
        if not text.strip():
            raise ValueError("type an expression")
        return {"run_id": run_id, "name": name, "expression": text}
    return {"run_id": run_id, "name": name, "value": parse_value_text(text)}


def editable_text(row: VariableRow) -> str | None:
    """The text to start editing ``row`` with; None when it holds a masked secret,
    which can only be replaced with an expression."""
    if row.masked:
        return None
    if isinstance(row.value, str):
        return row.value if parse_value_text(row.value) == row.value else row.text
    return row.text
