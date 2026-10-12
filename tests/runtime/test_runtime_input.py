"""The interpreter's input wiring: ctx.input, profiles, the session's lifecycle, the abort
signal and the emergency stop."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from runtime_helpers import Recorder, run
from runtime_input_helpers import FakeSession, InputRun, SessionFactory, backend, run_input

import j5_macro.runtime.input as run_input_module
from j5_macro.input.api import InputProfile, KeyboardProfile, MouseProfile
from j5_macro.runtime import RunOptions, RunOutcome, create_run
from j5_macro.runtime.clock import MonotonicClock
from j5_macro.runtime.control import RunControl, RunStopped
from j5_macro.runtime.input import RunAbort
from j5_macro.script.loader import load_script
from j5_macro.steps import StepContext, StepSpec, StepType, default_registry

CLICK = """
    steps:
      - id: press_it
        click: {x: 10, y: 20}
"""


# --- ctx.input availability -------------------------------------------------------------


def test_without_a_backend_input_steps_name_the_reason():
    outcome, recorder = run(CLICK, backend_reason="no X11 session (DISPLAY is not set)")
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.code == "capability_missing"
    assert "needs an input backend: no X11 session (DISPLAY is not set)" in \
        outcome.error.message
    assert recorder.of("step_failed", step_id="press_it")


def test_dry_runs_never_create_a_session():
    outcome, input_run = run_input(CLICK, mode="dry")
    assert outcome.status == "completed"
    assert input_run.factory.sessions == []
    finished = input_run.recorder.of("step_finished", step_id="press_it")[0]
    assert finished["result"]["dry_run"] is True


def test_one_session_per_run_bound_to_each_step():
    outcome, input_run = run_input("""
        steps:
          - id: first
            click: {x: 1, y: 2}
          - id: second
            move: {x: 3, y: 4}
    """, seed=7)
    assert outcome.status == "completed"
    session = input_run.factory.session
    assert session.abort is not None and session.backend is not None
    assert [c[0] for c in session.calls] == ["click", "move", "release_all"]
    # Each step gets its own seeded rng.
    assert session.rngs[0] is not session.rngs[1]


def test_the_session_is_created_only_when_a_step_needs_input():
    outcome, input_run = run_input("steps: [{log: hi}]")
    assert outcome.status == "completed"
    assert input_run.factory.sessions == []


def test_a_session_module_that_cannot_load_is_capability_missing(monkeypatch):
    monkeypatch.setattr(run_input_module, "SESSION_MODULE", "j5_macro.input.not_there")
    outcome, _ = run(CLICK, backend=backend())
    assert outcome.error is not None
    assert outcome.error.code == "capability_missing"
    assert "j5_macro.input.not_there" in outcome.error.message


# --- profiles ---------------------------------------------------------------------------


def test_profile_layers_merge_in_order():
    outcome, input_run = run_input("""
        input:
          mouse: {speed: 2.0, jitter: 3.0}
          keyboard: {wpm: 40, variance: 0.1}
        steps:
          - id: press_it
            input: {mouse: {jitter: 0.5}}
            click: {x: 1, y: 1}
          - id: write
            input: {keyboard: {wpm: 80}}
            type: {text: hi, keyboard: {strategy: linear}}
    """, input_profile={"mouse": {"strategy": "direct", "speed": 3.0}})
    assert outcome.status == "completed", outcome
    session = input_run.factory.session
    assert session.named("click")[0][2] == MouseProfile(strategy="direct", speed=3.0,
                                                        jitter=0.5)
    assert session.named("type_text")[0][2] == KeyboardProfile(strategy="linear", wpm=80,
                                                                variance=0.1)


def test_profile_defaults_without_overrides():
    _, input_run = run_input(CLICK)
    assert input_run.factory.session.named("click")[0][2] == InputProfile().mouse


@pytest.mark.parametrize("override", [
    "input: {mouse: {speed: -1}}",
    "input: {mouse: {wobble: 1}}",
    "input: {pointer: {}}",
])
def test_a_bad_profile_is_validation_failed(override):
    outcome, _ = run_input(f"""
        steps:
          - id: press_it
            {override}
            click: {{x: 1, y: 1}}
    """)
    assert outcome.error is not None
    assert outcome.error.code == "validation_failed"
    assert "invalid input profile" in outcome.error.message


# --- release on every ending ------------------------------------------------------------


def test_held_keys_are_released_when_the_run_completes():
    outcome, input_run = run_input("steps: [{type: hello}]")
    assert outcome.status == "completed"
    assert input_run.factory.session.named("release_all") == [
        ("release_all", "run_ended", None)]
    assert input_run.recorder.of("keys_released") == [{"keys": ["shift"],
                                                       "reason": "run_ended"}]
    # Released before the interpreter returns, i.e. before run_finished.
    assert input_run.recorder.types()[-1] == "keys_released"


def test_held_keys_are_released_when_the_run_fails():
    outcome, input_run = run_input("""
        steps:
          - type: hello
          - fail: image_not_found
    """)
    assert outcome.status == "failed"
    assert input_run.recorder.of("keys_released") == [{"keys": ["shift"],
                                                       "reason": "run_ended"}]


def test_held_keys_are_released_when_the_run_is_stopped():
    input_run = InputRun("""
        steps:
          - type: hello
          - sleep: 100s
    """)
    input_run.recorder.wait_count("step_started", 2)
    input_run.controller.stop("user")
    outcome = input_run.join()
    assert outcome.status == "stopped"
    summary = input_run.recorder.summary()
    assert summary[-3:] == ["state_changed:stopping/user", "input_state", "keys_released"]


class BrokenRelease(FakeSession):
    def release_all(self, reason: str) -> list[str]:
        raise OSError("display gone")


def test_release_failures_are_logged_not_raised():
    factory = SessionFactory()
    factory.session_type = BrokenRelease
    outcome, input_run = run_input(CLICK, factory=factory)
    assert outcome.status == "completed"
    logs = input_run.recorder.of("log", level="error")
    assert "display gone" in logs[0]["message"]


# --- stop, abort signal and emergency stop ----------------------------------------------


def test_stop_interrupts_a_held_key():
    input_run = InputRun("steps: [{key: {press: shift, hold: 100s}}]", scale=1.0)
    input_run.recorder.wait_count("input_state")
    started = time.monotonic()
    input_run.controller.stop("user")
    outcome = input_run.join()
    assert outcome.status == "stopped"
    assert time.monotonic() - started < 1.0
    assert input_run.recorder.of("keys_released")[0]["keys"] == ["shift"]


def test_pause_does_not_cut_a_held_key():
    input_run = InputRun("""
        steps:
          - id: hold_it
            key: {press: ctrl, hold: 30s}
          - id: after
            log: done
    """, scale=100.0)
    input_run.recorder.wait_count("input_state")
    input_run.controller.pause()
    input_run.recorder.wait_paused()
    # Paused only after the step finished: the hold was not interrupted.
    summary = input_run.recorder.summary()
    assert "step_finished:hold_it" in summary
    assert summary.index("step_finished:hold_it") < summary.index("state_changed:paused/user")
    input_run.controller.stop("user")
    assert input_run.join().status == "stopped"


def test_abort_signal_sleep_returns_only_on_stop():
    control = RunControl(MonotonicClock(), paused=False, breakpoints=())
    abort = RunAbort(control)
    abort.check()
    control.request_pause()
    started = time.monotonic()
    abort.sleep(0.15)
    assert time.monotonic() - started >= 0.14  # a pause request does not cut it short

    threading.Timer(0.05, control.stop, args=("interference",)).start()
    started = time.monotonic()
    with pytest.raises(RunStopped) as info:
        abort.sleep(5)
    assert time.monotonic() - started < 1.0
    assert info.value.reason == "interference"
    with pytest.raises(RunStopped):
        abort.check()


def test_emergency_stop_stops_the_run_with_reason_user():
    outcome, input_run = run_input("""
        steps:
          - id: corner
            click: {x: 0, y: 0}
            on_error: continue
          - id: never
            log: not reached
    """)
    assert outcome.status == "stopped"
    assert outcome.reason == "user"
    logs = input_run.recorder.of("log", step_id="corner")
    assert logs[0]["level"] == "warning"
    assert "emergency stop" in logs[0]["message"]
    assert "top-left corner" in logs[0]["message"]
    assert not input_run.recorder.of("step_started", step_id="never")
    assert not input_run.recorder.of("step_failed")


def _spin(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    """A long executor that polls ctx.check_stop between units of work (#55)."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        ctx.check_stop()
        time.sleep(0.01)
    return {"spun": True}


def test_long_executors_stop_through_check_stop():
    registry = default_registry()
    registry.register(StepType(StepSpec(name="spin", doc="Busy loop."), _spin))
    script = load_script("steps: [{spin: }]", lookup=registry.spec,
                         check=registry.check_params).script
    assert script is not None
    recorder = Recorder()
    controller = create_run(script, registry, recorder, RunOptions())
    result: list[RunOutcome] = []
    worker = threading.Thread(target=lambda: result.append(controller.run()), daemon=True)
    worker.start()
    recorder.wait_count("step_started")
    started = time.monotonic()
    controller.stop("user")
    worker.join(2)
    assert result and result[0].status == "stopped"
    assert time.monotonic() - started < 1.0
