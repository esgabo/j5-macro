"""The run and debugging view models, without Qt: params, run options, the debugger's
controls per state, pauses, variables and the log."""

from __future__ import annotations

from pathlib import Path

import pytest

from j5_macro.gui.models.debugger import PauseInfo, debug_actions, pause_from_state
from j5_macro.gui.models.params import (
    ParamError,
    ParamSpec,
    collect_params,
    convert_param,
    initial_texts,
    parse_param_specs,
    rememberable,
)
from j5_macro.gui.models.run_log import RunLog, level_visible
from j5_macro.gui.models.run_options import (
    KINDS,
    RunOptions,
    confirmation_text,
    engine_runs_dir,
    needs_confirmation,
    parse_display_scale,
    parse_seed,
    run_kind,
)
from j5_macro.gui.models.variables import (
    contains_mask,
    editable_text,
    parse_value_text,
    set_variable_payload,
    variable_rows,
)
from j5_macro.protocol import Event

DECLARED = [
    {"name": "user", "type": "string", "description": "Who", "secret": False,
     "required": True},
    {"name": "password", "type": "string", "secret": True, "required": True},
    {"name": "retries", "type": "integer", "secret": False, "required": False,
     "default": 2},
    {"name": "ratio", "type": "number", "required": False, "default": 0.5},
    {"name": "verbose", "type": "boolean", "required": False, "default": False},
    {"type": "string"},
    "junk",
]


def event(type_: str, seq: int, payload: dict[str, object] | None = None) -> Event:
    return Event(type=type_, seq=seq, run_id="r-1", payload=payload or {},
                 ts="2026-10-14T12:00:00.000Z", t_ms=seq)


def test_param_specs_and_hints() -> None:
    specs = parse_param_specs(DECLARED)  # type: ignore[arg-type]
    assert [s.name for s in specs] == ["user", "password", "retries", "ratio", "verbose"]
    user, password, retries, _, verbose = specs
    assert user.hint == "required" and user.description == "Who"
    assert password.secret and password.default is None
    assert retries.hint == "default: 2" and retries.default_text == "2"
    assert verbose.default_text == "false"
    assert ParamSpec.from_json({"name": "x", "type": "weird"}).type == "string"
    assert ParamSpec.from_json({"name": "s", "secret": True, "default": "leak"}).default \
        is None


def test_param_conversion() -> None:
    integer = ParamSpec("n", "integer")
    number = ParamSpec("x", "number")
    boolean = ParamSpec("b", "boolean")
    text = ParamSpec("s")
    assert convert_param(integer, " 42 ") == 42
    assert convert_param(number, "3") == 3 and convert_param(number, "2.5") == 2.5
    assert convert_param(boolean, "TRUE") is True and convert_param(boolean, "false") is False
    assert convert_param(text, " 007 # x ") == " 007 # x "
    for spec, bad in ((integer, "4.5"), (number, "abc"), (number, "inf"), (boolean, "yes")):
        with pytest.raises(ParamError):
            convert_param(spec, bad)


def test_collect_params_checks_and_leaves_empty_ones_out() -> None:
    specs = parse_param_specs(DECLARED)  # type: ignore[arg-type]
    values, errors = collect_params(specs, {"user": "", "password": "s3cret!",
                                            "retries": "x", "ratio": "", "verbose": ""})
    assert errors == {"user": "required", "retries": "expected a whole number"}
    assert values == {"password": "s3cret!"}
    values, errors = collect_params(specs, {"user": "bob", "password": "pw",
                                            "retries": "3", "ratio": " ", "verbose": "true"})
    assert errors == {}
    assert values == {"user": "bob", "password": "pw", "retries": 3, "verbose": True}
    # A message never repeats the text (a mistyped secret must not show).
    _, errors = collect_params([ParamSpec("pin", "integer", secret=True, required=True)],
                               {"pin": "12a4"})
    assert "12a4" not in errors["pin"]


def test_initial_and_remembered_texts_never_hold_secrets() -> None:
    specs = parse_param_specs(DECLARED)  # type: ignore[arg-type]
    texts = initial_texts(specs, {"user": "bob", "password": "leak"})
    assert texts == {"user": "bob", "password": "", "retries": "2", "ratio": "0.5",
                     "verbose": "false"}
    assert "password" not in rememberable(specs, {"user": "a", "password": "pw"})


def test_run_kinds_and_options_payload() -> None:
    assert run_kind("step") is KINDS["debug"]
    with pytest.raises(ValueError):
        run_kind("fast")
    assert KINDS["debug_dry"].mode == "dry" and KINDS["debug_dry"].stop_on_entry
    assert not KINDS["dry"].sends_input and KINDS["debug"].sends_input
    options = RunOptions(strict=True, allow_exec=True, seed=7, captures="all",
                         display_scale=1.5)
    assert options.payload(KINDS["dry"]) == {"strict": True, "allow_exec": True, "seed": 7,
                                             "captures": "all", "display_scale": 1.5}
    assert "strict" not in options.payload(KINDS["normal"])
    assert RunOptions().payload(KINDS["normal"]) == {}
    after = options.after_run()
    assert not after.allow_exec and after.seed == 7 and after.strict


def test_seed_and_display_scale_fields() -> None:
    assert parse_seed("") is None and parse_seed(" 12 ") == 12
    assert parse_display_scale("") is None
    assert parse_display_scale("1.25") == 1.25 and parse_display_scale("150%") == 1.5
    for bad in ("abc", "0", "-1", "nan"):
        with pytest.raises(ValueError):
            parse_display_scale(bad)
    with pytest.raises(ValueError):
        parse_seed("1.5")


def test_confirmation_only_for_real_input() -> None:
    assert needs_confirmation(KINDS["normal"], "x11", True)
    assert needs_confirmation(KINDS["debug"], "windows", True)
    assert not needs_confirmation(KINDS["normal"], "x11", False)
    assert not needs_confirmation(KINDS["dry"], "x11", True)
    assert not needs_confirmation(KINDS["debug_dry"], "x11", True)
    assert not needs_confirmation(KINDS["normal"], "fake", True)
    assert not needs_confirmation(KINDS["normal"], None, True)
    assert "ctrl+alt+shift+q" in confirmation_text("ctrl+alt+shift+q")
    assert "no emergency stop key" in confirmation_text(None)


def test_engine_runs_dir(tmp_path: Path) -> None:
    r, q, x = (str(tmp_path / name) for name in ("r", "q", "x"))
    assert engine_runs_dir(["python", "-m", "j5_macro.engine", "--runs-dir", r], {}) \
        == Path(r)
    assert engine_runs_dir(["e", f"--runs-dir={q}"], {"J5_RUNS_DIR": x}) == Path(q)
    assert engine_runs_dir(None, {"J5_RUNS_DIR": x}) == Path(x)
    assert engine_runs_dir(None, {"J5_RUNS_DIR": "rel"}, str(tmp_path)) == tmp_path / "rel"
    assert engine_runs_dir(None, {}).parts[-2:] == ("j5-macro", "runs")


def test_debug_actions_per_state() -> None:
    def names(**kwargs: object) -> set[str]:
        actions = debug_actions(**kwargs)  # type: ignore[arg-type]
        return {name for name in ("run", "pause", "resume", "step", "stop", "inspect")
                if getattr(actions, name)}

    assert names(engine_running=False, can_run=True, state="idle") == set()
    assert names(engine_running=True, can_run=True, state="idle") == {"run"}
    assert names(engine_running=True, can_run=False, state="finished") == set()
    assert names(engine_running=True, can_run=False, state="starting") == {"stop"}
    assert names(engine_running=True, can_run=False, state="running") == {"pause", "stop"}
    assert names(engine_running=True, can_run=False, state="running", pending=True) \
        == {"stop"}
    assert names(engine_running=True, can_run=False, state="paused") == {
        "resume", "step", "stop", "inspect"}
    assert names(engine_running=True, can_run=False, state="paused", pending=True) == {
        "stop", "inspect"}
    assert names(engine_running=True, can_run=False, state="stopping") == set()


def test_pause_info_texts() -> None:
    assert PauseInfo("breakpoint", "save").title == "Paused at a breakpoint, before 'save'"
    assert PauseInfo("step").title == "Paused before the first step"
    assert not PauseInfo("user", "x").recoverable
    error = PauseInfo("error", "click", error={"code": "image_not_found",
                                               "message": "ok.png not found"})
    assert error.recoverable and error.title == "Step 'click' failed"
    assert error.detail == "ok.png not found (image_not_found)"
    assert "Retry" in error.hint
    moved = PauseInfo("interference", "move", interference={"expected": [1, 2],
                                                          "actual": [5, 6]})
    assert moved.recoverable and "(5, 6)" in moved.detail
    assert pause_from_state(PauseInfo("step", "a"), {"step_id": "b"}) == PauseInfo("step", "b")
    same = PauseInfo("step", "a")
    assert pause_from_state(same, {"step_id": "a"}) is same
    assert pause_from_state(None, {"step_id": "a"}) is None


def test_run_log_pauses_counts_and_summary() -> None:
    log = RunLog()
    log.apply(event("run_started", 1, {"script_name": "S", "mode": "dry", "seed": 9}))
    log.apply(event("breakpoint_hit", 2, {"step_id": "a"}))
    assert log.current_step_id == "a"
    log.apply(event("state_changed", 3, {"state": "paused", "reason": "breakpoint"}))
    assert log.pause == PauseInfo("breakpoint", "a")
    log.apply(event("state_changed", 4, {"state": "running", "reason": "user"}))
    assert log.pause is None
    log.apply(event("step_started", 5, {"step_id": "b", "type": "click"}))
    log.apply(event("step_failed", 6, {"step_id": "b", "screenshot": "captures/1.png",
                                       "error": {"code": "image_not_found",
                                                 "message": "nope"}}))
    log.apply(event("state_changed", 7, {"state": "paused", "reason": "error"}))
    assert log.pause is not None and log.pause.recoverable
    assert log.pause.screenshot == "captures/1.png" and log.pause.step_id == "b"
    log.apply(event("state_changed", 8, {"state": "running", "reason": "user"}))
    log.apply(event("user_interference", 9, {"expected": [1, 2], "actual": [3, 4],
                                             "action": "pause"}))
    log.apply(event("state_changed", 10, {"state": "paused", "reason": "interference"}))
    assert log.pause is not None and log.pause.interference is not None
    assert log.pause.error is None
    log.apply(event("log", 11, {"level": "warning", "message": "careful"}))
    assert log.warnings == 1 and log.counts["error"] == 2
    assert log.summary() == "Paused · dry mode · 1 warning · 2 error lines · seed 9"
    log.apply(event("run_finished", 12, {"status": "completed", "duration_ms": 1500,
                                         "warnings": 3}))
    assert log.pause is None
    assert log.summary() == ("Completed in 1.5 s · dry mode · 3 warnings · 2 error lines · "
                             "seed 9")


def test_new_log_lines_and_levels() -> None:
    log = RunLog()
    mouse = log.apply(event("input_planned", 1, {"device": "mouse", "strategy": "human",
                                                 "seed": 1, "from": [0, 0], "to": [10, 20],
                                                 "duration_ms": 250}))
    assert mouse.text == "mouse plan (human): (0, 0) → (10, 20) in 250 ms"
    assert mouse.level == "debug"
    keys = log.apply(event("input_planned", 2, {"device": "keyboard", "strategy": "human",
                                                "seed": 1, "chars": 5, "wpm": 61.7}))
    assert keys.text == "keyboard plan (human): 5 chars at 62 wpm"
    moved = log.apply(event("user_interference", 3, {"expected": [1, 2], "actual": [3, 4],
                                                     "action": "pause"}))
    assert moved.text == "the mouse was moved: expected at (1, 2), was at (3, 4) (pause)"
    released = log.apply(event("keys_released", 4, {"keys": ["ctrl", "button:left"],
                                                     "reason": "interference"}))
    assert released.text == "released ctrl, button:left (interference)"
    assert level_visible("warning", "info") and not level_visible("debug", "info")
    assert level_visible("debug", "debug") and not level_visible("info", "error")


def test_variables_rows_masks_and_edits() -> None:
    rows = variable_rows({"n": 2, "name": "bob", "password": "***",
                          "data": {"list": [1, "x ***"], "ok": True}})
    by_name = {row.name: row for row in rows}
    assert [row.name for row in rows] == ["n", "name", "password", "data"]
    assert by_name["n"].text == "2" and by_name["name"].text == '"bob"'
    assert by_name["password"].masked and by_name["data"].masked
    assert [child.name for child in by_name["data"].children] == ["list", "ok"]
    assert [c.name for c in by_name["data"].children[0].children] == ["[0]", "[1]"]
    assert editable_text(by_name["password"]) is None
    assert editable_text(by_name["data"]) is None
    assert editable_text(by_name["n"]) == "2"
    assert editable_text(by_name["name"]) == "bob"
    assert editable_text(variable_rows({"s": "12"})[0]) == '"12"'  # stays a string
    assert contains_mask(["a", ["***"]]) and not contains_mask({"***": 1})
    assert parse_value_text("3") == 3 and parse_value_text("[1, 2]") == [1, 2]
    assert parse_value_text("hello") == "hello"
    assert set_variable_payload("r", "n", "n + 1", expression=True) == {
        "run_id": "r", "name": "n", "expression": "n + 1"}
    assert set_variable_payload("r", "n", "7", expression=False) == {
        "run_id": "r", "name": "n", "value": 7}
    with pytest.raises(ValueError):
        set_variable_payload("r", "", "1", expression=False)
    with pytest.raises(ValueError):
        set_variable_payload("r", "n", " ", expression=True)
