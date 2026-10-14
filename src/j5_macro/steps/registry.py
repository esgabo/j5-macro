"""The step registry: the single source of step types (docs/contract.md, "Script
format and step registry").

Validation, ``list_step_types``, the editor JSON Schema and future GUI forms all come
from it. Third-party packages add steps through the ``j5_macro.steps`` entry-point group
(:meth:`StepRegistry.load_plugins`; docs/development.md, "Writing a step plugin").
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, get_args

from j5_macro.protocol.payloads.commands import ParamInfo, ResultFieldInfo, StepTypeInfo
from j5_macro.steps.spec import (
    ParamProblem,
    ParamSpec,
    ParamType,
    ResultField,
    StepEffect,
    StepSpec,
    StepType,
)

ParamCheck = Callable[[Mapping[str, Any]], list[ParamProblem]]
"""A cross-field rule over a step's parameters, as written in the script."""

ENTRY_POINT_GROUP = "j5_macro.steps"

PLUGIN_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
"""What a plugin step type's name must look like (it is a YAML key and an id prefix)."""

_RESERVED_NAMES = frozenset({"id", "break", "timeout", "on_error", "input", "as"})
"""The common step fields, which a step type cannot be named after."""


@dataclass(frozen=True, slots=True)
class PluginProblem:
    """A ``j5_macro.steps`` entry point that was skipped, and why."""

    entry_point: str
    """``name = value`` of the entry point, e.g. ``my_steps = my_package.steps:register``."""
    message: str

    def __str__(self) -> str:
        return f"step plugin '{self.entry_point}' skipped: {self.message}"


@dataclass(slots=True)
class PluginReport:
    """What :meth:`StepRegistry.load_plugins` did."""

    loaded: list[str] = field(default_factory=list)
    """Names of the entry points whose step types were registered."""
    skipped: list[PluginProblem] = field(default_factory=list)


class StepRegistry:
    """Step types by name, with optional cross-field parameter checks."""

    def __init__(self) -> None:
        self._types: dict[str, StepType] = {}
        self._checks: dict[str, ParamCheck] = {}

    def register(self, step_type: StepType, check: ParamCheck | None = None) -> None:
        """Add a step type; ``check`` runs after :func:`validate_params` succeeds."""
        if not isinstance(step_type, StepType):
            raise TypeError(f"expected a StepType, got {type(step_type).__name__}")
        if not isinstance(step_type.spec, StepSpec):
            raise TypeError(f"expected a StepSpec, got {type(step_type.spec).__name__}")
        name = step_type.spec.name
        if name in self._types:
            raise ValueError(f"step type {name!r} is already registered")
        if step_type.spec.kind == "action" and step_type.executor is None:
            raise ValueError(f"action step {name!r} needs an executor")
        self._types[name] = step_type
        if check is not None:
            self._checks[name] = check

    def get(self, name: str) -> StepType | None:
        return self._types.get(name)

    def spec(self, name: str) -> StepSpec | None:
        """The spec of ``name``; usable directly as the loader's ``StepLookup``."""
        step_type = self._types.get(name)
        return None if step_type is None else step_type.spec

    def names(self) -> list[str]:
        """Registered names, in registration order."""
        return list(self._types)

    def __contains__(self, name: object) -> bool:
        return name in self._types

    def __len__(self) -> int:
        return len(self._types)

    def check_params(self, name: str, params: Mapping[str, Any]) -> list[ParamProblem]:
        """Cross-field problems of ``params`` (empty if none, or if ``name`` is unknown).

        Pass the parameters as written in the script, after :func:`validate_params`
        reported no problems. Values still containing ``${`` are not checked.
        """
        check = self._checks.get(name)
        return [] if check is None else check(params)

    def describe(self) -> list[dict[str, Any]]:
        """The ``list_step_types`` result entries (``StepTypeInfo`` wire shape)."""
        return [_info(t.spec).to_json() for t in self._types.values()]

    def load_plugins(self) -> PluginReport:
        """Run every ``j5_macro.steps`` entry point, in order of entry point name.

        Each entry point must resolve to a callable taking this registry, which registers
        the plugin's step types with :meth:`register`. Plugin steps must be action steps
        with an executor, named like ``PLUGIN_NAME_PATTERN`` and not after a common step
        field (``as`` included), and their spec must describe itself (``list_step_types``,
        JSON Schema), result fields included.

        A plugin that cannot be imported, is not callable, raises, or registers a step
        that breaks those rules is skipped as a whole: whatever it registered is removed
        and the problem is reported in :attr:`PluginReport.skipped`. Nothing raises. Names
        already taken win: call this after registering the built-ins, so a plugin that
        reuses a built-in name (or the name of a plugin loaded before it) is skipped.
        """
        report = PluginReport()
        found = sorted(entry_points(group=ENTRY_POINT_GROUP),
                       key=lambda ep: (ep.name, getattr(ep, "value", "")))
        for entry_point in found:
            label = _label(entry_point)
            problem = self._load_plugin(entry_point)
            if problem is None:
                report.loaded.append(entry_point.name)
            else:
                report.skipped.append(PluginProblem(label, problem))
        return report

    def _load_plugin(self, entry_point: Any) -> str | None:
        """Load one entry point; the problem, or None when its steps were registered."""
        try:
            register = entry_point.load()
        except Exception as exc:
            return f"cannot be imported: {_describe(exc)}"
        if not callable(register):
            return (f"expected a callable taking the step registry, got "
                    f"{type(register).__name__}")
        types, checks = dict(self._types), dict(self._checks)
        try:
            register(self)
            for name in self._types.keys() - types.keys():
                _check_plugin_type(self._types[name])
        except Exception as exc:
            self._types, self._checks = types, checks
            return _describe(exc)
        return None


def _info(spec: StepSpec) -> StepTypeInfo:
    # Imported here so that ``python -m j5_macro.steps.schema`` runs without a warning.
    from j5_macro.steps.schema import step_value_schema

    params = [
        ParamInfo(name=p.name, type=p.type, required=p.required, default=p.default,
                  doc=p.doc)
        for p in spec.params
    ]
    result = [ResultFieldInfo(name=f.name, type=f.type, optional=f.optional, doc=f.doc)
              for f in spec.result]
    return StepTypeInfo(name=spec.name, params=params, doc=spec.doc, result=result,
                        json_schema=step_value_schema(spec))


def _label(entry_point: Any) -> str:
    value = getattr(entry_point, "value", None)
    return entry_point.name if not value else f"{entry_point.name} = {value}"


def _describe(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


_PARAM_TYPES = frozenset(get_args(ParamType))
_EFFECTS = frozenset(get_args(StepEffect))


def _check_plugin_type(step_type: StepType) -> None:
    """Raise ValueError if a plugin's step type breaks the plugin rules."""
    spec = step_type.spec
    name = spec.name
    if not isinstance(name, str) or not PLUGIN_NAME_PATTERN.match(name):
        raise ValueError(f"invalid step type name {name!r}: expected letters, digits and _")
    if name in _RESERVED_NAMES:
        raise ValueError(f"step type {name!r} would clash with the common step field")
    if spec.kind != "action" or spec.blocks:
        raise ValueError(f"step type {name!r}: plugins can only add action steps "
                         "(no blocks)")
    if not callable(step_type.executor):
        raise ValueError(f"step type {name!r}: the executor is not callable")
    for param in spec.params:
        if not isinstance(param, ParamSpec) or param.type not in _PARAM_TYPES:
            raise ValueError(f"step type {name!r}: invalid parameter {param!r}")
    for result in spec.result:
        if not isinstance(result, ResultField) or result.type not in _PARAM_TYPES:
            raise ValueError(f"step type {name!r}: invalid result field {result!r}")
    if spec.shorthand is not None and spec.param(spec.shorthand) is None:
        raise ValueError(f"step type {name!r}: shorthand {spec.shorthand!r} is not a "
                         "parameter")
    if spec.effect is not None and spec.effect not in _EFFECTS:
        raise ValueError(f"step type {name!r}: invalid effect {spec.effect!r}")
    json.dumps(_info(spec).to_json())  # the spec must describe itself on the wire
