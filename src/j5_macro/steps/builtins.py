"""The built-in step types of the v1 contract."""

from __future__ import annotations

from j5_macro.steps import basic, control, input_steps, programs
from j5_macro.steps.registry import ParamCheck, PluginReport, StepRegistry
from j5_macro.steps.spec import StepType

BUILTINS: tuple[tuple[StepType, ParamCheck | None], ...] = (
    (input_steps.CLICK, input_steps.check_click),
    (input_steps.MOVE, input_steps.check_move),
    (input_steps.DRAG, input_steps.check_drag),
    (input_steps.SCROLL, None),
    (input_steps.TYPE, input_steps.check_type),
    (input_steps.KEY, input_steps.check_key),
    (input_steps.WAIT_IMAGE, input_steps.check_wait_image),
    (basic.SLEEP, None),
    (control.IF, None),
    (control.LOOP, control.check_loop),
    (basic.SET, basic.check_set),
    (basic.LOG, None),
    (basic.PAUSE, None),
    (programs.EXEC, programs.check_exec),
)
"""Step types with their cross-field checks, in the contract's table order."""


def default_registry() -> StepRegistry:
    """A registry with every built-in step and nothing else (tests, the bundled schema)."""
    registry = StepRegistry()
    for step_type, check in BUILTINS:
        registry.register(step_type, check)
    return registry


def registry_with_plugins() -> tuple[StepRegistry, PluginReport]:
    """The built-in steps plus every installed ``j5_macro.steps`` plugin.

    What the engine process and ``j5 schema`` use. Built-ins are registered first, so
    they win any name clash; the report lists the plugins that were skipped, for the
    caller to warn about.
    """
    registry = default_registry()
    return registry, registry.load_plugins()
