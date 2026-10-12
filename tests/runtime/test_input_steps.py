"""Input steps through the interpreter, with a fake session over FakeBackend."""

from __future__ import annotations

from typing import Any

import pytest
from runtime_input_helpers import backend, run_input

from j5_macro.backends.base import Button, Capabilities, Rect
from j5_macro.backends.fake import FakeBackend

NOTES = Rect(100, 200, 800, 600)


def one_step(step: str, **options: Any) -> tuple[Any, Any, dict[str, Any] | None]:
    """Run a one-step script; returns the outcome, the run and the step's result."""
    outcome, input_run = run_input(f"steps:\n  - id: it\n    {step}\n", **options)
    finished = input_run.recorder.of("step_finished", step_id="it")
    return outcome, input_run, finished[0].get("result") if finished else None


def test_click_at_absolute_coordinates():
    outcome, input_run, result = one_step("click: {x: 10, y: 20, button: right, clicks: 2}")
    assert outcome.status == "completed"
    assert result == {"x": 10, "y": 20, "button": "right", "clicks": 2}
    assert input_run.factory.session.named("click")[0][1] == (10, 20, Button.RIGHT, 2)


def test_click_relative_to_a_window_with_an_offset():
    outcome, input_run, result = one_step(
        "click: {x: 10, y: 20, window: {title: Notes}, offset: [5, -3]}",
        backend=backend(**{"Untitled - Notes": NOTES}))
    assert outcome.status == "completed", outcome
    assert result == {"x": 115, "y": 217, "button": "left", "clicks": 1,
                      "window": [100, 200, 800, 600]}
    session = input_run.factory.session
    assert session.named("find_window")[0][1] == "Notes"
    assert session.named("click")[0][1] == (115, 217, Button.LEFT, 1)


def test_interpolated_coordinates():
    outcome, input_run, result = one_step("move: {x: '${px}', y: '${px * 2}'}",
                                          variables={"px": 7})
    assert outcome.status == "completed", outcome
    assert result == {"x": 7, "y": 14}
    assert input_run.factory.session.named("move")[0][1] == (7, 14)


def test_move_relative_to_a_window():
    _, _, result = one_step("move: {x: 0, y: 0, window: {title: Notes}}",
                            backend=backend(Notes=NOTES))
    assert result == {"x": 100, "y": 200, "window": [100, 200, 800, 600]}


def test_missing_window_is_window_not_found():
    outcome, input_run, _ = one_step("click: {x: 1, y: 1, window: {title: Ghost}}")
    assert outcome.error is not None
    assert outcome.error.code == "window_not_found"
    assert outcome.error.details is not None
    assert outcome.error.details["title"] == "Ghost"
    assert not input_run.factory.session.named("click")


def test_window_needs_the_windows_capability():
    blind = FakeBackend(capabilities=Capabilities(
        pointer_position=True, move_abs=True, move_rel=True, char_input=True,
        screen_capture=False, windows=False, hotkey=False))
    outcome, input_run, _ = one_step("click: {x: 1, y: 1, window: {title: Notes}}",
                                     backend=blind)
    assert outcome.error is not None
    assert outcome.error.code == "capability_missing"
    assert "cannot find windows" in outcome.error.message
    assert not input_run.factory.session.named("find_window")


def test_window_without_a_title_is_validation_failed():
    outcome, _, _ = one_step("click: {x: 1, y: 1, window: {name: Notes}}")
    assert outcome.error is not None
    assert outcome.error.code == "validation_failed"


@pytest.mark.parametrize("step", [
    "click: {image: img/ok.png}",
    "move: {image: img/ok.png}",
    "drag: {from: {image: img/a.png}, to: {x: 1, y: 1}}",
    "wait_image: img/ok.png",
])
def test_image_targets_need_vision_outside_dry_runs(step):
    outcome, input_run, _ = one_step(step)
    assert outcome.error is not None
    assert outcome.error.code == "capability_missing"
    assert "M3" in outcome.error.message
    assert input_run.factory.sessions == []


def test_drag_between_points_in_windows():
    outcome, input_run, result = one_step(
        "drag: {from: {x: 1, y: 2, window: {title: Notes}}, to: {x: 300, y: 400}, "
        "button: middle}",
        backend=backend(Notes=NOTES))
    assert outcome.status == "completed", outcome
    assert result == {"from": [101, 202], "to": [300, 400], "button": "middle",
                      "from_window": [100, 200, 800, 600]}
    assert input_run.factory.session.named("drag")[0][1] == ((101, 202), (300, 400),
                                                             Button.MIDDLE)


def test_scroll():
    _, input_run, result = one_step("scroll: {dy: -3}")
    assert result == {"dx": 0, "dy": -3}
    assert input_run.factory.session.named("scroll")[0][1] == (0, -3)


def test_type_reports_the_length_not_the_text():
    _, input_run, result = one_step("type: {text: 'hunter2', keyboard: {strategy: direct}}")
    assert result == {"chars": 7}
    call = input_run.factory.session.named("type_text")[0]
    assert call[1] == "hunter2"
    assert call[2].strategy == "direct"


def test_key_with_hold():
    outcome, input_run, result = one_step("key: {press: ctrl+shift+s, hold: 500ms}")
    assert outcome.status == "completed", outcome
    assert result == {"press": "ctrl+shift+s", "hold": 0.5}
    assert input_run.factory.session.named("press")[0][1] == ("ctrl+shift+s", 0.5)


def test_key_without_hold():
    _, input_run, result = one_step("key: enter")
    assert result == {"press": "enter"}
    assert input_run.factory.session.named("press")[0][1] == ("enter", None)


def test_unknown_keys_are_validation_failed():
    outcome, _, _ = one_step("key: bad+key")
    assert outcome.error is not None
    assert outcome.error.code == "validation_failed"
    assert "unknown key 'bad'" in outcome.error.message


@pytest.mark.parametrize("step", [
    "click: {x: 1, y: 2, window: {title: Notes}, offset: [5, 5]}",
    "type: hello",
    "key: {press: ctrl+s, hold: 1s}",
])
def test_dry_runs_describe_and_send_nothing(step):
    outcome, input_run, result = one_step(step, mode="dry")
    assert outcome.status == "completed"
    assert result is not None and result["dry_run"] is True
    assert result["description"].startswith("would ")
    assert input_run.factory.sessions == []
