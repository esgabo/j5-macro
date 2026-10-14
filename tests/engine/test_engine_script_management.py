"""Script management: list_scripts, create_script, get_script, and file watching with
script_changed (docs/contract.md, "Script management")."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from engine_harness import Engine, FakeFactory, Message

from j5_macro.engine import scripts as scripts_module
from j5_macro.engine.scripts import new_script_text, script_version
from j5_macro.protocol.payloads import (
    CreateScriptResult,
    GetScriptResult,
    ListScriptsResult,
    ScriptChangedEvent,
)

POLL_S = 0.02
QUIET_S = 0.3
"""How long to watch for events that must not come (many polls)."""

GOOD = """\
name: Demo
input:
  mouse: {strategy: direct}
vars:
  count: 3
  names: [a, b]
params:
  who: {default: world}
steps:
  - id: greet
    log: hello
  - loop:
      times: 2
      do:
        - id: nap
          sleep: 10ms
"""

CHANGED = GOOD.replace("greet", "welcome")

INVALID = """\
name: Broken
steps:
  - id: greet
    frobnicate: {}
"""


@pytest.fixture
def factory() -> FakeFactory:
    return FakeFactory()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, factory: FakeFactory) -> Iterator[Engine]:
    monkeypatch.setattr(scripts_module, "WATCH_INTERVAL_S", POLL_S)
    eng = Engine(run_factory=factory)
    yield eng
    eng.close()


def write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def changes(engine: Engine) -> list[Message]:
    return engine.engine_events("script_changed")


def wait_changes(engine: Engine, count: int, timeout: float = 5.0) -> list[Message]:
    """Wait until ``count`` script_changed events have arrived, then return them all."""
    engine.writer.wait_for(lambda m: len(changes(engine)) >= count, timeout)
    return changes(engine)


def payload(event: Message) -> ScriptChangedEvent:
    return ScriptChangedEvent.from_json(event["payload"])


def get(engine: Engine, script_id: str) -> GetScriptResult:
    return GetScriptResult.from_json(engine.ok("get_script", {"script_id": script_id}))


def load(engine: Engine, path: Path) -> str:
    script_id: str = engine.ok("load", {"path": str(path)})["script_id"]
    return script_id


def run_and_finish(engine: Engine, script_id: str) -> None:
    engine.wait_finished(engine.start(script_id))


# --- list_scripts -------------------------------------------------------------------------


def listed(engine: Engine, **payload: Any) -> dict[str, Any]:
    result = ListScriptsResult.from_json(engine.ok("list_scripts", payload))
    return {info.path: info for info in result.scripts}


def test_list_scripts_recurses_and_filters(engine: Engine, tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", GOOD)
    (tmp_path / "sub" / "deeper").mkdir(parents=True)
    write(tmp_path / "sub" / "deeper" / "b.yml", "steps: []\n")
    write(tmp_path / "sub" / "broken.yaml", INVALID)
    write(tmp_path / "sub" / "unparseable.yaml", "steps:\n  - [oops\n")
    write(tmp_path / "config.yaml", "key: value\n")  # not a script: no steps
    write(tmp_path / "list.yaml", "- 1\n- 2\n")
    write(tmp_path / "notes.txt", "steps: []\n")  # not YAML by extension
    (tmp_path / ".hidden").mkdir()
    write(tmp_path / ".hidden" / "secret.yaml", GOOD)

    found = listed(engine, root=str(tmp_path))
    assert sorted(Path(p).relative_to(tmp_path).as_posix() for p in found) == [
        "a.yaml", "sub/broken.yaml", "sub/deeper/b.yml", "sub/unparseable.yaml"]
    assert all(Path(p).is_absolute() for p in found)
    a = found[str(tmp_path / "a.yaml")]
    assert (a.name, a.valid) == ("Demo", True)
    assert a.modified.endswith("Z")
    b = found[str(tmp_path / "sub" / "deeper" / "b.yml")]
    assert (b.name, b.valid) == ("b", True)  # no name: the file stem
    broken = found[str(tmp_path / "sub" / "broken.yaml")]
    assert (broken.name, broken.valid) == ("Broken", False)
    unparseable = found[str(tmp_path / "sub" / "unparseable.yaml")]
    assert (unparseable.name, unparseable.valid) == ("unparseable", False)


def test_list_scripts_relative_and_default_root(engine: Engine, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "scripts").mkdir()
    write(tmp_path / "scripts" / "a.yaml", GOOD)
    write(tmp_path / "top.yaml", GOOD)
    monkeypatch.chdir(tmp_path)
    assert list(listed(engine, root="scripts")) == [str(tmp_path / "scripts" / "a.yaml")]
    assert sorted(listed(engine)) == sorted([str(tmp_path / "scripts" / "a.yaml"),
                                             str(tmp_path / "top.yaml")])


def test_list_scripts_stops_at_the_cap(engine: Engine, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scripts_module, "MAX_LISTED_FILES", 3)
    for i in range(5):
        write(tmp_path / f"s{i}.yaml", "steps: []\n")
    assert len(listed(engine, root=str(tmp_path))) == 3


def test_list_scripts_missing_root(engine: Engine, tmp_path: Path) -> None:
    error = engine.error("list_scripts", {"root": str(tmp_path / "nope")})
    assert error["code"] == "validation_failed"
    assert error["details"]["reason"] == "no_directory"


# --- create_script ------------------------------------------------------------------------


def test_create_script_writes_and_loads(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "new.yaml"
    result = CreateScriptResult.from_json(
        engine.ok("create_script", {"path": str(path), "name": "My: new script"}))
    raw = path.read_bytes()
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert text == new_script_text("My: new script")
    assert result.version == script_version(text)
    script = get(engine, result.script_id)
    assert (script.name, script.steps, script.valid) == ("My: new script", [], True)
    assert script.path == str(path)
    assert script.version == result.version
    assert listed(engine, root=str(tmp_path))[str(path)].name == "My: new script"


def test_create_script_relative_path(engine: Engine, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = engine.ok("create_script", {"path": "rel.yaml", "name": "Rel"})
    assert (tmp_path / "rel.yaml").is_file()
    assert get(engine, result["script_id"]).path == str(tmp_path / "rel.yaml")


def test_create_script_never_overwrites(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "exists.yaml"
    write(path, GOOD)
    error = engine.error("create_script", {"path": str(path), "name": "X"})
    assert error["code"] == "validation_failed"
    assert error["details"]["reason"] == "exists"
    assert path.read_text(encoding="utf-8") == GOOD


def test_create_script_needs_the_directory(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "missing" / "new.yaml"
    error = engine.error("create_script", {"path": str(path), "name": "X"})
    assert error["code"] == "validation_failed"
    assert error["details"]["reason"] == "no_directory"
    assert not path.parent.exists()


# --- get_script ---------------------------------------------------------------------------


def test_get_script_from_path_and_source(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    write(path, GOOD)
    script = get(engine, load(engine, path))
    assert (script.source, script.version) == (GOOD, script_version(GOOD))
    assert (script.name, script.path, script.valid, script.errors) == (
        "Demo", str(path), True, [])
    greet, loop = script.steps
    assert (greet.id, greet.type, greet.line) == ("greet", "log", 10)
    assert greet.params["message"] == "hello"
    assert loop.blocks is not None and loop.blocks["do"][0].id == "nap"
    assert [p["name"] for p in script.params] == ["who"]
    assert script.vars == {"count": 3, "names": ["a", "b"]}
    assert script.input == {"mouse": {"strategy": "direct"}}

    from_source = engine.ok("get_script", {"script_id": engine.load_source(GOOD)})
    assert "path" not in from_source  # null is omitted
    assert from_source["source"] == GOOD

    assert engine.error("get_script", {"script_id": "s99"})["code"] == "script_not_found"


def test_get_script_with_invalid_current_text(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    write(path, GOOD)
    script_id = load(engine, path)
    write(path, INVALID)
    wait_changes(engine, 1)
    script = get(engine, script_id)
    assert (script.source, script.version) == (INVALID, script_version(INVALID))
    assert script.valid is False
    assert [e.code for e in script.errors] == ["unknown_step_type"]
    # The structure is still that of the last valid version.
    assert script.name == "Demo"
    assert script.vars == {"count": 3, "names": ["a", "b"]}
    assert [step.id for step in script.steps] == ["greet", "loop_1"]


# --- watching -----------------------------------------------------------------------------


def test_modified_file_emits_and_replaces_the_script(engine: Engine, factory: FakeFactory,
                                                     tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    write(path, GOOD)
    script_id = load(engine, path)
    write(path, CHANGED)
    event = wait_changes(engine, 1)[0]
    assert event["run_id"] is None
    change = payload(event)
    assert (change.script_id, change.source, change.valid) == (script_id, "disk", True)
    assert change.version == script_version(CHANGED)
    assert change.errors is None
    assert get(engine, script_id).version == change.version

    run_and_finish(engine, script_id)
    assert factory.last.script.steps[0].id == "welcome"

    time.sleep(QUIET_S)
    assert len(changes(engine)) == 1  # one change, one event


def test_invalid_edit_blocks_runs_until_fixed(engine: Engine, factory: FakeFactory,
                                              tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    write(path, GOOD)
    script_id = load(engine, path)

    write(path, INVALID)
    bad = payload(wait_changes(engine, 1)[0])
    assert (bad.source, bad.valid, bad.version) == ("disk", False, script_version(INVALID))
    assert bad.errors is not None and bad.errors[0].code == "unknown_step_type"
    error = engine.error("run", {"script_id": script_id, "mode": "normal"})
    assert error["code"] == "validation_failed"
    assert error["details"]["errors"][0]["code"] == "unknown_step_type"

    write(path, CHANGED)
    fixed = payload(wait_changes(engine, 2)[1])
    assert (fixed.source, fixed.valid, fixed.errors) == ("disk", True, None)
    run_and_finish(engine, script_id)
    assert factory.last.script.steps[0].id == "welcome"


def test_deleted_file_emits_once_and_keeps_the_script(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    write(path, GOOD)
    script_id = load(engine, path)
    path.unlink()
    deleted = payload(wait_changes(engine, 1)[0])
    assert (deleted.script_id, deleted.source, deleted.version) == (script_id, "deleted", None)
    assert deleted.valid is True
    time.sleep(QUIET_S)
    assert len(changes(engine)) == 1
    script = get(engine, script_id)
    assert (script.source, script.valid) == (GOOD, True)
    run_and_finish(engine, script_id)

    # Back with the same content: announced again, since clients were told it was gone.
    write(path, GOOD)
    back = payload(wait_changes(engine, 2)[1])
    assert (back.source, back.version, back.valid) == ("disk", script_version(GOOD), True)
    time.sleep(QUIET_S)
    assert len(changes(engine)) == 2


def test_touch_without_change_emits_nothing(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    write(path, GOOD)
    load(engine, path)
    time.sleep(QUIET_S)
    future = time.time() + 10
    os.utime(path, (future, future))
    write(path, GOOD)  # rewritten with the same bytes
    time.sleep(QUIET_S)
    assert changes(engine) == []


def test_store_update_is_not_reported_as_a_disk_change(engine: Engine,
                                                       tmp_path: Path) -> None:
    """What ``edit_script`` does: write the file and update the entry under the store's
    file lock. The watcher must not then report the same content as ``disk``."""
    path = tmp_path / "demo.yaml"
    write(path, GOOD)
    script_id = load(engine, path)
    store = engine.runs.scripts
    entry = store.get(script_id)
    with store.file_lock:
        write(path, CHANGED)
        store.update(entry, CHANGED, store.load_text(CHANGED, source_path=str(path)))
    time.sleep(QUIET_S)
    assert changes(engine) == []
    assert get(engine, script_id).version == script_version(CHANGED)


def test_source_scripts_are_not_watched(engine: Engine, tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine.load_source(GOOD)
    time.sleep(QUIET_S)
    assert changes(engine) == []


def test_each_loaded_copy_gets_its_own_event(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    write(path, GOOD)
    first, second = load(engine, path), load(engine, path)
    write(path, CHANGED)
    events = wait_changes(engine, 2)
    assert sorted(payload(e).script_id for e in events) == sorted([first, second])


def test_watcher_stops_at_shutdown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(scripts_module, "WATCH_INTERVAL_S", POLL_S)
    before = set(threading.enumerate())
    engine = Engine()
    watchers = [t for t in threading.enumerate()
                if t.name == "j5-script-watcher" and t not in before]
    assert len(watchers) == 1 and watchers[0].daemon
    path = tmp_path / "demo.yaml"
    write(path, GOOD)
    load(engine, path)
    engine.close()
    assert not watchers[0].is_alive()
    write(path, CHANGED)
    time.sleep(QUIET_S)
    assert changes(engine) == []
