"""The waits: ``wait_image_in``, ``wait_image_out``, ``wait_window_in`` and
``wait_window_out`` (docs/contract.md, "Script format and step registry": waiting).

Each waits in one direction for an image or a window, polling through ``ctx.vision``
every 0.25 s until its ``timeout`` (10 s by default; the step's common ``timeout``
replaces it). ``pause`` suspends a wait and ``stop`` ends it at once. ``_in`` steps
fail on timeout with ``image_not_found`` or ``window_not_found``, ``_out`` steps with
``still_visible``. Image waits take the image target parameters of
:mod:`j5_macro.steps.images`; while they poll, a missing ``window`` is "not found yet".

Dry runs never wait: each step checks once through
:func:`~j5_macro.steps.images.dry_search`, describes what it would do and goes on.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from j5_macro.backends.base import WindowInfo
from j5_macro.protocol.errors import ErrorCode
from j5_macro.steps.checks import is_interpolated
from j5_macro.steps.images import (
    describe_match,
    dry_locate,
    dry_search,
    match_report,
    search_options,
)
from j5_macro.steps.input_steps import (
    AREA_RESULT,
    DRY_RESULT,
    REGION,
    SCALE,
    THRESHOLD,
    WINDOW,
    check_image_options,
)
from j5_macro.steps.positions import rect_list
from j5_macro.steps.spec import (
    ParamProblem,
    ParamSpec,
    ResultField,
    StepContext,
    StepError,
    StepSpec,
    StepType,
    parse_duration,
)

DEFAULT_WAIT = "10s"


def _timeout(ctx: StepContext, params: Mapping[str, Any]) -> float:
    """The wait's own ``timeout`` in seconds (``ctx.vision`` replaces it with the step's
    common ``timeout`` when that is set)."""
    try:
        return parse_duration(params.get("timeout", DEFAULT_WAIT)).sample(ctx.rng)
    except ValueError as exc:
        raise StepError(ErrorCode.VALIDATION_FAILED, str(exc), {"param": "timeout"}) from exc


def _up_to(ctx: StepContext, params: Mapping[str, Any]) -> str:
    """How long the wait would last, for dry-run descriptions."""
    if ctx.timeout is not None:
        return f"{ctx.timeout:g}s"
    return str(params.get("timeout", DEFAULT_WAIT))


def _gone(seconds: float) -> dict[str, Any]:
    return {"gone": True, "waited_ms": max(0, round(seconds * 1000))}


def _dry(ctx: StepContext, description: str, report: Mapping[str, Any]) -> dict[str, Any]:
    ctx.log("info", description)
    return {"dry_run": True, **report, "description": description}


# --- images -----------------------------------------------------------------------------


def wait_image_in(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    if ctx.dry_run:
        match = dry_locate(ctx, params)
        return _dry(ctx, f"would wait up to {_up_to(ctx, params)} for image "
                    f"{params['image']}{describe_match(match)}",
                    {} if match is None else match_report(match, params))
    match = ctx.vision.wait(params["image"], timeout=_timeout(ctx, params),
                            **search_options(params))
    return match_report(match, params)


def wait_image_out(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    if ctx.dry_run:
        match = dry_locate(ctx, params)
        return _dry(ctx, f"would wait up to {_up_to(ctx, params)} for image "
                    f"{params['image']} to disappear{describe_match(match)}",
                    {} if match is None else match_report(match, params))
    return _gone(ctx.vision.wait_gone(params["image"], timeout=_timeout(ctx, params),
                                      **search_options(params)))


# --- windows ----------------------------------------------------------------------------


def _window_report(window: WindowInfo) -> dict[str, Any]:
    return {"window": window.title, "bbox": rect_list(window.rect)}


def _dry_window(ctx: StepContext, params: Mapping[str, Any], what: str) -> dict[str, Any]:
    """One look for the window. ``window`` is the matching window's full title or,
    when none matched or the lookup could not be made, the ``title`` asked for, so later
    steps that use it (``window: {title: "${editor.window}"}``) still check."""
    title = params["title"]
    looked = dry_search(ctx, f"window {title!r}",
                        lambda: [ctx.vision.find_window(title)])
    report: dict[str, Any] = {}
    found = ""
    if looked is not None:
        window = looked[0]
        report["found"] = window is not None
        if window is not None:
            report.update(_window_report(window))
            found = f" (found {window.title!r})"
        else:
            found = " (not found)"
    report.setdefault("window", title)
    return _dry(ctx, f"would wait up to {_up_to(ctx, params)} for {what} whose title "
                f"contains {title!r}{found}", report)


def wait_window_in(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    if ctx.dry_run:
        return _dry_window(ctx, params, "a window")
    return _window_report(ctx.vision.wait_window(params["title"],
                                                 timeout=_timeout(ctx, params)))


def wait_window_out(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    if ctx.dry_run:
        return _dry_window(ctx, params, "no window")
    return _gone(ctx.vision.wait_window_gone(params["title"],
                                             timeout=_timeout(ctx, params)))


# --- checks -----------------------------------------------------------------------------


def check_image_wait(params: Mapping[str, Any]) -> list[ParamProblem]:
    return check_image_options(params)


def check_wait_window(params: Mapping[str, Any]) -> list[ParamProblem]:
    title = params.get("title")
    if isinstance(title, str) and not is_interpolated(title) and not title:
        return [ParamProblem("title", "must not be empty")]
    return []


# --- specs ------------------------------------------------------------------------------


def _timeout_param(fails: str) -> ParamSpec:
    return ParamSpec(name="timeout", type="duration", default=DEFAULT_WAIT,
                     doc=f"Give up with {fails} after this long; the step's common "
                         "'timeout' replaces it when set.")


IMAGE = ParamSpec(name="image", type="string", required=True,
                  doc="Template image: a path relative to the script file, or \"#name\" "
                      "of an image embedded in the script.")
TITLE = ParamSpec(name="title", type="string", required=True,
                  doc="Text the window's title contains (case-sensitive), as in a step's "
                      "window: {title: ...}.")

GONE_RESULT = (
    ResultField(name="gone", type="boolean", doc="Always true: the target is gone."),
    ResultField(name="waited_ms", type="integer",
                doc="Milliseconds waited, paused time excluded (0 when it was already "
                    "gone)."),
)

DRY_MATCH = (
    ResultField(name="score", type="number", optional=True,
                doc="Dry runs: best match score, from 0 to 1."),
    *(ResultField(name=f.name, type=f.type, optional=True, doc=f"Dry runs: {f.doc}")
      for f in AREA_RESULT),
)
"""What a dry run's single search reports besides ``found``."""

WINDOW_RESULT = (
    ResultField(name="window", type="string",
                doc="The full title of the window, for window: {title: ...}."),
    ResultField(name="bbox", type="list",
                doc="[x, y, width, height] of the window's client area, on the desktop."),
)

WAIT_IMAGE_IN = StepType(StepSpec(
    name="wait_image_in",
    doc="Wait until an image is visible on screen.",
    shorthand="image",
    params=(IMAGE, _timeout_param("image_not_found"), THRESHOLD, REGION, WINDOW, SCALE),
    result=(
        ResultField(name="found", type="boolean",
                    doc="Whether the image was found (always true outside dry runs)."),
        ResultField(name="score", type="number", doc="Best match score, from 0 to 1."),
        *AREA_RESULT,
        *DRY_RESULT[:2],
    ),
), wait_image_in)

WAIT_IMAGE_OUT = StepType(StepSpec(
    name="wait_image_out",
    doc="Wait until an image is no longer visible on screen.",
    shorthand="image",
    params=(IMAGE, _timeout_param("still_visible"), THRESHOLD, REGION, WINDOW, SCALE),
    result=(*GONE_RESULT, *DRY_RESULT, *DRY_MATCH),
), wait_image_out)

WAIT_WINDOW_IN = StepType(StepSpec(
    name="wait_window_in",
    doc="Wait until a window whose title contains a text is visible.",
    shorthand="title",
    params=(TITLE, _timeout_param("window_not_found")),
    result=(
        *WINDOW_RESULT,
        *DRY_RESULT[:2],
        ResultField(name="found", type="boolean", optional=True,
                    doc="Dry runs: whether a window matched (when the lookup could be "
                        "made); without a match, 'window' is the title asked for and "
                        "there is no 'bbox'."),
    ),
), wait_window_in)

WAIT_WINDOW_OUT = StepType(StepSpec(
    name="wait_window_out",
    doc="Wait until no visible window's title contains a text.",
    shorthand="title",
    params=(TITLE, _timeout_param("still_visible")),
    result=(
        *GONE_RESULT,
        *DRY_RESULT[:2],
        ResultField(name="found", type="boolean", optional=True,
                    doc="Dry runs: whether a window matched."),
        ResultField(name="window", type="string", optional=True,
                    doc="Dry runs: the full title of the matching window, else the "
                        "title asked for."),
        ResultField(name="bbox", type="list", optional=True,
                    doc="Dry runs, when a window matched: its client area."),
    ),
), wait_window_out)
