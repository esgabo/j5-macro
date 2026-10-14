"""The params prompt: a script's declared params and the values typed for a run.

A script declares ``params`` (docs/contract.md, "Script format": ``type`` is ``string``,
``integer``, ``number`` or ``boolean``; a param without ``default`` is required; secret
params never have a default). ``load`` and ``get_script`` report them as
``{name, type, description, secret, required, default?}``.

The prompt works on texts, one per param, and :func:`collect_params` turns them into
``run``'s ``params``. An empty text leaves the param out, so the engine uses its
default (or answers ``validation_failed`` for a required one, which the prompt catches
first). Secret values are only ever passed through: :func:`rememberable` keeps the
non-secret texts, which the GUI remembers per script for the session.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

PARAM_TYPES = ("string", "integer", "number", "boolean")
_INTEGER = re.compile(r"[+-]?\d+")


@dataclass(frozen=True, slots=True)
class ParamSpec:
    name: str
    type: str = "string"
    description: str = ""
    secret: bool = False
    required: bool = False
    default: Any = None
    """The declared default (None when there is none; secret params never have one)."""

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> ParamSpec:
        type_ = raw.get("type")
        secret = raw.get("secret") is True
        return cls(
            name=str(raw.get("name", "")),
            type=type_ if type_ in PARAM_TYPES else "string",
            description=str(raw.get("description") or ""),
            secret=secret,
            required=raw.get("required") is True,
            default=None if secret else raw.get("default"),
        )

    @property
    def default_text(self) -> str:
        """The default as the prompt shows it ('' without one)."""
        return format_param_value(self.default) if self.default is not None else ""

    @property
    def hint(self) -> str:
        """A placeholder for the field: what an empty field means."""
        if self.required:
            return "required"
        if self.default is not None:
            return f"default: {self.default_text}"
        return "optional"


class ParamError(ValueError):
    """A text that is not a valid value for its param."""


def parse_param_specs(raw: Iterable[Mapping[str, Any]] | None) -> list[ParamSpec]:
    """The declared params, in the script's order (entries without a name skipped)."""
    specs = [ParamSpec.from_json(item) for item in raw or () if isinstance(item, Mapping)]
    return [spec for spec in specs if spec.name]


def format_param_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def convert_param(spec: ParamSpec, text: str) -> Any:
    """The value of ``text`` for ``spec``'s type, as the engine would accept it. Strings
    are taken verbatim; the other types ignore surrounding whitespace."""
    if spec.type == "string":
        return text
    stripped = text.strip()
    if spec.type == "integer":
        if not _INTEGER.fullmatch(stripped):
            raise ParamError("expected a whole number")
        return int(stripped)
    if spec.type == "number":
        if _INTEGER.fullmatch(stripped):
            return int(stripped)
        try:
            number = float(stripped)
        except ValueError:
            raise ParamError("expected a number") from None
        if not math.isfinite(number):
            raise ParamError("expected a finite number")
        return number
    lowered = stripped.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    raise ParamError("expected true or false")


def collect_params(specs: Iterable[ParamSpec],
                   texts: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """``(params, errors)``: the values for ``run``'s ``params`` (empty texts left out)
    and a message per param that is missing or invalid. Messages never repeat the text,
    so a mistyped secret is not shown either."""
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for spec in specs:
        text = texts.get(spec.name, "")
        if text == "" or (spec.type != "string" and not text.strip()):
            if spec.required:
                errors[spec.name] = "required"
            continue
        try:
            values[spec.name] = convert_param(spec, text)
        except ParamError as exc:
            errors[spec.name] = str(exc)
    return values, errors


def initial_texts(specs: Iterable[ParamSpec],
                  remembered: Mapping[str, str] | None = None) -> dict[str, str]:
    """What the prompt starts with: the text remembered from the last run of the script
    (non-secret params only), else the default, else ''."""
    remembered = remembered or {}
    texts: dict[str, str] = {}
    for spec in specs:
        if spec.secret:
            texts[spec.name] = ""
        elif spec.name in remembered:
            texts[spec.name] = remembered[spec.name]
        else:
            texts[spec.name] = spec.default_text
    return texts


def rememberable(specs: Iterable[ParamSpec], texts: Mapping[str, str]) -> dict[str, str]:
    """The texts that may be remembered for the next run: never a secret's."""
    return {spec.name: texts[spec.name] for spec in specs
            if not spec.secret and spec.name in texts}
