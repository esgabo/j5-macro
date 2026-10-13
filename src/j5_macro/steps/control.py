"""Control steps (``if``, ``loop``): specs and checks only; the interpreter runs them."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from j5_macro.steps.checks import exactly_one
from j5_macro.steps.spec import ParamProblem, ParamSpec, ResultField, StepSpec, StepType

IF = StepType(StepSpec(
    name="if",
    doc="Run 'then' when the condition holds, else the optional 'else' block.",
    kind="control",
    params=(
        ParamSpec(name="when", type="condition", required=True,
                  doc="An expression or a predefined condition such as image_visible."),
    ),
    blocks=("then", "else"),
    required_blocks=("then",),
    result=(ResultField(name="branch", type="string", doc="The block that ran: then or else."),),
))

LOOP = StepType(StepSpec(
    name="loop",
    doc="Repeat the 'do' block a number of times, while a condition holds, or for each "
        "item of a list.",
    kind="control",
    params=(
        ParamSpec(name="times", type="integer", doc="Number of iterations (0 or more)."),
        ParamSpec(name="while", type="condition",
                  doc="Repeat while this condition holds; checked before each iteration."),
        ParamSpec(name="for_each", type="any",
                  doc="A list, or an expression that evaluates to one."),
        ParamSpec(name="as", type="string", default="item",
                  doc="Variable that holds the current item of 'for_each'."),
    ),
    blocks=("do",),
    required_blocks=("do",),
    result=(ResultField(name="iterations", type="integer",
                        doc="How many iterations started."),),
))


def check_loop(params: Mapping[str, Any]) -> list[ParamProblem]:
    problems = exactly_one(params, "times", "while", "for_each")
    times = params.get("times")
    if isinstance(times, int) and times < 0:
        problems.append(ParamProblem("times", "must be 0 or more"))
    for_each = params.get("for_each")
    if for_each is not None and not isinstance(for_each, list | str):
        problems.append(ParamProblem("for_each", "expected a list or an expression"))
    return problems
