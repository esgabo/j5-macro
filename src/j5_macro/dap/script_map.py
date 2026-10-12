"""The loaded script's step tree as the adapter needs it: lines, paths and enclosing blocks.

Pure functions over the ``steps`` of the engine's ``load`` result and, when available, the
script's text, so breakpoint line mapping is testable without an engine.

Breakpoint lines map to steps like this (lines are 1-based, as the engine reports them):

1. A line where a step starts (its ``line``) is that step.
2. A blank or comment line maps like the next line with content.
3. A content line inside a step (indented deeper than the step's ``-`` and not closed by
   a line at or left of it) is the innermost step that contains it, e.g. ``click:`` under
   ``- id: open_menu``, or ``else:`` of an ``if``.
4. Any other line before a step (the header: ``name``, ``vars``, ``steps:``) is the first
   step that starts after it.
5. Otherwise (past the last step) there is no step: the breakpoint is not verified.

Without the script's text only rules 1 and 4 apply.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class StepInfo:
    id: str
    type: str
    path: tuple[int, ...]
    line: int | None
    order: int
    """Position in a pre-order walk of the tree (document order)."""


@dataclass(slots=True)
class ScriptMap:
    steps: list[StepInfo] = field(default_factory=list)
    """Every step, pre-order."""
    by_id: dict[str, StepInfo] = field(default_factory=dict)
    by_path: dict[tuple[int, ...], StepInfo] = field(default_factory=dict)

    @classmethod
    def from_tree(cls, tree: Any) -> ScriptMap:
        result = cls()
        result._walk(tree if isinstance(tree, list) else [])
        return result

    def _walk(self, nodes: Sequence[Any]) -> None:
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            step_id, path, line = node.get("id"), node.get("path"), node.get("line")
            if isinstance(step_id, str) and isinstance(path, list):
                info = StepInfo(id=step_id, type=str(node.get("type", "?")),
                                path=tuple(int(p) for p in path),
                                line=line if isinstance(line, int) else None,
                                order=len(self.steps))
                self.steps.append(info)
                self.by_id.setdefault(info.id, info)
                self.by_path.setdefault(info.path, info)
            blocks = node.get("blocks")
            if isinstance(blocks, Mapping):
                for children in blocks.values():
                    if isinstance(children, list):
                        self._walk(children)

    @property
    def first_step(self) -> StepInfo | None:
        return self.steps[0] if self.steps else None

    def enclosing(self, path: Sequence[int]) -> list[StepInfo]:
        """The block steps around the step at ``path``, innermost first.

        ``path`` alternates a position in a list and a block slot, so the enclosing steps
        are its odd-length proper prefixes."""
        found: list[StepInfo] = []
        for size in range(len(path) - 2, 0, -2):
            info = self.by_path.get(tuple(path[:size]))
            if info is not None:
                found.append(info)
        return found


@dataclass(frozen=True, slots=True)
class LineResolution:
    step: StepInfo | None
    message: str | None = None


def _is_filler(text: str) -> bool:
    stripped = text.strip()
    return not stripped or stripped.startswith("#")


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


def resolve_line(script: ScriptMap, line: int,
                 source: Sequence[str] | None = None) -> LineResolution:
    """The step a breakpoint on ``line`` (1-based) stops at; see the module docstring."""
    located = sorted((s for s in script.steps if s.line is not None),
                     key=lambda s: (s.line, s.order))
    if not located:
        return LineResolution(None, "the script has no steps")
    for step in located:
        if step.line == line:
            return LineResolution(step)

    if source is not None:
        target = line
        while 1 <= target <= len(source) and _is_filler(source[target - 1]):
            target += 1
        if target > len(source):
            return LineResolution(None, "no step at or after this line")
        if target != line:
            return resolve_line(script, target, source)
        container = _containing_step(located, line, source)
        if container is not None:
            return LineResolution(container)

    after = next((s for s in located if s.line is not None and s.line > line), None)
    if after is None:
        return LineResolution(None, "no step at or after this line")
    return LineResolution(after)


def _containing_step(located: Sequence[StepInfo], line: int,
                     source: Sequence[str]) -> StepInfo | None:
    """The innermost step whose YAML item contains the content line ``line``."""
    indent = _indent(source[line - 1])
    for step in reversed(located):
        assert step.line is not None
        if step.line >= line or step.line > len(source):
            continue
        item_indent = _indent(source[step.line - 1])
        if indent <= item_indent:
            continue
        closed = any(not _is_filler(source[n - 1]) and _indent(source[n - 1]) <= item_indent
                     for n in range(step.line + 1, line))
        if not closed:
            return step
    return None
