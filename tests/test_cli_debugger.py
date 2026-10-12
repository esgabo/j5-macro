"""The ``j5 run --step`` debugger: pure parsing and formatting, then whole sessions of
the real ``j5`` main against tests/fakes/fake_cli_engine.py (scenario ``debug``), with
the commands fed through ``sys.stdin``."""

from __future__ import annotations

import asyncio
import faulthandler
import io
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from j5_macro.cli import main as cli_main
from j5_macro.cli.debugger import (
    CommandSyntaxError,
    DebugCommand,
    LineSource,
    StepInfo,
    flatten_steps,
    format_breakpoints,
    format_list,
    format_location,
    format_variables,
    format_where,
    parse_command,
)
from j5_macro.cli.main import main
from j5_macro.cli.options import ENGINE_CMD_ENV

FAKE = Path(__file__).parent / "fakes" / "fake_cli_engine.py"
TEST_TIMEOUT = 60

SOURCE = """\
steps:
  - id: open_menu
    click: {x: 10, y: 20}
  - id: repeat
    loop:
      times: 2
      do:
        - id: bump
          set: {name: count, expr: "count + 1"}
  - id: save
    break: true
    key: ctrl+s
"""

STEP_TREE = [  # as the fake engine's load reports it (DEBUG_STEPS there)
    {"id": "open_menu", "type": "click", "path": [0], "line": 2},
    {"id": "repeat", "type": "loop", "path": [1], "line": 4,
     "blocks": {"do": [{"id": "bump", "type": "set", "path": [1, 0, 0], "line": 7}]}},
    {"id": "save", "type": "key", "path": [2], "line": 9, "break": True},
]


# -- parsing ------------------------------------------------------------------------------


@pytest.mark.parametrize(("line", "name"), [
    ("n", "next"), ("next", "next"), ("s", "step"), ("step", "step"), ("o", "out"),
    ("out", "out"), ("finish", "out"), ("c", "continue"), ("cont", "continue"),
    ("continue", "continue"), ("v", "vars"), ("vars", "vars"), ("w", "where"),
    ("where", "where"), ("l", "list"), ("list", "list"), ("h", "help"), ("help", "help"),
    ("?", "help"), ("q", "quit"), ("quit", "quit"), ("exit", "quit"), ("  N  ", "next"),
    ("b", "break"), ("break", "break"), ("clear", "clear"),
])
def test_parse_command_names_and_aliases(line, name):
    assert parse_command(line) == DebugCommand(name)


def test_parse_command_arguments():
    assert parse_command("") is None
    assert parse_command("   ") is None
    assert parse_command("p attempts + 1") == DebugCommand("print", ("attempts + 1",))
    assert parse_command("print  'a b' ") == DebugCommand("print", ("'a b'",))
    assert parse_command("set n = n + 1") == DebugCommand("set", ("n", "n + 1"))
    assert parse_command("set name=\"bob\"") == DebugCommand("set", ("name", '"bob"'))
    assert parse_command("set xs = [1, 2]") == DebugCommand("set", ("xs", "[1, 2]"))
    assert parse_command("b a b,c") == DebugCommand("break", ("a", "b", "c"))
    assert parse_command("clear a") == DebugCommand("clear", ("a",))


@pytest.mark.parametrize(("line", "message"), [
    ("frobnicate", "unknown command 'frobnicate'"),
    ("p", "print what?"),
    ("set", "usage: set NAME = EXPRESSION"),
    ("set x", "usage: set NAME = EXPRESSION"),
    ("set 1x = 2", "usage: set NAME = EXPRESSION"),
    ("set x == 2", "usage: set NAME = EXPRESSION"),
    ("set x =", "usage: set NAME = EXPRESSION"),
    ("n 3", "'n' takes no argument"),
    ("vars x", "'vars' takes no argument"),
])
def test_parse_command_errors(line, message):
    with pytest.raises(CommandSyntaxError, match=message):
        parse_command(line)


# -- formatting ---------------------------------------------------------------------------


def steps() -> list[StepInfo]:
    return flatten_steps(STEP_TREE)


def test_flatten_steps():
    flat = steps()
    assert [(s.id, s.type, s.path, s.line, s.depth, s.breakpoint) for s in flat] == [
        ("open_menu", "click", (0,), 2, 0, False),
        ("repeat", "loop", (1,), 4, 0, False),
        ("bump", "set", (1, 0, 0), 7, 1, False),
        ("save", "key", (2,), 9, 0, True),
    ]
    assert flatten_steps(None) == []
    assert flatten_steps([{"type": "no id"}, "junk"]) == []


def test_format_location():
    state = {"step_id": "bump", "line": 7, "iterations": [1]}
    assert format_location(state, steps()[2], "a.yaml") == (
        "-> bump  set  (a.yaml:7, iterations [1])")
    assert format_location({"step_id": "x"}, None, "a.yaml") == "-> x"
    assert format_location({"step_id": "p", "line": 3}, None, "a.yaml", after=True) == (
        "after p  (a.yaml:3)")
    assert format_location({}, None, "a.yaml") == "-> (no current step)"


def test_format_where_shows_the_source_with_a_marker():
    source = SOURCE.splitlines()
    state = {"step_id": "bump", "path": [1, 0, 0], "iterations": [0], "line": 8}
    assert format_where(state, steps()[2], "a.yaml", source) == [
        "step bump  set  path [1, 0, 0]  iterations [0]  a.yaml:8",
        "    6        times: 2",
        "    7        do:",
        "->  8          - id: bump",
        "    9            set: {name: count, expr: \"count + 1\"}",
        "   10    - id: save",
        "   11      break: true",
    ]
    # First line, no iterations, no source.
    assert format_where({"step_id": "open_menu", "path": [0], "line": 1}, None, "a.yaml",
                        source)[:2] == ["step open_menu  path [0]  a.yaml:1", "-> 1  steps:"]
    assert format_where({"step_id": "x", "line": 3}, None, "a.yaml", None) == [
        "step x  a.yaml:3"]
    assert format_where({"state": "running"}, None, "a.yaml", source) == [
        "no current step (state running)"]


def test_format_list_marks_the_current_step_and_breakpoints():
    assert format_list(steps(), "bump", frozenset({"open_menu"})) == [
        "  * open_menu  click  (line 2)",
        "    repeat     loop   (line 4)",
        "->    bump     set    (line 7)",
        "  * save       key    (line 9)",
    ]
    assert format_list([], None) == ["the script has no steps"]


def test_format_list_shows_a_window_around_the_current_step():
    many = [StepInfo(id=f"s{i}", type="log", path=(i,), line=i + 1, breakpoint=False)
            for i in range(20)]
    lines = format_list(many, "s10")
    assert lines[0].startswith("    s7 ")
    assert lines[3].startswith("->  s10")
    assert lines[-1].startswith("    s16")


def test_format_variables_and_breakpoints():
    assert format_variables({"n": 1, "name": "bob", "pw": "***"}) == [
        "n = 1", 'name = "bob"', 'pw = "***"']
    assert format_variables({}) == ["no variables"]
    assert format_breakpoints([], steps()) == ["breakpoint save  (break: true in the script)"]
    assert format_breakpoints(["bump", "open_menu"], steps()) == [
        "breakpoint bump", "breakpoint open_menu",
        "breakpoint save  (break: true in the script)"]
    assert format_breakpoints([], steps()[:3]) == ["no breakpoints"]


def test_line_source_reads_lines_then_none():
    async def read() -> list[str | None]:
        source = LineSource(io.StringIO("a\r\nb\n\nc"))
        return [await source.get() for _ in range(6)]

    assert asyncio.run(read()) == ["a", "b", "", "c", None, None]


# -- sessions against the fake engine -----------------------------------------------------


@pytest.fixture(autouse=True)
def watchdog():
    faulthandler.dump_traceback_later(TEST_TIMEOUT, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


@pytest.fixture
def record(tmp_path):
    return tmp_path / "commands.jsonl"


@pytest.fixture
def session(monkeypatch, tmp_path, record):
    """``session("n\\nc\\n", *args)`` runs ``j5 run script.yaml *args`` with those commands
    on a piped stdin against the fake engine; returns the exit code."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "script.yaml").write_text(SOURCE, encoding="utf-8", newline="\n")
    monkeypatch.setenv(ENGINE_CMD_ENV,
                       json.dumps([sys.executable, str(FAKE), "debug", str(record)]))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: False)

    def run(commands: str, *args: str) -> int:
        monkeypatch.setattr(sys, "stdin", io.StringIO(commands))
        return main(["run", "script.yaml", *args])

    return run


def sent(record: Path, type_: str | None = None) -> list[dict]:
    lines = record.read_text(encoding="utf-8").splitlines() if record.exists() else []
    commands = [json.loads(line) for line in lines]
    return [c["payload"] if type_ else c for c in commands
            if type_ is None or c["type"] == type_]


def controls(record: Path) -> list[str]:
    stepping = {"step_over", "step_into", "step_out", "resume", "stop"}
    return [c["type"] for c in sent(record) if c["type"] in stepping]


def test_stepping_commands(session, record, capsys):
    assert session("n\ns\no\nc\n", "--step") == 0
    assert controls(record) == ["step_over", "step_into", "step_out", "resume"]
    assert sent(record, "run")[0]["mode"] == "step"
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "run r-1  Save report  (mode step, seed 1234, backend fake)"
    assert "-> open_menu  click  (script.yaml:2)\n(j5) n\n" in out
    assert "-> bump  set  (script.yaml:7, iterations [0])" in out
    assert out.splitlines()[-1] == "✓ completed in 0.060s  (seed 1234)"


def test_an_empty_line_repeats_the_last_stepping_command(session, record):
    assert session("\ns\n\n\n", "--step") == 0
    # The first empty line has nothing to repeat; the EOF continues the run.
    assert controls(record) == ["step_into", "step_into", "step_into", "resume"]


def test_print_vars_and_set(session, record, capsys):
    commands = "p count\np [1, \"a\"]\np nope\nvars\nset count = 5\nset n = count\nvars\nc\n"
    assert session(commands, "--step") == 0
    out = capsys.readouterr().out
    assert "(j5) p count\n1\n" in out
    assert '(j5) p [1, "a"]\n[1, "a"]\n' in out
    assert "evaluate: [expression_error] unknown variable 'nope'" in out
    assert '(j5) vars\ncount = 1\npassword = "***"\n' in out
    # The variable_changed event, rendered before the next prompt.
    assert out.split("(j5) set count = 5\n")[1].startswith("  ")
    assert out.split("(j5) set count = 5\n")[1].split("\n")[0].endswith("count = 5  (was 1)")
    assert out.split("(j5) set n = count\n")[1].split("\n")[1] == "(j5) vars"
    assert 'count = 5\npassword = "***"\nn = 5\n' in out
    assert [e["expression"] for e in sent(record, "evaluate")] == [
        "count", '[1, "a"]', "nope", "5", "count"]
    assert sent(record, "set_variable") == [
        {"run_id": "r-1", "name": "count", "value": 5},
        {"run_id": "r-1", "name": "n", "value": 5}]


def test_set_with_a_bad_expression_sets_nothing(session, record, capsys):
    assert session("set count = nope\nc\n", "--step") == 0
    assert "evaluate: [expression_error]" in capsys.readouterr().out
    assert sent(record, "set_variable") == []


def test_break_clear_and_list(session, record, capsys):
    commands = "b\nb bump\nb bump open_menu\nb nosuch\nl\nclear nosuch\nclear bump\nclear\nc\n"
    assert session(commands, "--step") == 0
    out = capsys.readouterr().out
    assert "(j5) b\nbreakpoint save  (break: true in the script)\n" in out
    assert "breakpoints: bump\n" in out
    assert "breakpoints: bump, open_menu\n" in out
    assert "no step with id 'nosuch' (l lists the steps)" in out
    assert ("(j5) l\n"
            "->* open_menu  click  (line 2)\n"
            "    repeat     loop   (line 4)\n"
            "  *   bump     set    (line 7)\n"
            "  * save       key    (line 9)\n") in out
    assert "no breakpoint of yours on 'nosuch'" in out
    assert "breakpoints: open_menu\n" in out
    assert "no breakpoints of yours left" in out
    assert [p["step_ids"] for p in sent(record, "set_breakpoints")] == [
        ["bump"], ["bump", "open_menu"], ["open_menu"], []]
    assert all(p["script_id"] == "s-1" for p in sent(record, "set_breakpoints"))


def test_where_shows_the_script_source(session, capsys):
    assert session("s\ns\nw\nc\n", "--step") == 0
    out = capsys.readouterr().out
    assert ("(j5) w\n"
            "step bump  set  path [1, 0, 0]  iterations [0]  script.yaml:7\n"
            "    5      loop:\n"
            "    6        times: 2\n"
            "->  7        do:\n") in out


def test_break_from_the_command_line_without_step(session, record, capsys):
    assert session("vars\nc\n", "--break", "bump", "--break", "bump") == 0
    commands = [c["type"] for c in sent(record)]
    assert commands.index("set_breakpoints") < commands.index("run")
    assert sent(record, "set_breakpoints") == [{"script_id": "s-1", "step_ids": ["bump"]}]
    assert sent(record, "run")[0]["mode"] == "normal"
    out = capsys.readouterr().out
    assert "breakpoint bump" in out
    assert "-> bump  set  (script.yaml:7, iterations [0])\n(j5) vars\ncount = 1" in out
    assert controls(record) == ["resume"]


def test_unknown_break_ids_fail_before_running(session, record, capsys):
    assert session("", "--break", "bump", "--break", "nope", "--step") == 2
    assert sent(record, "run") == []
    assert sent(record, "set_breakpoints") == []
    assert ("j5: --break: script.yaml has no step with id 'nope' (its step ids: open_menu, "
            "repeat, bump, save)") in capsys.readouterr().err


def test_quit_stops_the_run(session, record, capsys):
    assert session("n\nq\n", "--step") == 3
    assert sent(record, "stop") == [{"run_id": "r-1", "reason": "user"}]
    out = capsys.readouterr().out
    assert "j5: stopping the run" in out
    assert out.splitlines()[-1].startswith("■ stopped (user)")


def test_end_of_input_continues_the_run(session, record, capsys):
    assert session("n\n", "--step", "--break", "save") == 0
    out = capsys.readouterr().out
    assert "j5: end of debugger commands; continuing the run to the end" in out
    # The breakpoint after the end of input does not prompt.
    assert "j5: the run paused (breakpoint); continuing (end of debugger commands)" in out
    assert controls(record) == ["step_over", "resume", "resume"]


def test_unknown_commands_and_help(session, capsys):
    assert session("frob\nn 2\nh\nc\n", "--step") == 0
    out = capsys.readouterr().out
    assert "unknown command 'frob' (h for help)" in out
    assert "'n' takes no argument" in out
    assert "  n, next " in out
    assert "c retries the step and n skips it" in out


def test_json_keeps_stdout_for_events(session, record, capsys):
    assert session("n\nset count = 2\nc\n", "--step", "--json") == 0
    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()]
    assert events[0]["type"] == "run_started"
    assert events[0]["payload"]["script_name"] == "Save report"
    assert events[-1]["type"] == "run_finished"
    assert "-> open_menu  click  (script.yaml:2)" in captured.err
    assert "(j5) n\n" in captured.err
    assert "count = 2\n" in captured.err  # said by the debugger, not by an event line


def test_ctrl_c_at_the_prompt_stops_the_run(session, record, monkeypatch, capsys):
    read_fd, write_fd = os.pipe()
    stdin = os.fdopen(read_fd, "r", encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stdin)

    def press() -> None:
        deadline = time.monotonic() + 30
        while not (record.exists() and sent(record, "get_state")):
            assert time.monotonic() < deadline, "the debugger never asked for the state"
            time.sleep(0.02)
        time.sleep(0.2)
        signal.raise_signal(signal.SIGINT)

    presser = threading.Thread(target=press, daemon=True)
    presser.start()
    try:
        assert main(["run", "script.yaml", "--step"]) == 130
    finally:
        os.close(write_fd)  # lets the reader thread see the end of input
    presser.join(5)
    assert sent(record, "stop") == [{"run_id": "r-1", "reason": "user"}]
    captured = capsys.readouterr()
    assert "stopping the run (Ctrl+C again to quit now)" in captured.err
    assert "(j5) \n" in captured.out
    assert captured.out.splitlines()[-1].startswith("■ stopped (user)")


def test_step_and_dry_run_are_exclusive(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["run", "x.yaml", "--step", "--dry-run"])
    assert exc.value.code == 2
    assert "not allowed with" in capsys.readouterr().err
