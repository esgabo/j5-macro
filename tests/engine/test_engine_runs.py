"""Run lifecycle: run, control forwarding, get_events and shutdown, with fake runs."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from engine_harness import TIMEOUT_S, Behaviour, Engine, FakeFactory, control_error

from j5_macro.engine import runs as runs_module
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import ErrorInfo
from j5_macro.protocol.payloads import (
    EvaluateResult,
    GetEventsResult,
    GetStateResult,
    RunFinishedEvent,
    RunResult,
    RunStartedEvent,
)
from j5_macro.runtime.api import ControlError, RunOutcome

RUN_ID = re.compile(r"r-\d{8}-\d{6}-[0-9a-f]{4}")
SCRIPT = """\
input:
  mouse: {strategy: human, speed: 1.0}
  keyboard: {strategy: human, wpm: 55}
steps:
  - id: hi
    log: hello
vars: {n: 1}
name: Greeter
"""
STEP = {"step_id": "hi", "path": [0], "line": 5}


def make(behaviour: Behaviour | None = None) -> tuple[Engine, FakeFactory]:
    factory = FakeFactory(behaviour)
    return Engine(run_factory=factory), factory


@pytest.fixture
def blocking() -> Iterator[tuple[Engine, FakeFactory]]:
    engine, factory = make(Behaviour(block=True))
    yield engine, factory
    for controller in factory.controllers:
        controller.release()
    engine.close()


def test_run_lifecycle_and_event_order() -> None:
    behaviour = Behaviour(events=[("step_started", {**STEP, "type": "log", "params": {}}),
                                  ("step_finished", {**STEP, "duration_ms": 1,
                                                     "result": None})])
    engine, factory = make(behaviour)
    script_id = engine.load_source(SCRIPT)
    run_cmd = engine.send("run", {"script_id": script_id, "mode": "dry", "seed": 42,
                                  "vars": {"n": 3},
                                  "input_profile": {"mouse": {"strategy": "direct"}},
                                  "captures": "all"})
    run_id = RunResult.from_json(engine.response(run_cmd)["result"]).run_id
    assert RUN_ID.fullmatch(run_id)
    finished = engine.wait_finished(run_id)
    engine.close()

    out = engine.writer.snapshot()
    events = engine.run_events(run_id)
    assert [e["type"] for e in events] == ["run_started", "step_started", "step_finished",
                                           "run_finished"]
    assert [e["seq"] for e in events] == [1, 2, 3, 4]
    assert events[-1] is not None and events[-1] == finished
    t_ms = [e["t_ms"] for e in events]
    assert t_ms == sorted(t_ms)
    # run_started, then the response, then whatever the worker emits.
    response_at = next(i for i, m in enumerate(out) if m.get("id") == run_cmd)
    assert out.index(events[0]) < response_at < out.index(events[1])

    started = RunStartedEvent.from_json(events[0]["payload"])
    assert (started.script_id, started.mode, started.seed) == (script_id, "dry", 42)
    assert started.script_name == "Greeter"
    assert started.backend == "none"
    assert started.input_profile == {"mouse": {"strategy": "direct", "speed": 1.0},
                                     "keyboard": {"strategy": "human", "wpm": 55}}
    done = RunFinishedEvent.from_json(finished["payload"])
    assert done.status == "completed" and done.reason is None and done.duration_ms >= 0

    options = factory.last.options
    assert (options.mode, options.seed, options.variables) == ("dry", 42, {"n": 3})
    assert options.input_profile == {"mouse": {"strategy": "direct"}}
    assert options.captures == "all"
    assert factory.last.script.steps[0].id == "hi"
    assert factory.last.registry is engine.registry


def test_seed_is_generated_and_backend_comes_from_controller() -> None:
    engine, _ = make(Behaviour(backend="x11"))
    run_id = engine.start(engine.load_source())
    engine.wait_finished(run_id)
    started = RunStartedEvent.from_json(engine.run_events(run_id)[0]["payload"])
    assert isinstance(started.seed, int) and 0 <= started.seed < 2**31
    assert started.backend == "x11"
    engine.close()


def test_failed_and_stopped_outcomes_reach_run_finished() -> None:
    error = ErrorInfo(code="image_not_found", message="no", step_id="hi")
    engine, _ = make(Behaviour(outcome=RunOutcome(status="failed", error=error)))
    run_id = engine.start(engine.load_source())
    payload = engine.wait_finished(run_id)["payload"]
    assert payload["status"] == "failed"
    assert payload["error"] == error.to_json()
    engine.close()


def test_events_emitted_while_creating_follow_run_started() -> None:
    engine, _ = make(Behaviour(on_create=[("log", {"level": "info", "message": "init"})]))
    run_id = engine.start(engine.load_source())
    engine.wait_finished(run_id)
    assert [e["type"] for e in engine.run_events(run_id)] == ["run_started", "log",
                                                              "run_finished"]
    engine.close()


def test_interpreter_exception_becomes_failed_run() -> None:
    engine, _ = make(Behaviour(raises=RuntimeError("kaboom")))
    run_id = engine.start(engine.load_source())
    payload = engine.wait_finished(run_id)["payload"]
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "internal_error"
    assert "kaboom" in payload["error"]["message"]
    assert "RuntimeError" in payload["error"]["details"]["traceback"]
    # The slot is free again.
    engine.wait_finished(engine.start(engine.load_source()))
    engine.close()


def test_factory_errors_answer_run_without_starting_it() -> None:
    engine, factory = make()
    script_id = engine.load_source()
    factory.error = ControlError(ErrorCode.BACKEND_UNAVAILABLE, "no X11 session")
    error = engine.error("run", {"script_id": script_id, "mode": "normal"})
    assert (error["code"], error["message"]) == ("backend_unavailable", "no X11 session")
    factory.error = RuntimeError("broken")
    assert engine.error("run", {"script_id": script_id, "mode": "normal"})["code"] \
        == "internal_error"
    assert not any(m["kind"] == "event" and m["run_id"] for m in engine.writer.snapshot())
    factory.error = None
    engine.wait_finished(engine.start(script_id))
    engine.close()


def test_default_factory_without_interpreter_is_internal_error(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs_module, "INTERPRETER_MODULE", "j5_macro.runtime.no_such_module")
    engine = Engine()
    error = engine.error("run", {"script_id": engine.load_source(), "mode": "normal"})
    assert error["code"] == "internal_error"
    assert "interpreter is not available" in error["message"]
    engine.close()


def test_run_validation_and_unknown_script() -> None:
    engine, _ = make()
    assert engine.error("run", {"script_id": "s9", "mode": "normal"})["code"] \
        == "script_not_found"
    assert engine.error("run", {"script_id": "s1", "mode": "fast"})["code"] \
        == "validation_failed"
    engine.close()


def test_busy_while_a_run_is_active(blocking: tuple[Engine, FakeFactory]) -> None:
    engine, factory = blocking
    script_id = engine.load_source()
    run_id = engine.start(script_id)
    error = engine.error("run", {"script_id": script_id, "mode": "normal"})
    assert error["code"] == "busy"
    assert error["details"] == {"run_id": run_id}
    factory.last.release()
    engine.wait_finished(run_id)
    second = engine.start(script_id)
    assert second != run_id


@pytest.mark.parametrize("command", ["pause", "resume", "step_over", "step_into",
                                     "step_out"])
def test_simple_control_commands_are_forwarded(
        blocking: tuple[Engine, FakeFactory], command: str) -> None:
    engine, factory = blocking
    run_id = engine.start(engine.load_source())
    assert engine.ok(command, {"run_id": run_id}) == {}
    assert factory.last.calls == [(command, ())]


def test_stop_set_variable_evaluate_and_get_state(
        blocking: tuple[Engine, FakeFactory]) -> None:
    engine, factory = blocking
    run_id = engine.start(engine.load_source())
    controller = factory.last

    state = GetStateResult.from_json(engine.ok("get_state", {"run_id": run_id}))
    assert (state.run_id, state.state, state.step_id, state.variables) == \
        (run_id, "paused", "hi", {"x": 1})
    assert engine.ok("set_variable", {"run_id": run_id, "name": "x", "value": [1, 2]}) == {}
    value = EvaluateResult.from_json(engine.ok("evaluate", {"run_id": run_id,
                                                            "expression": "x + 1"}))
    assert value.value == {"expr": "x + 1"}
    assert engine.error("stop", {"run_id": run_id, "reason": "bored"})["code"] \
        == "validation_failed"
    assert engine.ok("stop", {"run_id": run_id, "reason": "interference"}) == {}
    finished = engine.wait_finished(run_id)["payload"]
    assert (finished["status"], finished["reason"]) == ("stopped", "interference")
    assert controller.calls == [("state", ()), ("set_variable", ("x", [1, 2])),
                                ("evaluate", ("x + 1",)), ("stop", ("interference",))]


def test_stop_reason_defaults_to_user(blocking: tuple[Engine, FakeFactory]) -> None:
    engine, factory = blocking
    run_id = engine.start(engine.load_source())
    engine.ok("stop", {"run_id": run_id})
    assert engine.wait_finished(run_id)["payload"]["reason"] == "user"
    assert factory.last.calls == [("stop", ("user",))]


def test_control_error_maps_to_its_code() -> None:
    engine, factory = make(Behaviour(block=True, control_error=control_error("not paused")))
    run_id = engine.start(engine.load_source())
    error = engine.error("step_over", {"run_id": run_id})
    assert (error["code"], error["message"]) == ("invalid_state", "not paused")
    behaviour = factory.last.behaviour
    behaviour.control_error = ControlError(ErrorCode.EXPRESSION_ERROR, "bad expression")
    assert engine.error("evaluate", {"run_id": run_id, "expression": "1 +"})["code"] \
        == "expression_error"
    behaviour.control_error = None
    engine.close()  # end of input stops the blocked run


def test_set_breakpoints_is_kept_per_script_and_forwarded(
        blocking: tuple[Engine, FakeFactory]) -> None:
    engine, factory = blocking
    script_id = engine.load_source()
    other = engine.load_source()
    assert engine.ok("set_breakpoints", {"script_id": script_id, "step_ids": ["hi"]}) == {}
    run_id = engine.start(script_id)
    controller = factory.last
    assert controller.options.breakpoints == frozenset({"hi"})

    engine.ok("set_breakpoints", {"script_id": script_id, "step_ids": []})
    engine.ok("set_breakpoints", {"script_id": other, "step_ids": ["hi"]})
    assert controller.calls == [("set_breakpoints", (frozenset(),))]
    assert engine.error("set_breakpoints", {"script_id": "s9", "step_ids": []})["code"] \
        == "script_not_found"
    controller.release()
    engine.wait_finished(run_id)
    assert engine.ok("set_breakpoints", {"script_id": script_id, "step_ids": ["x"]}) == {}


def test_not_running(blocking: tuple[Engine, FakeFactory]) -> None:
    engine, factory = blocking
    assert engine.error("pause", {"run_id": "r-1"})["code"] == "not_running"
    run_id = engine.start(engine.load_source())
    assert engine.error("get_state", {"run_id": "r-other"})["code"] == "not_running"
    factory.last.release()
    engine.wait_finished(run_id)
    for command, payload in [("pause", {}), ("stop", {}), ("get_state", {}),
                             ("set_variable", {"name": "x", "value": 1}),
                             ("evaluate", {"expression": "1"})]:
        error = engine.error(command, {"run_id": run_id, **payload})
        assert error["code"] == "not_running", command


def test_get_events_for_runs() -> None:
    behaviour = Behaviour(events=[("log", {"level": "info", "message": str(i)})
                                  for i in range(5)])
    engine, _ = make(behaviour)
    run_id = engine.start(engine.load_source())
    engine.wait_finished(run_id)
    wire = engine.run_events(run_id)
    assert len(wire) == 7

    everything = GetEventsResult.from_json(engine.ok("get_events", {"run_id": run_id,
                                                                    "from_seq": 0}))
    assert everything.events == wire
    tail = engine.ok("get_events", {"run_id": run_id, "from_seq": 6})["events"]
    assert [e["type"] for e in tail] == ["log", "run_finished"]

    # The engine stream is separate and still served with a null or omitted run_id.
    engine_stream = engine.ok("get_events", {"from_seq": 1})["events"]
    assert engine_stream[0]["type"] == "ready"
    assert all(e["run_id"] is None for e in engine_stream)
    assert engine.error("get_events", {"run_id": "r-nope", "from_seq": 1})["code"] \
        == "not_running"
    engine.close()


def test_get_events_keeps_only_recent_finished_runs() -> None:
    engine, _ = make()
    engine.runs.keep = 2
    script_id = engine.load_source()
    run_ids = []
    for _ in range(3):
        run_ids.append(engine.start(script_id))
        engine.wait_finished(run_ids[-1])
    assert engine.error("get_events", {"run_id": run_ids[0], "from_seq": 1})["code"] \
        == "not_running"
    assert engine.ok("get_events", {"run_id": run_ids[1], "from_seq": 1})["events"]
    engine.close()


def test_end_of_input_stops_the_active_run(blocking: tuple[Engine, FakeFactory]) -> None:
    engine, factory = blocking
    run_id = engine.start(engine.load_source())
    assert factory.last.running.wait(TIMEOUT_S)
    assert engine.close() == 0
    finished = engine.run_events(run_id)[-1]
    assert finished["type"] == "run_finished"
    assert finished["payload"]["status"] == "stopped"
    assert factory.last.calls == [("stop", ("user",))]


def test_end_of_input_waits_only_a_bounded_time() -> None:
    engine, factory = make(Behaviour(block=True, ignore_stop=True))
    engine.runs.shutdown_timeout = 0.2
    run_id = engine.start(engine.load_source())
    assert engine.close() == 0
    assert not any(e["type"] == "run_finished" for e in engine.run_events(run_id))
    logs = [e["payload"]["message"] for e in engine.engine_events("log")]
    assert any("did not finish" in message for message in logs)
    factory.last.release()


def test_events_after_run_finished_are_dropped() -> None:
    engine, factory = make()
    run_id = engine.start(engine.load_source())
    engine.wait_finished(run_id)
    factory.last.sink("log", {"level": "info", "message": "late"})
    assert engine.run_events(run_id)[-1]["type"] == "run_finished"
    logs: list[Any] = [e["payload"]["message"] for e in engine.engine_events("log")]
    assert any("dropped 'log'" in message for message in logs)
    engine.close()
