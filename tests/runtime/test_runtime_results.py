"""Step results kept with ``as``, secret results, and ``at`` targets and regions that
act on a kept image result (docs/contract.md, "Results and ``as``", "Stored positions
and ``at``")."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from runtime_helpers import REGISTRY, Recorder, ScaledClock, load, no_vision, run
from runtime_input_helpers import backend, run_input
from runtime_vision_helpers import FakeToolkit, VisionRun, hit, miss, with_images

from j5_macro.backends.base import Button, Rect
from j5_macro.runtime import RunOptions
from j5_macro.runtime.interpreter import create_run

BOX = Rect(300, 400, 40, 20)  # centre (320, 410)
NOTES = Rect(100, 200, 800, 600)
MOVED = Rect(150, 250, 800, 600)
LABEL = {"found": True, "score": 0.97, "bbox": [300, 400, 40, 20], "center": [320, 410],
         "window": "Notes", "window_bbox": [200, 200, 40, 20]}
"""A wait_image result in the Notes window, kept while the window was at ``NOTES``."""


def finished(recorder: Recorder, step_id: str) -> dict[str, Any]:
    (payload,) = recorder.of("step_finished", step_id=step_id)
    result: dict[str, Any] = payload["result"]
    return result


# --- as ---------------------------------------------------------------------------------


def test_as_keeps_the_result_after_step_finished() -> None:
    outcome, rec = run("""
        steps:
          - id: nap
            sleep: 0s
            as: rest
          - id: show
            log: "slept ${rest.seconds}s"
    """)
    assert outcome.status == "completed", outcome
    assert rec.summary()[1:5] == ["step_started:nap", "step_finished:nap",
                                  "variable_changed:nap", "step_started:show"]
    assert rec.of("variable_changed") == [
        {"name": "rest", "old": None, "new": {"seconds": 0.0}, "step_id": "nap"}]
    assert rec.of("log")[0]["message"] == "slept 0.0s"


def test_as_in_the_long_form_replaces_a_variable() -> None:
    outcome, rec = run("""
        vars: {out: 1}
        steps:
          - id: say
            log: hi
            as: {name: out}
    """)
    assert outcome.status == "completed", outcome
    assert rec.of("variable_changed") == [
        {"name": "out", "old": 1, "new": {"level": "info", "message": "hi"},
         "step_id": "say"}]


def test_a_failed_step_keeps_nothing() -> None:
    outcome, rec = run("""
        steps:
          - id: broken
            on_error: continue
            fail: image_not_found
            as: res
          - id: after
            log: "${res}"
    """)
    assert outcome.error is not None and outcome.error.code == "expression_error"
    assert outcome.error.step_id == "after"
    assert rec.of("variable_changed") == []


def test_a_retried_step_keeps_the_result_of_the_attempt_that_succeeded() -> None:
    outcome, rec = run("""
        steps:
          - id: try
            on_error: retry
            flaky: 3
            as: res
    """)
    assert outcome.status == "completed", outcome
    assert rec.of("variable_changed", name="res") == [
        {"name": "res", "old": None, "new": {"tries": 3}, "step_id": "try"}]


def test_block_steps_keep_their_result_after_their_children() -> None:
    outcome, rec = run("""
        steps:
          - id: rounds
            loop:
              times: 2
              do:
                - id: inner
                  log: "${rounds}"
                  on_error: continue
            as: rounds
          - id: choice
            if:
              when: "rounds.iterations == 2"
              then:
                - id: yes
                  log: two
            as: branch
    """)
    assert outcome.status == "completed", outcome
    # Inside its own loop the variable does not exist yet.
    assert len(rec.of("step_failed", step_id="inner")) == 2
    assert rec.of("variable_changed", name="rounds")[0]["new"] == {"iterations": 2}
    assert rec.summary()[-3:] == ["step_finished:yes", "step_finished:choice",
                                  "variable_changed:choice"]
    assert rec.of("variable_changed", name="branch")[0]["new"] == {"branch": "then"}


def test_dry_runs_keep_the_dry_result() -> None:
    outcome, rec = run("""
        steps:
          - id: press
            key: enter
            as: pressed
          - id: show
            log: "${pressed.dry_run} ${pressed.description}"
    """, mode="dry")
    assert outcome.status == "completed", outcome
    assert rec.of("log", step_id="show")[0]["message"] == "true would press enter"


# --- secret results ---------------------------------------------------------------------

SECRET_SCRIPT = """
    vars:
      fetched: {exit_code: 0, stdout: "tok-123\\n", stderr: "", lines: ["a b", "   "]}
    steps:
      - id: get_token
        set: {name: copy, expr: fetched}
        as: {name: token, secret: true}
"""


def test_a_secret_result_is_registered_before_its_step_finished() -> None:
    recorder = Recorder()
    calls: list[tuple[list[str], list[str]]] = []

    def add_secrets(values: list[str]) -> None:
        calls.append((recorder.types(), values))

    controller = create_run(load(SECRET_SCRIPT), REGISTRY, recorder,
                            RunOptions(add_secrets=add_secrets), clock=ScaledClock(),
                            vision_factory=no_vision)
    assert controller.run().status == "completed"
    [(before, values)] = calls
    assert before[-1] == "variable_changed"  # the set step's own variable, then...
    assert recorder.types()[len(before):] == ["step_finished", "variable_changed"]
    # Every string of the result, stripped too; no keys, numbers or blank strings.
    assert values == ["tok-123\n", "tok-123", "copy", "a b"]


def test_results_that_are_not_secret_register_nothing() -> None:
    calls: list[list[str]] = []
    outcome, _ = run(SECRET_SCRIPT.replace("{name: token, secret: true}", "token"),
                     add_secrets=calls.append)
    assert outcome.status == "completed"
    assert calls == []


# --- at targets -------------------------------------------------------------------------


def one_step(step: str, **options: Any) -> tuple[Any, Any, dict[str, Any] | None]:
    """Run a one-step script with ``label`` kept; the outcome, the run and the result."""
    options.setdefault("variables", {"label": LABEL})
    outcome, input_run = run_input(f"steps:\n  - id: it\n    {step}\n", **options)
    found = input_run.recorder.of("step_finished", step_id="it")
    return outcome, input_run, found[0].get("result") if found else None


def test_at_follows_the_window_that_moved() -> None:
    outcome, input_run, result = one_step("click: {at: '${label}', button: right}",
                                          backend=backend(**{"My Notes": MOVED}))
    assert outcome.status == "completed", outcome
    session = input_run.factory.session
    assert session.named("find_window")[0][1] == "Notes"
    assert session.named("click")[0][1] == (370, 460, Button.RIGHT, 1)
    assert session.targets == [Rect(350, 450, 40, 20)]  # human landing in the area
    assert result == {"x": 370, "y": 460, "button": "right", "clicks": 1,
                      "bbox": [350, 450, 40, 20], "center": [370, 460], "window": "Notes",
                      "window_bbox": [200, 200, 40, 20]}


def test_at_with_an_offset_aims_at_the_exact_point() -> None:
    outcome, input_run, _ = one_step("click: {at: '${label}', offset: [0, 40]}",
                                     backend=backend(Notes=NOTES))
    assert outcome.status == "completed", outcome
    assert input_run.factory.session.named("click")[0][1] == (320, 450, Button.LEFT, 1)
    assert input_run.factory.session.targets == [None]


def test_at_without_a_window_uses_the_desktop_bbox() -> None:
    outcome, input_run, result = one_step(
        "move: {at: '${spot}', offset: [5, 0]}",
        variables={"spot": {"bbox": [10, 20, 30, 40], "center": [25, 40]}})
    assert outcome.status == "completed", outcome
    session = input_run.factory.session
    assert session.named("move")[0][1] == (30, 40)
    assert session.named("find_window") == []
    assert result == {"x": 30, "y": 40, "bbox": [10, 20, 30, 40], "center": [25, 40]}


def test_a_click_result_can_be_kept_and_used_again() -> None:
    outcome, run_ = run_input("""
        steps:
          - id: first
            click: {at: "${label}"}
            as: clicked
          - id: again
            click: {at: "${clicked}", clicks: 2}
    """, variables={"label": LABEL}, backend=backend(Notes=MOVED))
    assert outcome.status == "completed", outcome
    clicks = [call[1] for call in run_.factory.session.named("click")]
    assert clicks == [(370, 460, Button.LEFT, 1), (370, 460, Button.LEFT, 2)]


def test_drag_from_a_kept_result() -> None:
    outcome, input_run, result = one_step(
        "drag: {from: {at: '${label}', offset: [0, 5]}, to: {x: 1, y: 2, offset: [1, 1]}}",
        backend=backend(Notes=NOTES))
    assert outcome.status == "completed", outcome
    assert input_run.factory.session.named("drag")[0][1] == ((320, 415), (2, 3), Button.LEFT)
    assert result is not None
    assert (result["from"], result["to"]) == ([320, 415], [2, 3])
    assert result["from_bbox"] == [300, 400, 40, 20]
    assert result["from_window"] == "Notes"
    assert result["from_window_bbox"] == [200, 200, 40, 20]


def test_at_whose_window_is_gone_is_window_not_found() -> None:
    outcome, input_run, _ = one_step("click: {at: '${label}'}", backend=backend())
    assert outcome.error is not None and outcome.error.code == "window_not_found"
    assert outcome.error.details == {"title": "Notes", "attempt": 1}
    assert input_run.factory.session.named("click") == []


@pytest.mark.parametrize(("value", "message"), [
    ({**LABEL, "found": False}, "was not found"),
    ({"bbox": None, "dry_run": True}, "holds no position"),
    ("not a result", "must be an image result"),
    ({"bbox": [1, 2, 3]}, "positive size"),
    ({"bbox": [1, 2, 3, 4], "window": "Notes"}, "no 'window_bbox'"),
])
def test_at_needs_a_found_image_result(value: Any, message: str) -> None:
    outcome, input_run, _ = one_step("click: {at: '${spot}'}", variables={"spot": value},
                                     backend=backend(Notes=NOTES))
    assert outcome.error is not None and outcome.error.code == "validation_failed"
    assert outcome.error.details is not None and outcome.error.details["param"] == "at"
    assert message in outcome.error.message
    assert all(not session.named("click") for session in input_run.factory.sessions)


def test_dry_runs_describe_at_without_looking_the_window_up() -> None:
    outcome, run_ = run_input("""
        steps:
          - id: here
            click: {at: "${label}", offset: [0, 40]}
            as: clicked
          - id: nowhere
            move: {at: "${blank}"}
    """, mode="dry", variables={"label": LABEL, "blank": {"bbox": None, "dry_run": True}})
    assert outcome.status == "completed", outcome
    here = finished(run_.recorder, "here")
    assert here["description"] == ("would click left at the stored result at (320, 410) in "
                                   "window 'Notes' offset [0, 40]")
    assert here["bbox"] == [300, 400, 40, 20] and here["window_bbox"] == [200, 200, 40, 20]
    assert finished(run_.recorder, "nowhere")["description"] == (
        "would move to a stored result without a position")
    assert run_.factory.sessions == []


# --- image results and regions ----------------------------------------------------------


def vision_run(tmp_path: Path, steps: str, matches: list[Any], **options: Any) -> VisionRun:
    script = with_images(f"steps:\n{steps}", tmp_path, "img/x.png")
    return VisionRun(script, FakeToolkit(matches), **options)


def test_window_searches_report_where_the_match_is_in_the_window(tmp_path: Path) -> None:
    run_ = vision_run(tmp_path, """
  - id: find
    wait_image: {image: img/x.png, window: {title: Notes}}
    as: label
  - id: press
    click: {at: "${label}"}
""", [hit(0.97, BOX)], backend=backend(**{"Notes - x": NOTES}))
    assert run_.join().status == "completed"
    assert finished(run_.recorder, "find") == LABEL
    assert run_.factory.session.named("click")[0][1] == (320, 410, Button.LEFT, 1)


def test_image_targets_report_their_window_too(tmp_path: Path) -> None:
    run_ = vision_run(tmp_path, "  - id: c\n    move: {image: img/x.png, window: "
                      "{title: Notes}, offset: [1, 1]}\n", [hit(0.95, BOX)],
                      backend=backend(Notes=NOTES))
    assert run_.join().status == "completed"
    assert finished(run_.recorder, "c") == {
        "x": 321, "y": 411, "image": "img/x.png", "score": 0.95, "bbox": [300, 400, 40, 20],
        "center": [320, 410], "window": "Notes", "window_bbox": [200, 200, 40, 20]}


@pytest.mark.parametrize(("region", "grab"), [
    ("{at: '${label}'}", Rect(350, 450, 40, 20)),
    ("{at: '${label}', offset: [0, 40]}", Rect(350, 490, 40, 20)),
    ("{at: '${label}', offset: [100, 0], size: [100, 50]}", Rect(420, 435, 100, 50)),
])
def test_regions_next_to_a_kept_result_follow_its_window(tmp_path: Path, region: str,
                                                         grab: Rect) -> None:
    run_ = vision_run(tmp_path, f"  - id: near\n    wait_image: {{image: img/x.png, "
                      f"region: {region}}}\n", [hit(0.99, BOX)],
                      backend=backend(Notes=MOVED), variables={"label": LABEL})
    assert run_.join().status == "completed"
    assert run_.toolkit.capturer.grabs == [grab]
    assert run_.searches()[0]["region"] == [grab.x, grab.y, grab.width, grab.height]


def test_a_region_next_to_a_kept_result_ignores_the_step_window(tmp_path: Path) -> None:
    run_ = vision_run(tmp_path, "  - id: near\n    wait_image: {image: img/x.png, "
                      "window: {title: Other}, region: {at: '${spot}'}}\n", [hit(0.99, BOX)],
                      backend=backend(Notes=NOTES, Other=Rect(5, 5, 100, 100)),
                      variables={"spot": {"bbox": [10, 20, 30, 40]}})
    assert run_.join().status == "completed"
    assert run_.toolkit.capturer.grabs == [Rect(10, 20, 30, 40)]
    # The result is still relative to the step's window.
    assert finished(run_.recorder, "near")["window_bbox"] == [295, 395, 40, 20]


def test_a_region_next_to_a_result_whose_window_is_gone(tmp_path: Path) -> None:
    run_ = vision_run(tmp_path, "  - id: near\n    wait_image: {image: img/x.png, "
                      "region: {at: '${label}'}}\n", [hit(0.99, BOX)],
                      backend=backend(), variables={"label": LABEL})
    outcome = run_.join()
    assert outcome.error is not None and outcome.error.code == "window_not_found"
    assert run_.toolkit.capturer.grabs == []


def test_the_image_visible_condition_takes_a_region_next_to_a_result(tmp_path: Path) -> None:
    run_ = vision_run(tmp_path, """
  - id: look
    if:
      when: {image_visible: {image: img/x.png, region: {at: "${label}", offset: [0, 40]}}}
      then:
        - id: seen
          log: seen
""", [miss(0.2)], backend=backend(Notes=NOTES), variables={"label": LABEL})
    assert run_.join().status == "completed"
    assert run_.toolkit.capturer.grabs == [Rect(300, 440, 40, 20)]
    assert run_.recorder.of("branch_taken")[0]["branch"] == "else"
