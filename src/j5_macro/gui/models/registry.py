"""The step registry as ``list_step_types`` reports it, the palette's groups and the
template of a new step of each type (docs/contract.md, "Script format and step
registry")."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from j5_macro.script.images import REFERENCE_PREFIX


@dataclass(frozen=True, slots=True)
class ParamInfo:
    """One parameter of a step type."""

    name: str
    type: str
    required: bool = False
    default: Any = None
    doc: str = ""
    choices: tuple[Any, ...] | None = None


@dataclass(frozen=True, slots=True)
class StepType:
    """One step type of the registry (a built-in or a plugin's)."""

    name: str
    params: tuple[ParamInfo, ...] = ()
    doc: str = ""
    kind: str = "action"
    blocks: tuple[str, ...] = ()
    """Block slots, in path order (``if``: ``then``, ``else``)."""
    required_blocks: tuple[str, ...] = ()
    shorthand: str | None = None

    def param(self, name: str) -> ParamInfo | None:
        return next((p for p in self.params if p.name == name), None)

    @property
    def summary(self) -> str:
        """The first sentence of the documentation."""
        text = self.doc.strip()
        end = text.find(". ")
        return text if end < 0 else text[: end + 1]

    def tooltip(self) -> str:
        """The documentation and the parameters, for a palette tooltip."""
        lines = [f"{self.name}: {self.doc}".rstrip()]
        for param in self.params:
            flag = " (required)" if param.required else (
                "" if param.default is None else f" (default {param.default})")
            lines.append(f"  {param.name}{flag}: {param.doc}")
        if self.blocks:
            lines.append(f"  blocks: {', '.join(self.blocks)}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class StepRegistry:
    """Every step type the engine knows, in the engine's order."""

    types: tuple[StepType, ...] = ()
    _by_name: Mapping[str, StepType] = field(default_factory=dict, repr=False,
                                              compare=False)

    @classmethod
    def from_result(cls, result: Mapping[str, Any]) -> StepRegistry:
        """The ``list_step_types`` result; malformed entries are skipped."""
        raw = result.get("step_types")
        types: list[StepType] = []
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, Mapping) and isinstance(item.get("name"), str):
                types.append(_step_type(item))
        return cls.of(types)

    @classmethod
    def of(cls, types: Iterable[StepType]) -> StepRegistry:
        listed = tuple(types)
        return cls(types=listed, _by_name={t.name: t for t in listed})

    def get(self, name: str) -> StepType | None:
        return self._by_name.get(name)

    def __iter__(self) -> Iterator[StepType]:
        return iter(self.types)

    def __len__(self) -> int:
        return len(self.types)

    def blocks_of(self, name: str) -> tuple[str, ...]:
        step_type = self.get(name)
        return () if step_type is None else step_type.blocks


def _step_type(raw: Mapping[str, Any]) -> StepType:
    params: list[ParamInfo] = []
    for item in raw.get("params") or []:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            continue
        choices = item.get("choices")
        params.append(ParamInfo(
            name=item["name"], type=str(item.get("type", "any")),
            required=item.get("required") is True, default=item.get("default"),
            doc=str(item.get("doc") or ""),
            choices=tuple(choices) if isinstance(choices, list) else None))
    return StepType(
        name=str(raw["name"]), params=tuple(params), doc=str(raw.get("doc") or ""),
        kind=str(raw.get("kind") or "action"),
        blocks=_names(raw.get("blocks")), required_blocks=_names(raw.get("required_blocks")),
        shorthand=raw["shorthand"] if isinstance(raw.get("shorthand"), str) else None)


def _names(raw: object) -> tuple[str, ...]:
    return tuple(str(n) for n in raw) if isinstance(raw, list) else ()


# --- the palette ------------------------------------------------------------------------


PALETTE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Mouse", ("click", "move", "drag", "scroll")),
    ("Keyboard", ("type", "key")),
    ("Wait", ("wait_image_in", "wait_image_out", "wait_window_in", "wait_window_out",
              "sleep")),
    ("Flow", ("if", "loop", "pause")),
    ("Data", ("set", "log")),
    ("Programs", ("exec",)),
)
"""The built-in step types by group, in palette order."""
PLUGINS_GROUP = "Plugins"
"""Where step types not in :data:`PALETTE_GROUPS` (plugin steps) are listed."""


def palette_groups(registry: StepRegistry,
                   query: str = "") -> list[tuple[str, list[StepType]]]:
    """The groups of the palette with their step types, empty groups left out. ``query``
    keeps the types whose name or documentation contains it (case-insensitive)."""
    needle = query.strip().lower()

    def wanted(step_type: StepType) -> bool:
        return not needle or needle in step_type.name.lower() or \
            needle in step_type.doc.lower()

    grouped: list[tuple[str, list[StepType]]] = []
    placed: set[str] = set()
    for group, names in PALETTE_GROUPS:
        types = [t for name in names if (t := registry.get(name)) is not None]
        placed.update(t.name for t in types)
        grouped.append((group, [t for t in types if wanted(t)]))
    others = [t for t in registry if t.name not in placed]
    grouped.append((PLUGINS_GROUP, [t for t in others if wanted(t)]))
    return [(group, types) for group, types in grouped if types]


# --- new steps --------------------------------------------------------------------------

_TEMPLATES: Mapping[str, Mapping[str, Any]] = {
    "click": {"x": 0, "y": 0},
    "move": {"x": 0, "y": 0},
    "drag": {"from": {"x": 0, "y": 0}, "to": {"x": 100, "y": 0}},
    "type": {"text": ""},
    "key": {"press": "enter"},
    "wait_window_in": {"title": "Untitled"},
    "wait_window_out": {"title": "Untitled"},
    "sleep": {"duration": "1s"},
    "if": {"when": "true", "then": []},
    "loop": {"times": 2, "do": []},
    "set": {"name": "value", "value": 0},
    "log": {"message": "message"},
    "exec": {"command": ["program"]},
}
"""Parameters of a new step of a built-in type, chosen so the script stays valid."""

_PLACEHOLDERS: Mapping[str, Any] = {
    "string": "", "integer": 0, "number": 0, "boolean": False, "duration": "1s",
    "expression": "true", "condition": "true", "list": [], "object": {}, "any": "",
}


def new_step(step_type: StepType, image_names: Sequence[str] = ()) -> dict[str, Any]:
    """A new step of ``step_type`` as ``insert`` takes it: the type's key with a value
    for each required parameter (and an empty list for each required block). An image
    parameter takes the first embedded image when there is one, else a file name."""
    params: dict[str, Any] = dict(_TEMPLATES.get(step_type.name, {}))
    image = (f"{REFERENCE_PREFIX}{image_names[0]}" if image_names else "image.png")
    for param in step_type.params:
        if param.name == "image" and param.required:
            params["image"] = image
        elif param.required and param.name not in params:
            if param.choices:
                params[param.name] = param.choices[0]
            else:
                params[param.name] = _PLACEHOLDERS.get(param.type, "")
    for block in step_type.required_blocks:
        params.setdefault(block, [])
    return {step_type.name: params}
