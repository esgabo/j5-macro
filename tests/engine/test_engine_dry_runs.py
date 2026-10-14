"""Dry runs through the engine with the real interpreter: ``run``'s ``strict`` and
``run_finished.warnings`` (docs/contract.md, "Strict dry runs")."""

from __future__ import annotations

import json
import sys

import pytest
from engine_harness import Engine

WARNS = """\
steps:
  - id: one
    log: {message: careful, level: warning}
  - id: two
    log: {message: fine}
  - id: three
    log: {message: careful again, level: warning}
"""

COMMAND = json.dumps([sys.executable, "-c", "print('hi')"])
EXEC = f"""\
steps:
  - id: prog
    exec: {{command: {COMMAND}, wait: true}}
"""


@pytest.mark.parametrize("mode", ["normal", "dry"])
def test_run_finished_counts_the_warnings(mode: str) -> None:
    engine = Engine()
    run_id = engine.start(engine.load_source(WARNS), mode=mode)
    finished = engine.wait_finished(run_id)["payload"]
    engine.close()
    assert finished["status"] == "completed"
    assert finished["warnings"] == 2


def test_a_clean_run_has_no_warnings() -> None:
    engine = Engine()
    run_id = engine.start(engine.load_source())
    assert engine.wait_finished(run_id)["payload"]["warnings"] == 0
    engine.close()


@pytest.mark.parametrize("mode", ["normal", "step"])
def test_strict_needs_a_dry_run(mode: str) -> None:
    engine = Engine()
    script_id = engine.load_source()
    error = engine.error("run", {"script_id": script_id, "mode": mode, "strict": True})
    assert error["code"] == "validation_failed"
    assert error["details"] == {"field": "strict"}
    assert engine.ok("run", {"script_id": script_id, "mode": mode, "strict": False})
    engine.close()


def test_a_dry_run_warns_and_a_strict_one_fails() -> None:
    engine = Engine()
    script_id = engine.load_source(EXEC)
    run_id = engine.start(script_id, mode="dry")
    finished = engine.wait_finished(run_id)["payload"]
    assert (finished["status"], finished["warnings"]) == ("completed", 1)

    run_id = engine.start(script_id, mode="dry", strict=True)
    finished = engine.wait_finished(run_id)["payload"]
    assert finished["status"] == "failed"
    assert finished["error"]["code"] == "capability_missing"
    assert finished["warnings"] == 0
    engine.close()
