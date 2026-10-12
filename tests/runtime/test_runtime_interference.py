"""User interference through the whole interpreter: the real input session on the fake
backend, whose "user" moves the pointer in the middle of a movement (docs/contract.md,
"Run states and debugging semantics", Interference)."""

from __future__ import annotations

import random

from runtime_input_helpers import InputRun, run_input

from j5_macro.backends.base import Capabilities
from j5_macro.backends.fake import FakeBackend
from j5_macro.input.session import create_session
from j5_macro.runtime.seeding import derive_seed

SCRIPT = """
    input:
      mouse: {{strategy: linear, speed: 10, interference: {action}}}
    steps:
      - id: go
        move: {{x: 1000, y: 0}}
      - id: after
        log: done
"""
USER_AT = (300, 700)


def user_interferes(after_moves: int = 3, **kwargs: object) -> FakeBackend:
    backend = FakeBackend(**kwargs)  # type: ignore[arg-type]
    backend.user_moves_pointer(*USER_AT, after_moves=after_moves)
    return backend


def start(action: str = "pause", backend: FakeBackend | None = None,
          script: str | None = None, **options: object) -> InputRun:
    text = script if script is not None else SCRIPT.format(action=action)
    return InputRun(text, factory=create_session,  # type: ignore[arg-type]
                    backend=backend if backend is not None else user_interferes(),
                    scale=1.0, **options)


def test_pause_then_resume_runs_the_step_again_as_a_new_attempt():
    run = start(seed=7)
    run.recorder.wait_paused()
    assert run.recorder.summary()[1:6] == [
        "step_started:go", "input_planned", "user_interference", "input_executed",
        "state_changed:paused/interference"]
    assert run.controller.state()["step_id"] == "go"
    interference = run.recorder.of("user_interference")[0]
    assert interference["action"] == "pause"
    assert interference["actual"] == list(USER_AT)
    run.controller.resume()
    outcome = run.join()
    assert outcome.status == "completed"
    assert run.recorder.summary()[6:8] == ["state_changed:running/user", "step_started:go"]
    assert len(run.recorder.of("step_started", step_id="go")) == 2
    assert len(run.recorder.of("step_finished", step_id="go")) == 1
    assert run.recorder.of("step_failed") == []
    first, second = run.recorder.of("input_planned")
    # The retry starts where the user left the pointer, with the sub-seed of attempt 2.
    assert second["from"] == list(USER_AT)
    assert first["seed"] != second["seed"]
    for attempt, planned in ((1, first), (2, second)):
        expected = random.Random(derive_seed(7, "go", None, attempt)).getrandbits(63)
        assert planned["seed"] == expected
    assert run.recorder.of("log", step_id="after")


def test_pause_then_step_over_skips_the_step():
    run = start()
    run.recorder.wait_paused()
    run.controller.step_over()
    run.recorder.wait_paused(2)
    assert run.recorder.summary()[5:] == [
        "state_changed:paused/interference", "state_changed:running/step", "step_skipped:go",
        "state_changed:paused/step"]
    assert run.recorder.of("step_skipped")[0]["reason"] == "user"
    assert run.controller.state()["step_id"] == "after"
    run.controller.resume()
    assert run.join().status == "completed"
    assert len(run.recorder.of("step_started", step_id="go")) == 1
    assert run.recorder.of("step_finished", step_id="go") == []
    assert run.recorder.of("step_failed") == []


def test_stop_while_paused_by_interference():
    run = start()
    run.recorder.wait_paused()
    run.controller.stop("user")
    outcome = run.join()
    assert (outcome.status, outcome.reason) == ("stopped", "user")
    assert run.recorder.of("step_started", step_id="after") == []


def test_stop_action_ends_the_run_stopped_by_interference():
    run = start("stop")
    outcome = run.join()
    assert (outcome.status, outcome.reason) == ("stopped", "interference")
    assert run.recorder.of("user_interference")[0]["action"] == "stop"
    logs = run.recorder.of("log", step_id="go")
    assert logs[0]["level"] == "warning"
    assert "user interference" in logs[0]["message"]
    assert run.recorder.summary()[-1] == "state_changed:stopping/interference"
    assert run.recorder.of("step_failed") == []
    assert run.recorder.of("step_finished") == []
    assert run.recorder.of("step_started", step_id="after") == []


def test_interference_ignores_on_error():
    run = start(script="""
        input:
          mouse: {strategy: linear, speed: 10, interference: stop}
        steps:
          - id: go
            move: {x: 1000, y: 0}
            on_error: continue
    """)
    outcome = run.join()
    assert (outcome.status, outcome.reason) == ("stopped", "interference")


def test_interference_mid_drag_releases_the_button_before_pausing():
    backend = user_interferes(after_moves=4, pointer=(100, 100))
    run = start(backend=backend, script="""
        input:
          mouse: {strategy: linear, speed: 10}
        steps:
          - id: pull
            drag: {from: {x: 100, y: 100}, to: {x: 1000, y: 100}}
    """)
    run.recorder.wait_paused()
    assert backend.held_buttons == []
    assert run.recorder.of("keys_released") == [{"keys": ["button:left"],
                                                 "reason": "interference"}]
    types = run.recorder.types()
    assert types.index("user_interference") < types.index("keys_released") \
        < types.index("state_changed", 1)
    run.controller.step_over()  # the last step: skipping it ends the run
    assert run.join().status == "completed"
    assert run.recorder.of("step_skipped", step_id="pull")


def test_the_step_input_override_applies():
    run = start(script="""
        input:
          mouse: {strategy: linear, speed: 10}
        steps:
          - id: go
            move: {x: 1000, y: 0}
            input: {mouse: {interference: off}}
    """)
    assert run.join().status == "completed"
    assert run.recorder.of("user_interference") == []


NO_POINTER = Capabilities(pointer_position=False, move_abs=True, move_rel=True,
                          char_input=True, screen_capture=False, windows=True, hotkey=True)


def test_interference_detection_needs_pointer_position():
    outcome, run = run_input("""
        steps:
          - id: go
            click: {x: 10, y: 20}
    """, factory=create_session, backend=FakeBackend(capabilities=NO_POINTER))
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.code == "capability_missing"
    assert "pointer position" in outcome.error.message
    assert "interference" in outcome.error.message
    assert outcome.error.details is not None
    assert outcome.error.details["capability"] == "pointer_position"
    assert run.recorder.of("step_failed", step_id="go")


def test_without_pointer_position_interference_off_moves():
    backend = FakeBackend(capabilities=NO_POINTER)
    outcome, run = run_input("""
        input:
          mouse: {strategy: direct, interference: off}
        steps:
          - id: go
            click: {x: 10, y: 20}
          - id: keys
            key: ctrl+s
    """, factory=create_session, backend=backend)
    assert outcome.status == "completed", run.recorder.summary()
    assert backend.pointer == (10, 20)


def test_keyboard_steps_do_not_need_pointer_position():
    outcome, _ = run_input("""
        input:
          keyboard: {strategy: direct}
        steps:
          - id: keys
            key: ctrl+s
          - id: wheel
            scroll: {dy: 2}
    """, factory=create_session, backend=FakeBackend(capabilities=NO_POINTER))
    assert outcome.status == "completed"
