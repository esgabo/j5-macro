"""Built-in action steps that send no input: ``sleep``, ``set``, ``log``, ``pause``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from j5_macro.protocol.errors import ErrorCode
from j5_macro.steps.checks import exactly_one
from j5_macro.steps.spec import (
    LogLevel,
    ParamProblem,
    ParamSpec,
    ResultField,
    StepContext,
    StepError,
    StepSpec,
    StepType,
    parse_duration,
)

LOG_LEVELS: tuple[LogLevel, ...] = ("debug", "info", "warning", "error")


def run_sleep(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    try:
        duration = parse_duration(params["duration"])
    except ValueError as exc:
        raise StepError(ErrorCode.VALIDATION_FAILED, str(exc)) from exc
    seconds = duration.sample(ctx.rng)
    ctx.wait(seconds)
    return {"seconds": seconds}


def run_set(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    name = params["name"]
    expr = params.get("expr")
    value = ctx.evaluate(expr) if expr is not None else params.get("value")
    ctx.set_variable(name, value)
    return {"name": name, "value": value}


def run_log(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    level = params.get("level") or "info"
    if level not in LOG_LEVELS:
        raise StepError(ErrorCode.VALIDATION_FAILED,
                        f"invalid log level {level!r}: expected one of {', '.join(LOG_LEVELS)}")
    message = str(params["message"])
    ctx.log(cast(LogLevel, level), message)
    return {"level": level, "message": message}


def run_pause(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    message = params.get("message")
    text = None if message is None else str(message)
    ctx.request_pause(text)
    return {"message": text}


def check_set(params: Mapping[str, Any]) -> list[ParamProblem]:
    problems = exactly_one(params, "value", "expr")
    if params.get("name") == "":
        problems.append(ParamProblem("name", "must not be empty"))
    return problems


SLEEP = StepType(StepSpec(
    name="sleep",
    doc="Wait for a duration, optionally with variance (sleep: 2s±20%).",
    shorthand="duration",
    params=(ParamSpec(name="duration", type="duration", required=True,
                      doc="How long to wait, e.g. 2s, 500ms or 2s±20%."),),
    result=(ResultField(name="seconds", type="number",
                        doc="The duration waited, variance applied."),),
), run_sleep)

SET = StepType(StepSpec(
    name="set",
    doc="Set a variable to a value or to the result of an expression.",
    params=(
        ParamSpec(name="name", type="string", required=True, doc="Variable name."),
        ParamSpec(name="value", type="any", doc="Literal value (interpolated)."),
        ParamSpec(name="expr", type="expression", doc="Expression whose result is stored."),
    ),
    result=(
        ResultField(name="name", type="string", doc="The variable set."),
        ResultField(name="value", type="any", doc="The value it was set to."),
    ),
), run_set)

LOG = StepType(StepSpec(
    name="log",
    doc="Write a message to the run log.",
    shorthand="message",
    params=(
        ParamSpec(name="message", type="string", required=True,
                  doc="Text to log; supports ${...} interpolation."),
        ParamSpec(name="level", type="string", default="info", choices=LOG_LEVELS,
                  doc="Log level."),
    ),
    result=(
        ResultField(name="level", type="string", doc="The log level."),
        ResultField(name="message", type="string", doc="The message logged."),
    ),
), run_log)

PAUSE = StepType(StepSpec(
    name="pause",
    doc="Pause the run until resume, like a breakpoint written in the script.",
    shorthand="message",
    params=(ParamSpec(name="message", type="string",
                      doc="Optional reason shown while paused."),),
    result=(ResultField(name="message", type="string", optional=True,
                        doc="The reason given, if any."),),
), run_pause)
