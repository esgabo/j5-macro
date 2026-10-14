"""Choosing one match among several (docs/contract.md, "Script format and step registry":
choosing among matches).

An image search normally takes the template's best location. With ``pick``, ``nth``,
``after`` or ``before`` it looks at every match at or above the threshold instead and
chooses one:

- **Reading order.** Matches are sorted in rows from top to bottom, and left to right
  within a row. Two areas are in the same row when their vertical centres are less than
  half the taller one's height apart (:func:`same_row`); a row is started by its
  topmost match.
- **Anchors.** ``after`` keeps the matches that come after an anchor area in reading
  order (in a row below it, or in its row and to its right); ``before`` those before
  it. Both together keep the matches between them.
- **Choice.** Among the matches kept, ``pick`` takes ``first`` or ``last`` in reading
  order, the ``top``-, ``bottom``-, ``left``- or ``right``-most one, or the ``best``
  score; ``nth`` takes the n-th in reading order (1 is the first, -1 the last). Without
  either, ``after`` alone takes the first match after the anchor, ``before`` alone the
  last one before it (the nearest), and both the first between them.

Pure functions on rectangles: no capture, no numpy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from j5_macro.backends.base import Rect
from j5_macro.vision.api import Match

Pick = Literal["best", "first", "last", "top", "bottom", "left", "right"]
PICKS: tuple[Pick, ...] = ("best", "first", "last", "top", "bottom", "left", "right")
ANCHORS = ("after", "before")
"""The parameters that place the match relative to an anchor."""
SELECTION_KEYS = ("pick", "nth", *ANCHORS)
"""The parameters of an image target that choose among several matches."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Selection:
    """How to choose among the matches of one search. ``after`` and ``before`` are as
    the step wrote them: an image (searched in the same capture) or a stored image
    result."""

    pick: Pick | None = None
    nth: int | None = None
    after: Any = None
    before: Any = None

    def anchors(self) -> str:
        """``after #header and before #footer``: the anchors, for messages; empty
        without any."""
        return " and ".join(f"{name} {anchor_label(getattr(self, name))}"
                            for name in ANCHORS if getattr(self, name) is not None)


def none_chosen(count: int, kept: int, selection: Selection) -> str:
    """Why ``selection`` chose none of ``count`` matches, ``kept`` of which were placed
    as its anchors ask: ``2 matches, none after #header``, ``2 matches, no match 3``."""
    found = f"{count} match{'' if count == 1 else 'es'}"
    anchors = selection.anchors()
    if anchors and not kept:
        return f"{found}, none {anchors}"
    if anchors:
        found += f", {kept} {anchors}"
    return f"{found}, no match {selection.nth}"


def anchor_label(anchor: Any) -> str:
    """An anchor for messages: the image as written, or where a stored result was."""
    if isinstance(anchor, str):
        return anchor
    bbox = anchor.get("bbox") if hasattr(anchor, "get") else None
    if isinstance(bbox, list | tuple) and len(bbox) == 4:
        return f"the stored result at {list(bbox)}"
    return "a stored result"


def selection_of(target: Any) -> Selection | None:
    """The :class:`Selection` of an image target's parameters, None when it chooses the
    best match the usual way."""
    if not hasattr(target, "get") or all(target.get(k) is None for k in SELECTION_KEYS):
        return None
    return Selection(pick=target.get("pick"), nth=target.get("nth"),
                     after=target.get("after"), before=target.get("before"))


def selection_problem(selection: Selection) -> tuple[str, str] | None:
    """``(param, message)`` when ``pick`` or ``nth`` has a value no search can use (one
    built at run time; literal ones are checked when the script loads), else None."""
    if selection.pick is not None and selection.nth is not None:
        return "nth", "use only one of 'pick' and 'nth'"
    if selection.pick is not None and selection.pick not in PICKS:
        return "pick", f"must be one of {', '.join(PICKS)}"
    nth = selection.nth
    if nth is not None and (isinstance(nth, bool) or not isinstance(nth, int) or nth == 0):
        return "nth", "must be a whole number counting from 1 (or from -1, the last)"
    return None


def _center_y(rect: Rect) -> float:
    return rect.y + rect.height / 2


def _center_x(rect: Rect) -> float:
    return rect.x + rect.width / 2


def same_row(a: Rect, b: Rect) -> bool:
    """Whether two areas are on the same line of the screen."""
    return abs(_center_y(a) - _center_y(b)) < max(a.height, b.height) / 2


def reading_order(matches: Sequence[Match]) -> list[Match]:
    """``matches`` (all with a ``bbox``) in rows from top to bottom, each row from left
    to right."""
    rows: list[list[Match]] = []
    for match in sorted(matches, key=lambda m: (_center_y(_box(m)), _center_x(_box(m)))):
        if rows and same_row(_box(rows[-1][0]), _box(match)):
            rows[-1].append(match)
        else:
            rows.append([match])
    return [m for row in rows for m in sorted(row, key=lambda m: _center_x(_box(m)))]


def comes_after(rect: Rect, anchor: Rect) -> bool:
    """Whether ``rect`` comes after ``anchor`` in reading order."""
    if same_row(rect, anchor):
        return _center_x(rect) > _center_x(anchor)
    return _center_y(rect) > _center_y(anchor)


def _box(match: Match) -> Rect:
    assert match.bbox is not None
    return match.bbox


def eligible(ordered: Sequence[Match], after: Rect | None,
             before: Rect | None) -> list[Match]:
    """The matches (in reading order) after ``after`` and before ``before``."""
    return [m for m in ordered
            if (after is None or comes_after(_box(m), after))
            and (before is None or comes_after(before, _box(m)))]


def choose(candidates: Sequence[Match], selection: Selection, *,
           after: Rect | None = None, before: Rect | None = None) -> Match | None:
    """The match ``selection`` chooses among ``candidates`` (in reading order), placed
    relative to the resolved anchor areas; None when there is none."""
    kept = eligible(candidates, after, before)
    if not kept:
        return None
    if selection.nth is not None:
        index = selection.nth - 1 if selection.nth > 0 else len(kept) + selection.nth
        return kept[index] if 0 <= index < len(kept) else None
    pick = selection.pick
    if pick is None:
        pick = "last" if before is not None and after is None else "first"
    match pick:
        case "first":
            return kept[0]
        case "last":
            return kept[-1]
        case "top":
            return min(kept, key=lambda m: (_box(m).y, _box(m).x))
        case "bottom":
            return max(kept, key=lambda m: (_box(m).y, -_box(m).x))
        case "left":
            return min(kept, key=lambda m: (_box(m).x, _box(m).y))
        case "right":
            return max(kept, key=lambda m: (_box(m).x, -_box(m).y))
        case _:  # best: the highest score, the first in reading order on a tie
            return max(kept, key=lambda m: m.score)
