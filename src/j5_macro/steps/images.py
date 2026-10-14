"""Image targets shared by the input steps and the ``image_visible`` condition
(docs/contract.md, "Script format and step registry": images and coordinates).

An image target is a mapping with ``image`` and optional ``threshold``, ``scale``,
``region`` and ``window``; searches go through ``ctx.vision``
(:class:`~j5_macro.vision.api.StepVision`). Image results report where the match is
(:func:`match_report`), so they can be kept with ``as`` and used as ``at`` targets.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from j5_macro.protocol.errors import ErrorCode
from j5_macro.steps.positions import NoPosition, area_report, relative
from j5_macro.steps.spec import StepContext, StepError
from j5_macro.vision.api import DEFAULT_SCALE, DEFAULT_THRESHOLD, Match

DRY_RUN_SKIPPABLE = (ErrorCode.CAPABILITY_MISSING.value, ErrorCode.WINDOW_NOT_FOUND.value)
"""Search failures a dry run reports as a warning instead of failing the step (also
:class:`~j5_macro.steps.positions.NoPosition`: a region placed at a kept result that a dry
run could not find)."""


def dry_run_skips(exc: StepError) -> bool:
    """Whether a dry run reports ``exc`` as a warning and goes on."""
    return exc.info.code in DRY_RUN_SKIPPABLE or isinstance(exc, NoPosition)


def search_options(target: Mapping[str, Any]) -> dict[str, Any]:
    """The keyword arguments of ``ctx.vision.find``/``wait`` for an image target."""
    threshold, scale = target.get("threshold"), target.get("scale")
    options: dict[str, Any] = {
        "threshold": DEFAULT_THRESHOLD if threshold is None else threshold,
        "scale": DEFAULT_SCALE if scale is None else scale,
    }
    for name in ("region", "window"):
        if target.get(name) is not None:
            options[name] = target[name]
    return options


def window_title(target: Mapping[str, Any]) -> str | None:
    """The ``title`` of a target's ``window``, None without one."""
    window = target.get("window")
    title = window.get("title") if isinstance(window, Mapping) else None
    return title if isinstance(title, str) else None


def match_report(match: Match, target: Mapping[str, Any]) -> dict[str, Any]:
    """``found``, ``score``, ``bbox`` and ``center`` of a match of ``target`` and, for a
    window search, ``window`` (the title) and ``window_bbox``, for step results."""
    title = window_title(target)
    return {"found": match.found, "score": round(float(match.score), 4),
            **area_report(match.bbox, title,
                          relative(match.bbox, match.frame) if title is not None else None)}


def locate_target(ctx: StepContext, target: Mapping[str, Any]) -> Match:
    """Find an image target for an input step: one attempt, or a wait when the step's
    common ``timeout`` is set. Outside dry runs a miss raises ``image_not_found``."""
    match = ctx.vision.wait(target["image"], timeout=0.0, **search_options(target))
    if not ctx.dry_run and (not match.found or match.bbox is None):
        raise StepError(ErrorCode.IMAGE_NOT_FOUND, f"{target['image']} not found",
                        {"best_score": round(float(match.score), 4),
                         "threshold": search_options(target)["threshold"],
                         "template": target["image"]})
    return match


def dry_locate(ctx: StepContext, target: Mapping[str, Any],
               wait: float = 0.0) -> Match | None:
    """The single search of a dry run; None (after a warning) when vision or the window
    is not available, so the dry run goes on."""
    try:
        return ctx.vision.wait(target["image"], timeout=wait, **search_options(target))
    except StepError as exc:
        if not dry_run_skips(exc):
            raise
        ctx.log("warning", f"dry-run: image {target['image']} not searched: "
                f"{exc.info.message}")
        return None


def describe_match(match: Match | None, label: str = "") -> str:
    """`` (found at (x, y), score s)`` or `` (not found, best score s)``, prefixed with
    ``label`` (``from``, ``to``) when given; empty when no search was made."""
    if match is None:
        return ""
    prefix = f"{label} " if label else ""
    if match.found and match.bbox is not None:
        x, y = match.bbox.center
        return f" ({prefix}found at ({x}, {y}), score {match.score:.3f})"
    return f" ({prefix}not found, best score {match.score:.3f})"


# --- the image_visible condition --------------------------------------------------------


def image_visible(ctx: StepContext, argument: Any) -> bool:
    """``{image_visible: img/x.png}`` or ``{image_visible: {image: ..., threshold: ...,
    scale: ..., region: ..., window: ...}}``: one search. In dry runs a search that
    cannot be made (no vision, no window) is false with a warning."""
    if isinstance(argument, str):
        target: Mapping[str, Any] = {"image": argument}
    elif isinstance(argument, Mapping):
        unknown = set(argument) - {"image", "threshold", "scale", "region", "window"}
        if unknown:
            raise StepError(ErrorCode.VALIDATION_FAILED,
                            f"image_visible: unknown keys {', '.join(sorted(unknown))}",
                            {"condition": "image_visible"})
        target = argument
    else:
        target = {}
    image = target.get("image")
    if not isinstance(image, str) or not image:
        raise StepError(ErrorCode.VALIDATION_FAILED,
                        "image_visible needs an image path, e.g. {image_visible: img/x.png}",
                        {"condition": "image_visible"})
    try:
        match = ctx.vision.find(image, **search_options(target))
    except StepError as exc:
        if not ctx.dry_run or not dry_run_skips(exc):
            raise
        ctx.log("warning", f"image_visible {image}: {exc.info.message}; treated as false "
                "in dry-run")
        return False
    return match.found
