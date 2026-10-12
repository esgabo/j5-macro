"""The step registry: the single source of step types (docs/contract.md, "Script
format and step registry").

Validation, ``list_step_types``, the editor JSON Schema and future GUI forms all come
from it. Third-party packages add steps through the ``j5_macro.steps`` entry-point group.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib.metadata import entry_points
from typing import Any

from j5_macro.protocol.payloads.commands import ParamInfo, StepTypeInfo
from j5_macro.steps.spec import ParamProblem, StepSpec, StepType

ParamCheck = Callable[[Mapping[str, Any]], list[ParamProblem]]
"""A cross-field rule over a step's parameters, as written in the script."""

ENTRY_POINT_GROUP = "j5_macro.steps"


class StepRegistry:
    """Step types by name, with optional cross-field parameter checks."""

    def __init__(self) -> None:
        self._types: dict[str, StepType] = {}
        self._checks: dict[str, ParamCheck] = {}

    def register(self, step_type: StepType, check: ParamCheck | None = None) -> None:
        """Add a step type; ``check`` runs after :func:`validate_params` succeeds."""
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

    def load_plugins(self) -> list[str]:
        """Run every ``j5_macro.steps`` entry point; returns the plugin names loaded.

        Each entry point must resolve to a callable taking this registry, which
        registers the plugin's step types.
        """
        loaded = []
        for entry_point in entry_points(group=ENTRY_POINT_GROUP):
            register = entry_point.load()
            register(self)
            loaded.append(entry_point.name)
        return loaded


def _info(spec: StepSpec) -> StepTypeInfo:
    # Imported here so that ``python -m j5_macro.steps.schema`` runs without a warning.
    from j5_macro.steps.schema import step_value_schema

    params = [
        ParamInfo(name=p.name, type=p.type, required=p.required, default=p.default,
                  doc=p.doc)
        for p in spec.params
    ]
    return StepTypeInfo(name=spec.name, params=params, doc=spec.doc,
                        json_schema=step_value_schema(spec))
