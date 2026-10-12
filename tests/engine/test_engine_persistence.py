"""Run history on disk: files, retention, failures and the runs directory option."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from engine_harness import Behaviour, Engine, FakeFactory

from j5_macro.engine.app import parse_args
from j5_macro.engine.persistence import (
    RUNS_KEPT,
    RunStore,
    platform_state_dir,
    resolve_runs_dir,
)
from j5_macro.protocol.messages import ErrorInfo
from j5_macro.runtime.api import RunOutcome

SCRIPT = "name: Saved\nsteps:\n  - id: hi\n    log: hello\n"


def test_run_files(tmp_path: Path) -> None:
    script = tmp_path / "saved.yaml"
    script.write_text(SCRIPT, encoding="utf-8")
    runs_dir = tmp_path / "runs"
    error = ErrorInfo(code="image_not_found", message="no match ✗", step_id="hi")
    behaviour = Behaviour(events=[("log", {"level": "info", "message": "ñandú"})],
                          outcome=RunOutcome(status="failed", error=error))
    engine = Engine(run_factory=FakeFactory(behaviour), runs_dir=runs_dir)
    script_id = engine.ok("load", {"path": str(script)})["script_id"]
    run_id = engine.start(script_id, mode="step", seed=7)
    engine.wait_finished(run_id)
    engine.close()

    folder = runs_dir / run_id
    lines = (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == engine.run_events(run_id)

    meta = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    assert meta["run_id"] == run_id
    assert meta["script_id"] == script_id
    assert meta["script_name"] == "Saved"
    assert Path(meta["script_path"]) == script.absolute()
    assert (meta["mode"], meta["seed"]) == ("step", 7)
    assert meta["started"].endswith("Z") and meta["finished"] >= meta["started"]
    assert (meta["status"], meta["reason"]) == ("failed", None)
    assert meta["duration_ms"] == engine.run_events(run_id)[-1]["payload"]["duration_ms"]
    assert meta["error"] == error.to_json()
    assert not (folder / "run.json.tmp").exists()


def test_run_json_while_running_has_no_status(tmp_path: Path) -> None:
    factory = FakeFactory(Behaviour(block=True))
    engine = Engine(run_factory=factory, runs_dir=tmp_path)
    run_id = engine.start(engine.load_source(SCRIPT))
    meta = json.loads((tmp_path / run_id / "run.json").read_text(encoding="utf-8"))
    assert "status" not in meta
    assert meta["script_path"] is None
    factory.last.release()
    engine.wait_finished(run_id)
    engine.close()
    meta = json.loads((tmp_path / run_id / "run.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed"


def fake_run_dir(root: Path, index: int) -> Path:
    path = root / f"r-20260101-{index:06d}-abcd"
    path.mkdir(parents=True)
    (path / "run.json").write_text("{}", encoding="utf-8")
    return path


def test_retention_keeps_the_newest_runs(tmp_path: Path) -> None:
    old = [fake_run_dir(tmp_path, i) for i in range(RUNS_KEPT + 3)]
    unrelated = tmp_path / "notes"
    unrelated.mkdir()
    (tmp_path / "r-file").write_text("not a run", encoding="utf-8")

    engine = Engine(run_factory=FakeFactory(), runs_dir=tmp_path)
    run_id = engine.start(engine.load_source())
    engine.wait_finished(run_id)
    engine.close()

    kept = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("r-2"))
    assert len(kept) == RUNS_KEPT
    assert run_id in kept
    # The four oldest went; the newest old runs stayed.
    assert [p.exists() for p in old] == [False] * 4 + [True] * (RUNS_KEPT - 1)
    assert unrelated.is_dir()
    assert (tmp_path / "r-file").is_file()


def test_prune_orders_same_second_runs_by_mtime(tmp_path: Path) -> None:
    first = tmp_path / "r-20260101-000000-ffff"
    second = tmp_path / "r-20260101-000000-0000"
    first.mkdir()
    second.mkdir()
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    os.utime(second, ns=(2_000_000_000, 2_000_000_000))
    assert RunStore(tmp_path, keep=2).prune() == ["r-20260101-000000-ffff"]
    assert RunStore(tmp_path / "missing").prune() == []


def test_persistence_failure_is_not_fatal(tmp_path: Path) -> None:
    blocker = tmp_path / "runs"
    blocker.write_text("a file where the runs folder should be", encoding="utf-8")
    engine = Engine(run_factory=FakeFactory(), runs_dir=blocker)
    run_id = engine.start(engine.load_source())
    finished = engine.wait_finished(run_id)
    engine.close()
    assert finished["payload"]["status"] == "completed"
    logs = engine.engine_events("log")
    assert any("not saved" in e["payload"]["message"] for e in logs)
    assert all(e["payload"]["level"] == "warning" for e in logs)


def test_events_write_failure_is_logged_once(tmp_path: Path) -> None:
    factory = FakeFactory(Behaviour(events=[("log", {"level": "info", "message": "x"})] * 3))
    engine = Engine(run_factory=factory, runs_dir=tmp_path)
    original = engine.runs._create_files

    def broken_files(run: object) -> object:
        files = original(run)  # type: ignore[arg-type]
        assert files is not None
        files.close()  # appends now fail
        return files

    engine.runs._create_files = broken_files  # type: ignore[assignment,method-assign]
    run_id = engine.start(engine.load_source())
    finished = engine.wait_finished(run_id)
    engine.close()
    assert finished["payload"]["status"] == "completed"
    assert len(engine.run_events(run_id)) == 5
    warnings = [e for e in engine.engine_events("log") if "no longer saved" in
                e["payload"]["message"]]
    assert len(warnings) == 1
    # run.json is still completed at the end.
    meta = json.loads((tmp_path / run_id / "run.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed"


def test_without_runs_dir_nothing_is_written(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine = Engine(run_factory=FakeFactory())
    engine.wait_finished(engine.start(engine.load_source()))
    engine.close()
    assert list(tmp_path.iterdir()) == []


def test_resolve_runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path / "env"))
    assert resolve_runs_dir(tmp_path / "opt") == tmp_path / "opt"
    assert resolve_runs_dir(None) == tmp_path / "env"
    monkeypatch.delenv("J5_RUNS_DIR")
    assert resolve_runs_dir() == platform_state_dir() / "j5-macro" / "runs"


def test_platform_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert platform_state_dir() == tmp_path / "xdg"
    monkeypatch.setenv("XDG_STATE_HOME", "relative/ignored")
    assert platform_state_dir() == tmp_path / ".local" / "state"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert platform_state_dir() == tmp_path / "Library" / "Application Support"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert platform_state_dir() == tmp_path / "local"
    monkeypatch.delenv("LOCALAPPDATA")
    assert platform_state_dir() == tmp_path / "AppData" / "Local"


def test_engine_runs_dir_option() -> None:
    assert parse_args(["--runs-dir", "somewhere"]).runs_dir == "somewhere"
    assert parse_args([]).runs_dir is None


def test_runs_get_their_directory_for_artifacts(tmp_path: Path) -> None:
    factory = FakeFactory(Behaviour(block=True))
    engine = Engine(run_factory=factory, runs_dir=tmp_path)
    run_id = engine.start(engine.load_source(SCRIPT))
    run_dir = factory.last.options.run_dir
    assert run_dir == tmp_path / run_id
    assert run_dir.is_dir()  # created before the run's first step
    factory.last.release()
    engine.wait_finished(run_id)
    engine.close()


def test_runs_without_persistence_have_no_directory() -> None:
    factory = FakeFactory()
    engine = Engine(run_factory=factory)
    run_id = engine.start(engine.load_source(SCRIPT))
    engine.wait_finished(run_id)
    engine.close()
    assert factory.last.options.run_dir is None
