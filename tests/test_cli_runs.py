"""``j5 runs`` against the real engine (fake backend), and its pure rendering."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from j5_macro.cli.main import main
from j5_macro.cli.options import ENGINE_CMD_ENV
from j5_macro.cli.render import format_run_summary, format_runs, local_time

SCRIPT = """\
name: Two steps
steps:
  - id: greet
    log: hello
  - id: done
    log: bye
"""


@pytest.fixture
def runs_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("J5_BACKEND", "fake")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv(ENGINE_CMD_ENV, raising=False)
    return tmp_path / "runs"


@pytest.fixture
def script(tmp_path) -> Path:
    path = tmp_path / "two.yaml"
    path.write_text(SCRIPT, encoding="utf-8")
    return path


def run_twice(script: Path, capsys) -> list[str]:
    """Run the script once normally and once dry; the run ids, oldest first."""
    ids = []
    for extra in ([], ["--dry-run"]):
        assert main(["run", "--seed", "3", *extra, str(script)]) == 0
        ids += re.findall(r"^run (r-\d{8}-\d{6}-[0-9a-f]{4})", capsys.readouterr().out,
                          re.MULTILINE)
    assert len(ids) == 2
    return ids


def test_list_show_and_delete(runs_dir, script, capsys) -> None:
    first, second = run_twice(script, capsys)

    assert main(["runs"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["RUN", "STARTED", "SCRIPT", "MODE", "STATUS", "DURATION"]
    assert [line.split()[0] for line in lines[1:]] == [second, first]
    assert "Two steps  dry     completed" in lines[1]
    assert "Two steps  normal  completed" in lines[2]
    assert re.search(r"\d+\.\d{3}s$", lines[1])

    assert main(["runs", "--json", "-n", "1"]) == 0
    (listed,) = json.loads(capsys.readouterr().out)["runs"]
    assert (listed["run_id"], listed["mode"], listed["status"]) == (second, "dry", "completed")
    assert Path(listed["script_path"]) == script.absolute()

    assert main(["runs", "show", first]) == 0
    out = capsys.readouterr().out
    assert f"saved run {first}" in out
    assert f"  script    {script.absolute()}" in out
    assert re.search(rf"^run {first}  Two steps  \(mode normal, seed 3, backend fake\)$", out,
                     re.MULTILINE)
    assert re.search(r"^\s+\d+\.\d{3}s\s+\[info\] bye$", out, re.MULTILINE)
    assert "completed in " in out.splitlines()[-1]

    assert main(["runs", "show", "--json", first]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events == [json.loads(line) for line in
                      (runs_dir / first / "events.jsonl").read_text("utf-8").splitlines()]

    assert main(["runs", "delete", first]) == 0
    assert capsys.readouterr().out == f"deleted {first}\n"
    assert not (runs_dir / first).exists()
    assert main(["runs"]) == 0
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()[1:]] == [second]


def test_runs_dir_option(tmp_path, runs_dir, script, capsys) -> None:
    other = tmp_path / "elsewhere"
    assert main(["run", "--dry-run", "--runs-dir", str(other), str(script)]) == 0
    capsys.readouterr()
    (run_dir,) = other.iterdir()
    assert not runs_dir.exists()

    assert main(["runs"]) == 0
    assert capsys.readouterr().err.startswith("j5: no saved runs in ")
    assert main(["runs", "--runs-dir", str(other)]) == 0
    assert run_dir.name in capsys.readouterr().out
    assert main(["runs", "show", run_dir.name, "--runs-dir", str(other)]) == 0
    assert f"saved run {run_dir.name}" in capsys.readouterr().out
    assert main(["runs", "--runs-dir", str(other), "delete", run_dir.name]) == 0
    assert not run_dir.exists()


def test_unknown_and_malformed_run_ids(runs_dir, capsys) -> None:
    missing = "r-20260101-000000-abcd"
    assert main(["runs", "show", missing]) == 1
    assert capsys.readouterr().err == f"j5: no saved run {missing}\n"
    assert main(["runs", "show", "../etc"]) == 2
    assert "is not a run id" in capsys.readouterr().err
    assert main(["runs", "delete", missing, "../etc"]) == 2
    err = capsys.readouterr().err
    assert f"no saved run {missing}" in err and "is not a run id" in err
    assert main(["runs", "--limit", "-1"]) == 2


def test_show_an_unfinished_run(runs_dir, capsys) -> None:
    run_id = "r-20260101-000000-abcd"
    folder = runs_dir / run_id
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps({
        "run_id": run_id, "script_name": "Crashed", "mode": "normal",
        "started": "2026-01-01T00:00:00.000Z"}), encoding="utf-8")
    (folder / "events.jsonl").write_text(json.dumps({
        "v": 1, "kind": "event", "type": "run_started", "run_id": run_id, "seq": 1,
        "ts": "2026-01-01T00:00:00.000Z", "t_ms": 0,
        "payload": {"script_name": "Crashed", "mode": "normal"}}) + "\n", encoding="utf-8")

    assert main(["runs"]) == 0
    assert "Crashed  normal  unfinished         -" in capsys.readouterr().out
    assert main(["runs", "show", run_id]) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("(no run_finished")


# --- rendering ---------------------------------------------------------------------------


def test_format_runs_aligns_columns() -> None:
    lines = format_runs([
        {"run_id": "r-20260101-000001-abcd", "started": "x", "script_path": "/a/b/long.yaml",
         "mode": "dry", "status": "failed", "duration_ms": 12345},
        {"run_id": "r-20260101-000000-abcd", "started": "y"},
    ])
    assert lines == [
        "RUN                     STARTED  SCRIPT     MODE  STATUS      DURATION",
        "r-20260101-000001-abcd  x        long.yaml  dry   failed       12.345s",
        "r-20260101-000000-abcd  y        -          -     unfinished         -",
    ]


def test_local_time() -> None:
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d",
                        local_time("2026-10-12T15:10:51.412Z"))
    assert local_time("soon") == "soon"
    assert local_time(None) == "?"


def test_run_summary_lists_captures() -> None:
    lines = format_run_summary("r-1", {"started": "x", "script_path": "/s.yaml"},
                               ["captures/0001-a-1.png"], "/runs/r-1")
    assert lines == ["saved run r-1", "  script    /s.yaml", "  started   x",
                     "  captures  1 file in /runs/r-1", "    captures/0001-a-1.png"]


def test_a_closed_pipe_ends_quietly(monkeypatch):
    from j5_macro.cli import main as cli_main

    def broken(args):
        raise BrokenPipeError

    monkeypatch.setattr(cli_main, "_dispatch", broken)
    monkeypatch.setattr(cli_main, "_silence_stdout", lambda: None)
    assert cli_main.main(["runs"]) == 1
