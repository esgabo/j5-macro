"""Choosing among several matches (pick, nth, after, before) and resting on a target
before clicking (hover, dwell), through the interpreter with a fake toolkit."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
from runtime_helpers import REGISTRY
from runtime_input_helpers import backend
from runtime_vision_helpers import FakeToolkit, VisionRun, hit, miss, with_images

from j5_macro.backends.base import Button, Rect
from j5_macro.script import load_script

WIN = Rect(100, 50, 1000, 800)
HEADER = Rect(300, 400, 200, 30)  # between the two buttons
TOP = Rect(700, 200, 40, 20)  # centre (720, 210)
BOTTOM = Rect(700, 600, 40, 20)  # centre (720, 610)
ITEM = Rect(400, 300, 60, 20)  # centre (430, 310)
IMAGES = ("img/button.png", "img/header.png", "img/hovered.png")


def run_steps(tmp_path: Path, steps: str, toolkit: FakeToolkit, **options: Any) -> VisionRun:
    script = with_images(f"steps:\n{textwrap.indent(textwrap.dedent(steps), '  ')}",
                         tmp_path, *IMAGES)
    options.setdefault("backend", backend(Bank=WIN))
    return VisionRun(script, toolkit, **options)


def finished(run: VisionRun, index: int = 0) -> dict[str, Any]:
    return run.recorder.of("step_finished")[index]["result"]


def actions(session: Any) -> list[str]:
    """The pointer actions a session got, in order."""
    return [call[0] for call in session.calls if call[0] in ("move", "click")]


def two_buttons(**extra: Any) -> FakeToolkit:
    """Both buttons match perfectly; the header is between them."""
    return FakeToolkit(every=[[hit(1.0, TOP), hit(1.0, BOTTOM)]],
                       by_template={"header.png": hit(0.98, HEADER)}, **extra)


# --- choosing among matches -------------------------------------------------------------


def test_click_the_first_match_after_an_image_anchor(tmp_path: Path) -> None:
    toolkit = two_buttons()
    run = run_steps(tmp_path, """\
        - id: download
          click: {image: img/button.png, after: img/header.png}
        """, toolkit)
    assert run.join().status == "completed", run.recorder.summary()
    assert run.factory.session.named("click")[0][1] == (720, 610, Button.LEFT, 1)
    searches = run.searches()
    assert [s["template"] for s in searches] == ["img/header.png", "img/button.png"]
    assert searches[1]["found"] is True and searches[1]["bbox"] == [700, 600, 40, 20]
    result = finished(run)
    assert result["bbox"] == [700, 600, 40, 20] and result["score"] == 1.0
    assert result["matches"] == [
        {"score": 1.0, "bbox": [700, 200, 40, 20], "center": [720, 210]},
        {"score": 1.0, "bbox": [700, 600, 40, 20], "center": [720, 610]}]
    assert len(toolkit.listed) == 1


def test_before_takes_the_nearest_match_before_the_anchor(tmp_path: Path) -> None:
    run = run_steps(tmp_path, "- move: {image: img/button.png, before: img/header.png}\n",
                    two_buttons())
    assert run.join().status == "completed"
    assert run.factory.session.named("move")[0][1] == (720, 210)


def test_a_kept_result_as_anchor_follows_its_window(tmp_path: Path) -> None:
    toolkit = FakeToolkit([hit(0.98, HEADER)], every=[[hit(1.0, TOP), hit(1.0, BOTTOM)]])
    run = run_steps(tmp_path, """\
        - wait_image_in: {image: img/header.png, window: {title: Bank}}
          as: hdr
        - click: {image: img/button.png, window: {title: Bank}, after: "${hdr}"}
        """, toolkit)
    assert run.join().status == "completed", run.recorder.summary()
    assert run.factory.session.named("click")[0][1] == (720, 610, Button.LEFT, 1)
    assert finished(run, 1)["matches"][0]["window_bbox"] == [600, 150, 40, 20]
    assert len(run.searches()) == 2  # the kept anchor is not searched again


@pytest.mark.parametrize(("selection", "point"), [
    ("pick: last", (720, 610)),
    ("pick: top", (720, 210)),
    ("nth: 1", (720, 210)),
    ("nth: -1", (720, 610)),
])
def test_pick_and_nth(tmp_path: Path, selection: str, point: tuple[int, int]) -> None:
    run = run_steps(tmp_path, f"- click: {{image: img/button.png, {selection}}}\n",
                    two_buttons())
    assert run.join().status == "completed"
    assert run.factory.session.named("click")[0][1][:2] == point


def test_no_match_after_the_anchor_is_image_not_found_saying_why(tmp_path: Path) -> None:
    toolkit = FakeToolkit(every=[[hit(1.0, TOP)]],
                          by_template={"header.png": hit(0.98, HEADER)})
    run = run_steps(tmp_path, "- id: c\n  click: {image: img/button.png, "
                    "after: img/header.png}\n", toolkit, run_dir=tmp_path)
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "image_not_found"
    assert outcome.error.message.endswith(
        "(best score 1.000, threshold 0.9; 1 match, none after img/header.png)")
    assert run.searches()[1]["found"] is False
    assert run.searches()[1]["best_score"] == 1.0
    assert not run.factory.session.named("click")


def test_an_anchor_that_is_not_found_is_a_miss(tmp_path: Path) -> None:
    toolkit = FakeToolkit(every=[[hit(1.0, TOP), hit(1.0, BOTTOM)]],
                          by_template={"header.png": miss(0.4)})
    run = run_steps(tmp_path, "- wait_image_in: {image: img/button.png, timeout: 0.5s, "
                    "after: img/header.png}\n", toolkit)
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "image_not_found"
    assert "after img/header.png not found (best score 0.400)" in outcome.error.message
    assert len(toolkit.listed) == 3  # at 0, 0.25 and 0.5 s


def test_a_wait_polls_until_the_match_it_asks_for_appears(tmp_path: Path) -> None:
    toolkit = FakeToolkit(every=[[hit(1.0, TOP)], [hit(1.0, TOP), hit(0.95, BOTTOM)]])
    run = run_steps(tmp_path, """\
        - wait_image_in: {image: img/button.png, nth: 2}
          as: second
        """, toolkit)
    assert run.join().status == "completed"
    assert finished(run)["center"] == [720, 610] and finished(run)["score"] == 0.95
    assert len(toolkit.listed) == 2


def test_wait_image_out_waits_until_no_match_is_where_it_asks(tmp_path: Path) -> None:
    toolkit = FakeToolkit(every=[[hit(1.0, TOP), hit(1.0, BOTTOM)], [hit(1.0, TOP)]],
                          by_template={"header.png": hit(0.98, HEADER)})
    run = run_steps(tmp_path, "- wait_image_out: {image: img/button.png, "
                    "after: img/header.png}\n", toolkit)
    assert run.join().status == "completed"
    assert finished(run) == {"gone": True, "waited_ms": 250}


def test_image_visible_with_an_anchor(tmp_path: Path) -> None:
    toolkit = FakeToolkit(every=[[hit(1.0, TOP)]],
                          by_template={"header.png": hit(0.98, HEADER)})
    run = run_steps(tmp_path, """\
        - if:
            when: {image_visible: {image: img/button.png, before: img/header.png}}
            then: [{log: above}]
        - if:
            when: {image_visible: {image: img/button.png, after: img/header.png}}
            then: [{log: below}]
        """, toolkit)
    assert run.join().status == "completed"
    assert [e["message"] for e in run.recorder.of("log")] == ["above"]


def test_pick_best_keeps_the_best_match_and_lists_them_all(tmp_path: Path) -> None:
    toolkit = FakeToolkit(every=[[hit(0.97, BOTTOM), hit(0.93, TOP)]])
    run = run_steps(tmp_path, "- wait_image_in: {image: img/button.png, pick: best}\n",
                    toolkit)
    assert run.join().status == "completed"
    result = finished(run)
    assert result["center"] == [720, 610]
    assert [m["score"] for m in result["matches"]] == [0.93, 0.97]  # reading order


def test_an_interpolated_nth_is_checked_when_the_step_runs(tmp_path: Path) -> None:
    run = run_steps(tmp_path, """\
        - set: {name: n, value: two}
        - drag: {from: {image: img/button.png, nth: "${n}"}, to: {x: 1, y: 2}}
        """, two_buttons())
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "validation_failed"
    assert outcome.error.message.startswith("'nth' must be a whole number")


def test_choosing_needs_a_toolkit_that_finds_every_match(tmp_path: Path) -> None:
    toolkit = two_buttons()
    toolkit.listing = False
    run = run_steps(tmp_path, "- click: {image: img/button.png, pick: last}\n", toolkit)
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "capability_missing"


def test_dry_run_describes_the_selection(tmp_path: Path) -> None:
    run = run_steps(tmp_path, "- click: {image: img/button.png, nth: 2, "
                    "after: img/header.png}\n", two_buttons(), mode="dry")
    assert run.join().status == "completed"
    result = finished(run)
    assert result["description"] == (
        "would click left at image img/button.png (nth 2, after img/header.png) "
        "(not found, best score 1.000)")
    assert result["found"] is False and len(result["matches"]) == 2


# --- hover and dwell --------------------------------------------------------------------


def test_hover_waits_for_the_hovered_look_then_clicks(tmp_path: Path) -> None:
    toolkit = FakeToolkit([hit(1.0, ITEM), miss(0.5), miss(0.6), hit(0.96, ITEM)])
    run = run_steps(tmp_path, """\
        - id: excel
          timeout: 10s
          click: {image: img/button.png, hover: {image: img/hovered.png, timeout: 1s}}
        """, toolkit)
    assert run.join().status == "completed", run.recorder.summary()
    session = run.factory.session
    assert actions(session) == ["move", "click"]
    assert session.named("move")[0][1] == (430, 310)
    assert session.targets == [ITEM, ITEM]  # the click does not move off the item
    # The hovered look is searched around the item: twice its size, centred on it.
    assert toolkit.capturer.grabs[1:] == [Rect(370, 290, 120, 40)] * 3
    result = finished(run)
    assert result["hover"] == {"found": True, "score": 0.96, "bbox": [400, 300, 60, 20],
                               "center": [430, 310]}
    assert run.clock.t == pytest.approx(0.5)


def test_hover_fails_when_the_hovered_look_never_appears(tmp_path: Path) -> None:
    toolkit = FakeToolkit([hit(1.0, ITEM), miss(0.5)])
    run = run_steps(tmp_path, """\
        - timeout: 10s
          click: {image: img/button.png, hover: {image: img/hovered.png}}
        """, toolkit)
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "image_not_found"
    assert "img/hovered.png" in outcome.error.message
    assert run.clock.t == pytest.approx(2.0)  # hover's own 2 s, not the step's 10 s
    assert actions(run.factory.session) == ["move"]


def test_dwell_rests_on_the_target_before_pressing(tmp_path: Path) -> None:
    run = run_steps(tmp_path, "- click: {x: 10, y: 20, dwell: 300ms}\n", FakeToolkit())
    assert run.join().status == "completed"
    assert actions(run.factory.session) == ["move", "click"]
    assert run.clock.t == pytest.approx(0.3)
    assert finished(run)["dwell"] == pytest.approx(0.3)


def test_hover_with_an_xy_target_searches_the_step_window(tmp_path: Path) -> None:
    toolkit = FakeToolkit([hit(0.96, ITEM)])
    run = run_steps(tmp_path, "- click: {x: 10, y: 20, window: {title: Bank}, "
                    "hover: {image: img/hovered.png}}\n", toolkit)
    assert run.join().status == "completed"
    assert toolkit.capturer.grabs == [WIN]
    assert run.factory.session.named("move")[0][1] == (110, 70)


def test_dry_run_describes_the_rest_without_searching_the_hovered_look(
        tmp_path: Path) -> None:
    toolkit = FakeToolkit([hit(1.0, ITEM)])
    run = run_steps(tmp_path, "- click: {image: img/button.png, dwell: 300ms, "
                    "hover: {image: img/hovered.png}}\n", toolkit, mode="dry")
    assert run.join().status == "completed"
    assert finished(run)["description"] == (
        "would click left at image img/button.png once img/hovered.png appears there "
        "(up to 2s) after resting 300ms (found at (430, 310), score 1.000)")
    assert len(run.searches()) == 1


# --- validation -------------------------------------------------------------------------


def load_errors(steps: str) -> list[str]:
    result = load_script(f"steps:\n{textwrap.indent(textwrap.dedent(steps), '  ')}",
                         lookup=REGISTRY.spec, check=REGISTRY.check_params)
    return [error.message for error in result.errors]


@pytest.mark.parametrize(("step", "expected"), [
    ("click: {image: a.png, pick: last, nth: 2}", "use only one of 'pick' and 'nth'"),
    ("click: {image: a.png, nth: 0}", "counts from 1"),
    ("click: {image: a.png, pick: middle}", "expected one of"),
    ("click: {x: 1, y: 2, pick: last}", "only applies to 'image' targets"),
    ("click: {at: {bbox: [1, 2, 3, 4]}, after: b.png}", "only applies to 'image' targets"),
    ("click: {image: a.png, after: [1, 2]}", "must be an image or an image result"),
    ("wait_image_in: {image: a.png, before: {window: x}}", "must be an image or an image"),
    ("drag: {from: {image: a.png, nth: 0}, to: {x: 1, y: 2}}", "from.nth"),
    ("click: {image: a.png, hover: {timeout: 2s}}", "needs 'image'"),
    ("click: {image: a.png, hover: {image: b.png, wait: 2s}}", "unknown key(s) wait"),
    ("click: {image: a.png, after: \"#missing\"}", "no embedded image named 'missing'"),
    ("click: {image: a.png, dwell: soon}", "dwell"),
])
def test_invalid_parameters_fail_to_load(step: str, expected: str) -> None:
    errors = load_errors(f"- {step}\n")
    assert any(expected in message for message in errors), errors


def test_valid_selections_load() -> None:
    assert load_errors("""\
        - click: {image: a.png, after: "${hdr}", nth: -1}
        - move: {image: a.png, before: b.png, after: c.png, pick: right}
        - wait_image_out: {image: a.png, pick: first}
        - drag: {from: {image: a.png, after: b.png}, to: {image: a.png, pick: last}}
        - if:
            when: {image_visible: {image: a.png, nth: 2}}
            then: [{log: x}]
        - click: {image: a.png, hover: {image: b.png, timeout: 1s, threshold: 0.8}, dwell: 1s}
        """) == []
