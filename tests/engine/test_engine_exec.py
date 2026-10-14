"""``exec`` through the engine with the real interpreter: the run's ``allow_exec`` opt-in
and the masking of secrets in what the step reports."""

from __future__ import annotations

import json
import sys

from engine_harness import Engine

from j5_macro.runtime.api import RunOptions

SECRET = "s3cr#t pass"
COMMAND = json.dumps([sys.executable, "-c",
                      "import os, sys; print(sys.argv[1], os.environ['TOKEN'])", "${token}"])
SCRIPT = f"""\
params:
  token: {{secret: true}}
steps:
  - id: prog
    exec: {{command: {COMMAND}, wait: true, env: {{TOKEN: "${{token}}"}}}}
"""


def run_events(**payload: object) -> dict[str, dict[str, object]]:
    engine = Engine()
    script_id = engine.load_source(SCRIPT)
    run_id = engine.start(script_id, params={"token": SECRET}, **payload)
    engine.wait_finished(run_id)
    events = engine.run_events(run_id)
    engine.close()
    assert SECRET not in repr(events)
    return {e["type"]: e["payload"] for e in events}


def test_allow_exec_runs_the_program_and_masks_secrets() -> None:
    events = run_events(allow_exec=True)
    assert events["run_finished"]["status"] == "completed"
    started = events["step_started"]["params"]
    assert isinstance(started, dict)
    assert started["command"][-1] == "***"
    assert started["env"] == {"TOKEN": "***"}
    assert events["step_finished"]["result"] == {"exit_code": 0, "stdout": "*** ***\n",
                                                 "stderr": ""}


def test_without_allow_exec_the_step_fails() -> None:
    events = run_events()
    assert events["run_finished"]["status"] == "failed"
    error = events["step_failed"]["error"]
    assert isinstance(error, dict)
    assert error["code"] == "capability_missing"
    assert error["details"] == {"capability": "exec", "attempt": 1}


TOKEN = "tok-9f8e7d"
# Built by the program, so the command itself does not contain the token.
KEEP = json.dumps([sys.executable, "-c", "print('tok-' + '9f8e7d')"])
KEPT_SECRET = f"""\
steps:
  - id: get_token
    exec: {{command: {KEEP}, wait: true}}
    as: {{name: token, secret: true}}
  - id: show
    log: "token ${{token.stdout}}stripped ${{strip(token.stdout)}}"
  - id: broken
    set: {{name: x, expr: "token.stdout + 1"}}
"""


def test_exec_output_kept_as_a_secret_is_masked(tmp_path) -> None:
    engine = Engine(runs_dir=tmp_path)
    run_id = engine.start(engine.load_source(KEPT_SECRET), allow_exec=True)
    finished = engine.wait_finished(run_id)
    events = engine.run_events(run_id)
    engine.close()
    assert TOKEN not in repr(events)
    payloads = [e["payload"] for e in events]
    (result,) = [p["result"] for p in payloads
                 if p.get("step_id") == "get_token" and "result" in p]
    assert result == {"exit_code": 0, "stdout": "***", "stderr": ""}
    (log,) = [p["message"] for p in payloads if p.get("step_id") == "show" and "level" in p]
    assert log == "token ***stripped ***"
    assert finished["payload"]["status"] == "failed"  # the broken step: masked too
    files = list(tmp_path.rglob("*.json*"))
    assert files
    for path in files:
        assert TOKEN not in path.read_text(encoding="utf-8"), path


def test_allow_exec_defaults_to_false() -> None:
    assert RunOptions().allow_exec is False
