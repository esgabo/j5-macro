"""Run history commands: ``list_runs``, ``get_run`` and ``delete_run``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from engine_harness import Behaviour, Engine, FakeFactory

from j5_macro.engine.history import DEFAULT_LIMIT
from j5_macro.protocol.messages import ErrorInfo
from j5_macro.runtime.api import RunOutcome

SCRIPT = "name: Saved\nsteps:\n  - id: hi\n    log: hello\n"


def write_run(root: Path, run_id: str, *, started: str | None = None,
              status: str | None = "completed", script_path: str | None = None,
              events: int = 3, **meta: Any) -> Path:
    """A run directory as another engine session would have left it."""
    folder = root / run_id
    folder.mkdir(parents=True)
    data: dict[str, Any] = {
        "run_id": run_id, "script_id": "s1", "script_name": "Other",
        "script_path": script_path, "mode": "normal", "seed": 1,
        "started": started or f"{run_id[2:6]}-{run_id[6:8]}-{run_id[8:10]}T"
                              f"{run_id[11:13]}:{run_id[13:15]}:{run_id[15:17]}.000Z",
        **meta,
    }
    if status is not None:
        data.update(status=status, duration_ms=1500, finished=data["started"])
    (folder / "run.json").write_text(json.dumps(data), encoding="utf-8", newline="\n")
    lines = [json.dumps({"v": 1, "kind": "event", "type": "log", "run_id": run_id,
                         "seq": seq, "ts": data["started"], "t_ms": seq,
                         "payload": {"level": "info", "message": f"line {seq}"}})
             for seq in range(1, events + 1)]
    (folder / "events.jsonl").write_text("".join(line + "\n" for line in lines),
                                         encoding="utf-8", newline="\n")
    return folder


def run_id_at(second: int, suffix: str = "abcd") -> str:
    return f"r-20260101-{second // 3600:02d}{second // 60 % 60:02d}{second % 60:02d}-{suffix}"


def listed(engine: Engine, **payload: Any) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = engine.ok("list_runs", payload)["runs"]
    return runs


def warnings(engine: Engine) -> list[str]:
    return [e["payload"]["message"] for e in engine.engine_events("log")
            if e["payload"]["level"] == "warning"]


@pytest.fixture
def engine(tmp_path: Path):
    started = Engine(run_factory=FakeFactory(), runs_dir=tmp_path / "runs")
    yield started
    started.close()


# --- list_runs ---------------------------------------------------------------------------


def test_list_is_newest_first_and_limited(engine: Engine, tmp_path: Path) -> None:
    root = tmp_path / "runs"
    ids = [run_id_at(i) for i in range(DEFAULT_LIMIT + 5)]
    for run_id in ids:
        write_run(root, run_id)
    (root / "notes").mkdir()
    (root / "r-file").write_text("not a run", encoding="utf-8")

    runs = listed(engine)
    assert [r["run_id"] for r in runs] == ids[::-1][:DEFAULT_LIMIT]
    assert [r["run_id"] for r in listed(engine, limit=3)] == ids[::-1][:3]
    assert listed(engine, limit=0) == []
    assert runs[0] == {"run_id": ids[-1], "status": "completed",
                       "started": "2026-01-01T00:00:24.000Z", "duration_ms": 1500,
                       "script_name": "Other", "mode": "normal"}
    assert warnings(engine) == []


def test_negative_limit_is_rejected(engine: Engine) -> None:
    error = engine.error("list_runs", {"limit": -1})
    assert (error["code"], error["details"]) == ("validation_failed", {"field": "limit"})


def test_list_a_real_run(engine: Engine, tmp_path: Path) -> None:
    script = tmp_path / "saved.yaml"
    script.write_text(SCRIPT, encoding="utf-8")
    script_id = engine.ok("load", {"path": str(script)})["script_id"]
    run_id = engine.start(script_id, mode="dry")
    duration = engine.wait_finished(run_id)["payload"]["duration_ms"]
    engine.runs.get(run_id).finished.wait(5)

    (info,) = listed(engine)
    assert info.pop("started").endswith("Z")
    assert Path(info.pop("script_path")) == script.absolute()
    assert info == {"run_id": run_id, "status": "completed", "duration_ms": duration,
                    "script_name": "Saved", "mode": "dry"}


def test_filter_by_script_path(engine: Engine, tmp_path: Path) -> None:
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    for path in (a, b):
        path.write_text(SCRIPT, encoding="utf-8")
    root = tmp_path / "runs"
    # Runs of earlier engine sessions, found by path.
    earlier_a = write_run(root, run_id_at(1), script_path=str(a.absolute())).name
    earlier_b = write_run(root, run_id_at(2), script_path=str(b.absolute())).name
    write_run(root, run_id_at(3), script_path=None)

    id_a = engine.ok("load", {"path": str(a)})["script_id"]
    id_b = engine.ok("load", {"path": str(b)})["script_id"]
    run_a = engine.start(id_a)
    engine.wait_finished(run_a)
    run_b = engine.start(id_b)
    engine.wait_finished(run_b)

    assert [r["run_id"] for r in listed(engine, script_id=id_a)] == [run_a, earlier_a]
    assert [r["run_id"] for r in listed(engine, script_id=id_b)] == [run_b, earlier_b]
    assert len(listed(engine)) == 5


def test_filter_for_a_script_loaded_from_source(engine: Engine, tmp_path: Path) -> None:
    write_run(tmp_path / "runs", run_id_at(1), script_path=None)  # another session's
    first = engine.load_source(SCRIPT)
    second = engine.load_source(SCRIPT)
    run_first = engine.start(first)
    engine.wait_finished(run_first)
    run_second = engine.start(second)
    engine.wait_finished(run_second)
    assert [r["run_id"] for r in listed(engine, script_id=first)] == [run_first]
    assert [r["run_id"] for r in listed(engine, script_id=second)] == [run_second]


def test_filter_by_unknown_script(engine: Engine) -> None:
    assert engine.error("list_runs", {"script_id": "s9"})["code"] == "script_not_found"


def test_corrupt_runs_are_skipped_with_a_warning(engine: Engine, tmp_path: Path) -> None:
    root = tmp_path / "runs"
    good = write_run(root, run_id_at(1)).name
    (write_run(root, run_id_at(2)) / "run.json").write_text("{\"run_id\": ", encoding="utf-8")
    (write_run(root, run_id_at(3)) / "run.json").write_text("[1, 2]", encoding="utf-8")
    (write_run(root, run_id_at(4)) / "run.json").unlink()
    write_run(root, run_id_at(5), status="exploded")
    no_start = write_run(root, run_id_at(6))
    meta = json.loads((no_start / "run.json").read_text(encoding="utf-8"))
    del meta["started"]
    (no_start / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    (write_run(root, run_id_at(7)) / "run.json").write_bytes(b"\xff\xfe{")

    assert [r["run_id"] for r in listed(engine)] == [good]
    skipped = warnings(engine)
    assert len(skipped) == 6
    assert all("skipping r-20260101-0000" in message for message in skipped)


def test_without_a_runs_dir_the_history_is_empty() -> None:
    engine = Engine(run_factory=FakeFactory())
    try:
        run_id = engine.start(engine.load_source())
        engine.wait_finished(run_id)
        assert listed(engine) == []
        assert engine.error("get_run", {"run_id": run_id})["code"] == "not_running"
        assert engine.error("delete_run", {"run_id": run_id})["code"] == "not_running"
    finally:
        engine.close()


# --- the active run ----------------------------------------------------------------------


def test_active_run_is_listed_readable_and_not_deletable(tmp_path: Path) -> None:
    factory = FakeFactory(Behaviour(block=True,
                                    events=[("log", {"level": "info", "message": "a"})]))
    engine = Engine(run_factory=factory, runs_dir=tmp_path)
    try:
        write_run(tmp_path, run_id_at(1))
        run_id = engine.start(engine.load_source(SCRIPT))
        factory.last.running.wait(5)
        engine.event("log", run_id)

        newest = listed(engine)[0]
        assert newest["run_id"] == run_id
        assert "status" not in newest and "duration_ms" not in newest
        assert newest["script_name"] == "Saved"

        answer = engine.ok("get_run", {"run_id": run_id})
        assert "status" not in answer["run"]
        assert [e["type"] for e in answer["events"]] == ["run_started", "log"]

        error = engine.error("delete_run", {"run_id": run_id})
        assert error["code"] == "invalid_state"
        assert error["details"] == {"reason": "active", "run_id": run_id}
        assert (tmp_path / run_id).is_dir()

        factory.last.release()
        engine.wait_finished(run_id)
        engine.runs.get(run_id).finished.wait(5)
        assert listed(engine)[0]["status"] == "completed"
        assert engine.ok("delete_run", {"run_id": run_id}) == {}
        assert not (tmp_path / run_id).exists()
    finally:
        engine.close()


# --- get_run -----------------------------------------------------------------------------


def test_get_run_with_from_seq_and_captures(engine: Engine, tmp_path: Path) -> None:
    folder = write_run(tmp_path / "runs", run_id_at(1), events=5)
    captures = folder / "captures"
    captures.mkdir()
    (captures / "0002-find-1.png").write_bytes(b"png")
    (captures / "0001-find-1.png").write_bytes(b"png")
    (captures / "nested").mkdir()
    (captures / "nested" / "x.json").write_text("{}", encoding="utf-8")

    answer = engine.ok("get_run", {"run_id": folder.name})
    assert answer["run_id"] == folder.name
    assert answer["run"]["status"] == "completed"
    assert [e["seq"] for e in answer["events"]] == [1, 2, 3, 4, 5]
    assert answer["events"][0]["payload"] == {"level": "info", "message": "line 1"}
    assert answer["captures"] == ["captures/0001-find-1.png", "captures/0002-find-1.png",
                                  "captures/nested/x.json"]
    later = engine.ok("get_run", {"run_id": folder.name, "from_seq": 4})
    assert [e["seq"] for e in later["events"]] == [4, 5]


def test_get_run_of_a_real_run_matches_the_wire(tmp_path: Path) -> None:
    behaviour = Behaviour(events=[("log", {"level": "info", "message": "ñandú"})],
                          outcome=RunOutcome(status="failed", error=ErrorInfo(
                              code="image_not_found", message="no match")))
    engine = Engine(run_factory=FakeFactory(behaviour), runs_dir=tmp_path)
    try:
        run_id = engine.start(engine.load_source(SCRIPT))
        engine.wait_finished(run_id)
        engine.runs.get(run_id).finished.wait(5)
        answer = engine.ok("get_run", {"run_id": run_id})
        assert answer["events"] == engine.run_events(run_id)
        assert answer["run"]["status"] == "failed"
        assert answer["run"]["error"]["code"] == "image_not_found"
        assert answer["captures"] == []
    finally:
        engine.close()


def test_truncated_events_file(engine: Engine, tmp_path: Path) -> None:
    folder = write_run(tmp_path / "runs", run_id_at(1), events=2)
    with (folder / "events.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write('{"v":1,"kind":"event","type":"log","seq":3,"pay')
    answer = engine.ok("get_run", {"run_id": folder.name})
    assert [e["seq"] for e in answer["events"]] == [1, 2]
    assert warnings(engine) == []


def test_unreadable_event_lines_are_skipped_with_a_warning(engine: Engine,
                                                          tmp_path: Path) -> None:
    folder = write_run(tmp_path / "runs", run_id_at(1), events=0)
    (folder / "events.jsonl").write_text(
        '{"seq": 1, "type": "log"}\nnot json\n[1]\n{"seq": 4, "type": "log"}\n',
        encoding="utf-8", newline="\n")
    answer = engine.ok("get_run", {"run_id": folder.name})
    assert [e["seq"] for e in answer["events"]] == [1, 4]
    assert any("skipped 2 unreadable" in message for message in warnings(engine))


def test_get_run_without_files(engine: Engine, tmp_path: Path) -> None:
    folder = tmp_path / "runs" / run_id_at(1)
    folder.mkdir(parents=True)
    answer = engine.ok("get_run", {"run_id": folder.name})
    assert answer == {"run_id": folder.name, "run": {"run_id": folder.name}, "events": [],
                      "captures": []}


def test_get_unknown_run(engine: Engine) -> None:
    error = engine.error("get_run", {"run_id": run_id_at(9)})
    assert (error["code"], error["details"]) == ("not_running", {"run_id": run_id_at(9)})


# --- delete_run --------------------------------------------------------------------------


def test_delete_run(engine: Engine, tmp_path: Path) -> None:
    root = tmp_path / "runs"
    gone = write_run(root, run_id_at(1))
    (gone / "captures").mkdir()
    (gone / "captures" / "0001-a-1.png").write_bytes(b"png")
    kept = write_run(root, run_id_at(2))
    assert engine.ok("delete_run", {"run_id": gone.name}) == {}
    assert not gone.exists() and kept.is_dir()
    assert [r["run_id"] for r in listed(engine)] == [kept.name]
    assert engine.error("delete_run", {"run_id": gone.name})["code"] == "not_running"
    assert engine.error("get_run", {"run_id": gone.name})["code"] == "not_running"


# --- run ids cannot escape the runs dir ---------------------------------------------------


BAD_IDS = [
    "../outside", "..", ".", "", "r-..", "r-20260101-000001-abcd/..",
    "r-20260101-000001-abcd/../../outside", "..\\outside", "r-20260101-000001-ABCD",
    "r-20260101-000001-abcd\n", "r-2026010\uff11-000001-abcd", "/etc", "C:\\Windows",
]


@pytest.mark.parametrize("command", ["get_run", "delete_run"])
def test_bad_run_ids_are_rejected(engine: Engine, tmp_path: Path, command: str) -> None:
    outside = write_run(tmp_path, "outside")
    inside = write_run(tmp_path / "runs", run_id_at(1))
    for run_id in [*BAD_IDS, str(outside), str(inside), str(inside.absolute())]:
        error = engine.error(command, {"run_id": run_id})
        assert (error["code"], error["details"]) == ("validation_failed",
                                                     {"field": "run_id"}), run_id
    assert outside.is_dir() and inside.is_dir()
    assert (tmp_path / "runs").is_dir()


# --- secrets -----------------------------------------------------------------------------


def test_secrets_in_the_final_error_are_masked_in_run_json(tmp_path: Path) -> None:
    secret = "hunter22"
    error = ErrorInfo(code="internal_error", message=f"could not type {secret}")
    engine = Engine(run_factory=FakeFactory(Behaviour(outcome=RunOutcome(
        status="failed", error=error))), runs_dir=tmp_path)
    try:
        script_id = engine.load_source("params:\n  pw: {secret: true}\n" + SCRIPT)
        run_id = engine.start(script_id, params={"pw": secret})
        engine.wait_finished(run_id)
        engine.runs.get(run_id).finished.wait(5)
        assert secret not in (tmp_path / run_id / "run.json").read_text(encoding="utf-8")
        answer = engine.ok("get_run", {"run_id": run_id})
        assert answer["run"]["error"]["message"] == "could not type ***"
        assert secret not in json.dumps(answer)
    finally:
        engine.close()
