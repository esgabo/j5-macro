"""Input steps: final specs, placeholder executors.

Real executors arrive with the input masters (milestone M2) and vision (M3). Until
then, in dry-run mode each step logs what it would do and returns that description;
otherwise it fails with ``capability_missing``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from j5_macro.protocol.errors import ErrorCode
from j5_macro.steps.checks import check_point, is_interpolated
from j5_macro.steps.spec import (
    Executor,
    ParamProblem,
    ParamSpec,
    StepContext,
    StepError,
    StepSpec,
    StepType,
)

BUTTONS = ("left", "right", "middle")
VISION_PENDING = "image matching is not available yet (milestone M3); target not resolved"

Describe = Callable[[dict[str, Any]], str]


def placeholder(name: str, describe: Describe, *, vision: bool = False) -> Executor:
    """An executor that only works in dry-run mode."""

    def run(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
        uses_image = vision or "image" in params or _nested_image(params)
        if not ctx.dry_run:
            milestone = "the vision module (milestone M3)" if vision else \
                "an input backend (milestone M2)"
            raise StepError(ErrorCode.CAPABILITY_MISSING, f"'{name}' needs {milestone}")
        description = f"would {describe(params)}"
        ctx.log("info", description)
        if uses_image:
            ctx.log("warning", VISION_PENDING)
        return {"dry_run": True, "description": description}

    return run


def _nested_image(params: Mapping[str, Any]) -> bool:
    return any(isinstance(v, Mapping) and "image" in v for v in params.values())


def _target(params: Mapping[str, Any]) -> str:
    if "image" in params:
        where = f"image {params['image']}"
    else:
        where = f"({params.get('x')}, {params.get('y')})"
    window = params.get("window")
    if isinstance(window, Mapping) and window:
        where += f" in window {dict(window)}"
    return where


def _describe_click(p: dict[str, Any]) -> str:
    clicks = p.get("clicks", 1)
    times = "" if clicks == 1 else f" x{clicks}"
    offset = f" offset {p['offset']}" if p.get("offset") else ""
    return f"click {p.get('button', 'left')}{times} at {_target(p)}{offset}"


def _describe_drag(p: dict[str, Any]) -> str:
    return f"drag {p.get('button', 'left')} from {_target(p['from'])} to {_target(p['to'])}"


def _describe_wait(p: dict[str, Any]) -> str:
    return f"wait up to {p.get('timeout', '10s')} for image {p['image']}"


def _describe_key(p: dict[str, Any]) -> str:
    hold = f" holding {p['hold']}" if p.get("hold") is not None else ""
    return f"press {p['press']}{hold}"


# --- cross-field checks -----------------------------------------------------------------


def _check_threshold(params: Mapping[str, Any]) -> list[ParamProblem]:
    value = params.get("threshold")
    if isinstance(value, int | float) and not 0 <= value <= 1:
        return [ParamProblem("threshold", "must be between 0 and 1")]
    return []


def _check_length(params: Mapping[str, Any], name: str, length: int) -> list[ParamProblem]:
    value = params.get(name)
    if isinstance(value, list) and len(value) != length:
        return [ParamProblem(name, f"expected {length} numbers")]
    return []


def check_click(params: Mapping[str, Any]) -> list[ParamProblem]:
    problems = check_point(params) + _check_threshold(params)
    problems += _check_length(params, "offset", 2)
    clicks = params.get("clicks")
    if isinstance(clicks, int) and clicks < 1:
        problems.append(ParamProblem("clicks", "must be 1 or more"))
    return problems


def check_move(params: Mapping[str, Any]) -> list[ParamProblem]:
    return check_point(params)


def check_drag(params: Mapping[str, Any]) -> list[ParamProblem]:
    problems: list[ParamProblem] = []
    for name in ("from", "to"):
        value = params.get(name)
        if isinstance(value, Mapping):
            problems += check_point(value, name)
    return problems


def check_wait_image(params: Mapping[str, Any]) -> list[ParamProblem]:
    return _check_threshold(params) + _check_length(params, "region", 4)


def check_key(params: Mapping[str, Any]) -> list[ParamProblem]:
    press = params.get("press")
    if not isinstance(press, str) or is_interpolated(press):
        return []
    if any(not part.strip() for part in press.split("+")):
        return [ParamProblem("press", f"invalid key combination {press!r}")]
    return []


# --- specs ------------------------------------------------------------------------------


def _image(required: bool = False) -> ParamSpec:
    return ParamSpec(name="image", type="string", required=required,
                     doc="Template image, relative to the script file.")


X = ParamSpec(name="x", type="integer", doc="Horizontal position in physical pixels.")
Y = ParamSpec(name="y", type="integer", doc="Vertical position in physical pixels.")
WINDOW = ParamSpec(name="window", type="object",
                   doc="Window to work in, e.g. {title: ...}; coordinates become relative.")
THRESHOLD = ParamSpec(name="threshold", type="number", default=0.9,
                      doc="Minimum image match score, from 0 to 1.")
BUTTON = ParamSpec(name="button", type="string", default="left", choices=BUTTONS,
                   doc="Mouse button.")

CLICK = StepType(StepSpec(
    name="click",
    doc="Click on an image or at x/y.",
    params=(
        _image(), X, Y, WINDOW, BUTTON,
        ParamSpec(name="clicks", type="integer", default=1, doc="Number of clicks."),
        ParamSpec(name="offset", type="list",
                  doc="[dx, dy] added to the target point, in pixels."),
        THRESHOLD,
    ),
), placeholder("click", _describe_click))

MOVE = StepType(StepSpec(
    name="move",
    doc="Move the mouse to an image or to x/y.",
    params=(_image(), X, Y, WINDOW),
), placeholder("move", lambda p: f"move to {_target(p)}"))

DRAG = StepType(StepSpec(
    name="drag",
    doc="Press a button at one point, move to another and release.",
    params=(
        ParamSpec(name="from", type="object", required=True,
                  doc="Start point: {image: ...} or {x: ..., y: ...}."),
        ParamSpec(name="to", type="object", required=True,
                  doc="End point: {image: ...} or {x: ..., y: ...}."),
        BUTTON,
    ),
), placeholder("drag", _describe_drag))

SCROLL = StepType(StepSpec(
    name="scroll",
    doc="Scroll the mouse wheel.",
    params=(
        ParamSpec(name="dx", type="integer", default=0,
                  doc="Horizontal steps; positive scrolls right."),
        ParamSpec(name="dy", type="integer", default=0,
                  doc="Vertical steps; positive scrolls down."),
    ),
), placeholder("scroll", lambda p: f"scroll dx={p.get('dx', 0)}, dy={p.get('dy', 0)}"))

TYPE = StepType(StepSpec(
    name="type",
    doc="Type text, independent of the keyboard layout.",
    shorthand="text",
    params=(
        ParamSpec(name="text", type="string", required=True,
                  doc="Text to type; supports ${...} interpolation."),
        ParamSpec(name="keyboard", type="object",
                  doc="Keyboard profile override, e.g. {strategy: direct}."),
    ),
), placeholder("type", lambda p: f"type {len(str(p['text']))} characters"))

KEY = StepType(StepSpec(
    name="key",
    doc="Press a key or a combination such as ctrl+s.",
    shorthand="press",
    params=(
        ParamSpec(name="press", type="string", required=True,
                  doc="Key or combination joined with '+', e.g. enter or ctrl+s."),
        ParamSpec(name="hold", type="duration", doc="Keep the keys down this long."),
    ),
), placeholder("key", _describe_key))

WAIT_IMAGE = StepType(StepSpec(
    name="wait_image",
    doc="Wait until an image is visible on screen.",
    shorthand="image",
    params=(
        _image(required=True),
        ParamSpec(name="timeout", type="duration", default="10s",
                  doc="Give up with image_not_found after this long."),
        THRESHOLD,
        ParamSpec(name="region", type="list",
                  doc="[x, y, width, height] to search in; defaults to the whole desktop."),
        WINDOW,
    ),
), placeholder("wait_image", _describe_wait, vision=True))
