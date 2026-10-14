"""Script params through the engine: load reports them, run resolves them, and secret
values are masked in events, in the run history and in control answers."""

from __future__ import annotations

from typing import Any

from engine_harness import Behaviour, Engine, FakeFactory

from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.payloads import LoadResult
from j5_macro.runtime.api import ControlError
from j5_macro.steps import StepContext, StepSpec, StepType, default_registry

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
    # An expression is evaluated inside the engine, so a secret is copied, not "***".
    engine.ok("set_variable", {"run_id": run_id, "name": "copy", "expression": "password"})
    assert factory.last.calls[-1] == ("set_variable", ("copy", {"expr": "password"}))
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
    # A one-character secret is masked as a whole value only, not inside "pin 1".
    assert payloads["step_started"]["params"] == {"message": "pin 1"}
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


def test_a_short_secret_never_alters_keys_or_unrelated_text() -> None:
    events = [("step_started", {"step_id": "hi", "path": [0], "line": 7, "type": "log",
                                "params": {"message": "x marks x11", "text": "x"}}),
              ("step_finished", {"step_id": "hi", "path": [0], "line": 7, "duration_ms": 1,
                                 "result": {"x": "x", "box": "xx"}})]
    state = {"state": "paused", "step_id": "hi", "path": [0], "line": 7,
             "variables": {"x": "x", "password": "x", "extra": {"x": "x11"}}}
    engine, factory = make(Behaviour(block=True, state=state, events=events,
                                     backend="x11"))
    run_id = engine.start(engine.load_source(SCRIPT),
                          params={"user": "ann", "password": "x"})
    answer = engine.ok("get_state", {"run_id": run_id})
    assert answer["variables"] == {"x": "***", "password": "***", "extra": {"x": "x11"}}
    factory.last.release()
    engine.wait_finished(run_id)
    engine.close()
    events_seen = engine.run_events(run_id)
    payloads = {e["type"]: e["payload"] for e in events_seen}
    assert payloads["run_started"]["backend"] == "x11"
    assert payloads["step_started"]["params"] == {"message": "x marks x11", "text": "***"}
    assert payloads["step_finished"]["result"] == {"x": "***", "box": "xx"}
    # Warned right after run_started, naming the param but never showing its value.
    assert [e["type"] for e in events_seen][:2] == ["run_started", "log"]
    warning = events_seen[1]["payload"]
    assert warning["level"] == "warning"
    assert warning["message"] == (
        "secret param 'password' has fewer than 4 characters: it is masked only where "
        "a value is exactly that secret, not inside longer text")


def test_a_long_secret_logs_no_warning() -> None:
    engine, _ = make()
    run_id = engine.start(engine.load_source(SCRIPT),
                          params={"user": "ann", "password": "abcd"})
    engine.wait_finished(run_id)
    engine.close()
    assert not [e for e in engine.run_events(run_id) if e["type"] == "log"
                and e["payload"]["level"] == "warning"]


# --- results stored as secrets, through the real interpreter ----------------------------

TOKEN = "tok-123"


def _fetch(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    """Stands in for a command that prints a token."""
    return {"exit_code": 0, "stdout": f"{TOKEN}\n", "stderr": ""}


SECRET_RESULT = """\
steps:
  - id: get_token
    fetch:
    as: {name: token, secret: true}
  - id: show
    log: "token ${token.stdout}stripped ${strip(token.stdout)}"
  - id: check
    pause: look
  - id: broken
    set: {name: x, expr: "token.stdout + 1"}
"""


def test_a_result_stored_as_secret_is_masked_from_its_step_finished_on(tmp_path) -> None:
    registry = default_registry()
    registry.register(StepType(StepSpec(name="fetch", doc="Fetch a token."), _fetch))
    engine = Engine(registry=registry, runs_dir=tmp_path)
    run_id = engine.start(engine.load_source(SECRET_RESULT))
    engine.writer.wait_for(lambda m: m.get("type") == "state_changed"
                           and m["payload"].get("state") == "paused")
    state = engine.ok("get_state", {"run_id": run_id})
    assert state["variables"]["token"] == {"exit_code": 0, "stdout": "***", "stderr": ""}
    for expression in ("token.stdout", "strip(token.stdout)", "token"):
        answer = engine.ok("evaluate", {"run_id": run_id, "expression": expression})
        assert TOKEN not in repr(answer)
    engine.ok("resume", {"run_id": run_id})
    finished = engine.wait_finished(run_id)
    engine.close()

    events = engine.run_events(run_id)
    assert TOKEN not in repr(events)
    (result,) = [e["payload"]["result"] for e in events if e["type"] == "step_finished"
                 and e["payload"]["step_id"] == "get_token"]
    assert result == {"exit_code": 0, "stdout": "***", "stderr": ""}
    (stored,) = [e["payload"] for e in events if e["type"] == "variable_changed"
                 and e["payload"]["name"] == "token"]
    assert stored["step_id"] == "get_token" and stored["new"]["stdout"] == "***"
    (log,) = [e["payload"]["message"] for e in events if e["type"] == "log"
              and e["payload"].get("step_id") == "show"]
    assert log == "token ***stripped ***"
    assert finished["payload"]["status"] == "failed"
    for path in tmp_path.rglob("*.json*"):
        assert TOKEN not in path.read_text(encoding="utf-8"), path


def _fetch_short(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    """Stands in for a command that prints a two-character code."""
    return {"exit_code": 0, "stdout": "ok\n", "stderr": ""}


SHORT_RESULT = """\
steps:
  - id: get_code
    fetch:
    as: {name: code, secret: true}
  - id: show
    log: "${strip(code.stdout)} | okay, backend x11 ok"
"""


def test_a_short_result_stored_as_secret_follows_the_same_rule() -> None:
    registry = default_registry()
    registry.register(StepType(StepSpec(name="fetch", doc="Fetch a code."), _fetch_short))
    engine = Engine(registry=registry)
    run_id = engine.start(engine.load_source(SHORT_RESULT))
    engine.wait_finished(run_id)
    engine.close()

    events = engine.run_events(run_id)
    types = [(e["type"], e["payload"].get("step_id")) for e in events]
    at = types.index(("step_finished", "get_code"))
    assert types[at - 1] == ("log", "get_code")
    assert types[at + 1] == ("variable_changed", "get_code")
    warning = events[at - 1]["payload"]
    assert warning["level"] == "warning"
    assert "'code'" in warning["message"] and "fewer than 4 characters" in warning["message"]
    assert events[at]["payload"]["result"] == {"exit_code": 0, "stdout": "***", "stderr": ""}
    (log,) = [e["payload"]["message"] for e in events if e["type"] == "log"
              and e["payload"].get("step_id") == "show"]
    # The whole value is masked as such; "ok" inside longer text is left alone.
    assert log == "ok | okay, backend x11 ok"
