"""Choosing one match among several: reading order, anchors, pick and nth (pure)."""

from __future__ import annotations

import pytest

from j5_macro.backends.base import Rect
from j5_macro.vision.api import Match
from j5_macro.vision.selection import (
    Selection,
    choose,
    comes_after,
    none_chosen,
    reading_order,
    selection_of,
)


def match(x: int, y: int, score: float = 1.0, w: int = 40, h: int = 20) -> Match:
    return Match(found=True, score=score, bbox=Rect(x, y, w, h))


# Two rows: the second row's matches are a few pixels off a straight line.
A, B, C = match(500, 100, 0.95), match(100, 104, 0.97), match(300, 98, 0.9)
D, E = match(120, 300, 0.92), match(80, 306, 0.99)
ALL = [A, B, C, D, E]


def test_reading_order_is_rows_top_to_bottom_then_left_to_right() -> None:
    assert reading_order(ALL) == [B, C, A, E, D]
    assert reading_order([]) == []


def test_after_and_before_follow_reading_order() -> None:
    anchor = Rect(250, 100, 40, 20)  # in the first row, between B and C
    assert comes_after(C.bbox, anchor) and comes_after(A.bbox, anchor)
    assert not comes_after(B.bbox, anchor)
    assert comes_after(E.bbox, anchor)  # a row below, even though it is to the left
    assert comes_after(anchor, B.bbox)


@pytest.mark.parametrize(("selection", "expected"), [
    (Selection(pick="first"), B),
    (Selection(pick="last"), D),
    (Selection(pick="top"), C),
    (Selection(pick="bottom"), E),
    (Selection(pick="left"), E),
    (Selection(pick="right"), A),
    (Selection(pick="best"), E),
    (Selection(nth=2), C),
    (Selection(nth=-1), D),
    (Selection(nth=-5), B),
    (Selection(nth=6), None),
    (Selection(nth=-6), None),
])
def test_pick_and_nth(selection: Selection, expected: Match | None) -> None:
    assert choose(reading_order(ALL), selection) is expected


def test_anchors_choose_the_nearest_match_by_default() -> None:
    ordered = reading_order(ALL)
    header = Rect(0, 200, 600, 30)  # a header between the two rows
    assert choose(ordered, Selection(after="#h"), after=header) is E
    assert choose(ordered, Selection(before="#h"), before=header) is A
    assert choose(ordered, Selection(after="#h", pick="last"), after=header) is D
    assert choose(ordered, Selection(after="#h", nth=2), after=header) is D
    assert choose(ordered, Selection(after="#h", nth=3), after=header) is None
    between = Selection(after="#a", before="#b")
    assert choose(ordered, between, after=B.bbox, before=E.bbox) is C
    assert choose(ordered, between, after=A.bbox, before=E.bbox) is None
    assert choose(ordered, Selection(after="#h"), after=Rect(0, 900, 10, 10)) is None


def test_selection_of_a_target() -> None:
    assert selection_of({"image": "x.png"}) is None
    assert selection_of({"image": "x.png", "pick": None}) is None
    stored = {"bbox": [1, 2, 3, 4]}
    assert selection_of({"image": "x.png", "nth": 2, "after": stored}) == Selection(
        nth=2, after=stored)


def test_messages_say_why_nothing_was_chosen() -> None:
    assert none_chosen(2, 0, Selection(after="#h")) == "2 matches, none after #h"
    assert none_chosen(1, 0, Selection(after="#a", before={"bbox": [1, 2, 3, 4]})) == (
        "1 match, none after #a and before the stored result at [1, 2, 3, 4]")
    assert none_chosen(3, 3, Selection(nth=5)) == "3 matches, no match 5"
    assert none_chosen(3, 1, Selection(after="#h", nth=2)) == (
        "3 matches, 1 after #h, no match 2")
