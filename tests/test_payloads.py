from typing import Any

import pytest

from j5_macro.protocol import (
    COMMAND_PAYLOADS,
    COMMAND_RESULTS,
    EVENT_PAYLOADS,
    Command,
    DeleteEdit,
    EditScriptPayload,
    ErrorCode,
    HelloResult,
    InputPlannedEvent,
    InsertEdit,
    LogEvent,
    MoveEdit,
    PayloadError,
    RunPayload,
    SetMetaEdit,
    StepFailedEvent,
    UpdateEdit,
    decode,
    encode,
    parse_command_payload,
    parse_command_result,
    parse_event_payload,
)

# The contract's command and event tables. Changing these sets is a protocol change.
CONTRACT_COMMANDS = {
    "hello", "list_step_types", "load", "validate", "run", "pause", "resume", "step_over",
    "step_into", "step_out", "stop", "set_breakpoints", "get_state", "set_variable", "evaluate",
    "get_events",
    "list_scripts", "create_script", "get_script", "edit_script", "list_runs", "get_run",
    "delete_run",
    "list_windows", "screenshot", "capture_region", "pick_point", "find_image",
}
CONTRACT_EVENTS = {
    "ready", "script_changed", "run_started", "state_changed", "run_finished", "step_started",
    "step_finished", "step_skipped", "step_failed", "breakpoint_hit", "branch_taken",
    "loop_iteration", "variable_changed", "image_search", "input_planned", "input_executed",
    "input_state", "user_interference", "keys_released", "log", "trigger_fired",
}

RUN = {"run_id": "r-1"}
STEP = {"step_id": "open_menu", "path": [0, 2], "line": 12}
CAPS = {"backend": "x11", "input_strategies": ["direct", "linear", "human"],
        "step_types": ["click", "sleep"],
        "display_scales": [{"bbox": [0, 0, 1920, 1080], "scale": 1.25}]}
NODE = {"id": "s1", "type": "loop", "path": [0], "line": 3, "params": {"times": 3},
        "break": False, "blocks": {"do": [{"id": "c1", "type": "click", "path": [0, 0],
                                           "params": {"x": 1, "y": 2}, "break": True}]}}

# Minimal valid examples, written exactly as to_json emits them.
COMMAND_EXAMPLES: dict[str, dict[str, Any]] = {
    "hello": {"v_min": 1, "v_max": 1, "client": "cli"},
    "list_step_types": {},
    "load": {"path": "scripts/a.yaml"},
    "validate": {"script_id": "s"},
    "run": {"script_id": "s", "mode": "dry", "captures": "on_failure", "allow_exec": True,
            "strict": True},
    "pause": RUN, "resume": RUN, "step_over": RUN, "step_into": RUN, "step_out": RUN,
    "stop": {"run_id": "r-1", "reason": "user"},
    "set_breakpoints": {"script_id": "s", "step_ids": ["a", "b"]},
    "get_state": RUN,
    "set_variable": {"run_id": "r-1", "name": "n", "value": None},
    "evaluate": {"run_id": "r-1", "expression": "n + 1"},
    "get_events": {"from_seq": 3},
    "list_scripts": {},
    "create_script": {"path": "a.yaml", "name": "A"},
    "get_script": {"script_id": "s"},
    "edit_script": {"script_id": "s", "base_version": "v1", "edits": [
        {"op": "insert", "step": {"sleep": "1s"}, "to": {"after_step_id": "a"}},
        {"op": "update", "step_id": "a", "params": {"x": 3}, "common": {"break": True}},
        {"op": "move", "step_id": "a", "to": {"parent_step_id": "c", "block": "else",
                                             "index": 0}},
        {"op": "delete", "step_id": "b"},
        {"op": "set_meta", "vars": {"k": "v"}, "images": {"b": {"png": "iVBORw0K"}}},
    ]},
    "list_runs": {"limit": 5},
    "get_run": {"run_id": "r-1", "from_seq": 0},
    "delete_run": RUN,
    "list_windows": {},
    "screenshot": {"path": "shot.png", "window": {"title": "Notes"},
                   "region": [0, 0, 100, 50]},
    "capture_region": {"bbox": [10, 20, 30, 40], "window": {"title": "Notes"},
                       "script_id": "s", "name": "save", "overwrite": True, "padding": 2,
                       "transparent": True},
    "pick_point": {"window": {"title": "Notes"}},
    "find_image": {"image": "#save", "script_id": "s", "threshold": 0.8, "scale": 1.0,
                   "region": [0, 0, 400, 300], "window": {"title": "Notes"},
                   "display_scale": 1.5, "show": True, "show_ms": 1000},
}
RESULT_EXAMPLES: dict[str, dict[str, Any]] = {
    "hello": {"v": 1, "capabilities": CAPS},
    "list_step_types": {"step_types": [{
        "name": "sleep", "doc": "Wait.", "schema": {"type": "object"},
        "params": [{"name": "duration", "type": "duration", "required": True, "default": None,
                    "doc": ""}],
        "result": [{"name": "seconds", "type": "number", "optional": False,
                    "doc": "Slept."}]}]},
    "load": {"script_id": "s", "steps": [NODE],
             "params": [{"name": "who", "type": "string", "secret": False,
                         "required": False, "default": "you"}],
             "images": [{"name": "button", "format": "png", "bytes": 202,
                         "width": 48, "height": 48, "scale": 1.5}],
             "warnings": [{"message": "generated id", "step_id": "c1", "line": 4}]},
    "validate": {"ok": False, "errors": [{"code": "validation_failed", "message": "bad",
                                          "step_id": "a", "line": 2}]},
    "run": RUN,
    "pause": {}, "resume": {}, "step_over": {}, "step_into": {}, "step_out": {}, "stop": {},
    "set_breakpoints": {}, "set_variable": {}, "delete_run": {},
    "get_state": {"run_id": "r-1", "state": "paused", "step_id": "a", "path": [1, 0, 2],
                  "iterations": [3], "line": 9, "variables": {"n": 1}},
    "evaluate": {"value": [1, 2]},
    "get_events": {"events": [{"v": 1, "kind": "event", "type": "log", "seq": 3}]},
    "list_scripts": {"scripts": [{"path": "a.yaml", "name": "A",
                                  "modified": "2026-10-12T10:00:00.000Z", "valid": True}]},
    "create_script": {"script_id": "s", "version": "0123456789abcdef"},
    "get_script": {"script_id": "s", "version": "v1", "source": "version: 1\n",
                   "name": "A", "steps": [NODE], "params": [], "images": [],
                   "valid": False, "errors": [{"code": "validation_failed",
                                               "message": "bad", "line": 3}]},
    "edit_script": {"version": "v2", "valid": True, "errors": []},
    "list_runs": {"runs": [{"run_id": "r-1", "status": "completed",
                            "started": "2026-10-12T10:00:00.000Z", "duration_ms": 900,
                            "script_name": "A", "script_path": "/s/a.yaml",
                            "mode": "normal"}]},
    "get_run": {"run_id": "r-1", "run": {"status": "completed"}, "events": [],
                "captures": ["captures/0001-a-1.png"]},
    "list_windows": {"windows": [{"title": "Notes", "bbox": [0, 30, 800, 570]}]},
    "screenshot": {"path": "/tmp/shot.png", "bbox": [0, 30, 800, 570], "display_scale": 1.0,
                   "window": "Notes", "window_bbox": [0, 0, 800, 570]},
    "capture_region": {"bbox": [10, 50, 30, 40], "display_scale": 1.5, "image_name": "save",
                       "version": "0123456789abcdef", "scale": 1.5, "window": "Notes",
                       "window_bbox": [10, 20, 30, 40],
                       "warnings": [{"code": "flat", "message": "flat"}]},
    "pick_point": {"x": 212, "y": 178, "display_scale": 1.0, "window": "Notes",
                   "window_area": [0, 30, 800, 570], "window_point": [212, 148]},
    "find_image": {"found": True, "score": 0.98, "region": [0, 0, 1280, 800], "scale": 1.0,
                   "display_scale": 1.0, "capture_scale": 1.0, "elapsed_ms": 12,
                   "bbox": [10, 20, 30, 40], "center": [25, 40], "window": "Notes",
                   "window_bbox": [10, 20, 30, 40]},
}
EVENT_EXAMPLES: dict[str, dict[str, Any]] = {
    "ready": {"v": 1, "backend": "x11", "capabilities": CAPS},
    "script_changed": {"script_id": "s", "source": "deleted",
                       "valid": False, "errors": [{"code": "parse_error", "message": "x"}]},
    "run_started": {"script_id": "s", "mode": "normal", "seed": 7, "backend": "x11",
                    "input_profile": {}, "display_scale": 1.25,
                    "display_scale_source": "detected", "capture_scale": 1.0},
    "state_changed": {"state": "paused", "reason": "step"},
    "run_finished": {"status": "failed", "duration_ms": 10, "warnings": 2,
                     "error": {"code": "image_not_found", "message": "timeout"}},
    "step_started": {**STEP, "type": "click", "params": {"x": 1}},
    "step_finished": {**STEP, "duration_ms": 5},
    "step_skipped": {**STEP, "reason": "user"},
    "step_failed": {**STEP, "error": {"code": "template_missing", "message": "no file"}},
    "breakpoint_hit": STEP,
    "branch_taken": {**STEP, "condition": {"image_visible": "a.png"}, "value": False,
                     "branch": "else"},
    "loop_iteration": {**STEP, "index": 0, "total": 3},
    "variable_changed": {"name": "n", "old": None, "new": 1},
    "image_search": {"template": "a.png", "threshold": 0.9, "found": False, "best_score": 0.5,
                     "region": [0, 0, 100, 100], "scale": 1.5, "display_scale": 1.5,
                     "capture_scale": 1.0, "attempt": 1, "elapsed_ms": 30},
    "input_planned": {"device": "mouse", "strategy": "human", "seed": 3, "from": [0, 0],
                      "to": [10, 20], "duration_ms": 300, "plan_ref": "plans/1.json"},
    "input_executed": {"device": "keyboard", "actual_duration_ms": 120, "aborted": False},
    "input_state": {"held": ["ctrl"]},
    "user_interference": {"expected": [1, 2], "actual": [50, 60], "action": "pause"},
    "keys_released": {"keys": ["ctrl", "shift"], "reason": "stop"},
    "log": {"level": "info", "message": "hi"},
    "trigger_fired": {"trigger_id": "t1", "kind": "time"},
}


def test_registries_match_contract():
    assert set(COMMAND_PAYLOADS) == CONTRACT_COMMANDS
    assert set(COMMAND_RESULTS) == CONTRACT_COMMANDS
    assert set(EVENT_PAYLOADS) == CONTRACT_EVENTS
    assert set(COMMAND_EXAMPLES) == CONTRACT_COMMANDS
    assert set(RESULT_EXAMPLES) == CONTRACT_COMMANDS
    assert set(EVENT_EXAMPLES) == CONTRACT_EVENTS


@pytest.mark.parametrize("name", sorted(CONTRACT_COMMANDS))
def test_command_payload_round_trip(name: str):
    example = COMMAND_EXAMPLES[name]
    parsed = parse_command_payload(name, example)
    assert isinstance(parsed, COMMAND_PAYLOADS[name])
    assert parsed.to_json() == example
    assert type(parsed).from_json(parsed.to_json()) == parsed


@pytest.mark.parametrize("name", sorted(CONTRACT_COMMANDS))
def test_command_result_round_trip(name: str):
    example = RESULT_EXAMPLES[name]
    parsed = parse_command_result(name, example)
    assert isinstance(parsed, COMMAND_RESULTS[name])
    assert parsed.to_json() == example


@pytest.mark.parametrize("name", sorted(CONTRACT_EVENTS))
def test_event_payload_round_trip(name: str):
    example = EVENT_EXAMPLES[name]
    parsed = parse_event_payload(name, example)
    assert isinstance(parsed, EVENT_PAYLOADS[name])
    assert parsed.to_json() == example


def test_payload_travels_inside_envelope():
    payload = RunPayload(script_id="s", mode="dry", seed=4)
    line = encode(Command(type="run", id="c1", payload=payload.to_json()))
    decoded = decode(line)
    assert isinstance(decoded, Command)
    assert parse_command_payload(decoded.type, decoded.payload) == payload


def error_of(fn: Any, *args: Any) -> str:
    with pytest.raises(PayloadError) as info:
        fn(*args)
    assert isinstance(info.value, ValueError)
    assert info.value.error.code == ErrorCode.VALIDATION_FAILED
    assert info.value.error.message == str(info.value)
    return str(info.value)


def test_missing_required_field():
    assert error_of(RunPayload.from_json, {"mode": "normal"}) == \
        "payload.script_id: missing required field"


def test_wrong_type():
    assert error_of(RunPayload.from_json, {"script_id": 3, "mode": "normal"}) == \
        "payload.script_id: expected string, got integer"
    assert error_of(RunPayload.from_json, "nope") == "payload: expected object, got string"


def test_bool_is_not_int():
    message = error_of(RunPayload.from_json, {"script_id": "s", "mode": "normal", "seed": True})
    assert message == "payload.seed: expected integer, got boolean"


def test_int_accepted_as_float():
    event = parse_event_payload("image_search", {**EVENT_EXAMPLES["image_search"],
                                                 "threshold": 1, "best_score": 0})
    assert event is not None
    assert event.to_json()["threshold"] == 1.0
    assert isinstance(event.to_json()["threshold"], float)
    data = {**EVENT_EXAMPLES["image_search"], "threshold": False}
    assert error_of(parse_event_payload, "image_search", data) == \
        "payload.threshold: expected number, got boolean"


def test_invalid_literal():
    assert error_of(RunPayload.from_json, {"script_id": "s", "mode": "fast"}) == \
        "payload.mode: expected one of normal, step, dry"
    assert error_of(LogEvent.from_json, {"level": "trace", "message": "x"}) == \
        "payload.level: expected one of debug, info, warning, error"


def test_nested_error_path():
    data = {"script_id": "s", "base_version": "v", "edits": [
        {"op": "delete", "step_id": "a"},
        {"op": "move", "step_id": "a", "to": {"parent_step_id": "c", "block": "do",
                                             "index": "1"}},
    ]}
    assert error_of(EditScriptPayload.from_json, data) == \
        "payload.edits[1].to.index: expected integer, got string"
    data = {"code": "busy"}
    assert error_of(StepFailedEvent.from_json, {**STEP, "error": data}) == \
        "payload.error.message: missing required field"


def test_result_errors_are_rooted_at_result():
    assert error_of(HelloResult.from_json, {"v": 1}) == \
        "result.capabilities: missing required field"


def test_edit_union_dispatches_on_op():
    parsed = EditScriptPayload.from_json(COMMAND_EXAMPLES["edit_script"])
    kinds = [type(edit) for edit in parsed.edits]
    assert kinds == [InsertEdit, UpdateEdit, MoveEdit, DeleteEdit, SetMetaEdit]
    bad = {"script_id": "s", "base_version": "v", "edits": [{"op": "rename"}]}
    assert error_of(EditScriptPayload.from_json, bad) == \
        "payload.edits[0].op: expected one of insert, update, move, delete, set_meta"
    bad = {"script_id": "s", "base_version": "v", "edits": [{"step_id": "a"}]}
    assert error_of(EditScriptPayload.from_json, bad) == \
        "payload.edits[0].op: missing required field"


def test_exactly_one_location():
    assert error_of(parse_command_payload, "load", {}) == \
        "payload: expected exactly one of path, source"
    assert error_of(parse_command_payload, "load", {"path": "a", "source": "b"}) == \
        "payload: expected exactly one of path, source"
    edit = {"op": "insert", "step": {"sleep": "1s"},
            "to": {"after_step_id": "a", "index": 1}}
    assert error_of(InsertEdit.from_json, edit) == \
        "payload.to: after_step_id excludes parent_step_id, block and index"
    edit = {"op": "insert", "step": {"sleep": "1s"}, "to": {"block": "then"}}
    assert error_of(InsertEdit.from_json, edit) == \
        "payload.to: parent_step_id and block go together"
    top = InsertEdit.from_json({"op": "insert", "step": {"log": "x"}, "to": {}})
    assert top.to.parent_step_id is None and top.to.index is None


def test_unknown_fields_ignored():
    parsed = RunPayload.from_json({"script_id": "s", "mode": "normal", "future": 1})
    assert "future" not in parsed.to_json()


def test_optional_omitted_and_defaults():
    parsed = RunPayload.from_json({"script_id": "s", "mode": "normal"})
    assert parsed.vars is None and parsed.seed is None and parsed.captures == "on_failure"
    assert parsed.allow_exec is False and parsed.strict is False
    assert parsed.to_json() == {"script_id": "s", "mode": "normal", "captures": "on_failure",
                                "allow_exec": False, "strict": False}
    assert "line" not in LogEvent(level="info", message="m").to_json()


@pytest.mark.parametrize("scale", [0, -1.5, float("inf")])
def test_run_display_scale_must_be_positive(scale):
    with pytest.raises(PayloadError, match="display_scale"):
        RunPayload.from_json({"script_id": "s", "mode": "normal", "display_scale": scale})


def test_run_display_scale():
    parsed = RunPayload.from_json({"script_id": "s", "mode": "normal", "display_scale": 2})
    assert parsed.display_scale == 2.0
    assert parsed.to_json()["display_scale"] == 2.0


def test_keyword_field_uses_wire_name():
    event = InputPlannedEvent.from_json(EVENT_EXAMPLES["input_planned"])
    assert event.start == [0, 0]
    assert "from" in event.to_json() and "start" not in event.to_json()


def test_get_events_engine_stream():
    parsed = parse_command_payload("get_events", {"run_id": None, "from_seq": 0})
    assert parsed.to_json() == {"from_seq": 0}


def test_unknown_types():
    assert error_of(parse_command_payload, "explode", {}) == \
        "type: unknown command 'explode'"
    assert parse_event_payload("future_event", {"x": 1}) is None
