"""The fields of the step form: which widget a parameter gets, and the text of a value
in a field and back (pure functions, no Qt).

Every ``parse_*`` function returns a :class:`Parsed`: the value to send with
``edit_script`` (``None`` removes the parameter) or the problem to show next to the
field. Strings with ``${...}`` are accepted for any scalar, as the engine does: they are
interpolated and checked when the step runs.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from j5_macro.gui.models.registry import ParamInfo
from j5_macro.steps.spec import parse_duration

FieldKind = Literal[
    "string", "text", "integer", "number", "boolean", "choice", "duration", "expression",
    "condition", "image", "offset", "region", "window", "target", "command", "mapping",
    "keyboard", "any",
]
"""How a parameter is edited:

- ``string``, ``text`` (several lines: ``type``'s ``text``), ``integer``, ``number``,
  ``boolean``, ``choice`` (a fixed set), ``duration``, ``expression``;
- ``condition``: an expression or ``{image_visible: ...}``;
- ``image``: a path or ``#name`` of an embedded image;
- ``offset``: ``[dx, dy]`` in pixels or ``w``/``h`` units;
- ``region``: ``[x, y, w, h]`` or ``{at, offset?, size?}``;
- ``window``: ``{title}``;
- ``target``: a point of ``drag`` (``{x, y}``, ``{image, ...}`` or ``{at}``, with
  ``window`` and ``offset``);
- ``command``: ``exec``'s program and arguments (a list of strings) or command line;
- ``mapping``: names to scalar values (``exec``'s ``env``);
- ``keyboard``: a keyboard profile (``type``'s ``keyboard``);
- ``any``: any value, written as JSON (plain text is a string).
"""

INTERPOLATION = "${"


def field_kind(step_type: str, param: ParamInfo) -> FieldKind:
    """The field a parameter of ``step_type`` is edited with."""
    name, kind = param.name, param.type
    if param.choices:
        return "choice"
    if name == "image" and kind == "string":
        return "image"
    if name == "offset":
        return "offset"
    if name == "region":
        return "region"
    if name == "window" and kind == "object":
        return "window"
    if step_type == "drag" and name in ("from", "to"):
        return "target"
    if step_type == "exec" and name == "command":
        return "command"
    if step_type == "exec" and name == "env":
        return "mapping"
    if step_type == "type" and name == "text":
        return "text"
    if name == "keyboard" and kind == "object":
        return "keyboard"
    simple: dict[str, FieldKind] = {
        "string": "string", "integer": "integer", "number": "number",
        "boolean": "boolean", "duration": "duration", "expression": "expression",
        "condition": "condition",
    }
    return simple.get(kind, "any")


TARGET_PARAMS = ("image", "x", "y", "at")
"""The parameters of a ``click`` or ``move`` target, edited together."""
TARGET_EXTRAS = ("window", "offset", "threshold", "scale", "region")
"""The parameters that go with a target, edited in the same group."""


def has_target(param_names: Sequence[str]) -> bool:
    """Whether a step type's parameters form a ``click``/``move`` target."""
    return all(name in param_names for name in TARGET_PARAMS)


# --- parsing and formatting -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Parsed:
    value: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def is_interpolated(text: str) -> bool:
    return INTERPOLATION in text


def format_scalar(value: Any) -> str:
    """A scalar's text in a field (``None`` is empty)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return f"{value:.1f}"
    return str(value)


def parse_string(text: str, *, required: bool = False) -> Parsed:
    """A string; empty removes the parameter unless it is required (then ``""``)."""
    if text == "":
        return Parsed("" if required else None)
    return Parsed(text)


def parse_integer(text: str) -> Parsed:
    text = text.strip()
    if not text:
        return Parsed(None)
    if is_interpolated(text):
        return Parsed(text)
    try:
        return Parsed(int(text))
    except ValueError:
        return Parsed(error="expected a whole number")


def parse_number(text: str) -> Parsed:
    text = text.strip()
    if not text:
        return Parsed(None)
    if is_interpolated(text):
        return Parsed(text)
    try:
        number = float(text)
    except ValueError:
        return Parsed(error="expected a number")
    if not math.isfinite(number):
        return Parsed(error="expected a number")
    return Parsed(int(number) if re.fullmatch(r"[+-]?\d+", text) else number)


def parse_duration_text(text: str) -> Parsed:
    """``2s``, ``500ms``, ``1.5m``, ``2s±20%``; a bare number is seconds."""
    text = text.strip()
    if not text:
        return Parsed(None)
    if is_interpolated(text):
        return Parsed(text)
    number = parse_number(text)
    if number.ok and not isinstance(number.value, str):
        return number if number.value >= 0 else Parsed(error="must not be negative")
    try:
        parse_duration(text)
    except ValueError as exc:
        return Parsed(error=str(exc))
    return Parsed(text)


# --- offsets ----------------------------------------------------------------------------

_UNIT_ITEM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)[wh]$")


def parse_offset_item(text: str) -> Parsed:
    """One item of an offset: pixels (``12``, ``-3``) or a multiple of the target's
    width or height (``0.5w``, ``-2h``)."""
    text = text.strip()
    if not text:
        return Parsed(0)
    if is_interpolated(text):
        return Parsed(text)
    if _UNIT_ITEM.match(text):
        return Parsed(text)
    number = parse_number(text)
    if number.ok:
        return number
    return Parsed(error=f"{text!r}: expected pixels or a multiple such as 0.5w or -2h")


def parse_offset(dx: str, dy: str) -> Parsed:
    """``[dx, dy]``; both empty (or zero) removes the offset."""
    items = [parse_offset_item(dx), parse_offset_item(dy)]
    problem = next((item.error for item in items if not item.ok), None)
    if problem is not None:
        return Parsed(error=problem)
    values = [item.value for item in items]
    if all(v == 0 for v in values) and not (dx.strip() or dy.strip()):
        return Parsed(None)
    return Parsed(values)


def format_offset(value: Any) -> tuple[str, str]:
    if isinstance(value, list | tuple) and len(value) == 2:
        return format_scalar(value[0]), format_scalar(value[1])
    return "", ""


# --- regions ----------------------------------------------------------------------------

RegionMode = Literal["none", "rect", "at"]


def region_mode(value: Any) -> RegionMode:
    if isinstance(value, Mapping):
        return "at"
    if isinstance(value, list | tuple):
        return "rect"
    return "none"


def parse_rect(texts: Sequence[str]) -> Parsed:
    """``[x, y, w, h]`` from four fields (whole numbers or ``${...}``)."""
    values: list[Any] = []
    for label, text in zip(("x", "y", "width", "height"), texts, strict=True):
        item = parse_integer(text)
        if not item.ok or item.value is None:
            return Parsed(error=f"{label}: expected a whole number")
        values.append(item.value)
    if any(isinstance(v, int) and v <= 0 for v in values[2:]):
        return Parsed(error="the width and height must be positive")
    return Parsed(values)


def parse_anchored_region(at: str, offset: tuple[str, str],
                          size: tuple[str, str]) -> Parsed:
    """``{at, offset?, size?}``: a rectangle next to a stored result."""
    if not at.strip():
        return Parsed(error="at: give a stored result such as ${label}")
    region: dict[str, Any] = {"at": at.strip()}
    parsed_offset = parse_offset(*offset)
    if not parsed_offset.ok:
        return parsed_offset
    if parsed_offset.value is not None:
        region["offset"] = parsed_offset.value
    if size[0].strip() or size[1].strip():
        parsed_size = parse_offset(*size)
        if not parsed_size.ok:
            return parsed_size
        region["size"] = parsed_size.value
    return Parsed(region)


# --- free values ------------------------------------------------------------------------


def parse_literal(text: str) -> Parsed:
    """Any value: JSON (``3``, ``true``, ``null``, ``[1, 2]``, ``{"a": 1}``,
    ``"quoted"``), else the text itself as a string. Empty removes the value."""
    if text.strip() == "":
        return Parsed(None)
    try:
        return Parsed(json.loads(text))
    except ValueError:
        stripped = text.strip()
        if stripped[:1] in "[{" or stripped[:1] == '"':
            return Parsed(error="not valid JSON; write lists as [1, 2] and mappings as "
                                '{"name": "value"}')
        return Parsed(text)


def format_literal(value: Any) -> str:
    """The text :func:`parse_literal` reads back as ``value``."""
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            json.loads(value)
        except ValueError:
            if value.strip() and value.strip()[:1] not in '[{"':
                return value
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def parse_lines(text: str) -> list[str]:
    """One item per line, blank lines left out."""
    return [line for line in text.splitlines() if line.strip()]


def format_lines(values: Any) -> str:
    if isinstance(values, list | tuple):
        return "\n".join(str(v) for v in values)
    return "" if values is None else str(values)


def parse_scalar_text(text: str) -> Any:
    """A mapping value typed in a table cell: ``true``/``false`` and numbers keep their
    type, anything else is a string."""
    stripped = text.strip()
    if stripped in ("true", "false"):
        return stripped == "true"
    number = parse_number(stripped)
    if number.ok and number.value is not None and not isinstance(number.value, str):
        return number.value
    return text


# --- problems ---------------------------------------------------------------------------


def problem_field(problem: Mapping[str, Any]) -> str | None:
    """The parameter or common field an engine problem is about: the first part of
    ``details.param`` (``from`` for ``from.image``), else ``details.field``."""
    details = problem.get("details")
    if not isinstance(details, Mapping):
        return None
    for key in ("param", "field"):
        value = details.get(key)
        if isinstance(value, str) and value:
            return value.split(".")[0].split("[")[0]
    return None


def step_problems(problems: Sequence[Mapping[str, Any]],
                  step_id: str) -> tuple[dict[str, list[str]], list[str]]:
    """The problems of one step: ``({field: [messages]}, [messages about no field])``."""
    by_field: dict[str, list[str]] = {}
    general: list[str] = []
    for problem in problems:
        if problem.get("step_id") != step_id:
            continue
        message = str(problem.get("message", ""))
        name = problem_field(problem)
        if name is None:
            general.append(message)
        else:
            by_field.setdefault(name, []).append(message)
    return by_field, general


def problems_by_step(problems: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Every problem's message under its step id (problems without one left out)."""
    found: dict[str, list[str]] = {}
    for problem in problems:
        step_id = problem.get("step_id")
        if isinstance(step_id, str):
            found.setdefault(step_id, []).append(str(problem.get("message", "")))
    return found
