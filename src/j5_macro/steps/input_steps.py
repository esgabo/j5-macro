"""Input steps: ``click``, ``move``, ``drag``, ``scroll``, ``type`` and ``key`` (the
waits are in :mod:`j5_macro.steps.waits`).

In normal and step mode they drive the run's input session through ``ctx.input``
(docs/contract.md, "Mouse and keyboard masters"). In dry-run mode each step logs what it
would do and returns that description; image targets are still searched once, so the
description says where the target would be.

Coordinates (contract, "Script format and step registry"): ``x``/``y`` are absolute
desktop pixels; with ``window: {title: ...}`` they are relative to the top-left corner
of the client area of the first visible window whose title contains ``title``;
``offset: [dx, dy]`` is added to the resulting point. Offsets of image and ``at``
targets may be multiples of the target's size (``0.5w``, ``-2h``), and their pixels are
multiplied by the display scale ratio (:mod:`j5_macro.steps.positions`).

Image targets (``image``, with ``threshold``, ``scale``, ``region`` and ``window``) are
searched through ``ctx.vision``: once, or until the step's common ``timeout`` when it is
set (a missing window then counts as "not found yet"); a miss fails with
``image_not_found``. ``at`` targets are image results kept with
``as``, placed where they are now (following their window; see
:mod:`j5_macro.steps.positions`). For both, the action lands on the centre of the area
plus ``offset``, and the area is passed to the input layer as the ``target`` for
human-like landing (without an offset). Results report the area used, so they can be
kept with ``as`` in turn.
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
    dry_search,
    locate_target,
    match_report,
    window_missing,
    window_title,
)
from j5_macro.steps.positions import (
    FindWindow,
    aim,
    area_report,
    lengths_problem,
    locate_stored,
    read_stored,
    rect_list,
    region_problem,
    relative,
    stored_report,
)
from j5_macro.steps.spec import (
    Executor,
    ParamProblem,
    ParamSpec,
    ResultField,
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


def input_step(describe: Describe, perform: Perform) -> Executor:
    """An executor that runs ``perform``, or in dry-run mode searches each image target
    once and describes what the step would do."""

    def run(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
        if not ctx.dry_run:
            return perform(ctx, params)
        result: dict[str, Any] = {"dry_run": True}
        found = ""
        if "image" in params:
            match = dry_locate(ctx, params)
            found = describe_match(match)
            if match is not None:
                result.update(match_report(match, params))
        elif "at" in params:
            result.update(stored_report(read_stored(params["at"])))
        else:
            _dry_window(ctx, params)
        for name in ("from", "to"):
            target = params.get(name)
            if isinstance(target, Mapping) and "image" in target:
                found += describe_match(dry_locate(ctx, target), name)
            elif isinstance(target, Mapping) and "at" not in target:
                _dry_window(ctx, target)
        description = f"would {describe(params)}{found}"
        ctx.log("info", description)
        return {**result, "description": description}

    return run


def _dry_window(ctx: StepContext, target: Mapping[str, Any]) -> None:
    """The window of an ``x``/``y`` target, looked up once in a dry run: a warning when
    it is missing, ``window_not_found`` in a strict dry run (see
    :func:`~j5_macro.steps.images.dry_search`)."""
    title = window_title(target)
    if title is None:
        return
    looked = dry_search(ctx, f"window {title!r}", lambda: [ctx.vision.find_window(title)],
                        lambda found: None if found[0] is not None
                        else window_missing(title, " in a strict dry run"))
    if looked is not None and looked[0] is None:
        ctx.log("warning", f"dry-run: no visible window whose title contains {title!r}")


def _target(params: Mapping[str, Any]) -> str:
    if "image" in params:
        where = f"image {params['image']}"
    elif "at" in params:
        where = _describe_stored(params["at"])
    else:
        where = f"({params.get('x')}, {params.get('y')})"
    window = params.get("window")
    if isinstance(window, Mapping) and window:
        where += f" in window {dict(window)}"
    return where


def _describe_stored(value: Any) -> str:
    """A stored result as a dry run sees it: where it was, without looking it up."""
    stored = read_stored(value)
    if stored.bbox is None:
        return "a stored result without a position"
    x, y = stored.bbox.center
    where = f"the stored result at ({x}, {y})"
    if stored.window is not None:
        where += f" in window {stored.window!r}"
    return where


def _describe_click(p: dict[str, Any]) -> str:
    clicks = p.get("clicks", 1)
    times = "" if clicks == 1 else f" x{clicks}"
    return f"click {p.get('button', 'left')}{times} at {_target(p)}{_offset(p)}"


def _describe_move(p: dict[str, Any]) -> str:
    return f"move to {_target(p)}{_offset(p)}"


def _describe_drag(p: dict[str, Any]) -> str:
    start, end = p["from"], p["to"]
    return (f"drag {p.get('button', 'left')} from {_target(start)}{_offset(start)} "
            f"to {_target(end)}{_offset(end)}")


def _offset(p: Mapping[str, Any]) -> str:
    return f" offset {p['offset']}" if p.get("offset") else ""


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


def _finder(inp: StepInput, name: str) -> FindWindow:
    """Window lookups for a stored result's ``window``."""

    def find(title: str) -> Rect:
        rect = _window(inp, {"title": title}, name)
        assert rect is not None  # _window raises otherwise
        return rect

    return find


def _resolve_target(ctx: StepContext, inp: StepInput, target: Mapping[str, Any],
                    where: str = "") -> tuple[tuple[int, int], Rect | None, dict[str, Any]]:
    """Where a target (``image``, ``at`` or ``x``/``y``) is: the point to act on, with
    its ``offset``, the area the input layer may land in, and what to report about the
    target in the result."""
    prefix = f"{where}." if where else ""
    report: dict[str, Any]
    area: Rect | None
    if "image" in target:
        match = locate_target(ctx, target)
        assert match.bbox is not None  # locate_target raises otherwise
        report = {"image": target["image"], **match_report(match, target)}
        del report["found"]
        area = match.bbox
        point = area.center
        # The offset follows the template: the same display scale ratio.
        ratio = match.display_ratio
    elif "at" in target:
        stored = read_stored(target["at"], f"{prefix}at")
        area, frame = locate_stored(stored, _finder(inp, f"{prefix}at"), f"{prefix}at")
        report = area_report(area, stored.window, relative(area, frame))
        point = area.center
        ratio = ctx.display_ratio(point)
    else:
        point, report = _resolve_point(inp, target, where)
        area = None
        ratio = 1.0  # x/y and their offsets are positions: never scaled
    x, y, landing = aim(point, area, target.get("offset"), f"{prefix}offset", ratio)
    return (x, y), landing, report


def _resolve_point(inp: StepInput, target: Mapping[str, Any],
                   where: str = "") -> tuple[tuple[int, int], dict[str, Any]]:
    """The absolute point of an ``x``/``y`` target (``window``-relative when given) and
    what to report about it in the result."""
    prefix = f"{where}." if where else ""
    x, y = _int(target.get("x"), f"{prefix}x"), _int(target.get("y"), f"{prefix}y")
    rect = _window(inp, target.get("window"), f"{prefix}window")
    report: dict[str, Any] = {}
    if rect is not None:
        report["window"] = target["window"]["title"]
        report["window_area"] = rect_list(rect)
        x, y = rect.x + x, rect.y + y
    return (x, y), report


def _button(params: Mapping[str, Any]) -> Button:
    try:
        return Button(params.get("button", "left"))
    except ValueError as exc:
        raise StepError(ErrorCode.VALIDATION_FAILED,
                        f"'button' must be one of {', '.join(BUTTONS)}",
                        {"param": "button"}) from exc


def perform_click(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    inp = ctx.input
    button = _button(params)
    clicks = _int(params.get("clicks", 1), "clicks")
    if clicks < 1:
        raise StepError(ErrorCode.VALIDATION_FAILED, "'clicks' must be 1 or more",
                        {"param": "clicks"})
    (x, y), target, report = _resolve_target(ctx, inp, params)
    ctx.check_stop()
    inp.click(x, y, button=button, clicks=clicks, target=target)
    return {"x": x, "y": y, "button": button.value, "clicks": clicks, **report}


def perform_move(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    inp = ctx.input
    (x, y), target, report = _resolve_target(ctx, inp, params)
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
        result.update((f"{name}_{key}", value) for key, value in report.items()
                      if key != "image")
    return result


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


def check_image_options(params: Mapping[str, Any]) -> list[ParamProblem]:
    problems = _check_threshold(params)
    region = region_problem(params.get("region"))
    if region is not None:
        problems.append(ParamProblem("region", region))
    scale = params.get("scale")
    if isinstance(scale, int | float) and not isinstance(scale, bool) and scale <= 0:
        problems.append(ParamProblem("scale", "must be greater than 0"))
    if "image" not in params:
        # ``scale`` is filled in with its default, so only another value is a mistake.
        problems += [ParamProblem(name, "only applies to 'image' targets")
                     for name in ("region", "scale")
                     if name in params and not (name == "scale" and scale == DEFAULT_SCALE)]
    return problems


def _check_offset(params: Mapping[str, Any], where: str = "") -> list[ParamProblem]:
    """``offset``: two items, each pixels or, for targets with a size (``image``,
    ``at``), a multiple of it (``0.5w``)."""
    sized = "image" in params or "at" in params
    problem = lengths_problem(params.get("offset"), sized)
    return [] if problem is None else [ParamProblem(f"{where}offset", problem)]


def check_click(params: Mapping[str, Any]) -> list[ParamProblem]:
    problems = check_point(params) + check_image_options(params)
    problems += _check_offset(params)
    clicks = params.get("clicks")
    if isinstance(clicks, int) and clicks < 1:
        problems.append(ParamProblem("clicks", "must be 1 or more"))
    return problems


def check_move(params: Mapping[str, Any]) -> list[ParamProblem]:
    return check_point(params) + check_image_options(params) + _check_offset(params)


def check_drag(params: Mapping[str, Any]) -> list[ParamProblem]:
    problems: list[ParamProblem] = []
    for name in ("from", "to"):
        value = params.get(name)
        if isinstance(value, Mapping):
            problems += check_point(value, name) + _check_offset(value, f"{name}.")
    return problems


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
                     doc="Template image: a path relative to the script file, or \"#name\" "
                         "of an image embedded in the script.")


X = ParamSpec(name="x", type="integer", doc="Horizontal position in physical pixels.")
Y = ParamSpec(name="y", type="integer", doc="Vertical position in physical pixels.")
AT = ParamSpec(name="at", type="any",
               doc="An image result kept with 'as', e.g. \"${label}\": the action aims at "
                   "its area, where it is now (following its window).")
OFFSET = ParamSpec(name="offset", type="list",
                   doc="[dx, dy] added to the target point: pixels, or with an image or "
                       "'at' target multiples of its size such as 0.5w or -2h; with an "
                       "image or 'at' target the mouse aims at that exact point, and "
                       "pixels follow the display scale.")
WINDOW = ParamSpec(name="window", type="object",
                   doc="Window to work in, e.g. {title: ...}; coordinates become relative.")
THRESHOLD = ParamSpec(name="threshold", type="number", default=DEFAULT_THRESHOLD,
                      doc="Minimum image match score, from 0 to 1.")
SCALE = ParamSpec(name="scale", type="number", default=DEFAULT_SCALE,
                  doc="Extra factor the image is resized by before searching, on top of "
                      "the display scale / capture scale ratio j5 applies itself.")
REGION = ParamSpec(name="region", type="any",
                   doc="[x, y, width, height] to search in (relative to 'window' when "
                       "given), or {at: <image result>, offset: [dx, dy], size: [w, h]} "
                       "next to a kept result (pixels, or multiples of its size such as "
                       "2w); defaults to the window, else the whole desktop.")
BUTTON = ParamSpec(name="button", type="string", default="left", choices=BUTTONS,
                   doc="Mouse button.")

AREA_RESULT = (
    ResultField(name="bbox", type="list",
                doc="[x, y, width, height] of the area, on the desktop."),
    ResultField(name="center", type="list", doc="[x, y] of the area's centre, on the desktop."),
    ResultField(name="window", type="string", optional=True,
                doc="The title of the window searched in, when the search had one."),
    ResultField(name="window_bbox", type="list", optional=True,
                doc="[x, y, width, height] of the area relative to the window's client "
                    "area, when the search had a window."),
)
"""Where an image result is: what makes it usable as an ``at`` target."""

TARGET_RESULT = (
    ResultField(name="image", type="string", optional=True,
                doc="The template of an image target."),
    ResultField(name="score", type="number", optional=True,
                doc="Match score of an image target."),
    ResultField(name="bbox", type="list", optional=True,
                doc="Image and 'at' targets: [x, y, width, height] of the area aimed at, "
                    "on the desktop."),
    ResultField(name="center", type="list", optional=True,
                doc="Image and 'at' targets: [x, y] of the area's centre, on the desktop."),
    ResultField(name="window", type="string", optional=True,
                doc="The title of the target's window, when it has one."),
    ResultField(name="window_bbox", type="list", optional=True,
                doc="Image and 'at' targets in a window: [x, y, width, height] of the "
                    "area relative to the window's client area."),
    ResultField(name="window_area", type="list", optional=True,
                doc="An x/y target with a window: the window's client area used, "
                    "[x, y, width, height] on the desktop."),
)

DRY_RESULT = (
    ResultField(name="dry_run", type="boolean", optional=True,
                doc="True in dry runs, whose result is dry_run, description and, when an "
                    "image was searched or 'at' was given, found, score and the area "
                    "fields."),
    ResultField(name="description", type="string", optional=True,
                doc="Dry runs: what the step would do."),
    ResultField(name="found", type="boolean", optional=True,
                doc="Dry runs: whether the image was found."),
)

CLICK = StepType(StepSpec(
    name="click",
    doc="Click on an image, a kept image result or at x/y.",
    params=(
        _image(), X, Y, AT, WINDOW, BUTTON,
        ParamSpec(name="clicks", type="integer", default=1, doc="Number of clicks."),
        OFFSET, THRESHOLD, SCALE, REGION,
    ),
    result=(
        ResultField(name="x", type="integer", doc="The point clicked, on the desktop."),
        ResultField(name="y", type="integer", doc="The point clicked, on the desktop."),
        ResultField(name="button", type="string", doc="The button clicked."),
        ResultField(name="clicks", type="integer", doc="How many clicks."),
        *TARGET_RESULT, *DRY_RESULT,
    ),
), input_step(_describe_click, perform_click))

MOVE = StepType(StepSpec(
    name="move",
    doc="Move the mouse to an image, a kept image result or x/y.",
    params=(_image(), X, Y, AT, WINDOW, OFFSET, THRESHOLD, SCALE, REGION),
    result=(
        ResultField(name="x", type="integer", doc="Where the pointer went, on the desktop."),
        ResultField(name="y", type="integer", doc="Where the pointer went, on the desktop."),
        *TARGET_RESULT, *DRY_RESULT,
    ),
), input_step(_describe_move, perform_move))

DRAG = StepType(StepSpec(
    name="drag",
    doc="Press a button at one point, move to another and release.",
    params=(
        ParamSpec(name="from", type="object", required=True,
                  doc="Start point: {image: ...}, {at: ...} or {x: ..., y: ...}, each "
                      "with an optional offset."),
        ParamSpec(name="to", type="object", required=True,
                  doc="End point: {image: ...}, {at: ...} or {x: ..., y: ...}, each "
                      "with an optional offset."),
        BUTTON,
    ),
    result=(
        ResultField(name="from", type="list", doc="[x, y] where the button went down."),
        ResultField(name="to", type="list", doc="[x, y] where the button went up."),
        ResultField(name="button", type="string", doc="The button dragged with."),
        ResultField(name="from_bbox", type="list", optional=True,
                    doc="The start target's fields, prefixed with from_ (from_bbox, "
                        "from_center, from_window, from_window_bbox, from_score...)."),
        ResultField(name="to_bbox", type="list", optional=True,
                    doc="The end target's fields, prefixed with to_."),
        *DRY_RESULT,
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
    result=(
        ResultField(name="dx", type="integer", doc="Horizontal steps scrolled."),
        ResultField(name="dy", type="integer", doc="Vertical steps scrolled."),
        *DRY_RESULT,
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
    result=(
        ResultField(name="chars", type="integer",
                    doc="How many characters were typed (never the text)."),
        *DRY_RESULT,
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
    result=(
        ResultField(name="press", type="string", doc="The combination pressed."),
        ResultField(name="hold", type="number", optional=True,
                    doc="Seconds the keys were held, when 'hold' was given."),
        *DRY_RESULT,
    ),
), input_step(_describe_key, perform_key))
