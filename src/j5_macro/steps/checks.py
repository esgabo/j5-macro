"""Small helpers for cross-field parameter checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from j5_macro.steps.spec import ParamProblem


def is_interpolated(value: Any) -> bool:
    """True for strings with ``${...}``, which are only checked at run time."""
    return isinstance(value, str) and "${" in value


def exactly_one(params: Mapping[str, Any], *names: str) -> list[ParamProblem]:
    """One problem unless exactly one of ``names`` is present (a null value counts)."""
    present = [name for name in names if name in params]
    if len(present) == 1:
        return []
    listed = ", ".join(f"'{name}'" for name in names)
    if not present:
        return [ParamProblem(names[0], f"expected one of {listed}")]
    return [ParamProblem(present[1], f"use only one of {listed}")]


def check_point(params: Mapping[str, Any], param: str = "") -> list[ParamProblem]:
    """A target is ``image`` or both ``x`` and ``y``, never both forms.

    ``param`` names the parameter holding the target, for nested targets (``from``).
    """
    has_image = "image" in params
    has_x, has_y = "x" in params, "y" in params
    where = f"{param}." if param else ""
    if has_image and (has_x or has_y):
        return [ParamProblem(f"{where}image", "use either 'image' or 'x'/'y', not both")]
    if has_image or (has_x and has_y):
        return []
    if has_x or has_y:
        missing = "y" if has_x else "x"
        return [ParamProblem(f"{where}{missing}", "'x' and 'y' go together")]
    return [ParamProblem(param or "image", "expected 'image' or both 'x' and 'y'")]
