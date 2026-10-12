"""Breakpoint line mapping and enclosing blocks of the DAP adapter."""

from __future__ import annotations

import pytest

from dap_support import SCRIPT, tree_of
from j5_macro.dap.script_map import ScriptMap, resolve_line

SOURCE = SCRIPT.splitlines()


@pytest.fixture(scope="module")
def script() -> ScriptMap:
    return ScriptMap.from_tree(tree_of())


def lines(script: ScriptMap) -> dict[str, int | None]:
    return {step.id: step.line for step in script.steps}


def test_tree_is_walked_in_document_order(script: ScriptMap) -> None:
    assert lines(script) == {"first": 8, "outer": 11, "check": 15, "inner": 19,
                             "other": 22, "last": 24}
    assert script.by_id["inner"].path == (1, 0, 0, 0, 0)


@pytest.mark.parametrize(("line", "expected"), [
    (8, "first"),     # exact
    (11, "outer"),
    (19, "inner"),    # exact, nested
    (9, "first"),     # content inside a step
    (13, "outer"),    # `times:` of the loop
    (17, "check"),    # `when:` of the if
    (20, "inner"),    # parameters of a nested step
    (21, "check"),    # `else:` belongs to the if
    (23, "other"),
    (25, "last"),     # inside the last step
    (7, "first"),     # comment: the next content line
    (10, "outer"),    # blank line between steps
    (1, "first"),     # header: the first step after it
    (6, "first"),     # `steps:`
])
def test_lines_resolve_to_steps(script: ScriptMap, line: int, expected: str) -> None:
    resolution = resolve_line(script, line, SOURCE)
    assert resolution.step is not None
    assert resolution.step.id == expected


@pytest.mark.parametrize("line", [26, 27, 400])
def test_lines_after_the_last_step_do_not_resolve(script: ScriptMap, line: int) -> None:
    resolution = resolve_line(script, line, SOURCE)
    assert resolution.step is None
    assert resolution.message == "no step at or after this line"


def test_without_source_the_next_step_is_used(script: ScriptMap) -> None:
    assert resolve_line(script, 8).step is not None
    assert resolve_line(script, 9).step.id == "outer"  # type: ignore[union-attr]
    assert resolve_line(script, 20).step.id == "other"  # type: ignore[union-attr]
    assert resolve_line(script, 25).step is None


def test_empty_script() -> None:
    assert resolve_line(ScriptMap(), 1).message == "the script has no steps"


def test_enclosing_blocks_innermost_first(script: ScriptMap) -> None:
    assert [s.id for s in script.enclosing((1, 0, 0, 0, 0))] == ["check", "outer"]
    assert [s.id for s in script.enclosing((1, 0, 0))] == ["outer"]
    assert script.enclosing((0,)) == []
