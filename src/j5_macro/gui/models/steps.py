"""The step tree of a script, as ``load`` and ``get_script`` report it.

Each step has its stable ``id``, its ``type``, its ``path`` (docs/contract.md, "Script
format and step registry": list positions alternating with block slots), its source
``line``, its parameters and common fields, and, for blocks (``if``, ``loop``), the
steps of each block slot in the order the step type declares them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

SUMMARY_LENGTH = 60
"""Longest summary :func:`step_summary` returns."""


@dataclass(frozen=True, slots=True)
class StepItem:
    id: str
    type: str
    path: tuple[int, ...]
    line: int | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    breakpoint: bool = False
    """The script's own ``break: true``."""
    common: Mapping[str, Any] = field(default_factory=dict)
    """Common fields other than ``id`` and ``break``: ``on_error``, ``timeout``,
    ``input`` and ``as``, when present."""
    blocks: Mapping[str, tuple[StepItem, ...]] = field(default_factory=dict)
    """Block slot name (``then``, ``else``, ``do``) to its steps."""

    @property
    def depth(self) -> int:
        """1 at the top level, 2 inside one block, and so on (as stepping counts it)."""
        return (len(self.path) + 1) // 2

    @property
    def children(self) -> Iterator[tuple[str, StepItem]]:
        """``(block, step)`` for each direct child, block by block."""
        for block, steps in self.blocks.items():
            for step in steps:
                yield block, step


_COMMON = ("on_error", "timeout", "input", "as")


def parse_step(raw: Mapping[str, Any]) -> StepItem:
    """One step node of a ``load``/``get_script`` result; unknown fields are ignored."""
    blocks: dict[str, tuple[StepItem, ...]] = {}
    raw_blocks = raw.get("blocks")
    if isinstance(raw_blocks, Mapping):
        for name, steps in raw_blocks.items():
            blocks[str(name)] = tuple(parse_steps(steps))
    params = raw.get("params")
    path = raw.get("path")
    line = raw.get("line")
    return StepItem(
        id=str(raw.get("id", "")),
        type=str(raw.get("type", "")),
        path=tuple(int(n) for n in path) if isinstance(path, list) else (),
        line=line if isinstance(line, int) else None,
        params=dict(params) if isinstance(params, Mapping) else {},
        breakpoint=raw.get("break") is True,
        common={key: raw[key] for key in _COMMON if raw.get(key) is not None},
        blocks=blocks,
    )


def parse_steps(raw: object) -> list[StepItem]:
    """The ``steps`` list of a ``load``/``get_script`` result; [] when it is not a list."""
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        return []
    return [parse_step(item) for item in raw if isinstance(item, Mapping)]


def iter_steps(steps: Sequence[StepItem]) -> Iterator[StepItem]:
    """Every step, depth first, in source order."""
    for step in steps:
        yield step
        for _, child in step.children:
            yield from iter_steps([child])


def find_step(steps: Sequence[StepItem], step_id: str) -> StepItem | None:
    return next((step for step in iter_steps(steps) if step.id == step_id), None)


def step_summary(step: StepItem, limit: int = SUMMARY_LENGTH) -> str:
    """A one-line description of the parameters: ``x=120 y=40 button=left``."""
    parts = [f"{name}={_short(value)}" for name, value in step.params.items()]
    text = " ".join(parts)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
