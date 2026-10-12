"""``on_error`` modes."""

from __future__ import annotations

from runtime_helpers import Background, run


def test_on_error_stop_ends_the_run_failed():
    outcome, rec = run("""
        steps:
          - id: f
            fail: image_not_found
          - id: after
            log: never
    """)
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.code == "image_not_found"
    assert outcome.error.step_id == "f"
    assert outcome.error.line == 3
    assert outcome.error.details == {"why": "test", "attempt": 1}
    assert rec.summary()[-2:] == ["step_started:f", "step_failed:f"]
    failed = rec.of("step_failed")[0]
    assert failed["error"]["code"] == "image_not_found"
    assert failed["path"] == [0]


def test_on_error_continue_logs_and_goes_on():
    outcome, rec = run("""
        steps:
          - id: f
            fail: template_missing
            on_error: continue
          - id: after
            log: next
    """)
    assert outcome.status == "completed"
    assert rec.summary()[1:5] == ["step_started:f", "step_failed:f", "log:f", "step_started:after"]
    assert rec.of("log", step_id="f")[0]["level"] == "warning"
    assert not any(t == "state_changed" for t in rec.types()[1:])


def test_on_error_retry_succeeds_within_three_attempts():
    outcome, rec = run("""
        steps:
          - id: f
            flaky: 3
            on_error: retry
    """)
    assert outcome.status == "completed"
    attempts = [p["error"]["details"]["attempt"] for p in rec.of("step_failed")]
    assert attempts == [1, 2]
    assert len(rec.of("step_started", step_id="f")) == 3
    assert rec.of("step_finished", step_id="f")[0]["result"] == {"tries": 3}


def test_on_error_retry_gives_up_after_three_attempts():
    outcome, rec = run("""
        steps:
          - id: f
            flaky: 10
            on_error: retry
          - log: never
    """)
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.details == {"attempt": 3}
    assert [p["error"]["details"]["attempt"] for p in rec.of("step_failed")] == [1, 2, 3]
    assert rec.of("log") == []


def test_failures_inside_loops_carry_iterations():
    outcome, rec = run("""
        steps:
          - id: block
            loop:
              times: 1
              do:
                - id: f
                  flaky: 2
                  on_error: retry
    """)
    assert outcome.status == "completed"
    assert [p["iterations"] for p in rec.of("step_failed")] == [[0]]


def test_on_error_pause_then_resume_retries():
    bg = Background("""
        steps:
          - id: f
            flaky: 2
            on_error: pause
          - id: after
            log: done
    """)
    bg.recorder.wait_paused()
    assert bg.recorder.summary()[1:5] == ["step_started:f", "variable_changed:f",
                                          "step_failed:f", "state_changed:paused/error"]
    state = bg.controller.state()
    assert state["state"] == "paused"
    assert state["step_id"] == "f"
    bg.controller.resume()
    outcome = bg.join()
    assert outcome.status == "completed"
    assert bg.recorder.summary()[5:7] == ["state_changed:running/user", "step_started:f"]
    assert bg.recorder.of("step_finished", step_id="f")[0]["result"] == {"tries": 2}


def test_on_error_pause_then_step_over_skips():
    bg = Background("""
        steps:
          - id: f
            fail: image_not_found
            on_error: pause
          - id: after
            log: done
    """)
    bg.recorder.wait_paused()
    bg.controller.step_over()
    bg.recorder.wait_paused(2)
    assert bg.recorder.summary()[3:] == [
        "state_changed:paused/error", "state_changed:running/step", "step_skipped:f",
        "state_changed:paused/step"]
    assert bg.recorder.of("step_skipped")[0]["reason"] == "user"
    assert bg.controller.state()["step_id"] == "after"
    bg.controller.resume()
    assert bg.join().status == "completed"
    assert len(bg.recorder.of("step_started", step_id="f")) == 1
