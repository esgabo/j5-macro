"""The ``exec`` step with real programs (docs/contract.md, "Running programs"): the
programs are this Python (``sys.executable -c``), so the tests run on every platform."""

from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from runtime_helpers import REGISTRY, Background, load, run

from j5_macro import processes
from j5_macro.script.loader import load_script


def py(code: str, *args: str) -> str:
    """A command list running ``code`` with this Python, as YAML (JSON is YAML)."""
    return json.dumps([sys.executable, "-c", textwrap.dedent(code), *args])


def exec_script(command: str, **params: Any) -> str:
    options = "".join(f", {name}: {json.dumps(value)}" for name, value in params.items())
    return f"""
        steps:
          - id: prog
            exec: {{command: {command}{options}}}
    """


def result_of(rec: Any, step_id: str = "prog") -> dict[str, Any]:
    (finished,) = rec.of("step_finished", step_id=step_id)
    return dict(finished["result"])


def error_of(rec: Any, step_id: str = "prog") -> dict[str, Any]:
    (failed,) = rec.of("step_failed", step_id=step_id)
    return dict(failed["error"])


def wait_for_file(path: Path, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} was never written"
        time.sleep(0.02)
    time.sleep(0.05)  # let the writer finish
    return path.read_text(encoding="utf-8")


def test_waits_and_reports_the_output():
    command = py("import sys; print('out'); sys.stderr.write('err')")
    outcome, rec = run(exec_script(command, wait=True), allow_exec=True)
    assert outcome.status == "completed"
    assert result_of(rec) == {"exit_code": 0, "stdout": "out\n", "stderr": "err"}
    (started,) = rec.of("step_started", step_id="prog")
    assert started["params"] == {"command": json.loads(command), "shell": False,
                                 "wait": True, "check": True}


def test_a_non_zero_exit_fails_with_check():
    command = py("import sys; sys.stderr.write('bad things\\n'); sys.exit(3)")
    outcome, rec = run(exec_script(command, wait=True), allow_exec=True)
    assert outcome.status == "failed"
    error = error_of(rec)
    assert error["code"] == "exec_failed"
    assert error["message"].endswith("exited with code 3: bad things")
    details = error["details"]
    assert (details["reason"], details["exit_code"]) == ("exit_code", 3)
    assert details["command"] == json.loads(command)
    assert details["stderr"] == "bad things\n"


def test_a_non_zero_exit_is_a_result_without_check():
    command = py("import sys; sys.exit(3)")
    outcome, rec = run(exec_script(command, wait=True, check=False), allow_exec=True)
    assert outcome.status == "completed"
    assert result_of(rec)["exit_code"] == 3


def test_without_allow_exec_nothing_runs(tmp_path):
    marker = tmp_path / "ran"
    command = py("import sys; open(sys.argv[1], 'w').close()", str(marker))
    outcome, rec = run(exec_script(command, wait=True))
    assert outcome.status == "failed"
    error = error_of(rec)
    assert error["code"] == "capability_missing"
    assert error["details"] == {"capability": "exec", "attempt": 1}
    assert "--allow-exec" in error["message"]
    assert not marker.exists()


@pytest.mark.parametrize("allow", [True, False])
def test_dry_runs_only_describe(tmp_path, allow):
    marker = tmp_path / "ran"
    command = py("import sys; open(sys.argv[1], 'w').close()", str(marker))
    outcome, rec = run(exec_script(command, wait=True, timeout="5s", cwd=str(tmp_path)),
                       mode="dry", allow_exec=allow)
    assert outcome.status == "completed"
    result = result_of(rec)
    assert result["dry_run"] is True
    assert result["description"].startswith("would run `")
    assert str(tmp_path) in result["description"]
    assert result["description"].endswith("and wait up to 5s for it to exit")
    warnings = [log for log in rec.of("log") if log["level"] == "warning"]
    assert bool(warnings) is not allow
    time.sleep(0.2)
    assert not marker.exists()


def test_detached_programs_run_on_their_own(tmp_path):
    marker = tmp_path / "pid"
    command = py("""
        import os, sys, time
        time.sleep(1.5)
        with open(sys.argv[1], "w") as f:
            f.write(str(os.getpid()))
    """, str(marker))
    started = time.monotonic()
    outcome, rec = run(exec_script(command), allow_exec=True)
    assert outcome.status == "completed"
    assert time.monotonic() - started < 1.5  # it did not wait
    pid = result_of(rec)["pid"]
    assert wait_for_file(marker) == str(pid)


def test_a_detached_program_that_cannot_start_fails():
    outcome, rec = run(exec_script(json.dumps(["j5-no-such-program-x"])), allow_exec=True)
    assert outcome.status == "failed"
    error = error_of(rec)
    assert error["code"] == "exec_failed"
    assert error["details"]["reason"] == "start"
    assert error["details"]["command"] == ["j5-no-such-program-x"]


def test_a_missing_program_fails_with_exec_failed():
    outcome, rec = run(exec_script(json.dumps(["j5-no-such-program-x", "a"]), wait=True),
                       allow_exec=True)
    assert outcome.status == "failed"
    error = error_of(rec)
    assert (error["code"], error["details"]["reason"]) == ("exec_failed", "start")
    assert error["details"]["command"] == ["j5-no-such-program-x", "a"]


def test_timeout_kills_the_program():
    command = py("import time; time.sleep(30)")
    started = time.monotonic()
    outcome, rec = run(exec_script(command, wait=True, timeout="300ms"), allow_exec=True)
    assert time.monotonic() - started < 10
    assert outcome.status == "failed"
    error = error_of(rec)
    assert error["code"] == "exec_failed"
    assert (error["details"]["reason"], error["details"]["timeout"]) == ("timeout", 0.3)


def test_the_common_timeout_replaces_the_step_timeout():
    script = f"""
        steps:
          - id: prog
            timeout: 200ms
            exec: {{command: {py("import time; time.sleep(30)")}, wait: true,
                    timeout: 1h}}
    """
    outcome, rec = run(script, allow_exec=True)
    assert outcome.status == "failed"
    assert error_of(rec)["details"]["timeout"] == 0.2


# A program that starts a child and waits; the child writes ``survived`` if it is still
# alive two seconds later.
FAMILY = """
    import subprocess, sys, time
    child = "import sys, time; time.sleep(2); open(sys.argv[1], 'w').close()"
    subprocess.Popen([sys.executable, "-c", child, sys.argv[1]])
    open(sys.argv[2], "w").close()
    time.sleep(30)
"""


def test_stop_kills_the_program_and_its_children(tmp_path):
    survived, ready = tmp_path / "survived", tmp_path / "ready"
    background = Background(exec_script(py(FAMILY, str(survived), str(ready)), wait=True),
                            allow_exec=True)
    wait_for_file(ready)
    background.controller.stop()
    outcome = background.join(5.0)
    assert outcome.status == "stopped"
    time.sleep(2.5)
    assert not survived.exists()


def test_timeout_kills_the_children_too(tmp_path):
    survived, ready = tmp_path / "survived", tmp_path / "ready"
    outcome, _ = run(exec_script(py(FAMILY, str(survived), str(ready)), wait=True,
                                 timeout="1s"), allow_exec=True)
    assert outcome.status == "failed"
    assert ready.exists()
    time.sleep(2.5)
    assert not survived.exists()


def test_pause_lets_the_program_finish_and_pauses_after_the_step(tmp_path):
    ready = tmp_path / "ready"
    command = py("""
        import sys, time
        open(sys.argv[1], "w").close()
        time.sleep(0.5)
        print("done")
    """, str(ready))
    script = f"""
        steps:
          - id: prog
            exec: {{command: {command}, wait: true}}
          - id: after
            log: next
    """
    background = Background(script, allow_exec=True, scale=1.0)
    wait_for_file(ready)
    background.controller.pause()
    background.recorder.wait_paused()
    summary = background.recorder.summary()
    assert summary[-2:] == ["step_finished:prog", "state_changed:paused/user"]
    assert result_of(background.recorder)["stdout"] == "done\n"
    background.controller.resume()
    assert background.join().status == "completed"


def test_env_adds_variables():
    command = py("import os; print(os.environ['J5_TEST_A'], os.environ['J5_TEST_B'], "
                 "'PATH' in os.environ)")
    outcome, rec = run(exec_script(command, wait=True,
                                   env={"J5_TEST_A": 42, "J5_TEST_B": True}),
                       allow_exec=True)
    assert outcome.status == "completed"
    assert result_of(rec)["stdout"] == "42 true True\n"


def test_cwd_defaults_to_the_script_directory(tmp_path):
    (tmp_path / "sub").mkdir()
    command = py("import os; print(os.getcwd())")
    source = textwrap.dedent(f"""
        steps:
          - id: here
            exec: {{command: {command}, wait: true}}
          - id: below
            exec: {{command: {command}, wait: true, cwd: sub}}
    """)
    result = load_script(source, lookup=REGISTRY.spec, check=REGISTRY.check_params,
                         source_path=str(tmp_path / "script.yaml"))
    assert result.script is not None
    outcome, rec = run(result.script, allow_exec=True)
    assert outcome.status == "completed"
    here = result_of(rec, "here")["stdout"].strip()
    below = result_of(rec, "below")["stdout"].strip()
    assert os.path.samefile(here, tmp_path)
    assert os.path.samefile(below, tmp_path / "sub")


def test_output_is_capped():
    command = py(f"import sys; sys.stdout.write('x' * {processes.MAX_OUTPUT + 1000})")
    outcome, rec = run(exec_script(command, wait=True), allow_exec=True)
    assert outcome.status == "completed"
    result = result_of(rec)
    assert result["stdout"] == "x" * processes.MAX_OUTPUT
    assert result["truncated"] is True


def test_a_command_string_is_split_without_a_shell():
    # On Windows the string goes to the program as it is; Python splits it the same way.
    command = f'"{sys.executable}" -c "import sys; print(sys.argv[1:])" "a b" c'
    outcome, rec = run(exec_script(json.dumps(command), wait=True), allow_exec=True)
    assert outcome.status == "completed"
    assert result_of(rec)["stdout"] == "['a b', 'c']\n"


def test_shell_runs_the_string_through_the_shell():
    outcome, rec = run(exec_script(json.dumps("echo one&& echo two"), wait=True,
                                   shell=True), allow_exec=True)
    assert outcome.status == "completed"
    lines = [line.strip() for line in result_of(rec)["stdout"].splitlines()]
    assert lines == ["one", "two"]


def test_interpolated_arguments():
    script = f"""
        vars: {{word: hello}}
        steps:
          - id: prog
            exec: {{command: {py("import sys; print(sys.argv[1])", "${word}!")}, wait: true}}
    """
    outcome, rec = run(script, allow_exec=True)
    assert outcome.status == "completed"
    assert result_of(rec)["stdout"] == "hello!\n"


# --- validation -------------------------------------------------------------------------


def problems(step: str) -> list[str]:
    source = textwrap.dedent(f"""
        steps:
          - id: prog
            exec: {step}
    """)
    result = load_script(source, lookup=REGISTRY.spec, check=REGISTRY.check_params)
    return [error.message for error in result.errors]


def test_the_short_form_is_the_command():
    script = load("""
        steps:
          - id: prog
            exec: mousepad notes.txt
    """)
    assert script.steps[0].params == {"command": "mousepad notes.txt", "shell": False,
                                      "wait": False, "check": True}


@pytest.mark.parametrize(("step", "message"), [
    ("{command: []}", "the command is empty"),
    ("{command: ''}", "the command is empty"),
    ("{command: {a: 1}}", "expected a list of strings or a command line string"),
    ("{command: [ls, [a]]}", "every item of a command list must be a string or a number"),
    ("{command: [ls, -l], shell: true}", "with shell: true the command must be a string"),
    ("{command: ls, timeout: 5s}", "only applies with wait: true"),
    ("{command: ls, cwd: ''}", "must not be empty"),
    ("{command: ls, env: {A: [1]}}", "the value of A must be a string, a number or a "
                                     "boolean"),
    ("{command: ls, env: {'A=B': x}}", "invalid environment variable name 'A=B'"),
])
def test_invalid_parameters(step, message):
    found = problems(step)
    assert len(found) == 1 and message in found[0], found


@pytest.mark.skipif(sys.platform == "win32", reason="Windows passes the string unchanged")
def test_an_unclosed_quote_is_invalid():
    (message,) = problems("{command: \"echo 'oops\"}")
    assert "cannot split the command line" in message


def test_valid_forms():
    assert problems("{command: [sleep, 5], wait: true, timeout: 10s, cwd: /tmp, "
                    "env: {A: x, B: 2}}") == []
    assert problems("{command: 'echo hi | wc -c', shell: true, wait: true}") == []
    assert problems("{command: '${cmd}'}") == []
