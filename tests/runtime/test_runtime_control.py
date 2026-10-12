"""Control commands from another thread: pause, stop, stepping, breakpoints, state."""

from __future__ import annotations

import time

import pytest
from runtime_helpers import Background

from j5_macro.protocol.payloads.commands import GetStateResult
from j5_macro.runtime import ControlError

LONG_SLEEP = """
    vars: {n: 1}
    steps:
      - id: nap
        sleep: 20s
      - id: after
        log: done
"""


def _code(exc: pytest.ExceptionInfo[ControlError]) -> str:
    return exc.value.info.code


def test_pause_during_wait_resumes_with_remaining_time():
    bg = Background(LONG_SLEEP)  # 20 s of clock time = 0.2 s real
    bg.recorder.wait_count("step_started", step_id="nap")
    time.sleep(0.05)
    bg.controller.pause()
    bg.recorder.wait_paused()
    assert bg.controller.state()["state"] == "paused"
    assert bg.controller.state()["step_id"] == "nap"
    time.sleep(0.1)  # 10 s paused: not part of the wait
    resumed_at = bg.clock.now()
    bg.controller.resume()
    assert bg.join().status == "completed"
    finished_at = bg.clock.now()
    assert bg.recorder.summary()[1:6] == [
        "step_started:nap", "state_changed:paused/user", "state_changed:running/user",
        "step_finished:nap", "step_started:after"]
    nap = bg.recorder.of("step_finished", step_id="nap")[0]
    assert nap["result"] == {"seconds": 20.0}
    assert 17_000 <= nap["duration_ms"] <= 24_000  # active time only
    assert finished_at - resumed_at < 19  # resumed with the remaining time


def test_pause_is_a_noop_when_paused():
    bg = Background(LONG_SLEEP, mode="step")
    bg.recorder.wait_paused()
    bg.controller.pause()
    bg.controller.resume()
    assert bg.join().status == "completed"
    assert len(bg.recorder.of("state_changed", state="paused")) == 1


def test_stop_during_wait_is_immediate():
    bg = Background(LONG_SLEEP.replace("20s", "1h"))
    bg.recorder.wait_count("step_started", step_id="nap")
    started = time.monotonic()
    bg.controller.stop()
    bg.controller.stop()  # idempotent
    outcome = bg.join()
    assert time.monotonic() - started < 0.5
    assert outcome.status == "stopped"
    assert outcome.reason == "user"
    assert bg.recorder.summary()[-1] == "state_changed:stopping/user"
    assert bg.recorder.of("step_finished") == []


def test_stop_while_paused():
    bg = Background(LONG_SLEEP, mode="step")
    bg.recorder.wait_paused()
    bg.controller.stop("interference")
    outcome = bg.join()
    assert (outcome.status, outcome.reason) == ("stopped", "interference")
    assert bg.recorder.summary() == ["state_changed:paused/step",
                                     "state_changed:stopping/interference"]


STEPPING = """
    steps:
      - id: a
        log: a
      - id: lp
        loop:
          times: 2
          do:
            - id: b
              log: b
            - id: c
              log: c
      - id: d
        log: d
"""


class Stepper:
    def __init__(self, script: str = STEPPING, **options):
        self.bg = Background(script, mode="step", **options)
        self.pauses = 1
        self.bg.recorder.wait_paused()

    def at(self) -> str:
        return self.bg.controller.state()["step_id"]

    def do(self, command: str) -> str:
        getattr(self.bg.controller, command)()
        self.pauses += 1
        self.bg.recorder.wait_paused(self.pauses)
        return self.at()

    def logs(self) -> list[str]:
        return [p["message"] for p in self.bg.recorder.of("log")]


def test_step_mode_starts_paused_before_the_first_step():
    s = Stepper()
    assert s.bg.recorder.summary() == ["state_changed:paused/step"]
    assert s.at() == "a"
    assert s.bg.controller.state()["path"] == [0]


def test_step_over_into_out_across_nested_blocks():
    s = Stepper()
    assert s.do("step_over") == "lp"
    assert s.logs() == ["a"]
    assert s.bg.recorder.summary()[1:] == [
        "state_changed:running/step", "step_started:a", "log:a", "step_finished:a",
        "state_changed:paused/step"]
    assert s.do("step_into") == "b"
    assert s.bg.controller.state()["path"] == [1, 0, 0]
    assert s.do("step_over") == "c"
    assert s.do("step_over") == "b"  # next iteration, same level
    assert s.logs() == ["a", "b", "c"]
    assert s.do("step_out") == "d"  # finishes the remaining iterations
    assert s.logs() == ["a", "b", "c", "b", "c"]
    assert s.bg.recorder.of("step_finished", step_id="lp")[0]["result"] == {"iterations": 2}
    s.bg.controller.step_over()  # last step: runs to the end
    assert s.bg.join().status == "completed"
    assert s.logs()[-1] == "d"


def test_step_over_a_block_runs_it_whole():
    s = Stepper()
    s.do("step_over")
    assert s.do("step_over") == "d"
    assert s.logs() == ["a", "b", "c", "b", "c"]


def test_step_into_a_simple_step_is_step_over():
    s = Stepper()
    assert s.do("step_into") == "lp"


def test_step_out_at_top_level_runs_to_the_end():
    s = Stepper()
    s.bg.controller.step_out()
    assert s.bg.join().status == "completed"
    assert s.logs() == ["a", "b", "c", "b", "c", "d"]


def test_step_into_an_if_pauses_in_the_taken_branch():
    s = Stepper("""
        steps:
          - id: choose
            if:
              when: "false"
              then:
                - id: yes
                  log: yes
              else:
                - id: no
                  log: no
          - id: end
            log: end
    """)
    assert s.do("step_into") == "no"
    assert s.bg.controller.state()["path"] == [0, 1, 0]
    assert s.do("step_out") == "end"
    assert s.logs() == ["no"]


def test_commands_need_a_paused_run():
    bg = Background(LONG_SLEEP)
    bg.recorder.wait_count("step_started", step_id="nap")
    for command in ("resume", "step_over", "step_into", "step_out"):
        with pytest.raises(ControlError) as exc:
            getattr(bg.controller, command)()
        assert _code(exc) == "invalid_state"
    with pytest.raises(ControlError) as exc:
        bg.controller.set_variable("n", 2)
    assert _code(exc) == "invalid_state"
    with pytest.raises(ControlError) as exc:
        bg.controller.evaluate("n")
    assert _code(exc) == "invalid_state"
    assert bg.controller.state()["state"] == "running"
    bg.controller.stop()
    bg.join()


def test_state_set_variable_and_evaluate_while_paused():
    s = Stepper(LONG_SLEEP)
    state = s.bg.controller.state()
    assert state == {"state": "paused", "step_id": "nap", "path": [0], "line": 4,
                     "variables": {"n": 1}}
    GetStateResult.from_json({**state, "run_id": "r-1"})
    s.bg.controller.set_variable("n", 41)
    assert s.bg.recorder.of("variable_changed")[-1] == {"name": "n", "old": 1, "new": 41}
    assert s.bg.controller.evaluate("n + 1") == 42
    with pytest.raises(ControlError) as exc:
        s.bg.controller.evaluate("n +")
    assert _code(exc) == "expression_error"
    assert exc.value.info.details is not None
    s.bg.controller.stop()
    s.bg.join()


# --- breakpoints ----------------------------------------------------------------------

LOOPED = """
    steps:
      - id: lp
        loop:
          times: 3
          do:
            - id: x
              log: x
      - id: marked
        break: true
        log: end
"""


def test_breakpoint_in_a_loop_fires_every_iteration_without_retriggering():
    bg = Background(LOOPED, breakpoints=frozenset({"x"}))
    for i in range(3):
        bg.recorder.wait_paused(i + 1)
        hits = bg.recorder.of("breakpoint_hit", step_id="x")
        assert len(hits) == i + 1
        assert hits[-1]["iterations"] == [i]
        assert bg.recorder.summary()[-2:] == ["breakpoint_hit:x",
                                              "state_changed:paused/breakpoint"]
        bg.controller.resume()
        bg.recorder.wait_count("step_started", i + 1, step_id="x")
    bg.recorder.wait_paused(4)  # the script's break: true
    assert bg.recorder.of("breakpoint_hit")[-1]["step_id"] == "marked"
    bg.controller.resume()
    assert bg.join().status == "completed"
    assert len(bg.recorder.of("breakpoint_hit")) == 4
    assert len(bg.recorder.of("log")) == 4


def test_set_breakpoints_replaces_client_breakpoints_during_a_run():
    bg = Background(LOOPED, breakpoints=frozenset({"x"}))
    bg.recorder.wait_paused()
    bg.controller.set_breakpoints([])
    bg.controller.resume()
    bg.recorder.wait_paused(2)
    assert bg.controller.state()["step_id"] == "marked"  # break: true stays
    bg.controller.resume()
    assert bg.join().status == "completed"
    assert [p["step_id"] for p in bg.recorder.of("breakpoint_hit")] == ["x", "marked"]


def test_pause_step_pauses_after_it():
    bg = Background("""
        steps:
          - id: hold
            pause: check the screen
          - id: after
            log: done
    """)
    bg.recorder.wait_paused()
    assert bg.recorder.summary()[1:] == ["step_started:hold", "step_finished:hold",
                                         "state_changed:paused/breakpoint"]
    assert bg.recorder.of("step_finished")[0]["result"] == {"message": "check the screen"}
    bg.controller.resume()
    assert bg.join().status == "completed"
    assert bg.recorder.of("log")[0]["message"] == "done"


def test_set_breakpoints_adds_a_breakpoint_during_a_run():
    bg = Background(LONG_SLEEP)
    bg.recorder.wait_count("step_started", step_id="nap")
    bg.controller.set_breakpoints(["after"])
    bg.recorder.wait_paused()
    assert bg.recorder.summary()[-3:] == ["step_finished:nap", "breakpoint_hit:after",
                                          "state_changed:paused/breakpoint"]
    bg.controller.resume()
    assert bg.join().status == "completed"


def test_stepping_from_a_breakpoint_does_not_retrigger_it():
    bg = Background(LOOPED, breakpoints=frozenset({"x"}))
    bg.recorder.wait_paused()
    bg.controller.step_over()  # runs x, then reaches x again in the next iteration
    bg.recorder.wait_paused(2)
    assert bg.recorder.summary()[-7:] == [
        "state_changed:running/step", "step_started:x", "log:x", "step_finished:x",
        "loop_iteration:lp", "breakpoint_hit:x", "state_changed:paused/breakpoint"]
    assert [p["iterations"] for p in bg.recorder.of("breakpoint_hit")] == [[0], [1]]
    bg.controller.stop()
    bg.join()


# --- state transitions of the control commands -----------------------------------------


def _last_states(bg: Background, count: int) -> list[str]:
    return [s for s in bg.recorder.summary() if s.startswith("state_changed")][-count:]


@pytest.mark.parametrize("command", ["step_over", "step_into", "step_out"])
def test_every_step_command_goes_running_then_paused_with_reason_step(command):
    s = Stepper()
    s.do("step_over")
    assert s.do("step_into") == "b"  # depth 2, so step_out pauses too
    s.do(command)
    assert _last_states(s.bg, 3) == ["state_changed:paused/step",
                                     "state_changed:running/step",
                                     "state_changed:paused/step"]
    s.bg.controller.resume()
    assert s.bg.join().status == "completed"
    assert _last_states(s.bg, 1) == ["state_changed:running/user"]


# --- step_out in nested blocks ----------------------------------------------------------

NESTED = """
    steps:
      - id: outer
        loop:
          times: 2
          do:
            - id: inner
              loop:
                times: 2
                do:
                  - id: check
                    if:
                      when: "true"
                      then:
                        - id: a
                          log: a
                        - id: b
                          log: b
                  - id: c
                    log: c
            - id: d
              log: d
      - id: e
        log: e
"""


def test_step_out_in_nested_blocks_finishes_remaining_iterations():
    s = Stepper(NESTED)
    for expected in ("inner", "check", "a"):
        assert s.do("step_into") == expected
    state = s.bg.controller.state()
    assert (state["path"], state["iterations"]) == ([0, 0, 0, 0, 0, 0, 0], [0, 0])
    assert s.do("step_out") == "c"  # finishes the if's branch
    assert s.logs() == ["a", "b"]
    assert s.bg.controller.state()["iterations"] == [0, 0]
    assert s.do("step_out") == "d"  # finishes the inner loop, its second iteration too
    assert s.logs() == ["a", "b", "c", "a", "b", "c"]
    assert s.bg.controller.state()["iterations"] == [0]
    assert s.do("step_out") == "e"  # finishes the outer loop's second iteration
    assert s.logs() == ["a", "b", "c"] * 2 + ["d"] + ["a", "b", "c"] * 2 + ["d"]
    assert "iterations" not in s.bg.controller.state()
    s.bg.controller.step_out()  # top level: runs to the end
    assert s.bg.join().status == "completed"
    assert s.logs()[-1] == "e"


# --- get_state ---------------------------------------------------------------------------


def test_get_state_reports_loop_iterations():
    bg = Background(NESTED, breakpoints=frozenset({"b", "inner"}))
    seen = []
    for count in range(1, 7):
        bg.recorder.wait_paused(count)
        state = bg.controller.state()
        GetStateResult.from_json({**state, "run_id": "r-1"})
        hit = bg.recorder.of("breakpoint_hit")[-1]
        assert (state["step_id"], state.get("iterations")) == (hit["step_id"],
                                                                hit.get("iterations"))
        seen.append((state["step_id"], state["iterations"]))
        bg.controller.resume()
    assert seen == [("inner", [0]), ("b", [0, 0]), ("b", [0, 1]),
                    ("inner", [1]), ("b", [1, 0]), ("b", [1, 1])]
    assert bg.join().status == "completed"


def test_get_state_during_a_paused_wait_inside_a_loop():
    bg = Background("""
        steps:
          - id: lp
            loop:
              times: 3
              do:
                - id: nap
                  sleep: 20s
    """)
    bg.recorder.wait_count("step_started", 2, step_id="nap")
    bg.controller.pause()
    bg.recorder.wait_paused()
    state = bg.controller.state()
    assert (state["step_id"], state["path"], state["iterations"]) == ("nap", [0, 0, 0], [1])
    bg.controller.stop()
    bg.join()


def test_step_mode_reports_the_first_step_as_soon_as_it_pauses():
    """A client asking get_state right when the first paused arrives gets the step."""
    from runtime_helpers import REGISTRY, Recorder, ScaledClock, load

    from j5_macro.runtime import RunOptions, create_run

    seen: list[dict] = []
    holder: dict = {}

    class Probe(Recorder):
        def __call__(self, type_, payload):
            super().__call__(type_, payload)
            if type_ == "state_changed" and payload["state"] == "paused" and not seen:
                seen.append(holder["controller"].state())

    script = load("steps:\n  - id: a\n    log: one\n  - id: b\n    log: two\n")
    holder["controller"] = create_run(script, REGISTRY, Probe(),
                                      RunOptions(mode="step", seed=1), clock=ScaledClock())
    import threading

    thread = threading.Thread(target=holder["controller"].run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)
    holder["controller"].stop()
    thread.join(5)
    assert seen and (seen[0]["step_id"], seen[0]["path"]) == ("a", [0])
