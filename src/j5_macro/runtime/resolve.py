"""Run-time parameter resolution and conditions.

Parameters are interpolated with the current variables and re-checked against their
spec; ``expression`` and ``condition`` parameters are left as written, because they are
evaluated (by the executor or the interpreter), not interpolated.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from j5_macro.expr import ExpressionError, evaluate, interpolate
from j5_macro.protocol.errors import ErrorCode
from j5_macro.steps.registry import StepRegistry
from j5_macro.steps.spec import StepContext, StepError, StepSpec, check_value

ConditionFn = Callable[[StepContext, Any], bool]
"""A predefined condition such as ``image_visible``: ``fn(ctx, argument) -> bool``.
The argument is interpolated before the call."""

UNEVALUATED = ("expression", "condition")


def expression_failure(exc: ExpressionError) -> StepError:
    """The :class:`StepError` for a failed expression or interpolation."""
    return StepError(ErrorCode.EXPRESSION_ERROR, exc.info.message, exc.info.details)


def interpolated(value: Any, variables: Mapping[str, Any]) -> Any:
    """:func:`interpolate`, raising :class:`StepError` on failure."""
    try:
        return interpolate(value, variables)
    except ExpressionError as exc:
        raise expression_failure(exc) from None


def evaluated(expression: str, variables: Mapping[str, Any]) -> Any:
    """:func:`evaluate`, raising :class:`StepError` on failure."""
    try:
        return evaluate(expression, variables)
    except ExpressionError as exc:
        raise expression_failure(exc) from None


def resolve_params(registry: StepRegistry, spec: StepSpec, params: Mapping[str, Any],
                   variables: Mapping[str, Any]) -> dict[str, Any]:
    """Interpolate ``params`` and check them again; raises :class:`StepError`."""
    resolved: dict[str, Any] = {}
    for name, value in params.items():
        param = spec.param(name)
        if param is None or param.type in UNEVALUATED:
            resolved[name] = value
            continue
        new = interpolated(value, variables)
        problem = check_value(param, new)
        if problem is not None:
            raise StepError(ErrorCode.VALIDATION_FAILED,
                            f"parameter '{name}' after interpolation: {problem}",
                            {"param": name})
        resolved[name] = new
    problems = registry.check_params(spec.name, resolved)
    if problems:
        first = problems[0]
        raise StepError(ErrorCode.VALIDATION_FAILED, f"parameter '{first.param}': "
                        f"{first.message}", {"param": first.param, **(first.details or {})})
    return resolved


def unavailable_condition(name: str) -> str:
    return f"unknown predefined condition {name!r}"


def check_condition(condition: Any, ctx: StepContext,
                    conditions: Mapping[str, ConditionFn]) -> bool:
    """Evaluate an expression string or a one-key predefined condition mapping.

    An unknown predefined condition fails with ``expression_error``; in dry-run it is
    false and logs a warning instead.
    """
    if isinstance(condition, str):
        return bool(evaluated(condition, ctx.variables))
    if not isinstance(condition, Mapping) or len(condition) != 1:
        raise StepError(ErrorCode.VALIDATION_FAILED,
                        "expected an expression or a single predefined condition")
    ((name, argument),) = condition.items()
    function = conditions.get(name)
    if function is None:
        message = unavailable_condition(name)
        if ctx.dry_run:
            ctx.log("warning", f"{message}; treated as false in dry-run")
            return False
        raise StepError(ErrorCode.EXPRESSION_ERROR, message, {"condition": name})
    return bool(function(ctx, interpolated(argument, ctx.variables)))
