"""``edit_script`` through the engine: versions, conflicts, saving and ``script_changed``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from engine_harness import Engine, FakeFactory

from j5_macro.engine.scripts import script_version
from j5_macro.protocol.payloads import EditScriptResult, ScriptChangedEvent

SCRIPT = """\
# Greets, then naps.
name: Edited
steps:
  - id: greet
    log: hello   # say it
  - sleep: 1s
"""


@pytest.fixture
def factory() -> FakeFactory:
    return FakeFactory()


@pytest.fixture
def engine(factory: FakeFactory) -> Iterator[Engine]:
    eng = Engine(run_factory=factory)
    yield eng
    eng.close()


@pytest.fixture
def script_file(tmp_path: Path) -> Path:
    path = tmp_path / "edited.yaml"
    path.write_bytes(SCRIPT.encode("utf-8"))
    return path


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def edit(engine: Engine, script_id: str, base: str, *edits: dict[str, Any]) -> dict[str, Any]:
    return engine.call("edit_script", {"script_id": script_id, "base_version": base,
                                       "edits": list(edits)})


def changes(engine: Engine) -> list[ScriptChangedEvent]:
    return [ScriptChangedEvent.from_json(e["payload"])
            for e in engine.engine_events("script_changed")]


def test_edit_a_file_backed_script(engine: Engine, factory: FakeFactory,
                                   script_file: Path) -> None:
    script_id = engine.ok("load", {"path": str(script_file)})["script_id"]
    response = edit(engine, script_id, script_version(SCRIPT),
                    {"op": "update", "step_id": "sleep_1", "params": {"duration": "2s"}},
                    {"op": "insert", "step": {"log": "bye"}, "to": {}})
    assert response["ok"] is True, response
    result = EditScriptResult.from_json(response["result"])

    saved = read(script_file)
    assert saved == ("# Greets, then naps.\nname: Edited\nsteps:\n"
                     "  - id: greet\n    log: hello   # say it\n"
                     "  - id: sleep_1\n    sleep: 2s\n"
                     "  - id: log_2\n    log: bye\n")
    assert b"\r\n" not in script_file.read_bytes()
    assert result == EditScriptResult(version=script_version(saved), valid=True)
    assert changes(engine) == [ScriptChangedEvent(script_id=script_id, version=result.version,
                                                  source="edit", valid=True)]
    assert not list(script_file.parent.glob(".*.tmp"))

    # The next run executes the edited script.
    engine.wait_finished(engine.start(script_id))
    assert [s.id for s in factory.last.script.steps] == ["greet", "sleep_1", "log_2"]
    assert factory.last.script.steps[1].params["duration"] == "2s"

    # Editing on top of the new version works; the old one is a conflict now.
    again = edit(engine, script_id, result.version, {"op": "delete", "step_id": "log_2"})
    assert again["ok"] is True, again
    stale = edit(engine, script_id, result.version, {"op": "delete", "step_id": "greet"})
    assert stale["ok"] is False
    assert stale["error"]["code"] == "conflict"


def test_conflict_when_the_file_changed_on_disk(engine: Engine, script_file: Path) -> None:
    script_id = engine.ok("load", {"path": str(script_file)})["script_id"]
    changed = SCRIPT.replace("hello", "hi there")
    script_file.write_bytes(changed.encode("utf-8"))
    error = engine.error("edit_script", {
        "script_id": script_id, "base_version": script_version(SCRIPT),
        "edits": [{"op": "delete", "step_id": "greet"}]})
    assert error["code"] == "conflict"
    assert error["details"] == {"version": script_version(changed)}
    assert read(script_file) == changed
    assert changes(engine) == []

    # With the version on disk the edit applies, even before the watcher noticed.
    response = edit(engine, script_id, script_version(changed),
                    {"op": "delete", "step_id": "sleep_1"})
    assert response["ok"] is True, response
    assert "hi there" in read(script_file)
    assert "sleep" not in read(script_file)


def test_an_invalid_result_is_saved_and_blocks_runs_until_fixed(
        engine: Engine, script_file: Path) -> None:
    script_id = engine.ok("load", {"path": str(script_file)})["script_id"]
    bad = EditScriptResult.from_json(engine.ok("edit_script", {
        "script_id": script_id, "base_version": script_version(SCRIPT),
        "edits": [{"op": "update", "step_id": "greet", "params": {"level": "loud"}}]}))
    assert bad.valid is False
    assert bad.errors and bad.errors[0].step_id == "greet"
    assert "level: loud" in read(script_file)
    event = changes(engine)[-1]
    assert (event.source, event.valid, event.version) == ("edit", False, bad.version)
    assert event.errors == bad.errors

    refused = engine.error("run", {"script_id": script_id, "mode": "normal"})
    assert refused["code"] == "validation_failed"

    fixed = EditScriptResult.from_json(engine.ok("edit_script", {
        "script_id": script_id, "base_version": bad.version,
        "edits": [{"op": "update", "step_id": "greet", "params": {"level": "warning"}}]}))
    assert fixed.valid is True and fixed.errors == []
    assert changes(engine)[-1].valid is True
    engine.wait_finished(engine.start(script_id))


def test_an_edit_that_cannot_apply_changes_nothing(engine: Engine, script_file: Path) -> None:
    script_id = engine.ok("load", {"path": str(script_file)})["script_id"]
    error = engine.error("edit_script", {
        "script_id": script_id, "base_version": script_version(SCRIPT),
        "edits": [{"op": "delete", "step_id": "greet"},
                  {"op": "move", "step_id": "greet", "to": {}}]})
    assert error["code"] == "validation_failed"
    assert error["details"] == {"edit": 1}
    assert "unknown step id 'greet'" in error["message"]
    assert read(script_file) == SCRIPT
    assert changes(engine) == []


def test_edit_a_script_loaded_from_source_in_memory(engine: Engine, factory: FakeFactory,
                                                    tmp_path: Path) -> None:
    script_id = engine.load_source(SCRIPT)
    result = EditScriptResult.from_json(engine.ok("edit_script", {
        "script_id": script_id, "base_version": script_version(SCRIPT),
        "edits": [{"op": "move", "step_id": "sleep_1", "to": {"index": 0}}]}))
    assert result.valid is True
    assert result.version != script_version(SCRIPT)
    assert changes(engine)[-1].source == "edit"
    assert list(tmp_path.iterdir()) == []
    engine.wait_finished(engine.start(script_id))
    assert [s.id for s in factory.last.script.steps] == ["sleep_1", "greet"]
    error = engine.error("edit_script", {"script_id": script_id,
                                         "base_version": script_version(SCRIPT),
                                         "edits": []})
    assert error["details"] == {"version": result.version}


def test_unknown_script_and_unreadable_file(engine: Engine, script_file: Path) -> None:
    error = engine.error("edit_script", {"script_id": "s99", "base_version": "x",
                                         "edits": []})
    assert error["code"] == "script_not_found"
    script_id = engine.ok("load", {"path": str(script_file)})["script_id"]
    script_file.unlink()
    error = engine.error("edit_script", {"script_id": script_id,
                                         "base_version": script_version(SCRIPT), "edits": []})
    assert error["code"] == "script_not_found"


def test_no_change_answers_the_version_without_an_event(engine: Engine,
                                                        tmp_path: Path) -> None:
    text = "steps:\n  - id: a\n    log: a\n"
    path = tmp_path / "same.yaml"
    path.write_bytes(text.encode("utf-8"))
    script_id = engine.ok("load", {"path": str(path)})["script_id"]
    result = engine.ok("edit_script", {"script_id": script_id,
                                       "base_version": script_version(text), "edits": []})
    assert result["version"] == script_version(text)
    assert changes(engine) == []


def test_a_parse_error_in_the_current_text(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_bytes(b"steps:\n  - id: a\n    log: a\n")
    script_id = engine.ok("load", {"path": str(path)})["script_id"]
    broken = "steps: [\n"
    path.write_bytes(broken.encode("utf-8"))
    error = engine.error("edit_script", {"script_id": script_id,
                                         "base_version": script_version(broken),
                                         "edits": [{"op": "delete", "step_id": "a"}]})
    assert error["code"] == "parse_error"
    assert error["line"] is not None
