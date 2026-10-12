"""In-memory model of a loaded script (docs/contract.md, "Formato de script").

The loader builds it from YAML; the interpreter only ever reads it. Both sides depend
on this module and nothing else, which keeps them decoupled.

Paths
-----
``Step.path`` locates a step in the tree and alternates list positions and block slots:
``(3,)`` is the fourth top-level step; ``(3, 1, 0)`` is the first step of the second
block slot of that step. Slots are numbered in the order the step type declares them
(``if``: ``then`` = 0, ``else`` = 1; ``loop``: ``do`` = 0). Runtime loop iteration
indices are not part of the static path; the interpreter adds them to events.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from j5_macro.protocol.messages import ErrorInfo

OnError = Literal["stop", "continue", "retry", "pause"]
StepPath = tuple[int, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Step:
    """One step. ``params`` are type-specific and already defaulted by the loader."""

    id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    path: StepPath = ()
    line: int | None = None
    breakpoint: bool = False
    on_error: OnError = "stop"
    timeout: float | None = None
    """Seconds, already parsed from the YAML duration."""
    input: dict[str, Any] | None = None
    """Per-step input profile override (``mouse`` / ``keyboard`` keys)."""
    blocks: dict[str, list[Step]] = field(default_factory=dict)
    """Child step lists of block steps, keyed by slot name (``then``, ``else``, ``do``)."""

    def walk(self) -> Iterator[Step]:
        """This step and all nested steps, in document order."""
        yield self
        for children in self.blocks.values():
            for child in children:
                yield from child.walk()


@dataclass(frozen=True, slots=True, kw_only=True)
class Script:
    """A parsed and validated script."""

    name: str
    steps: list[Step]
    version: int = 1
    """Script format version (the ``version:`` key), not the file revision."""
    input: dict[str, Any] = field(default_factory=dict)
    vars: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None
    text: str = ""
    warnings: list[ErrorInfo] = field(default_factory=list)

    def walk(self) -> Iterator[Step]:
        """Every step in document order."""
        for step in self.steps:
            yield from step.walk()

    def find(self, step_id: str) -> Step | None:
        return next((step for step in self.walk() if step.id == step_id), None)
