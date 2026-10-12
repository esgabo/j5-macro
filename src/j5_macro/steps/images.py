"""Image targets shared by the input steps and the ``image_visible`` condition
(docs/contract.md, "Script format and step registry": images and coordinates).

An image target is a mapping with ``image`` and optional ``threshold``, ``scale``,
``region`` and ``window``; searches go through ``ctx.vision``
(:class:`~j5_macro.vision.api.StepVision`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from j5_macro.backends.base import Rect
from j5_macro.protocol.errors import ErrorCode
from j5_macro.steps.spec import StepContext, StepError
from j5_macro.vision.api import DEFAULT_SCALE, DEFAULT_THRESHOLD, Match

DRY_RUN_SKIPPABLE = (ErrorCode.CAPABILITY_MISSING.value, ErrorCode.WINDOW_NOT_FOUND.value)
"""Search failures a dry run reports as a warning instead of failing the step."""


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


def rect_list(rect: Rect | None) -> list[int] | None:
    return None if rect is None else [rect.x, rect.y, rect.width, rect.height]


def match_report(match: Match) -> dict[str, Any]:
    """``found``, ``score`` and ``bbox`` of a match, for step results."""
    return {"found": match.found, "score": round(float(match.score), 4),
            "bbox": rect_list(match.bbox)}


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
        if exc.info.code not in DRY_RUN_SKIPPABLE:
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
        if not ctx.dry_run or exc.info.code not in DRY_RUN_SKIPPABLE:
            raise
        ctx.log("warning", f"image_visible {image}: {exc.info.message}; treated as false "
                "in dry-run")
        return False
    return match.found
