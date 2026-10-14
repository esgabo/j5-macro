"""Positions on screen: offsets, stored image results and the areas they describe
(docs/contract.md, "Script format and step registry": coordinates, stored positions and
``at``, regions next to a stored result).

- :func:`aim` is the one place where an ``offset`` moves a point, for every kind of
  target (``x``/``y``, ``image``, ``at``) and for regions placed ``at`` a stored result.
  The point belongs to an *area* (the match, the stored result's area; None for
  coordinates), which is what the human strategy lands in when there is no offset.
  Offset items are pixels, multiplied by the display scale ratio (display / capture
  scale) when the target has an area, or multiples of the area's size (``0.5w``,
  ``-2h``), which only targets with an area have (:func:`length_problem`).
- A *stored result* is an image result kept with ``as`` (``wait_image_in``, an image or
  ``at`` target of ``click``/``move``): ``bbox`` on the desktop and, when the search had a
  window, ``window`` (its title) and ``window_bbox`` (relative to its client area).
  :func:`read_stored` checks one, :func:`locate_stored` finds where it is now (looking
  the window up again), and :func:`area_report` writes those fields into a result.

Nothing here touches the backend: window lookups go through a :data:`FindWindow`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from j5_macro.backends.base import Rect
from j5_macro.protocol.errors import ErrorCode
from j5_macro.steps.spec import StepError

FindWindow = Callable[[str], Rect]
"""The client area of the window whose title contains the given text; raises
``StepError`` (``window_not_found``, ``capability_missing``) when it cannot."""

RatioAt = Callable[[tuple[int, int]], float]
"""The display scale ratio (display / capture scale) at a desktop point."""

REGION_KEYS = ("at", "offset", "size")
"""The keys of a region placed next to a stored result."""

SIZE_UNIT = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))([wh])$")
"""A length relative to the target's area: ``0.5w``, ``-2h``."""


def _failure(param: str, message: str) -> StepError:
    return StepError(ErrorCode.VALIDATION_FAILED, f"'{param}' {message}", {"param": param})


class NoPosition(StepError):
    """A stored result that says where nothing is: its image was not found, or it comes
    from a dry run that could not search. ``validation_failed``; dry runs go on with a
    warning instead, as when a window is missing."""

    def __init__(self, param: str, message: str) -> None:
        super().__init__(ErrorCode.VALIDATION_FAILED, f"'{param}' {message}",
                         {"param": param})


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _interpolated(value: Any) -> bool:
    return isinstance(value, str) and "${" in value


def rect_list(rect: Rect | None) -> list[int] | None:
    return None if rect is None else [rect.x, rect.y, rect.width, rect.height]


def _rect(value: Any) -> Rect | None:
    """``[x, y, w, h]`` with a positive size as a Rect, else None."""
    if not isinstance(value, list | tuple) or len(value) != 4 or not all(map(_number, value)):
        return None
    x, y, width, height = (round(v) for v in value)
    return Rect(x, y, width, height) if width > 0 and height > 0 else None


# --- offsets ----------------------------------------------------------------------------


def _unit(value: Any) -> tuple[float, str] | None:
    """``0.5w`` as ``(0.5, "w")``; None for anything else."""
    if not isinstance(value, str):
        return None
    found = SIZE_UNIT.match(value)
    if found is None:
        return None
    number = float(found.group(1))
    return (number, found.group(2)) if math.isfinite(number) else None


def length_problem(value: Any, sized: bool, *, positive: bool = False) -> str | None:
    """What is wrong with one item of an ``offset`` (or of an anchored region's
    ``size``), or None: a number of pixels, or a multiple of the target's width or
    height (``0.5w``) when the target has a size (``sized``). Strings with ``${`` are
    only checked once interpolated."""
    if _interpolated(value):
        return None
    unit = _unit(value)
    if unit is not None:
        if not sized:
            return (f"{value!r} is relative to the target's size, but an x/y target has "
                    "none; use pixels")
        number = unit[0]
    elif _number(value) and math.isfinite(value):
        number = value
    else:
        what = ("a number of pixels or a multiple of the target's width or height, such "
                "as 0.5w or -2h") if sized else "a number of pixels"
        return f"items must be {what}, got {value!r}"
    if positive and number <= 0:
        return "must be positive"
    return None


def lengths_problem(value: Any, sized: bool, *, positive: bool = False) -> str | None:
    """What is wrong with an ``offset`` (``[dx, dy]``) or a ``size`` (``[w, h]``,
    ``positive``), or None; the whole value may still be ``${...}``."""
    if value is None or _interpolated(value):
        return None
    if not isinstance(value, list | tuple) or len(value) != 2:
        return "expected 2 numbers"
    for item in value:
        problem = length_problem(item, sized, positive=positive)
        if problem is not None:
            return problem
    return None


def _length(value: Any, area: Rect | None, ratio: float, param: str,
            positive: bool = False) -> float:
    """One offset or size item in pixels: a number times ``ratio``, or a multiple of the
    area's width or height."""
    problem = length_problem(value, area is not None, positive=positive)
    if problem is None and _interpolated(value):
        problem = f"items must be numbers, got {value!r}"
    if problem is not None:
        raise _failure(param, problem)
    unit = _unit(value)
    if unit is not None:
        assert area is not None  # length_problem refuses units without an area
        number, axis = unit
        return number * (area.width if axis == "w" else area.height)
    return float(value) * ratio


def offset_delta(offset: Any, area: Rect | None, param: str = "offset",
                 ratio: float = 1.0) -> tuple[int, int]:
    """``offset`` (``[dx, dy]`` or None) in pixels. ``area`` is the area the offset is
    relative to (None for plain coordinates): ``w``/``h`` items are multiples of its
    size, and pixel items are multiplied by ``ratio`` (the display scale ratio; callers
    pass 1 for plain coordinates)."""
    if offset is None:
        return 0, 0
    if not isinstance(offset, list | tuple) or len(offset) != 2:
        raise _failure(param, "must be [dx, dy]")
    return (round(_length(offset[0], area, ratio, param)),
            round(_length(offset[1], area, ratio, param)))


def aim(point: tuple[int, int], area: Rect | None, offset: Any,
        param: str = "offset", ratio: float = 1.0) -> tuple[int, int, Rect | None]:
    """The point to act on (``point`` plus ``offset``) and the area the input layer may
    land in: ``area`` itself, unless an offset moves the point away from it."""
    dx, dy = offset_delta(offset, area, param, ratio)
    if (dx, dy) == (0, 0):
        return point[0], point[1], area
    return point[0] + dx, point[1] + dy, None


# --- stored results ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoredArea:
    """An image result read back from a variable."""

    bbox: Rect | None
    """On the desktop, when the result was made; None for a dry run that could not
    search."""
    window: str | None = None
    window_bbox: Rect | None = None
    found: bool | None = None


def stored_problem(value: Any) -> str | None:
    """What is wrong with ``value`` as a stored result, or None. Strings with ``${`` are
    only checked once interpolated; a ``bbox`` that is None, or missing from a dry run's
    result (the search could not be made), is accepted here and refused when the step
    really runs (:class:`NoPosition`)."""
    if _interpolated(value):
        return None
    if not isinstance(value, Mapping):
        return "must be an image result kept with 'as' (a mapping with 'bbox')"
    if "bbox" not in value and value.get("dry_run") is not True:
        return "needs 'bbox' ([x, y, width, height]): keep an image result with 'as'"
    for name in ("bbox", "window_bbox"):
        item = value.get(name)
        if item is not None and not _interpolated(item) and _rect(item) is None:
            return f"'{name}' must be [x, y, width, height] with a positive size"
    window = value.get("window")
    if window is not None and not _interpolated(window):
        if not isinstance(window, str) or not window:
            return "'window' must be the title of the window searched"
        if value.get("window_bbox") is None and value.get("bbox") is not None:
            return "has a 'window' but no 'window_bbox'"
    return None


def read_stored(value: Any, param: str = "at") -> StoredArea:
    """``value`` as a :class:`StoredArea`; ``validation_failed`` when it is not one."""
    problem = stored_problem(value)
    if problem is None and _interpolated(value):
        problem = "must be an image result, not text"
    if problem is not None:
        raise _failure(param, problem)
    window = value.get("window")
    found = value.get("found")
    return StoredArea(bbox=_rect(value.get("bbox")),
                      window=window if isinstance(window, str) else None,
                      window_bbox=_rect(value.get("window_bbox")),
                      found=found if isinstance(found, bool) else None)


def locate_stored(stored: StoredArea, find_window: FindWindow,
                  param: str = "at") -> tuple[Rect, Rect | None]:
    """Where a stored result is now: its area on the desktop, and the client area of its
    window (looked up again, so the area follows a window that moved; None without one).
    """
    if stored.found is False:
        raise NoPosition(param, "is a result whose image was not found")
    if stored.bbox is None:
        raise NoPosition(param, "holds no position (a dry run that could not search)")
    if stored.window is None or stored.window_bbox is None:
        return stored.bbox, None
    frame = find_window(stored.window)
    box = stored.window_bbox
    return Rect(frame.x + box.x, frame.y + box.y, box.width, box.height), frame


def relative(rect: Rect | None, frame: Rect | None) -> Rect | None:
    """``rect`` relative to the top-left corner of ``frame``."""
    if rect is None or frame is None:
        return None
    return Rect(rect.x - frame.x, rect.y - frame.y, rect.width, rect.height)


def area_report(bbox: Rect | None, window: str | None = None,
                window_bbox: Rect | None = None) -> dict[str, Any]:
    """``bbox`` and ``center`` on the desktop and, for a window search, ``window`` (the
    title) and ``window_bbox``: the fields that make a result usable as ``at``."""
    report: dict[str, Any] = {
        "bbox": rect_list(bbox),
        "center": None if bbox is None else list(bbox.center),
    }
    if window is not None:
        report["window"] = window
        report["window_bbox"] = rect_list(window_bbox)
    return report


def stored_report(stored: StoredArea) -> dict[str, Any]:
    """:func:`area_report` of a stored result as it was stored (dry runs)."""
    return area_report(stored.bbox, stored.window, stored.window_bbox)


# --- regions next to a stored result ----------------------------------------------------


def region_problem(value: Any) -> str | None:
    """What is wrong with a ``region`` (``[x, y, w, h]`` or ``{at, offset?, size?}``), or
    None. Strings with ``${`` are only checked once interpolated."""
    if value is None or _interpolated(value):
        return None
    if isinstance(value, list):
        return None if len(value) == 4 else "expected 4 numbers"
    if not isinstance(value, Mapping):
        return "expected [x, y, width, height] or {at: ..., offset: ..., size: ...}"
    unknown = sorted(str(k) for k in value if k not in REGION_KEYS)
    if unknown:
        return f"unknown key(s) {', '.join(unknown)}; expected at, offset and size"
    if "at" not in value:
        return "needs 'at', a stored image result"
    problem = stored_problem(value["at"])
    if problem is not None:
        return f"'at' {problem}"
    for name in ("offset", "size"):
        problem = lengths_problem(value.get(name), True, positive=name == "size")
        if problem is not None:
            return f"'{name}' {problem}"
    return None


def anchored_region(value: Mapping[str, Any], find_window: FindWindow,
                    param: str = "region", ratio_at: RatioAt | None = None) -> Rect:
    """A region ``{at, offset?, size?}``: ``size`` (the stored area's size by default)
    centred on the stored area's centre plus ``offset``, on the desktop. Pixel items of
    ``offset`` and ``size`` are multiplied by the display scale ratio at the stored
    area's centre (``ratio_at``; 1 without it)."""
    problem = region_problem(value)
    if problem is not None:
        raise _failure(param, problem)
    area, _ = locate_stored(read_stored(value["at"], f"{param}.at"), find_window,
                            f"{param}.at")
    ratio = 1.0 if ratio_at is None else ratio_at(area.center)
    size = value.get("size")
    if size is None:
        width, height = area.width, area.height
    else:
        if not isinstance(size, list | tuple) or len(size) != 2:
            raise _failure(f"{param}.size", "must be [width, height], both positive")
        width, height = (max(1, round(_length(item, area, ratio, f"{param}.size",
                                              positive=True)))
                         for item in size)
    x, y, _ = aim(area.center, area, value.get("offset"), f"{param}.offset", ratio)
    return Rect(x - width // 2, y - height // 2, width, height)
