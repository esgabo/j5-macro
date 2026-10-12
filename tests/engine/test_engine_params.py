"""Script params through the engine: load reports them, run resolves them, and secret
values are masked in events, in the run history and in control answers."""

from __future__ import annotations

from typing import Any

from engine_harness import Behaviour, Engine, FakeFactory

from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.payloads import LoadResult
from j5_macro.runtime.api import ControlError

SECRET = "s3cr#t pass"
SCRIPT = """\
params:
  user: {description: Account name}
  password: {secret: true}
  retries: {type: integer, default: 2}
vars: {greeting: hi}
steps:
  - id: hi
    log: hello
"""


def make(behaviour: Behaviour | None = None, **kwargs: Any) -> tuple[Engine, FakeFactory]:
    factory = FakeFactory(behaviour)
    return Engine(run_factory=factory, **kwargs), factory


def test_load_reports_the_declared_params() -> None:
    engine, _ = make()
    result = LoadResult.from_json(engine.ok("load", {"source": SCRIPT}))
    engine.close()
    assert result.params == [
        {"name": "user", "type": "string", "secret": False, "required": True,
         "description": "Account name"},
        {"name": "password", "type": "string", "secret": True, "required": True},
        {"name": "retries", "type": "integer", "secret": False, "required": False,
         "default": 2},
    ]


def test_params_become_variables_of_the_run() -> None:
    engine, factory = make()
    script_id = engine.load_source(SCRIPT)
    run_id = engine.start(script_id, params={"user": "ACME #42", "password": SECRET,
                                             "retries": "5"},
                          vars={"greeting": "hello"})
    engine.wait_finished(run_id)
    engine.close()
    assert factory.last.options.variables == {"greeting": "hello", "user": "ACME #42",
                                              "password": SECRET, "retries": 5}


def error_of(payload: dict[str, Any]) -> dict[str, Any]:
    engine, factory = make()
    script_id = engine.load_source(SCRIPT)
    error = engine.error("run", {"script_id": script_id, "mode": "normal", **payload})
    engine.close()
    assert factory.controllers == []
    assert error["code"] == "validation_failed"
    return error


def test_missing_params_are_all_listed() -> None:
    error = error_of({"params": {"retries": 1}})
    assert error["message"] == "missing required params: 'user', 'password'"
    assert error["details"] == {"param": "user", "reason": "missing",
                                "missing": ["user", "password"]}


def test_unknown_params_and_vars_and_wrong_types_are_rejected() -> None:
    base = {"user": "a", "password": "p"}
    assert error_of({"params": {**base, "colour": "red"}})["details"] == {
        "param": "colour", "reason": "unknown"}
    assert error_of({"params": base, "vars": {"nope": 1}})["details"] == {
        "var": "nope", "reason": "unknown"}
    wrong = error_of({"params": {**base, "retries": "many"}})
    assert wrong["details"] == {"param": "retries", "reason": "type"}
    secret = error_of({"params": {"user": "a", "password": 3}})
    assert secret["details"] == {"param": "password", "reason": "type"}


LEAKS = [
    ("step_started", {"step_id": "hi", "path": [0], "line": 7, "type": "type",
                      "params": {"text": SECRET}}),
    ("log", {"level": "info", "message": f"user ann, password {SECRET}!", "step_id": "hi"}),
    ("step_failed", {"step_id": "hi", "path": [0], "line": 7,
                     "error": {"code": "expression_error",
                               "message": f"cannot add {SECRET!r} and 1"}}),
]


def test_secret_values_are_masked_in_events_and_history(tmp_path) -> None:
    engine, _ = make(Behaviour(events=LEAKS), runs_dir=tmp_path)
    script_id = engine.load_source(SCRIPT)
    run_id = engine.start(script_id, params={"user": "ann", "password": SECRET})
    engine.wait_finished(run_id)
    replay = engine.ok("get_events", {"run_id": run_id, "from_seq": 1})
    engine.close()

    assert SECRET not in repr(replay)
    text = repr(engine.run_events(run_id))
    assert SECRET not in text
    events = {e["type"]: e["payload"] for e in engine.run_events(run_id)}
    assert events["step_started"]["params"] == {"text": "***"}
    assert events["log"]["message"] == "user ann, password ***!"
    assert events["step_failed"]["error"]["message"] == "cannot add '***' and 1"
    (history,) = [p for p in tmp_path.rglob("events.jsonl")]
    saved = history.read_text(encoding="utf-8")
    assert SECRET not in saved and "user ann, password ***!" in saved


def test_secret_values_are_masked_in_control_answers() -> None:
    state = {"state": "paused", "step_id": "hi", "path": [0], "line": 7,
             "variables": {"user": "ann", "password": SECRET, "both": f"ann:{SECRET}"}}
    engine, factory = make(Behaviour(block=True, state=state))
    run_id = engine.start(engine.load_source(SCRIPT),
                          params={"user": "ann", "password": SECRET})
    answer = engine.ok("get_state", {"run_id": run_id})
    assert answer["variables"] == {"user": "ann", "password": "***", "both": "ann:***"}
    assert engine.ok("evaluate", {"run_id": run_id, "expression": "password"})["value"] == {
        "expr": "password"}
    factory.last.behaviour.control_error = ControlError(ErrorCode.EXPRESSION_ERROR,
                                                        f"bad value {SECRET}")
    error = engine.error("evaluate", {"run_id": run_id, "expression": "x"})
    assert error["message"] == "bad value ***"
    factory.last.behaviour.control_error = None
    for controller in factory.controllers:
        controller.release()
    engine.close()


def test_a_numeric_secret_never_masks_positions() -> None:
    position = {"step_id": "hi", "path": [0, 0, 1], "iterations": [1, 0], "line": 1}
    events = [
        ("step_started", {**position, "type": "log", "params": {"message": "pin 1"}}),
        ("loop_iteration", {"step_id": "hi", "path": [0], "line": 1, "index": 1, "total": 1}),
        ("step_finished", {**position, "duration_ms": 1, "result": {"value": 1}}),
    ]
    state = {"state": "paused", **position, "variables": {"password": 1, "n": 1}}
    engine, factory = make(Behaviour(block=True, state=state, events=events))
    run_id = engine.start(engine.load_source(SCRIPT), params={"user": "ann", "password": "1"})
    answer = engine.ok("get_state", {"run_id": run_id})
    assert {k: answer[k] for k in position} == position
    assert answer["variables"] == {"password": "***", "n": "***"}
    factory.last.release()
    engine.wait_finished(run_id)
    engine.close()
    payloads = {e["type"]: e["payload"] for e in engine.run_events(run_id)}
    assert payloads["step_started"]["path"] == [0, 0, 1]
    assert payloads["step_started"]["iterations"] == [1, 0]
    assert payloads["step_started"]["params"] == {"message": "pin ***"}
    assert (payloads["loop_iteration"]["index"], payloads["loop_iteration"]["total"]) == (1, 1)
    assert payloads["step_finished"]["duration_ms"] == 1
    assert payloads["step_finished"]["result"] == {"value": "***"}


def test_runs_without_secrets_are_untouched() -> None:
    engine, _ = make(Behaviour(events=[("log", {"level": "info", "message": "***x"})]))
    run_id = engine.start(engine.load_source(SCRIPT.replace(
        "password: {secret: true}", "password: {}")), params={"user": "a", "password": "x"})
    engine.wait_finished(run_id)
    engine.close()
    (log,) = [e for e in engine.run_events(run_id) if e["type"] == "log"]
    assert log["payload"]["message"] == "***x"
