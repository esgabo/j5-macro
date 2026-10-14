"""In-memory model of a loaded script (docs/contract.md, "Script format and step registry").

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
ParamType = Literal["string", "integer", "number", "boolean"]
NO_DEFAULT: Any = object()
"""``ScriptParam.default`` of a required param."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ScriptParam:
    """A value the script takes from outside (the ``params`` section)."""

    name: str
    type: ParamType = "string"
    description: str | None = None
    default: Any = NO_DEFAULT
    secret: bool = False
    line: int | None = None

    @property
    def required(self) -> bool:
        return self.default is NO_DEFAULT

    def to_json(self) -> dict[str, Any]:
        """The ``load`` result's description of this param (a secret's default is never
        reported; secrets have none anyway)."""
        info: dict[str, Any] = {"name": self.name, "type": self.type,
                                "secret": self.secret, "required": self.required}
        if self.description is not None:
            info["description"] = self.description
        if not self.required and not self.secret:
            info["default"] = self.default
        return info


@dataclass(frozen=True, slots=True, kw_only=True)
class StoreAs:
    """The common ``as`` field: the variable that keeps a step's result."""

    name: str
    secret: bool = False
    """The result's strings become secrets of the run (masked) when it is stored."""

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "secret": self.secret}


@dataclass(frozen=True, slots=True, kw_only=True)
class EmbeddedImage:
    """A template carried in the script (the ``images`` section), already decoded from
    base64 and checked; steps refer to it as ``#name``."""

    name: str
    format: str
    """The key it was written under: ``png``."""
    data: bytes = field(repr=False)
    """The image file's bytes."""
    width: int
    height: int
    scale: float | None = None
    """The display scale it was captured at, when given: its capture scale, replacing
    the script's ``capture_scale``."""
    line: int | None = None

    def to_json(self) -> dict[str, Any]:
        """The ``load`` and ``get_script`` description: never the data itself."""
        info: dict[str, Any] = {"name": self.name, "format": self.format,
                                "bytes": len(self.data), "width": self.width,
                                "height": self.height}
        if self.scale is not None:
            info["scale"] = self.scale
        return info


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
    store_as: StoreAs | None = None
    """The common ``as`` field; None when the result is not kept."""
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
    params: dict[str, ScriptParam] = field(default_factory=dict)
    """Declared params, in declaration order."""
    vars: dict[str, Any] = field(default_factory=dict)
    images: dict[str, EmbeddedImage] = field(default_factory=dict)
    """Embedded templates, by name, in declaration order."""
    capture_scale: float | None = None
    """The display scale the templates and pixel offsets were taken at (the top-level
    ``capture_scale``); None when not given, which means 1."""
    source_path: str | None = None
    text: str = ""
    warnings: list[ErrorInfo] = field(default_factory=list)

    def walk(self) -> Iterator[Step]:
        """Every step in document order."""
        for step in self.steps:
            yield from step.walk()

    def find(self, step_id: str) -> Step | None:
        return next((step for step in self.walk() if step.id == step_id), None)
