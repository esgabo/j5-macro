"""The real ``j5`` CLI against the real engine and interpreter (no fakes)."""

import json
import re
from pathlib import Path

import pytest

from j5_macro.cli.main import main
from j5_macro.cli.options import ENGINE_CMD_ENV

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "hello.yaml"


@pytest.fixture(autouse=True)
def isolated_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv(ENGINE_CMD_ENV, raising=False)
    return tmp_path / "runs"


def test_validate_example(capsys):
    assert main(["validate", str(EXAMPLE)]) == 0
    assert capsys.readouterr().out.strip().endswith("valid (2 warnings)")


def test_dry_run_example_end_to_end(capsys, isolated_runs):
    assert main(["run", "--dry-run", "--seed", "7", "--json", str(EXAMPLE)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    run_events = [e for e in events if e.get("run_id")]
    types = [e["type"] for e in run_events]
    assert types[0] == "run_started"
    assert types[-1] == "run_finished"
    assert run_events[-1]["payload"]["status"] == "completed"
    assert [e["seq"] for e in run_events] == list(range(1, len(run_events) + 1))

    changes = [e["payload"]["new"] for e in run_events if e["type"] == "variable_changed"]
    assert changes == [1, 2, 3]
    branches = [e["payload"]["branch"] for e in run_events if e["type"] == "branch_taken"]
    assert branches == ["else", "then", "else"]
    typed = next(e for e in run_events
                 if e["type"] == "step_started" and e["payload"]["step_id"] == "type_name")
    assert typed["payload"]["params"]["text"] == "report_3.txt"

    (run_dir,) = isolated_runs.iterdir()
    persisted = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(persisted) == len(run_events)
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["status"] == "completed"


def test_run_header_names_the_run_and_the_script(capsys):
    assert main(["run", "--dry-run", "--seed", "7", str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^run r-\d{8}-\d{6}-[0-9a-f]{4}  Hello j5  \(mode dry, seed 7, "
                     r"backend \w+\)$", out, re.MULTILINE)


def test_real_run_without_backend_fails_cleanly(capsys, monkeypatch):
    monkeypatch.setenv("J5_BACKEND", "none")
    assert main(["run", str(EXAMPLE)]) == 1
    out = capsys.readouterr().out
    assert "capability_missing" in out


def test_a_step_that_keeps_failing_ends_an_unattended_run(tmp_path, capsys, monkeypatch):
    """Without a terminal, an on_error: pause step is retried a bounded number of times,
    then the run stops and j5 exits with code 1 (never an endless retry loop)."""
    monkeypatch.setenv("J5_BACKEND", "fake")
    script = tmp_path / "flaky.yaml"
    script.write_text("steps:\n  - id: broken\n    on_error: pause\n"
                      "    log: \"${missing + 1}\"\n  - id: after\n    log: never\n",
                      encoding="utf-8", newline="\n")
    assert main(["run", str(script)]) == 1
    captured = capsys.readouterr()
    assert captured.err.count("retrying it (no terminal to ask)") == 2
    assert "stopping the run after 2 retries" in captured.err
    assert "never" not in captured.out
