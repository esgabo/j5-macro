"""The GUI's view models, without Qt: built from engine results and events."""

from __future__ import annotations

from j5_macro.gui.models import (
    EngineInfo,
    RunLog,
    ScriptState,
    find_step,
    format_event,
    iter_steps,
    parse_run_list,
    parse_script_list,
    parse_steps,
    step_summary,
)
from j5_macro.gui.models.run_log import event_level
from j5_macro.gui.models.script import format_problem
from j5_macro.protocol import Event

STEPS = [
    {"id": "start", "type": "log", "path": [0], "line": 5,
     "params": {"message": "hi", "level": "info"}, "break": False, "on_error": "stop"},
    {"id": "repeat", "type": "loop", "path": [1], "line": 7, "params": {"times": 2},
     "blocks": {"do": [
         {"id": "check", "type": "if", "path": [1, 0, 0], "line": 10,
          "params": {"when": "n == 2"}, "break": True, "as": {"name": "b", "secret": False},
          "blocks": {"then": [{"id": "yes", "type": "log", "path": [1, 0, 0, 0, 0],
                               "params": {"message": "two"}}],
                     "else": [{"id": "no", "type": "sleep", "path": [1, 0, 0, 1, 0],
                               "params": {"duration": "1s"}}]}}]}},
    {"id": "save", "type": "key", "path": [2], "params": {"press": "ctrl+s"},
     "unknown_field": 1},
]


def event(type_: str, seq: int, payload: dict[str, object] | None = None,
          run_id: str | None = "r-1") -> Event:
    return Event(type=type_, seq=seq, run_id=run_id, payload=payload or {},
                 ts="2026-10-14T12:00:00.000Z", t_ms=seq)


def test_steps_keep_ids_types_nesting_and_common_fields() -> None:
    steps = parse_steps(STEPS)
    assert [s.id for s in iter_steps(steps)] == ["start", "repeat", "check", "yes", "no",
                                                 "save"]
    repeat = steps[1]
    assert list(repeat.blocks) == ["do"]
    check = find_step(steps, "check")
    assert check is not None
    assert check.type == "if" and check.line == 10 and check.breakpoint
    assert check.path == (1, 0, 0) and check.depth == 2
    assert [(block, step.id) for block, step in check.children] == [("then", "yes"),
                                                                    ("else", "no")]
    assert check.common == {"as": {"name": "b", "secret": False}}
    assert steps[0].common == {"on_error": "stop"}
    assert find_step(steps, "missing") is None
    assert parse_steps("not a list") == [] and parse_steps(None) == []


def test_step_summary_is_short() -> None:
    steps = parse_steps(STEPS)
    assert step_summary(steps[0]) == "message=hi level=info"
    assert step_summary(steps[1]) == "times=2"
    long = parse_steps([{"id": "t", "type": "type", "params": {"text": "x" * 100}}])[0]
    summary = step_summary(long, limit=20)
    assert len(summary) == 20 and summary.endswith("…")


def test_script_state_from_get_script() -> None:
    state = ScriptState.from_get_script({
        "script_id": "s1", "version": "abc", "source": "version: 1\n", "name": "",
        "path": "/tmp/x/demo.yaml", "steps": STEPS, "params": [{"name": "p"}],
        "images": [], "valid": False,
        "errors": [{"code": "parse_error", "message": "bad", "line": 3}],
    }, warnings=[{"message": "generated id"}])
    assert state.script_id == "s1" and state.version == "abc"
    assert state.title == "demo.yaml"
    assert not state.valid and len(state.steps) == 3
    assert state.params == ({"name": "p"},)
    assert state.warnings == ({"message": "generated id"},)
    assert format_problem(state.errors[0]) == "line 3: bad (parse_error)"
    assert format_problem({"message": "plain"}) == "plain"


def test_script_list() -> None:
    entries = parse_script_list({"scripts": [
        {"path": "/w/a.yaml", "name": "A", "modified": "2026-01-01T00:00:00Z", "valid": True},
        {"path": "/w/sub/b.yaml", "name": "", "valid": False},
        {"name": "no path"},
    ]})
    assert [(e.name, e.valid) for e in entries] == [("A", True), ("b", False)]
    assert entries[1].relative_to("/w") == "sub/b.yaml"
    assert entries[1].relative_to("/elsewhere") == "/w/sub/b.yaml"
    assert parse_script_list({}) == []


def test_engine_info_from_hello() -> None:
    info = EngineInfo.from_hello(1, {
        "backend": "fake", "input_strategies": ["direct", "human"], "step_types": ["log"],
        "vision": True, "display_scales": [{"bbox": [0, 0, 10, 10], "scale": 1.0}],
        "stop_key": "ctrl+alt+shift+q", "something_new": 1})
    assert info.backend == "fake" and info.stop_key == "ctrl+alt+shift+q"
    assert info.input_strategies == ("direct", "human")
    assert info.capabilities["something_new"] == 1
    assert info.summary() == "backend: fake · vision · stop key: ctrl+alt+shift+q"
    bare = EngineInfo.from_hello(1, {"backend": None})
    assert bare.summary() == "no input backend · no stop key"


def test_run_log_follows_state_and_current_step() -> None:
    log = RunLog()
    assert not log.active and log.state == "idle"
    log.apply(event("run_started", 1, {"script_name": "Demo", "mode": "dry", "seed": 7,
                                       "backend": "fake"}))
    assert log.run_id == "r-1" and log.active and log.state == "running"
    log.apply(event("step_started", 2, {"step_id": "start", "type": "log"}))
    assert log.current_step_id == "start"
    log.apply(event("state_changed", 3, {"state": "paused", "reason": "breakpoint"}))
    assert log.state == "paused" and log.pause_reason == "breakpoint"
    log.apply(event("state_changed", 4, {"state": "running", "reason": "user"}))
    assert log.pause_reason is None
    line = log.apply(event("run_finished", 5, {"status": "failed", "duration_ms": 1500,
                                               "warnings": 2,
                                               "error": {"message": "boom"}}))
    assert line.text == "run failed in 1.5 s, 2 warnings: boom" and line.level == "error"
    assert log.state == "finished" and not log.active and log.current_step_id is None
    assert log.status == "failed"
    assert [line.seq for line in log.lines] == [1, 2, 3, 4, 5]


def test_event_lines() -> None:
    assert format_event(event("step_started", 1, {"step_id": "a", "type": "click"})) == \
        "▶ a (click)"
    assert format_event(event("step_finished", 1, {
        "step_id": "a", "duration_ms": 3,
        "result": {"dry_run": True, "description": "would click"}})) == \
        "✓ a (3 ms): would click"
    assert format_event(event("step_failed", 1, {
        "step_id": "a", "error": {"code": "x", "message": "no"}})) == "✗ a: no"
    assert format_event(event("log", 1, {"level": "warning", "message": "careful"})) == \
        "[warning] careful"
    assert format_event(event("image_search", 1, {
        "template": "#ok", "found": False, "best_score": 0.41234, "threshold": 0.9,
        "attempt": 2})) == "image #ok: not found (score 0.412, threshold 0.9, attempt 2)"
    assert format_event(event("branch_taken", 1, {
        "step_id": "c", "condition": "n == 2", "value": False, "branch": "else"})) == \
        "c: n == 2 is false, else"
    assert format_event(event("loop_iteration", 1, {"step_id": "l", "index": 0,
                                                    "total": 3})) == "l: iteration 1 of 3"
    assert format_event(event("variable_changed", 1, {"name": "n", "new": [1]})) == "n = [1]"
    # Unknown events are still shown.
    assert format_event(event("from_the_future", 1, {"x": 1})) == 'from_the_future {"x": 1}'
    assert format_event(event("trigger_fired", 1)) == "trigger_fired"
    assert event_level(event("log", 1, {"level": "error"})) == "error"
    assert event_level(event("input_planned", 1)) == "debug"


def test_run_list() -> None:
    runs = parse_run_list({"runs": [
        {"run_id": "r-20260101-000000-abcd", "script_name": "Demo", "mode": "dry",
         "status": "completed", "started": "2026-01-01T00:00:00Z", "duration_ms": 1234},
        {"run_id": "r-20260101-000001-abcd", "status": None},
        {"no": "id"},
    ]})
    assert [r.status for r in runs] == ["completed", None]
    assert runs[0].duration_text == "1.2 s" and runs[1].duration_text == ""
