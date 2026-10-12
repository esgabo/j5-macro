"""``j5 run --step`` and ``--break`` with the real CLI, engine and interpreter (no fakes
but the input backend), the debugger commands fed through a piped ``sys.stdin``."""

from __future__ import annotations

import faulthandler
import io
import re
import sys
from pathlib import Path

import pytest

from j5_macro.cli import main as cli_main
from j5_macro.cli.main import main
from j5_macro.cli.options import ENGINE_CMD_ENV

TEST_TIMEOUT = 60
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "hello.yaml"

SCRIPT = """\
version: 1
name: Debug me
params:
  password: {secret: true}
vars:
  n: 0
steps:
  - id: hello
    log: "start ${password}"
  - id: repeat
    loop:
      times: 2
      do:
        - id: bump
          set: {name: n, expr: "n + 1"}
        - id: note
          log: "n is ${n}"
  - id: broken
    on_error: pause
    set: {name: m, expr: "missing + n"}
  - id: done
    log: "done with n=${n}"
"""


@pytest.fixture(autouse=True)
def watchdog():
    faulthandler.dump_traceback_later(TEST_TIMEOUT, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


@pytest.fixture
def session(tmp_path, monkeypatch):
    """``session("n\\nc\\n", *args)``: ``j5 run debug.yaml --fast --seed 7 *args`` with
    those commands on a piped stdin; returns the exit code."""
    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("J5_BACKEND", "fake")
    monkeypatch.setenv("J5_TEST_PASSWORD", "hunter2")
    monkeypatch.delenv(ENGINE_CMD_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "debug.yaml").write_text(SCRIPT, encoding="utf-8", newline="\n")
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: False)

    def run(commands: str, *args: str, script: str = "debug.yaml") -> int:
        monkeypatch.setattr(sys, "stdin", io.StringIO(commands))
        params = ["--param-env", "password=J5_TEST_PASSWORD"] if script == "debug.yaml" else []
        return main(["run", script, "--fast", "--seed", "7", *params, *args])

    return run


def after(out: str, command: str) -> str:
    """The output that follows the prompt where ``command`` was entered."""
    return out.split(f"(j5) {command}\n", 1)[1]


def test_a_debugging_session(session, capsys):
    commands = ("w\nn\ns\nw\nvars\np n + 10\nset n = 40\nl\no\nb done\nc\n"
                "set missing = 1\nc\nvars\nc\n")
    assert session(commands, "--step") == 0
    out = capsys.readouterr().out
    assert "hunter2" not in out
    assert re.search(r"^run r-\d{8}-\d{6}-[0-9a-f]{4}  Debug me  "
                     r"\(mode step, seed 7, backend fake\)$", out, re.MULTILINE)
    assert "⏸ paused (step)\n-> hello  log  (debug.yaml:8)\n(j5) w\n" in out
    assert after(out, "w").startswith(
        "step hello  log  path [0]  debug.yaml:8\n"
        "    6    n: 0\n"
        "    7  steps:\n"
        "->  8    - id: hello\n"
        '    9      log: "start ${password}"\n')
    # n runs hello and pauses before the loop; s enters it.
    assert "[info] start ***" in after(out, "n")
    assert "-> repeat  loop  (debug.yaml:10)\n(j5) s\n" in out
    assert re.search(r"-> bump  set  \(debug\.yaml:14(, iterations \[0\])?\)\n\(j5\) w\n", out)
    where_bump = out.split("(j5) w\n")[2]
    assert re.match(r"step bump  set  path \[1, 0, 0\](  iterations \[0\])?  debug\.yaml:14\n",
                    where_bump)
    assert "\n-> 14          - id: bump\n" in where_bump
    assert after(out, "vars").startswith('n = 0\npassword = "***"\n')
    assert after(out, "p n + 10").startswith("10\n")
    assert "n = 40  (was 0)" in after(out, "set n = 40")
    assert after(out, "l").startswith(
        "    hello   log   (line 8)\n"
        "    repeat  loop  (line 10)\n"
        "->    bump  set   (line 14)\n"
        "      note  log   (line 16)\n"
        "    broken  set   (line 18)\n"
        "    done    log   (line 21)\n")
    # o finishes both iterations and pauses before broken.
    out_of_loop = after(out, "o")
    assert "n = 41  (was 40)" in out_of_loop
    assert "n = 42  (was 41)" in out_of_loop
    assert "-> broken  set  (debug.yaml:18)\n(j5) b done\nbreakpoints: done\n" in out
    # c: broken fails and pauses; set the missing variable, then c retries it.
    failed = after(out, "c")
    assert "✗ broken  [expression_error] unknown variable 'missing'" in failed
    assert "step 'broken' failed: c retries it, n skips it\n(j5) set missing = 1\n" in failed
    retried = after(out, "set missing = 1")
    assert "m = 43  (was null)" in retried
    assert "breakpoint done" in retried
    assert "-> done  log  (debug.yaml:21)\n(j5) vars\n" in retried
    last_vars = out.split("(j5) vars\n")[2]
    assert last_vars.startswith('n = 42\npassword = "***"\n')
    assert "m = 43\n" in last_vars
    assert out.splitlines()[-1].startswith("✓ completed in ")


def test_next_skips_a_failed_step(session, capsys):
    assert session("c\nn\nc\n", "--break", "broken") == 0
    out = capsys.readouterr().out
    assert "  Debug me  (mode normal, seed 7" in out
    assert "step 'broken' failed: c retries it, n skips it\n(j5) n\n" in out
    assert "↷ broken  skipped (user)" in out
    assert "-> done  log  (debug.yaml:21)\n(j5) c\n" in out
    assert "[info] done with n=2" in out


def test_quit_stops_the_run(session, capsys):
    assert session("n\nq\n", "--step") == 3
    out = capsys.readouterr().out
    assert "j5: stopping the run" in out
    assert out.splitlines()[-1].startswith("■ stopped (user) after")


def test_end_of_input_continues_to_the_end(session, capsys):
    assert session("", "--step", script=str(EXAMPLE)) == 0
    out = capsys.readouterr().out
    assert "-> start  log  (" in out
    assert "j5: end of debugger commands; continuing the run to the end" in out
    assert out.splitlines()[-1].startswith("✓ completed in ")


def test_unknown_break_id_is_an_error_before_running(session, tmp_path, capsys):
    assert session("", "--break", "nope") == 2
    assert "j5: --break: debug.yaml has no step with id 'nope' (its step ids: hello, repeat, " \
           "bump, note, broken, done)" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def test_secret_values_stay_masked_in_print(session, capsys):
    assert session("p password\np 'x' + password\nc\nc\nn\nc\n", "--step") == 0
    out = capsys.readouterr().out
    assert "hunter2" not in out
    assert after(out, "p password").startswith('"***"\n')
    assert after(out, "p 'x' + password").startswith('"x***"\n')
