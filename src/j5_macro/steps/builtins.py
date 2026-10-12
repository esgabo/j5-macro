"""The built-in step types of the v1 contract."""

from __future__ import annotations

from j5_macro.steps import basic, control, input_steps
from j5_macro.steps.registry import ParamCheck, StepRegistry
from j5_macro.steps.spec import StepType

BUILTINS: tuple[tuple[StepType, ParamCheck | None], ...] = (
    (input_steps.CLICK, input_steps.check_click),
    (input_steps.MOVE, input_steps.check_move),
    (input_steps.DRAG, input_steps.check_drag),
    (input_steps.SCROLL, None),
    (input_steps.TYPE, None),
    (input_steps.KEY, input_steps.check_key),
    (input_steps.WAIT_IMAGE, input_steps.check_wait_image),
    (basic.SLEEP, None),
    (control.IF, None),
    (control.LOOP, control.check_loop),
    (basic.SET, basic.check_set),
    (basic.LOG, None),
    (basic.PAUSE, None),
)
"""Step types with their cross-field checks, in the contract's table order."""


def default_registry(*, plugins: bool = False) -> StepRegistry:
    """A registry with every built-in step; ``plugins`` also loads entry-point steps."""
    registry = StepRegistry()
    for step_type, check in BUILTINS:
        registry.register(step_type, check)
    if plugins:
        registry.load_plugins()
    return registry
