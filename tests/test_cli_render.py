"""Unit tests of the pure rendering in j5_macro.cli.render and the option parsers."""

from __future__ import annotations

import argparse
import io

import pytest

from j5_macro.cli.options import (
    ENGINE_CMD_ENV,
    engine_command,
    parse_scalar,
    parse_var,
)
from j5_macro.cli.render import (
    ASCII_SYMBOLS,
    UNICODE_SYMBOLS,
    RunRenderer,
    Style,
    compact,
    depth_of,
    format_problem,
    format_step_types,
    format_warning,
    symbols_for,
    use_color,
    validate_summary,
)
from j5_macro.client import DEFAULT_COMMAND
from j5_macro.protocol import ErrorInfo, Event


def event(type_: str, t_ms: int = 0, **payload: object) -> Event:
    return Event(type=type_, seq=1, ts="2026-10-12T10:00:00.000Z", t_ms=t_ms, run_id="r-1",
                 payload=dict(payload))


def body(lines: list[str]) -> list[str]:
    """Drop the elapsed-time column."""
    return [line.split("s  ", 1)[1] for line in lines]


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


# -- styling ------------------------------------------------------------------------------


def test_no_color_when_not_a_tty():
    assert use_color(io.StringIO(), {}) is False


def test_color_on_a_tty_unless_no_color():
    assert use_color(FakeTty(), {}) is True
    assert use_color(FakeTty(), {"NO_COLOR": "1"}) is False
    assert use_color(FakeTty(), {"NO_COLOR": ""}) is True


def test_plain_style_has_no_escape_codes():
    renderer = RunRenderer()
    lines = renderer.render(event("step_failed", step_id="a", path=[0],
                                  error={"code": "x", "message": "boom"}))
    assert "\x1b[" not in "".join(lines)


def test_color_style_paints():
    style = Style(color=True)
    assert style.paint("ok", "green") == "\x1b[32mok\x1b[0m"
    lines = RunRenderer(style=style).render(event("step_finished", step_id="a", path=[0]))
    assert "\x1b[32m✓\x1b[0m" in lines[0]


def test_ascii_symbols_for_streams_that_cannot_encode_unicode():
    cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    assert symbols_for(cp1252) == ASCII_SYMBOLS
    utf8 = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    assert symbols_for(utf8) == UNICODE_SYMBOLS
    lines = RunRenderer(style=Style(symbols=ASCII_SYMBOLS)).render(
        event("step_started", step_id="a", path=[0], type="click"))
    assert body(lines) == ["> a  click"]


# -- helpers ------------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "depth"), [
    ([0], 0), ([3, 1, 0], 1), ([1, 0, 2, 0, 0], 2), ([], 0), (None, 0),
])
def test_depth_of(path, depth):
    assert depth_of(path) == depth


def test_compact_shortens_long_values():
    assert compact({"x": 1}) == '{"x": 1}'
    long = compact({"text": "a" * 200}, width=30)
    assert len(long) == 30
    assert long.endswith("...")


# -- run events ---------------------------------------------------------------------------


def test_elapsed_time_column():
    (line,) = RunRenderer().render(event("step_started", 1512, step_id="a", path=[0],
                                         type="sleep"))
    assert line == "  1.512s  ▶ a  sleep"


def test_step_lines_and_nesting():
    renderer = RunRenderer()
    events = [
        event("step_started", step_id="outer", path=[0], type="loop", params={"times": 2}),
        event("loop_iteration", step_id="outer", path=[0], index=0, total=2),
        event("step_started", step_id="cond", path=[0, 0, 0], type="if"),
        event("branch_taken", step_id="cond", path=[0, 0, 0], condition="x > 1", value=True,
              branch="then"),
        event("step_started", step_id="inner", path=[0, 0, 0, 0, 0], type="click",
              params={"x": 1}),
        event("log", level="warning", message="careful", step_id="inner"),
        event("variable_changed", name="n", old=None, new=1, step_id="inner"),
        event("step_failed", step_id="inner", path=[0, 0, 0, 0, 0],
              error={"code": "image_not_found", "message": "gone"}, screenshot="runs/a.png"),
        event("step_skipped", step_id="other", path=[0, 0, 1], reason="condition"),
        event("loop_iteration", step_id="outer", path=[0], index=1),
        event("step_finished", step_id="outer", path=[0], duration_ms=40, result={"n": 2}),
        event("log", level="info", message="done"),
    ]
    lines = [line for e in events for line in renderer.render(e)]
    assert body(lines) == [
        '▶ outer  loop  {"times": 2}',
        "↻ outer  iteration 1/2",
        "  ▶ cond  if",
        "  ⑂ cond  if x > 1 = true → then",
        '    ▶ inner  click  {"x": 1}',
        "      [warning] careful",
        "      n = 1  (was null)",
        "    ✗ inner  [image_not_found] gone",
        "      screenshot: runs/a.png",
        "  ↷ other  skipped (condition)",
        "↻ outer  iteration 2",
        '✓ outer  40ms  {"n": 2}',
        "[info] done",
    ]


def test_branch_with_predefined_condition():
    (line,) = RunRenderer().render(event(
        "branch_taken", step_id="b", path=[2], condition={"image_visible": "x.png"},
        value=False, branch="else"))
    assert body([line]) == ['⑂ b  if {"image_visible": "x.png"} = false → else']


def test_state_changes():
    renderer = RunRenderer()
    assert renderer.render(event("state_changed", state="running")) == []
    assert body(renderer.render(event("state_changed", state="paused",
                                      reason="breakpoint"))) == ["⏸ paused (breakpoint)"]
    assert body(renderer.render(event("state_changed", state="stopping"))) == ["■ stopping"]


def test_user_interference():
    renderer = RunRenderer()
    stop = renderer.render(event("user_interference", expected=[10, 20], actual=[300, 400],
                                 action="stop"))
    assert body(stop) == ["you moved the mouse: the pointer was at (300, 400), expected "
                          "near (10, 20); stopping the run"]
    pause = renderer.render(event("user_interference", expected=[10, 20], actual=[300, 400],
                                  action="pause"))
    assert body(pause)[0].endswith("; pausing the run")
    assert body(renderer.render(event("state_changed", state="paused",
                                      reason="interference"))) == ["⏸ paused (interference)"]
    assert body(renderer.render(event("keys_released", keys=["button:left"],
                                      reason="interference"))) == [
        "released button:left (interference)"]
    assert renderer.render(event("user_interference", expected="?", action=None))


def test_quiet_and_unknown_events_render_nothing():
    renderer = RunRenderer()
    for type_ in ("input_planned", "input_executed", "input_state", "image_search",
                  "from_the_future"):
        assert renderer.render(event(type_, whatever=1)) == []


def test_malformed_payloads_do_not_crash():
    renderer = RunRenderer()
    assert renderer.render(event("step_failed", step_id="a", error="nope"))
    assert renderer.render(event("loop_iteration", step_id="a", index="x"))
    assert renderer.render(event("run_finished"))
    assert renderer.render(event("keys_released", keys=None))


def test_run_header_and_summaries():
    renderer = RunRenderer()
    assert renderer.render(event("run_started", script_id="s-1", script_name="Save report",
                                 mode="dry", seed=42, backend="x11")) == [
        "run r-1  Save report  (mode dry, seed 42, backend x11)"]
    assert renderer.render(event("run_finished", status="completed", duration_ms=1234)) == [
        "✓ completed in 1.234s  (seed 42)"]
    assert renderer.render(event("run_finished", status="failed", duration_ms=10,
                                 error={"code": "c", "message": "m"})) == [
        "✗ failed after 0.010s  (seed 42): [c] m"]
    assert renderer.render(event("run_finished", status="stopped", reason="user",
                                 duration_ms=5)) == ["■ stopped (user) after 0.005s  (seed 42)"]


def test_run_header_shows_scales_other_than_1():
    assert RunRenderer().render(event(
        "run_started", script_name="X", mode="normal", seed=1, backend="x11",
        display_scale=1.5, display_scale_source="detected", capture_scale=1.25)) == [
        "run r-1  X  (mode normal, seed 1, backend x11, display scale 1.5, "
        "capture scale 1.25)"]
    assert RunRenderer().render(event(
        "run_started", script_name="X", mode="normal", seed=1, display_scale=1.0,
        display_scale_source="default", capture_scale=1.0)) == [
        "run r-1  X  (mode normal, seed 1)"]


def test_run_header_without_a_script_name_shows_the_script_id():
    assert RunRenderer().render(event("run_started", script_id="s1", mode="step", seed=1)) == [
        "run r-1  s1  (mode step, seed 1)"]
    colored = RunRenderer(style=Style(color=True)).render(
        event("run_started", script_name="X", mode="normal", seed=1))
    assert colored == ["\x1b[1mrun r-1\x1b[0m  X  (mode normal, seed 1)"]


def test_seed_from_the_command_line_when_the_engine_omits_it():
    renderer = RunRenderer(seed=9)
    renderer.render(event("run_started", script_id="s", mode="normal"))
    assert renderer.render(event("run_finished", status="completed", duration_ms=0)) == [
        "✓ completed in 0.000s  (seed 9)"]


# -- problems and step types --------------------------------------------------------------


def test_problem_lines():
    error = ErrorInfo(code="parse_error", message="bad indent", line=3)
    assert format_problem("a.yaml", error) == "a.yaml:3: error[parse_error]: bad indent"
    no_line = ErrorInfo(code="script_not_found", message="cannot open")
    assert format_problem("a.yaml", no_line) == "a.yaml: error[script_not_found]: cannot open"
    assert format_warning("a.yaml", {"message": "m", "line": 2}) == "a.yaml:2: warning: m"
    assert format_warning("a.yaml", {"message": "m"}) == "a.yaml: warning: m"


def test_validate_summaries():
    assert validate_summary("a", 0, 0) == "a: valid"
    assert validate_summary("a", 0, 1) == "a: valid (1 warning)"
    assert validate_summary("a", 1, 0) == "a: invalid (1 error)"
    assert validate_summary("a", 2, 3) == "a: invalid (2 errors, 3 warnings)"


def test_step_type_listing():
    lines = format_step_types([{"name": "sleep", "doc": "Wait.\nMore.", "params": [
        {"name": "duration", "type": "duration", "required": True, "doc": "How long."},
        {"name": "jitter", "type": "number", "required": False, "default": 0.1},
        {"name": "note", "type": "string", "required": False},
    ]}])
    assert lines == ["sleep  Wait.", "  duration: duration (required)  How long.",
                     "  jitter: number = 0.1", "  note: string"]


# -- options ------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "value"), [
    ("3", 3), ("-2", -2), ("2.5", 2.5), ("true", True), ("false", False), ("null", None),
    ("bob", "bob"), ("", ""), ("  ", "  "), ("yes", "yes"), ("[1, 2]", "[1, 2]"),
    ("a: b", "a: b"), ("'3'", "3"), ("x=y", "x=y"), ("2026-10-12", "2026-10-12"),
    ("{", "{"),
])
def test_parse_scalar(text, value):
    assert parse_scalar(text) == value
    assert type(parse_scalar(text)) is type(value)


def test_parse_var():
    assert parse_var("n=3") == ("n", 3)
    assert parse_var("url=http://x/?a=b") == ("url", "http://x/?a=b")
    for bad in ("n", "=3", "2x=1", "a b=1"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_var(bad)


def test_engine_command():
    assert engine_command({}) == list(DEFAULT_COMMAND)
    assert engine_command({ENGINE_CMD_ENV: '["e", "-x"]'}) == ["e", "-x"]
    for bad in ("nope", "[]", '"e"', "[1]"):
        with pytest.raises(ValueError, match=ENGINE_CMD_ENV):
            engine_command({ENGINE_CMD_ENV: bad})
