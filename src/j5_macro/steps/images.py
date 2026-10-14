"""Image targets shared by the input steps and the ``image_visible`` condition
(docs/contract.md, "Script format and step registry": images and coordinates).

An image target is a mapping with ``image`` and optional ``threshold``, ``scale``,
``region`` and ``window``, plus ``pick``, ``nth``, ``after`` and ``before`` to choose
among several matches (:mod:`j5_macro.vision.selection`); searches go through
``ctx.vision``
(:class:`~j5_macro.vision.api.StepVision`). Image results report where the match is
(:func:`match_report`), so they can be kept with ``as`` and used as ``at`` targets.

Dry runs never wait: every search a dry run makes instead of a wait, an image target or
a window lookup goes through :func:`dry_search`, the one place that decides what a
search that cannot be made, or an unmet check, means in a dry run. In a strict dry run
checks of the screen must be satisfied until a step that sends input or starts a program
has run (``ctx.relaxed_by``); after it they are warnings, since they may depend on it
(docs/contract.md, "Strict dry runs").
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from j5_macro.protocol.errors import ErrorCode
from j5_macro.steps.positions import NoPosition, area_report, relative
from j5_macro.steps.spec import StepContext, StepError
from j5_macro.vision.api import DEFAULT_SCALE, DEFAULT_THRESHOLD, Match
from j5_macro.vision.selection import SELECTION_KEYS, selection_of

DRY_RUN_SKIPPABLE = (ErrorCode.CAPABILITY_MISSING.value, ErrorCode.WINDOW_NOT_FOUND.value)
"""Search failures a dry run reports as a warning instead of failing the step (also
:class:`~j5_macro.steps.positions.NoPosition`: a region placed at a kept result that a dry
run could not find). A strict dry run fails the step with them, except for those that
depend on the screen once it is relaxed (:data:`SCREEN_DEPENDENT`)."""
SCREEN_DEPENDENT = (ErrorCode.WINDOW_NOT_FOUND.value,)
"""Search failures that depend on what is on screen (a missing window, which an earlier
click or program may open; also :class:`~j5_macro.steps.positions.NoPosition`), unlike
``capability_missing``, which no earlier step can change."""
STRICT = " in a strict dry run"
"""Appended to the message of an unmet check that fails a strict dry run."""

_T = TypeVar("_T")


def dry_run_skips(ctx: StepContext, exc: StepError) -> bool:
    """Whether a dry run reports ``exc`` as a warning and goes on: never outside dry runs;
    in strict ones only for a failure that depends on the screen once relaxed."""
    if not ctx.dry_run:
        return False
    if ctx.strict:
        return ctx.relaxed_by is not None and (exc.info.code in SCREEN_DEPENDENT
                                               or isinstance(exc, NoPosition))
    return exc.info.code in DRY_RUN_SKIPPABLE or isinstance(exc, NoPosition)


def strict_checks(ctx: StepContext) -> bool:
    """Whether checks of the screen must be satisfied: in a strict dry run, until a step
    that sends input or starts a program has run."""
    return ctx.strict and ctx.relaxed_by is None


def relaxed_warning(error: StepError, relaxed_by: str) -> str:
    """The warning of an unmet check in a strict dry run that is no longer strict."""
    return (f"dry-run: {error.info.message}; not a failure in a strict dry run because "
            f"it may depend on {relaxed_by}")


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
    select = selection_of(target)
    if select is not None:
        options["select"] = select
    return options


def not_found_note(match: Match) -> str:
    """``; 2 matches, none after #header``: why a search that chose among several
    matches found none, to append to a message; empty otherwise."""
    return f"; {match.note}" if match.note else ""


def window_title(target: Mapping[str, Any]) -> str | None:
    """The ``title`` of a target's ``window``, None without one."""
    window = target.get("window")
    title = window.get("title") if isinstance(window, Mapping) else None
    return title if isinstance(title, str) else None


def match_report(match: Match, target: Mapping[str, Any]) -> dict[str, Any]:
    """``found``, ``score``, ``bbox`` and ``center`` of a match of ``target`` and, for a
    window search, ``window`` (the title) and ``window_bbox``, for step results; with
    ``matches`` (each with ``score`` and the same area fields) when the search chose
    among several."""
    title = window_title(target)
    report = {"found": match.found, "score": round(float(match.score), 4),
              **area_report(match.bbox, title,
                            relative(match.bbox, match.frame) if title is not None
                            else None)}
    if match.matches is not None:
        report["matches"] = [
            {"score": round(float(m.score), 4),
             **area_report(m.bbox, title,
                           relative(m.bbox, match.frame) if title is not None else None)}
            for m in match.matches]
    return report


def locate_target(ctx: StepContext, target: Mapping[str, Any]) -> Match:
    """Find an image target for an input step: one attempt, or a wait when the step's
    common ``timeout`` is set (which also waits for a missing ``window``). Outside dry
    runs a miss raises ``image_not_found``."""
    match = ctx.vision.wait(target["image"], timeout=0.0, **search_options(target))
    if not ctx.dry_run and (not match.found or match.bbox is None):
        raise StepError(ErrorCode.IMAGE_NOT_FOUND,
                        f"{target['image']} not found{not_found_note(match)}",
                        {"best_score": round(float(match.score), 4),
                         "threshold": search_options(target)["threshold"],
                         "template": target["image"]})
    return match


def dry_search(ctx: StepContext, subject: str, search: Callable[[], _T],
               unmet: Callable[[_T, str], StepError | None] | None = None) -> _T | None:
    """The single search a dry run makes instead of waiting (``search`` is one image
    search or one window lookup): its outcome, or None (after a warning naming
    ``subject``) when it cannot be made (no vision, no backend, no window), so the dry
    run goes on. In a strict dry run a search that cannot be made fails the step, and so
    does an outcome for which ``unmet(outcome, when)`` returns the error the real run
    would end with (an image or a window that is not there, or still there; ``when`` is
    :data:`STRICT` to append to its message). Once the strict dry run is relaxed
    (``ctx.relaxed_by``), a missing window and an unmet outcome are warnings that say
    why instead."""
    try:
        outcome = search()
    except StepError as exc:
        if not dry_run_skips(ctx, exc):
            raise
        message = f"dry-run: {subject} not searched: {exc.info.message}"
        if ctx.strict and ctx.relaxed_by is not None:
            message += (f"; not a failure in a strict dry run because it may depend on "
                        f"{ctx.relaxed_by}")
        ctx.log("warning", message)
        return None
    if ctx.strict and unmet is not None:
        if ctx.relaxed_by is None:
            error = unmet(outcome, STRICT)
            if error is not None:
                raise error
        else:
            error = unmet(outcome, "")
            if error is not None:
                ctx.log("warning", relaxed_warning(error, ctx.relaxed_by))
    return outcome


def window_missing(title: str, when: str = "") -> StepError:
    """``window_not_found`` for a window looked up once."""
    return StepError(ErrorCode.WINDOW_NOT_FOUND,
                     f"no visible window whose title contains {title!r}{when}",
                     {"title": title})


def strict_not_found(image: str, match: Match, threshold: float,
                     when: str = STRICT) -> StepError:
    """``image_not_found`` for the single search of a strict dry run."""
    return StepError(
        ErrorCode.IMAGE_NOT_FOUND,
        f"{image} not found{when} (one attempt; best score "
        f"{match.score:.3f}, threshold {threshold:g}{not_found_note(match)})",
        {"best_score": round(float(match.score), 4), "threshold": threshold,
         "template": image, "attempts": 1},
        screenshot=match.capture)


def strict_still_visible(image: str, match: Match, when: str = STRICT) -> StepError:
    """``still_visible`` for the single search of a strict dry run."""
    return StepError(
        ErrorCode.STILL_VISIBLE,
        f"{image} still visible{when} (one attempt; score "
        f"{match.score:.3f})",
        {"image": image, "score": round(float(match.score), 4)},
        screenshot=match.capture)


def dry_locate(ctx: StepContext, target: Mapping[str, Any], *,
               present: bool = True) -> Match | None:
    """The single search of an image target or an image wait in a dry run (see
    :func:`dry_search`). A strict dry run fails when the image is not found, or with
    ``present=False`` (``wait_image_out``) when it is found; once relaxed, it warns."""
    image, options = target["image"], search_options(target)

    def unmet(match: Match, when: str) -> StepError | None:
        if match.found == present:
            return None
        return (strict_not_found(image, match, options["threshold"], when) if present
                else strict_still_visible(image, match, when))

    failing = (lambda m: m.found != present) if strict_checks(ctx) else None
    return dry_search(ctx, f"image {image}",
                      lambda: ctx.vision.find(image, failing=failing, **options), unmet)


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
    scale: ..., region: ..., window: ..., pick/nth/after/before: ...}}``: one search
    (true when the match it chooses exists). A missing window is false
    (nothing is searched), and so is not finding the image: never a failure. In dry
    runs a search that cannot be made (no vision) is false with a warning; strict dry
    runs fail as a real run does."""
    if isinstance(argument, str):
        target: Mapping[str, Any] = {"image": argument}
    elif isinstance(argument, Mapping):
        unknown = set(argument) - {"image", "threshold", "scale", "region", "window",
                                   *SELECTION_KEYS}
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

    def search() -> bool:
        try:
            return ctx.vision.find(image, **search_options(target)).found
        except StepError as exc:
            if exc.info.code != ErrorCode.WINDOW_NOT_FOUND:
                raise
            return False  # an image cannot be visible in a window that is not there

    if not ctx.dry_run:
        return search()
    return dry_search(ctx, f"image_visible {image} (treated as false)", search) is True
