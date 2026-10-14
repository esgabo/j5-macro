"""The script header in the editor: params, vars, the input profile and the embedded
images, and the ``set_meta`` values they become (docs/contract.md, "Edit kinds").
Pure functions, tested without a display."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from j5_macro.gui.models.fields import Parsed, format_literal, parse_literal, parse_number

PARAM_TYPES = ("string", "integer", "number", "boolean")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
"""Params and vars are variable names (identifiers)."""


# --- params -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParamRow:
    """One declared param as the header form edits it. ``default`` is the text of the
    default (ignored when ``required``)."""

    name: str
    type: str = "string"
    description: str = ""
    secret: bool = False
    required: bool = True
    default: str = ""

    @classmethod
    def from_info(cls, info: Mapping[str, Any]) -> ParamRow:
        """A param of ``get_script``'s ``params``."""
        default = info.get("default")
        return cls(
            name=str(info.get("name", "")),
            type=str(info.get("type") or "string"),
            description=str(info.get("description") or ""),
            secret=info.get("secret") is True,
            required=info.get("required") is not False,
            default=format_default(default),
        )


def format_default(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def parse_default(kind: str, text: str) -> Parsed:
    """A param's default typed as text, converted to its type."""
    if kind == "string":
        return Parsed(text)
    stripped = text.strip()
    if kind == "boolean":
        if stripped in ("true", "false"):
            return Parsed(stripped == "true")
        return Parsed(error="expected true or false")
    number = parse_number(stripped)
    if not number.ok or number.value is None or isinstance(number.value, str):
        return Parsed(error="expected a number" if kind == "number"
                      else "expected a whole number")
    if kind == "integer":
        if isinstance(number.value, float):
            return Parsed(error="expected a whole number")
        return Parsed(number.value)
    return Parsed(number.value)


def params_value(rows: Sequence[ParamRow]) -> Parsed:
    """The ``set_meta`` ``params`` for ``rows`` (``{}`` removes the section), or the first
    problem (``name: message``)."""
    value: dict[str, Any] = {}
    for row in rows:
        name = row.name.strip()
        if not NAME.match(name):
            return Parsed(error=f"{name or '(empty)'}: a param name is letters, digits and "
                                "_, not starting with a digit")
        if name in value:
            return Parsed(error=f"{name}: declared twice")
        if row.type not in PARAM_TYPES:
            return Parsed(error=f"{name}: unknown type {row.type!r}")
        entry: dict[str, Any] = {}
        if row.type != "string":
            entry["type"] = row.type
        if row.description.strip():
            entry["description"] = row.description.strip()
        if row.secret:
            entry["secret"] = True
        elif not row.required:
            default = parse_default(row.type, row.default)
            if not default.ok:
                return Parsed(error=f"{name}: default: {default.error}")
            entry["default"] = default.value
        value[name] = entry or None
    return Parsed(value)


# --- vars -------------------------------------------------------------------------------


def var_rows(variables: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(name, value text)`` for each var."""
    return [(str(name), format_literal(value)) for name, value in variables.items()]


def vars_value(rows: Sequence[tuple[str, str]]) -> Parsed:
    """The ``set_meta`` ``vars`` for ``(name, value text)`` rows (values as in
    :func:`~j5_macro.gui.models.fields.parse_literal`, empty is ``null``)."""
    value: dict[str, Any] = {}
    for name, text in rows:
        name = name.strip()
        if not NAME.match(name):
            return Parsed(error=f"{name or '(empty)'}: a var name is letters, digits and _, "
                                "not starting with a digit")
        if name in value:
            return Parsed(error=f"{name}: declared twice")
        parsed = parse_literal(text)
        if not parsed.ok:
            return Parsed(error=f"{name}: {parsed.error}")
        value[name] = parsed.value
    return Parsed(value)


# --- input profiles ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProfileSetting:
    """One setting of an input profile (docs/scripts.md, "Input profiles")."""

    name: str
    kind: str
    """``choice``, ``number``, ``integer`` or ``boolean``."""
    default: Any
    choices: tuple[str, ...] = ()
    doc: str = ""


MOUSE_SETTINGS = (
    ProfileSetting("strategy", "choice", "human", ("direct", "linear", "human"),
                   "direct jumps, linear moves at a constant speed, human curves"),
    ProfileSetting("speed", "number", 1.0, doc="Speed multiplier; greater than 0"),
    ProfileSetting("path", "choice", "windmouse", ("windmouse", "bezier"),
                   "Path shape of human movements"),
    ProfileSetting("overshoot", "number", 0.1,
                   doc="Probability (0 to 1) of overshooting a long movement"),
    ProfileSetting("jitter", "number", 1.0, doc="Sideways tremor, in pixels"),
    ProfileSetting("interference", "choice", "pause", ("pause", "stop", "off"),
                   "What to do when you move the mouse during a movement"),
    ProfileSetting("tolerance", "integer", 20,
                   doc="Pixels the pointer may stray before it counts as interference"),
)
KEYBOARD_SETTINGS = (
    ProfileSetting("strategy", "choice", "human", ("direct", "linear", "human"),
                   "direct types all at once, linear at a fixed interval, human "
                   "irregularly"),
    ProfileSetting("wpm", "number", 60, doc="Typing speed in words per minute"),
    ProfileSetting("variance", "number", 0.3, doc="Spread of the intervals between keys"),
    ProfileSetting("typos", "boolean", False,
                   doc="Occasionally hit a neighbouring key and correct it"),
)
PROFILE_DEVICES = (("mouse", MOUSE_SETTINGS), ("keyboard", KEYBOARD_SETTINGS))


def parse_setting(setting: ProfileSetting, text: str) -> Parsed:
    """A setting's text (empty: not set, the default applies)."""
    stripped = text.strip()
    if not stripped:
        return Parsed(None)
    if setting.kind == "choice":
        if stripped not in setting.choices:
            return Parsed(error=f"expected one of {', '.join(setting.choices)}")
        return Parsed(stripped)
    if setting.kind == "boolean":
        if stripped not in ("true", "false"):
            return Parsed(error="expected true or false")
        return Parsed(stripped == "true")
    number = parse_number(stripped)
    if not number.ok or isinstance(number.value, str):
        return Parsed(error="expected a number")
    if setting.kind == "integer" and isinstance(number.value, float):
        return Parsed(error="expected a whole number")
    return number


# --- images -----------------------------------------------------------------------------


def image_summary(info: Mapping[str, Any]) -> str:
    """``48 x 48 px, 1.2 KB, scale 1.5`` for an entry of ``get_script``'s ``images``."""
    parts: list[str] = []
    width, height = info.get("width"), info.get("height")
    if isinstance(width, int) and isinstance(height, int):
        parts.append(f"{width} x {height} px")
    size = info.get("bytes")
    if isinstance(size, int):
        parts.append(f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} bytes")
    scale = info.get("scale")
    if isinstance(scale, int | float):
        parts.append(f"scale {scale:g}")
    return ", ".join(parts)


def image_names(images: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(info["name"]) for info in images if isinstance(info.get("name"), str)]
