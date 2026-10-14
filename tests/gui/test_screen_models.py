"""The screen tools' and the history's view models: no Qt, no display."""

from __future__ import annotations

import os
from datetime import UTC, timedelta, timezone
from typing import Any

import pytest

from j5_macro.gui.models.history import (
    RunDetail,
    format_started,
    history_row,
    parse_events,
    search_failures,
)
from j5_macro.gui.models.runs import RunSummary, parse_run_list
from j5_macro.gui.models.screen import (
    BUSY,
    CANCELLED,
    FAILED,
    MISSING,
    FindOutcome,
    capture_file,
    capture_name,
    describe_pick,
    embedded_name,
    file_value,
    find_args,
    image_target,
    image_targets,
    image_values,
    pick_values,
    point_target,
    point_targets,
    rect_within,
    resolve_file,
    target_edit,
    tool_problem,
)
from j5_macro.gui.models.steps import StepItem


def step(type_: str, step_id: str = "s1", **params: Any) -> StepItem:
    return StepItem(id=step_id, type=type_, path=(0,), params=params)


# --- targets ----------------------------------------------------------------------------


def test_image_targets_of_the_step_types() -> None:
    click = step("click", image="img/save.png", window={"title": "Notes"},
                 region=[0, 0, 400, 300], threshold=0.8, scale=1.5)
    [target] = image_targets(click)
    assert (target.param, target.image, target.window, target.region, target.threshold,
            target.scale, target.label) == (None, "img/save.png", "Notes", (0, 0, 400, 300),
                                            0.8, 1.5, "image")
    assert not target.has_point
    # A click on a point still offers an image target (a capture replaces the point).
    [by_point] = image_targets(step("click", x=1, y=2))
    assert by_point.image is None and by_point.has_point
    assert image_targets(step("wait_image_in", image="#icon"))[0].image == "#icon"
    assert image_targets(step("log", message="hi")) == []

    drag = step("drag", **{"from": {"image": "a.png"}, "to": {"x": 3, "y": 4}})
    assert [t.label for t in image_targets(drag)] == ["from.image"]
    when = step("if", when={"image_visible": "#ok"})
    [condition] = image_targets(when)
    assert (condition.label, condition.image, condition.condition_shorthand) == \
        ("when.image_visible", "#ok", True)
    loop = step("loop", **{"while": {"image_visible": {"image": "b.png", "threshold": 0.7}}})
    [inner] = image_targets(loop)
    assert (inner.param, inner.image, inner.threshold) == ("while", "b.png", 0.7)
    assert image_targets(step("if", when="n > 1")) == []


def test_point_targets() -> None:
    [click] = point_targets(step("click", x=10, y=20, window={"title": "Notes"}))
    assert (click.param, click.x, click.y, click.window, click.label) == \
        (None, 10, 20, "Notes", "point")
    [by_image] = point_targets(step("move", image="a.png"))
    assert by_image.has_image and by_image.x is None
    drag = point_targets(step("drag", **{"from": {"image": "a.png"}, "to": {"x": 3, "y": 4}}))
    assert [(t.param, t.x, t.has_image) for t in drag] == [("from", None, True),
                                                         ("to", 3, False)]
    assert point_targets(step("wait_image_in", image="a.png")) == []


def test_find_args_search_as_the_step() -> None:
    args = find_args(image_target({"image": "a.png", "window": {"title": "W"},
                                   "region": [1, 2, 3, 4], "threshold": 0.8}))
    assert args.image == "a.png"
    assert args.kwargs() == {"window": "W", "region": (1, 2, 3, 4), "threshold": 0.8,
                             "scale": None}
    with pytest.raises(ValueError, match="no image"):
        find_args(image_target({}))
    with pytest.raises(ValueError, match="computed at run time"):
        find_args(image_target({"image": "${icon}"}))
    with pytest.raises(ValueError, match="window title"):
        find_args(image_target({"image": "a.png", "window": {"title": "${target}"}}))
    with pytest.raises(ValueError, match="region"):
        find_args(image_target({"image": "a.png", "region": "${area}"}))


def test_find_outcome() -> None:
    outcome = FindOutcome.from_result(
        {"found": False, "score": 0.61234, "bbox": [110, 220, 30, 20],
         "region": [100, 200, 300, 200], "window": "Notes", "window_bbox": [10, 20, 30, 20],
         "scale": 1.0, "display_scale": 1.0, "capture_scale": 1.0, "elapsed_ms": 12}, 0.9)
    assert outcome.summary == "Not found: best score 0.612 (threshold 0.9)"
    assert outcome.details()[0] == ("Best location at [110, 220, 30, 20] on the desktop, "
                                    "[10, 20, 30, 20] in “Notes”")
    assert rect_within(outcome.bbox or (), outcome.region or ()) == (10, 20, 30, 20)
    found = FindOutcome.from_result({"found": True, "score": 1.0})
    assert found.summary == "Found: score 1.000 (threshold 0.9)"
    assert found.details()[0] == "The template is larger than the searched area"


# --- writing targets --------------------------------------------------------------------


def test_target_edits() -> None:
    click = step("click", "c1", x=1, y=2)
    [target] = image_targets(click)
    assert target_edit(click, None, image_values(target, "#save")) == {
        "op": "update", "step_id": "c1",
        "params": {"image": "#save", "x": None, "y": None, "at": None}}
    drag = step("drag", "d1", **{"from": {"image": "a.png", "threshold": 0.8},
                                 "to": {"x": 3, "y": 4}})
    assert target_edit(drag, "from", {"image": "#b"}) == {
        "op": "update", "step_id": "d1",
        "params": {"from": {"image": "#b", "threshold": 0.8}}}
    assert target_edit(drag, "to", {"x": 5, "y": 6, "window": None}) == {
        "op": "update", "step_id": "d1", "params": {"to": {"x": 5, "y": 6}}}
    # A condition keeps its short form when only the image changes.
    when = step("if", "i1", when={"image_visible": "a.png"})
    assert target_edit(when, "when", {"image": "#a"}) == {
        "op": "update", "step_id": "i1", "params": {"when": {"image_visible": "#a"}}}
    assert target_edit(when, "when", {"image": "#a", "threshold": 0.7}) == {
        "op": "update", "step_id": "i1",
        "params": {"when": {"image_visible": {"image": "#a", "threshold": 0.7}}}}


PICK = {"x": 620, "y": 340, "display_scale": 1.0, "window": "Notes",
        "window_area": [500, 300, 400, 300], "window_point": [120, 40]}


def test_pick_values_are_window_relative() -> None:
    assert pick_values(PICK) == {"x": 120, "y": 40, "window": {"title": "Notes"},
                                 "image": None, "at": None}
    target = point_target({"x": 1, "y": 2})
    assert pick_values(PICK, target) == {"x": 120, "y": 40, "window": {"title": "Notes"},
                                         "at": None}
    assert pick_values(PICK, target, use_window=False) == {"x": 620, "y": 340,
                                                           "window": None, "at": None}
    assert pick_values({"x": 5, "y": 6}, target) == {"x": 5, "y": 6, "window": None,
                                                     "at": None}
    assert describe_pick(PICK) == "(120, 40) in “Notes” ((620, 340) on the desktop)"
    assert describe_pick(PICK, use_window=False) == "(620, 340) on the desktop"


def test_capture_names() -> None:
    assert capture_name("click_1") == "click_1"
    assert capture_name("click_1", ["click_1", "click_1_2"]) == "click_1_3"
    assert capture_name("1st step!") == "image_1st_step"
    assert capture_name(None) == "image"
    assert capture_name("ok", ["ok"]) == "ok_2"
    assert capture_file("save") == "save.png"
    assert capture_file("save", ["Save.PNG", "save_2.png"]) == "save_3.png"
    assert capture_file("a/b") == "a_b.png"
    assert capture_file(None) == "template.png"


def test_image_values_and_paths(tmp_path: Any) -> None:
    script = str(tmp_path / "scripts" / "s.yaml")
    assert file_value(str(tmp_path / "scripts" / "img" / "a.png"), script) == "img/a.png"
    outside = str(tmp_path / "other" / "a.png")
    assert file_value(outside, script) == os.path.abspath(outside)
    assert file_value("x.png", None) == "x.png"
    assert resolve_file("img/a.png", script) == os.path.normpath(
        str(tmp_path / "scripts" / "img" / "a.png"))
    assert resolve_file(outside, script) == outside
    assert embedded_name("#icon") == "icon"
    assert embedded_name("icon.png") is None
    assert embedded_name("#${name}") is None


def test_tool_problems() -> None:
    assert tool_problem("invalid_state", "cancelled", {"reason": "cancelled"}).kind \
        == CANCELLED
    assert tool_problem("invalid_state", "cancelled", {"reason": "cancelled"}).quiet
    busy = tool_problem("busy", "a run is active", {"run_id": "r-1"})
    assert busy.kind == BUSY and "running" in busy.message
    assert "overlay" in tool_problem("busy", "x", {"reason": "overlay"}).message
    overlay = tool_problem("capability_missing", "no tkinter", {"capability": "overlay"})
    assert overlay.kind == MISSING and "python3-tk" in overlay.hint
    vision = tool_problem("capability_missing", "vision is not installed")
    assert "j5-macro[vision]" in vision.hint and "j5-macro[vision]" in vision.text
    windows = tool_problem("capability_missing", "the backend cannot list windows")
    assert "x11" in windows.hint
    other = tool_problem("window_not_found", "no window")
    assert (other.kind, other.message) == (FAILED, "no window (window_not_found)")


# --- history ----------------------------------------------------------------------------


def test_format_started_and_rows() -> None:
    plus_two = timezone(timedelta(hours=2))
    assert format_started("2026-10-12T10:00:00.000Z", plus_two) == "2026-10-12 12:00:00"
    assert format_started("2026-10-12T10:00:00.000Z", UTC) == "2026-10-12 10:00:00"
    assert format_started("garbage") == "garbage"
    [run] = parse_run_list({"runs": [{"run_id": "r-1", "status": "failed",
                                      "started": "2026-10-12T10:00:00.000Z",
                                      "duration_ms": 1500, "mode": "normal",
                                      "warnings": 2}]})
    assert run.warnings == 2
    row = history_row(run, UTC)
    assert row.columns == ["2026-10-12 10:00:00", "normal", "failed", "1.5 s", "2", "r-1"]
    assert row.level == "error"
    unfinished = history_row(RunSummary(run_id="r-2", started="x"))
    assert (unfinished.status, unfinished.warnings, unfinished.level) == \
        ("(unfinished)", "", "warning")


def wire(type_: str, seq: int, **payload: Any) -> dict[str, Any]:
    return {"v": 1, "kind": "event", "type": type_, "run_id": "r-1", "seq": seq,
            "ts": "2026-10-12T10:00:00.000Z", "t_ms": seq, "payload": payload}


SEARCH = {"template": "#stripes", "threshold": 0.9, "found": False, "best_score": 0.41,
          "bbox": [150, 120, 60, 40], "region": [100, 100, 400, 300], "scale": 1.0,
          "display_scale": 1.0, "capture_scale": 1.0, "elapsed_ms": 5}
FAILED_RUN = {
    "run_id": "r-1", "dir": "/runs/r-1",
    "run": {"run_id": "r-1", "status": "failed", "mode": "normal", "duration_ms": 2100,
            "script_path": "/s/a.yaml", "warnings": 1,
            "error": {"code": "image_not_found", "message": "not found", "step_id": "wait"}},
    "captures": ["captures/0001-wait-2.png"],
    "events": [
        wire("run_started", 1, script_name="A", mode="normal", seed=1, backend="x11"),
        wire("step_started", 2, step_id="outer", type="loop"),
        wire("step_started", 3, step_id="wait", type="wait_image_in"),
        wire("image_search", 4, **SEARCH, attempt=1),
        wire("image_search", 5, **{**SEARCH, "best_score": 0.43},
             attempt=2, screenshot="captures/0001-wait-2.png"),
        wire("step_failed", 6, step_id="wait",
             error={"code": "image_not_found", "message": "not found",
                    "details": {"best_score": 0.43, "threshold": 0.9, "attempts": 2,
                                "template": "#stripes"}},
             screenshot="captures/0001-wait-2.png"),
        wire("step_failed", 7, step_id="outer", error={"code": "image_not_found",
                                                       "message": "not found"}),
        wire("run_finished", 8, status="failed", duration_ms=2100, warnings=1),
        {"seq": "bad"},
    ],
}


def test_run_detail_pairs_the_failure_capture_with_its_template() -> None:
    detail = RunDetail.from_get_run(FAILED_RUN)
    assert [line.seq for line in detail.lines] == list(range(1, 9))
    assert detail.lines[-1].text == "run failed in 2.1 s, 1 warning"
    assert detail.status == "failed" and detail.warnings == 1
    assert detail.headline() == ("failed in 2.1 s (normal mode), 1 warning at wait: "
                                 "not found")
    assert detail.capture_path("captures/0001-wait-2.png") == os.path.join(
        "/runs/r-1", "captures", "0001-wait-2.png")
    [failure] = detail.failures
    assert (failure.step_id, failure.template, failure.capture, failure.best_score,
            failure.threshold, failure.attempts, failure.embedded) == \
        ("wait", "#stripes", "captures/0001-wait-2.png", 0.43, 0.9, 2, "stripes")
    assert failure.bbox_in_capture == (50, 20, 60, 40)
    assert failure.score_text == "best score 0.430 (threshold 0.9)"
    assert failure.template_file("/s/a.yaml") is None
    assert detail.capture_searches["captures/0001-wait-2.png"]["attempt"] == 2


def test_failures_without_a_matching_search() -> None:
    events = parse_events([
        wire("step_started", 1, step_id="click"),
        wire("step_failed", 2, step_id="click", screenshot="captures/0001-click-1.png",
             error={"code": "image_not_found", "message": "gone",
                    "details": {"template": "img/a.png", "best_score": 0.5}}),
        wire("step_started", 3, step_id="other"),
        wire("step_failed", 4, step_id="other", error={"code": "x", "message": "y"}),
    ])
    [failure] = search_failures(events)
    assert (failure.template, failure.best_score, failure.bbox_in_capture) == \
        ("img/a.png", 0.5, None)
    assert failure.template_file("/s/a.yaml") == os.path.abspath("/s/img/a.png")
