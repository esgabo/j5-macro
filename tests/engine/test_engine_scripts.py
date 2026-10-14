"""Script commands: list_step_types, load, validate and the advertised capabilities."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from engine_harness import Engine

from j5_macro.engine import scripts as scripts_module
from j5_macro.engine.scripts import format_seconds
from j5_macro.protocol.messages import ErrorInfo
from j5_macro.protocol.payloads import (
    HelloResult,
    ListStepTypesResult,
    LoadResult,
    ReadyEvent,
    ValidateResult,
)
from j5_macro.steps import default_registry

GOOD = """\
name: Demo
input:
  mouse: {strategy: human, speed: 1.0}
steps:
  - id: greet
    log: hello
    timeout: 1500ms
    on_error: continue
  - loop:
      times: 2
      do:
        - id: nap
          break: true
          sleep: 10ms
    as: rounds
"""

BAD = """\
steps:
  - id: greet
    frobnicate: {}
  - log: {}
"""


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = Engine()
    yield eng
    eng.close()


def test_hello_and_ready_list_registered_step_types(engine: Engine) -> None:
    names = default_registry().names()
    ready = engine.engine_events("ready")[0]
    assert ReadyEvent.from_json(ready["payload"]).capabilities.step_types == names
    hello = HelloResult.from_json(engine.ok("hello", {"v_min": 1, "v_max": 1,
                                                       "client": "again"}))
    assert hello.capabilities.step_types == names


def test_list_step_types_matches_registry(engine: Engine) -> None:
    result = ListStepTypesResult.from_json(engine.ok("list_step_types", {}))
    assert [info.name for info in result.step_types] == default_registry().names()
    click = next(info for info in result.step_types if info.name == "click")
    assert click.json_schema
    assert any(param.name == "button" for param in click.params)
    assert [field.name for field in click.result][:4] == ["x", "y", "button", "clicks"]
    button = next(param for param in click.params if param.name == "button")
    assert button.choices == ["left", "right", "middle"]
    assert next(p for p in click.params if p.name == "x").choices is None
    assert (click.kind, click.blocks, click.shorthand) == ("action", [], None)
    by_name = {info.name: info for info in result.step_types}
    assert (by_name["if"].kind, by_name["if"].blocks) == ("control", ["then", "else"])
    assert by_name["if"].required_blocks == ["then"]
    assert by_name["loop"].blocks == ["do"]
    assert by_name["sleep"].shorthand == "duration"


def test_load_from_path(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    path.write_text(GOOD, encoding="utf-8")
    result = LoadResult.from_json(engine.ok("load", {"path": str(path)}))
    assert result.script_id == "s1"
    greet, loop = result.steps
    assert (greet.id, greet.type, greet.path, greet.line) == ("greet", "log", [0], 5)
    assert greet.params["message"] == "hello"
    assert greet.timeout == "1.5s"  # normalized from 1500ms
    assert greet.on_error == "continue"
    assert greet.blocks is None
    assert greet.store_as is None
    assert loop.store_as == {"name": "rounds", "secret": False}
    assert loop.id == "loop_1"  # generated, so there is a warning
    assert loop.blocks is not None
    (nap,) = loop.blocks["do"]
    assert (nap.id, nap.path, nap.breakpoint) == ("nap", [1, 0, 0], True)
    assert [w.step_id for w in result.warnings] == ["loop_1"]
    assert result.warnings[0].line == 9


def test_load_from_source_assigns_new_ids(engine: Engine) -> None:
    first = LoadResult.from_json(engine.ok("load", {"source": GOOD}))
    second = LoadResult.from_json(engine.ok("load", {"source": GOOD}))
    assert (first.script_id, second.script_id) == ("s1", "s2")
    assert first.steps == second.steps


def test_load_with_errors(engine: Engine) -> None:
    error = engine.error("load", {"source": BAD})
    details = error["details"]
    errors = [ErrorInfo.from_json(e) for e in details["errors"]]
    assert len(errors) == 2
    assert error["code"] == errors[0].code == "unknown_step_type"
    assert errors[0].step_id == "greet"
    assert errors[0].message in error["message"]
    assert "and 1 more" in error["message"]
    assert errors[1].code == "validation_failed"
    assert details["warnings"] == []
    # A failed load does not consume a script id.
    assert engine.load_source() == "s1"


def test_load_parse_error_and_missing_file(engine: Engine, tmp_path: Path) -> None:
    error = engine.error("load", {"source": "steps: [\n"})
    assert error["code"] == "parse_error"
    assert error["details"]["errors"][0]["line"] >= 1
    missing = engine.error("load", {"path": str(tmp_path / "nope.yaml")})
    assert missing["code"] == "script_not_found"


def test_load_payload_needs_exactly_one_of_path_and_source(engine: Engine) -> None:
    assert engine.error("load", {})["code"] == "validation_failed"
    assert engine.error("load", {"path": "a", "source": "b"})["code"] == "validation_failed"


@pytest.fixture
def unwatched_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """An engine whose file watcher never polls, so only ``validate`` reads files."""
    monkeypatch.setattr(scripts_module, "WATCH_INTERVAL_S", 3600.0)
    eng = Engine()
    yield eng
    eng.close()


def test_validate_rereads_a_changed_file(unwatched_engine: Engine, tmp_path: Path) -> None:
    engine = unwatched_engine
    path = tmp_path / "demo.yaml"
    path.write_text(GOOD, encoding="utf-8")
    script_id = engine.ok("load", {"path": str(path)})["script_id"]

    ok = ValidateResult.from_json(engine.ok("validate", {"script_id": script_id}))
    assert ok.ok is True and ok.errors == []
    assert engine.ok("validate", {"script_id": script_id})["warnings"][0]["step_id"] \
        == "loop_1"

    path.write_text(BAD, encoding="utf-8")
    bad = ValidateResult.from_json(engine.ok("validate", {"script_id": script_id}))
    assert bad.ok is False
    assert [e.code for e in bad.errors] == ["unknown_step_type", "validation_failed"]

    path.unlink()
    gone = ValidateResult.from_json(engine.ok("validate", {"script_id": script_id}))
    assert gone.ok is False
    assert gone.errors[0].code == "script_not_found"

    # The loaded script is not replaced: the same id still names the original steps.
    path.write_text(GOOD.replace("greet", "renamed"), encoding="utf-8")
    assert engine.ok("validate", {"script_id": script_id})["ok"] is True
    assert engine.runs.scripts.get(script_id).script.steps[0].id == "greet"


def test_validate_source_script_and_unknown_id(engine: Engine) -> None:
    script_id = engine.load_source(GOOD)
    assert engine.ok("validate", {"script_id": script_id})["ok"] is True
    error = engine.error("validate", {"script_id": "s99"})
    assert error["code"] == "script_not_found"


def test_format_seconds() -> None:
    assert format_seconds(2.0) == "2s"
    assert format_seconds(0.5) == "500ms"
    assert format_seconds(1.5) == "1.5s"
    assert format_seconds(0.0) == "0s"
