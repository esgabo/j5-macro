"""Input steps: ``click``, ``move``, ``drag``, ``scroll``, ``type``, ``key`` and
``wait_image``.

In normal and step mode they drive the run's input session through ``ctx.input``
(docs/contract.md, "Mouse and keyboard masters"). In dry-run mode each step logs what it
would do and returns that description; image targets are still searched once, so the
description says where the target would be.

Coordinates (contract, "Script format and step registry"): ``x``/``y`` are absolute
desktop pixels; with ``window: {title: ...}`` they are relative to the top-left corner
of the first visible window whose title contains ``title``; ``offset: [dx, dy]`` is
added to the resulting point.

Image targets (``image``, with ``threshold``, ``scale``, ``region`` and ``window``) are
searched through ``ctx.vision``: once, or until the step's common ``timeout`` when it is
set; a miss fails with ``image_not_found``. The action lands on the centre of the match
plus ``offset``, and the match is passed to the input layer as the ``target`` area for
human-like landing (without an offset).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from j5_macro.backends.base import Button, Rect, UnknownKey
from j5_macro.input.api import StepInput
from j5_macro.input.planning import find_control_character
from j5_macro.protocol.errors import ErrorCode
from j5_macro.steps.checks import check_point, is_interpolated
from j5_macro.steps.images import (
    describe_match,
    dry_locate,
    locate_target,
    match_report,
    search_options,
)
from j5_macro.steps.spec import (
    Executor,
    ParamProblem,
    ParamSpec,
    StepContext,
    StepError,
    StepSpec,
    StepType,
    parse_duration,
)
from j5_macro.vision.api import DEFAULT_SCALE, DEFAULT_THRESHOLD

BUTTONS = ("left", "right", "middle")

Describe = Callable[[dict[str, Any]], str]
Perform = Callable[[StepContext, dict[str, Any]], dict[str, Any]]


def input_step(describe: Describe, perform: Perform, *, image_wait: bool = False) -> Executor:
    """An executor that runs ``perform``, or in dry-run mode searches each image target
    once and describes what the step would do. ``image_wait``: the step itself is a
    wait for its ``image`` (``wait_image``)."""

    def run(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
        if not ctx.dry_run:
            return perform(ctx, params)
        result: dict[str, Any] = {"dry_run": True}
        found = ""
        if "image" in params:
            wait = _wait_seconds(ctx, params) if image_wait else 0.0
            match = dry_locate(ctx, params, wait)
            found = describe_match(match)
            if match is not None:
                result.update(match_report(match))
        for name in ("from", "to"):
            target = params.get(name)
            if isinstance(target, Mapping) and "image" in target:
                found += describe_match(dry_locate(ctx, target), name)
        description = f"would {describe(params)}{found}"
        ctx.log("info", description)
        return {**result, "description": description}

    return run


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


# --- execution --------------------------------------------------------------------------


def _int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StepError(ErrorCode.VALIDATION_FAILED, f"'{name}' must be a number",
                        {"param": name})
    return round(value)


def _window(inp: StepInput, window: Any, name: str) -> Rect | None:
    """The frame of the step's ``window``, or None without one."""
    if window is None:
        return None
    title = window.get("title") if isinstance(window, Mapping) else None
    if not isinstance(title, str) or not title:
        raise StepError(ErrorCode.VALIDATION_FAILED,
                        f"'{name}' needs a non-empty 'title', e.g. {{title: Notepad}}",
                        {"param": name})
    rect = inp.find_window(title)
    if rect is None:
        raise StepError(ErrorCode.WINDOW_NOT_FOUND,
                        f"no visible window whose title contains {title!r}",
                        {"title": title})
    return rect


def _resolve_target(ctx: StepContext, inp: StepInput, target: Mapping[str, Any],
                    where: str = "") -> tuple[tuple[int, int], Rect | None, dict[str, Any]]:
    """The point of an image or ``x``/``y`` target, the matched area (None for
    coordinates) and what to report about it in the result."""
    if "image" not in target:
        point, report = _resolve_point(inp, target, where)
        return point, None, report
    match = locate_target(ctx, target)
    assert match.bbox is not None  # locate_target raises otherwise
    report = {"image": target["image"], **match_report(match)}
    del report["found"]
    return match.bbox.center, match.bbox, report


def _wait_seconds(ctx: StepContext, params: Mapping[str, Any]) -> float:
    try:
        return parse_duration(params.get("timeout", DEFAULT_WAIT)).sample(ctx.rng)
    except ValueError as exc:
        raise StepError(ErrorCode.VALIDATION_FAILED, str(exc), {"param": "timeout"}) from exc


def _resolve_point(inp: StepInput, target: Mapping[str, Any],
                   where: str = "") -> tuple[tuple[int, int], dict[str, Any]]:
    """The absolute point of an ``x``/``y`` target (``window``-relative when given) and
    what to report about it in the result."""
    prefix = f"{where}." if where else ""
    x, y = _int(target.get("x"), f"{prefix}x"), _int(target.get("y"), f"{prefix}y")
    rect = _window(inp, target.get("window"), f"{prefix}window")
    report: dict[str, Any] = {}
    if rect is not None:
        report["window"] = [rect.x, rect.y, rect.width, rect.height]
        x, y = rect.x + x, rect.y + y
    return (x, y), report


def _offset(params: Mapping[str, Any], point: tuple[int, int]) -> tuple[int, int]:
    offset = params.get("offset")
    if offset is None:
        return point
    if not isinstance(offset, list) or len(offset) != 2:
        raise StepError(ErrorCode.VALIDATION_FAILED, "'offset' must be [dx, dy]",
                        {"param": "offset"})
    return point[0] + _int(offset[0], "offset"), point[1] + _int(offset[1], "offset")


def _button(params: Mapping[str, Any]) -> Button:
    try:
        return Button(params.get("button", "left"))
    except ValueError as exc:
        raise StepError(ErrorCode.VALIDATION_FAILED,
                        f"'button' must be one of {', '.join(BUTTONS)}",
                        {"param": "button"}) from exc


def _landing(params: Mapping[str, Any], point: tuple[int, int],
             area: Rect | None) -> tuple[int, int, Rect | None]:
    """The final point (``offset`` added) and the ``target`` area for the input layer:
    the match itself, unless an offset moves the point away from it."""
    x, y = _offset(params, point)
    return x, y, area if (x, y) == point else None


def perform_click(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    inp = ctx.input
    button = _button(params)
    clicks = _int(params.get("clicks", 1), "clicks")
    if clicks < 1:
        raise StepError(ErrorCode.VALIDATION_FAILED, "'clicks' must be 1 or more",
                        {"param": "clicks"})
    point, area, report = _resolve_target(ctx, inp, params)
    x, y, target = _landing(params, point, area)
    ctx.check_stop()
    inp.click(x, y, button=button, clicks=clicks, target=target)
    return {"x": x, "y": y, "button": button.value, "clicks": clicks, **report}


def perform_move(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    inp = ctx.input
    point, area, report = _resolve_target(ctx, inp, params)
    x, y, target = _landing(params, point, area)
    ctx.check_stop()
    inp.move(x, y, target=target)
    return {"x": x, "y": y, **report}


def perform_drag(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    inp = ctx.input
    start, _, start_report = _resolve_target(ctx, inp, params["from"], "from")
    end, _, end_report = _resolve_target(ctx, inp, params["to"], "to")
    button = _button(params)
    ctx.check_stop()
    inp.drag(start, end, button=button)
    result: dict[str, Any] = {"from": list(start), "to": list(end), "button": button.value}
    for name, report in (("from", start_report), ("to", end_report)):
        for key in ("window", "bbox", "score"):
            if key in report:
                result[f"{name}_{key}"] = report[key]
    return result


def perform_wait_image(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    match = ctx.vision.wait(params["image"], timeout=_wait_seconds(ctx, params),
                            **search_options(params))
    return match_report(match)


def perform_scroll(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    dx, dy = _int(params.get("dx", 0), "dx"), _int(params.get("dy", 0), "dy")
    ctx.input.scroll(dx, dy)
    return {"dx": dx, "dy": dy}


def perform_type(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    text = params["text"]
    if not isinstance(text, str):
        text = str(text)
    keyboard = params.get("keyboard")
    inp = ctx.input_for({"keyboard": keyboard}) if keyboard is not None else ctx.input
    inp.type_text(text)
    # The text itself is not repeated: it may be a secret.
    return {"chars": len(text)}


def perform_key(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    combo = params["press"]
    if not isinstance(combo, str) or any(not p.strip() for p in combo.split("+")):
        raise StepError(ErrorCode.VALIDATION_FAILED, f"invalid key combination {combo!r}",
                        {"param": "press"})
    hold: float | None = None
    if params.get("hold") is not None:
        try:
            hold = parse_duration(params["hold"]).sample(ctx.rng)
        except ValueError as exc:
            raise StepError(ErrorCode.VALIDATION_FAILED, str(exc), {"param": "hold"}) from exc
    inp = ctx.input
    try:
        inp.press(combo, hold=hold)
    except (UnknownKey, ValueError) as exc:
        raise StepError(ErrorCode.VALIDATION_FAILED, f"cannot press {combo!r}: {exc}",
                        {"param": "press"}) from exc
    result: dict[str, Any] = {"press": combo}
    if hold is not None:
        result["hold"] = hold
    return result


# --- cross-field checks -----------------------------------------------------------------


def _check_threshold(params: Mapping[str, Any]) -> list[ParamProblem]:
    value = params.get("threshold")
    if isinstance(value, int | float) and not 0 <= value <= 1:
        return [ParamProblem("threshold", "must be between 0 and 1")]
    return []


def _check_image_options(params: Mapping[str, Any]) -> list[ParamProblem]:
    problems = _check_threshold(params) + _check_length(params, "region", 4)
    scale = params.get("scale")
    if isinstance(scale, int | float) and not isinstance(scale, bool) and scale <= 0:
        problems.append(ParamProblem("scale", "must be greater than 0"))
    if "image" not in params:
        # ``scale`` is filled in with its default, so only another value is a mistake.
        problems += [ParamProblem(name, "only applies to 'image' targets")
                     for name in ("region", "scale")
                     if name in params and not (name == "scale" and scale == DEFAULT_SCALE)]
    return problems


def _check_length(params: Mapping[str, Any], name: str, length: int) -> list[ParamProblem]:
    value = params.get(name)
    if isinstance(value, list) and len(value) != length:
        return [ParamProblem(name, f"expected {length} numbers")]
    return []


def check_click(params: Mapping[str, Any]) -> list[ParamProblem]:
    problems = check_point(params) + _check_image_options(params)
    problems += _check_length(params, "offset", 2)
    clicks = params.get("clicks")
    if isinstance(clicks, int) and clicks < 1:
        problems.append(ParamProblem("clicks", "must be 1 or more"))
    return problems


def check_move(params: Mapping[str, Any]) -> list[ParamProblem]:
    return check_point(params) + _check_image_options(params)


def check_drag(params: Mapping[str, Any]) -> list[ParamProblem]:
    problems: list[ParamProblem] = []
    for name in ("from", "to"):
        value = params.get(name)
        if isinstance(value, Mapping):
            problems += check_point(value, name)
    return problems


def check_wait_image(params: Mapping[str, Any]) -> list[ParamProblem]:
    return _check_image_options(params)


def text_problem(text: Any) -> ParamProblem | None:
    """The problem with ``type``'s ``text``: a control character it cannot press.

    The message and details give the character's index and code point, never the text
    itself, which may be a secret."""
    if not isinstance(text, str):
        return None
    index = find_control_character(text)
    if index is None:
        return None
    code_point = f"U+{ord(text[index]):04X}"
    return ParamProblem("text", f"control character {code_point} at index {index} cannot "
                        r"be typed; only \n, \r, \t and \b are allowed",
                        {"index": index, "code_point": code_point})


def check_type(params: Mapping[str, Any]) -> list[ParamProblem]:
    """Literal text is checked when the script loads; interpolated text when it runs."""
    text = params.get("text")
    if is_interpolated(text):
        return []
    problem = text_problem(text)
    return [] if problem is None else [problem]


def _checked_text(executor: Executor) -> Executor:
    """``executor`` after checking the interpolated ``text``, before any input or dry-run
    description (the registry's check skips text that still contains ``${``)."""

    def run(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any] | None:
        problem = text_problem(params.get("text"))
        if problem is not None:
            raise StepError(ErrorCode.VALIDATION_FAILED,
                            f"parameter 'text': {problem.message}",
                            {"param": problem.param, **(problem.details or {})})
        return executor(ctx, params)

    return run


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
THRESHOLD = ParamSpec(name="threshold", type="number", default=DEFAULT_THRESHOLD,
                      doc="Minimum image match score, from 0 to 1.")
SCALE = ParamSpec(name="scale", type="number", default=DEFAULT_SCALE,
                  doc="Scale applied to the image before searching, to compensate images "
                      "captured at another display scaling.")
REGION = ParamSpec(name="region", type="list",
                   doc="[x, y, width, height] to search in (relative to 'window' when "
                       "given); defaults to the window, else the whole desktop.")
DEFAULT_WAIT = "10s"
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
        THRESHOLD, SCALE, REGION,
    ),
), input_step(_describe_click, perform_click))

MOVE = StepType(StepSpec(
    name="move",
    doc="Move the mouse to an image or to x/y.",
    params=(_image(), X, Y, WINDOW, THRESHOLD, SCALE, REGION),
), input_step(lambda p: f"move to {_target(p)}", perform_move))

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
), input_step(_describe_drag, perform_drag))

SCROLL = StepType(StepSpec(
    name="scroll",
    doc="Scroll the mouse wheel.",
    params=(
        ParamSpec(name="dx", type="integer", default=0,
                  doc="Horizontal steps; positive scrolls right."),
        ParamSpec(name="dy", type="integer", default=0,
                  doc="Vertical steps; positive scrolls down."),
    ),
), input_step(lambda p: f"scroll dx={p.get('dx', 0)}, dy={p.get('dy', 0)}",
              perform_scroll))

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
), _checked_text(input_step(lambda p: f"type {len(str(p['text']))} characters",
                            perform_type)))

KEY = StepType(StepSpec(
    name="key",
    doc="Press a key or a combination such as ctrl+s.",
    shorthand="press",
    params=(
        ParamSpec(name="press", type="string", required=True,
                  doc="Key or combination joined with '+', e.g. enter or ctrl+s."),
        ParamSpec(name="hold", type="duration", doc="Keep the keys down this long."),
    ),
), input_step(_describe_key, perform_key))

WAIT_IMAGE = StepType(StepSpec(
    name="wait_image",
    doc="Wait until an image is visible on screen.",
    shorthand="image",
    params=(
        _image(required=True),
        ParamSpec(name="timeout", type="duration", default=DEFAULT_WAIT,
                  doc="Give up with image_not_found after this long; the step's common "
                      "'timeout' replaces it when set."),
        THRESHOLD, REGION, WINDOW, SCALE,
    ),
), input_step(_describe_wait, perform_wait_image, image_wait=True))
