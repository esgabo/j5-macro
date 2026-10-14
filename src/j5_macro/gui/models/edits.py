"""The ``edit_script`` edits behind the editor's actions (docs/contract.md, "Edit
kinds"): where a step goes, moving it up, down or by drag and drop, deleting and
duplicating it. Pure functions of the step tree, so they are tested without a display.

Positions are the contract's ``to``: ``{after_step_id}``, or ``{parent_step_id?, block?,
index?}``. A function returns ``None`` when the action does nothing (moving the first
step up) or cannot apply (dropping a block into itself).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from j5_macro.gui.models.registry import StepRegistry
from j5_macro.gui.models.steps import StepItem, iter_steps

Position = dict[str, Any]
Edit = dict[str, Any]
Where = Literal["before", "after", "into"]
DEFAULT_ON_ERROR = "stop"


@dataclass(frozen=True, slots=True)
class Location:
    """Where a step is: its list (``siblings``), its index there, and the step and block
    holding that list (``None`` at the top level)."""

    step: StepItem
    siblings: tuple[StepItem, ...]
    index: int
    parent: StepItem | None = None
    block: str | None = None

    def list_position(self, index: int | None = None) -> Position:
        """A position in this step's list (``index`` None: the end)."""
        position: Position = {}
        if self.parent is not None:
            position = {"parent_step_id": self.parent.id, "block": self.block}
        if index is not None:
            position["index"] = index
        return position


def locate(steps: Sequence[StepItem], step_id: str, parent: StepItem | None = None,
           block: str | None = None) -> Location | None:
    for index, step in enumerate(steps):
        if step.id == step_id:
            return Location(step, tuple(steps), index, parent, block)
        for name, children in step.blocks.items():
            found = locate(children, step_id, step, name)
            if found is not None:
                return found
    return None


def contains(step: StepItem, step_id: str) -> bool:
    """Whether ``step_id`` is ``step`` or one of the steps of its blocks."""
    return any(s.id == step_id for s in iter_steps([step]))


# --- positions --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Target:
    """Where something is dropped or inserted: before, after or into a step, or into a
    block slot of a step (``block`` set, ``where`` is ``into``). ``step_id`` None is the
    end of the script."""

    step_id: str | None = None
    where: Where = "after"
    block: str | None = None


def position_for(steps: Sequence[StepItem], registry: StepRegistry, target: Target,
                 moving: str | None = None) -> Position | None:
    """The position ``target`` names, for a new step (``moving`` None) or for the step
    ``moving``. None when moving there changes nothing or is impossible."""
    if target.step_id is None:
        if moving is not None:
            last = steps[-1] if steps else None
            if last is not None and last.id == moving:
                return None
        return {}
    location = locate(steps, target.step_id)
    if location is None:
        return None
    if moving is not None:
        moved = locate(steps, moving)
        if moved is None:
            return None
        if contains(moved.step, target.step_id):
            return None  # onto itself, or into its own blocks
    step = location.step
    if target.where == "into":
        block = target.block
        if block is None:
            blocks = registry.blocks_of(step.type) or tuple(step.blocks)
            if not blocks:
                return position_for(steps, registry, Target(step.id, "after"), moving)
            block = blocks[0]
        children = step.blocks.get(block, ())
        if moving is not None and children and children[-1].id == moving:
            return None
        return {"parent_step_id": step.id, "block": block}
    if target.where == "after":
        if moving == step.id:
            return None
        if (moving is not None and location.index + 1 < len(location.siblings)
                and location.siblings[location.index + 1].id == moving):
            return None
        return {"after_step_id": step.id}
    # before
    if moving == step.id:
        return None
    if location.index == 0:
        return location.list_position(0)
    previous = location.siblings[location.index - 1]
    if previous.id == moving:
        return None
    return {"after_step_id": previous.id}


def insert_target(selected_step: str | None,
                  selected_block: tuple[str, str] | None = None) -> Target:
    """Where a new step goes: right after the selected step, at the end of the selected
    block row (``(step id, block)``), else at the end of the script."""
    if selected_block is not None:
        return Target(selected_block[0], "into", selected_block[1])
    if selected_step is not None:
        return Target(selected_step, "after")
    return Target()


# --- edits ------------------------------------------------------------------------------


def insert_edit(step: Mapping[str, Any], position: Position) -> Edit:
    return {"op": "insert", "step": dict(step), "to": dict(position)}


def move_edit(step_id: str, position: Position) -> Edit:
    return {"op": "move", "step_id": step_id, "to": dict(position)}


def delete_edit(step_id: str) -> Edit:
    return {"op": "delete", "step_id": step_id}


def update_edit(step_id: str, params: Mapping[str, Any] | None = None,
                common: Mapping[str, Any] | None = None) -> Edit:
    edit: Edit = {"op": "update", "step_id": step_id, "params": dict(params or {})}
    if common:
        edit["common"] = dict(common)
    return edit


def move_up(steps: Sequence[StepItem], step_id: str) -> Edit | None:
    """Move a step before the one above it; the first step of a block leaves the block,
    going right before the step that holds it."""
    location = locate(steps, step_id)
    if location is None:
        return None
    if location.index > 0:
        if location.index == 1:
            return move_edit(step_id, location.list_position(0))
        return move_edit(step_id, {"after_step_id": location.siblings[location.index - 2].id})
    if location.parent is None:
        return None
    outer = locate(steps, location.parent.id)
    assert outer is not None
    if outer.index == 0:
        return move_edit(step_id, outer.list_position(0))
    return move_edit(step_id, {"after_step_id": outer.siblings[outer.index - 1].id})


def move_down(steps: Sequence[StepItem], step_id: str) -> Edit | None:
    """Move a step after the one below it; the last step of a block leaves the block,
    going right after the step that holds it."""
    location = locate(steps, step_id)
    if location is None:
        return None
    if location.index + 1 < len(location.siblings):
        return move_edit(step_id, {"after_step_id": location.siblings[location.index + 1].id})
    if location.parent is None:
        return None
    return move_edit(step_id, {"after_step_id": location.parent.id})


def drop_edit(steps: Sequence[StepItem], registry: StepRegistry, step_id: str,
              target: Target) -> Edit | None:
    """The ``move`` that drops ``step_id`` on ``target``; None when nothing changes."""
    position = position_for(steps, registry, target, moving=step_id)
    return None if position is None else move_edit(step_id, position)


# --- copies -----------------------------------------------------------------------------


def step_as_yaml(step: StepItem, registry: StepRegistry, *,
                 keep_ids: bool = False) -> dict[str, Any]:
    """The step as ``insert`` takes it (and as it is written), its blocks included.
    Parameters equal to their declared default are left out, since the engine reports
    defaults as if they were written; ids are left out unless ``keep_ids``, so the
    engine gives the copy new ones."""
    step_type = registry.get(step.type)
    params: dict[str, Any] = {}
    for name, value in step.params.items():
        spec = step_type.param(name) if step_type is not None else None
        if spec is not None and spec.default is not None and value == spec.default \
                and type(value) is type(spec.default):
            continue
        params[name] = value
    for block, children in step.blocks.items():
        params[block] = [step_as_yaml(child, registry, keep_ids=keep_ids)
                         for child in children]
    node: dict[str, Any] = {}
    if keep_ids:
        node["id"] = step.id
    node[step.type] = params
    if step.breakpoint:
        node["break"] = True
    for name, value in step.common.items():
        if name == "on_error" and value == DEFAULT_ON_ERROR:
            continue  # reported even when not written
        node[name] = _as_written(value) if name == "as" else value
    return node


def _as_written(value: Any) -> Any:
    """``as`` as written: the name alone unless it is secret."""
    if isinstance(value, Mapping) and value.get("secret") is not True and "name" in value:
        return value["name"]
    return dict(value) if isinstance(value, Mapping) else value


def duplicate_edit(steps: Sequence[StepItem], registry: StepRegistry,
                   step_id: str) -> Edit | None:
    """Insert a copy of the step (with its blocks) right after it."""
    location = locate(steps, step_id)
    if location is None:
        return None
    return insert_edit(step_as_yaml(location.step, registry), {"after_step_id": step_id})


def neighbour_after_delete(steps: Sequence[StepItem], step_id: str) -> str | None:
    """The step to select once ``step_id`` is deleted: the next one in its list, else
    the previous one, else the step holding the list."""
    location = locate(steps, step_id)
    if location is None:
        return None
    if location.index + 1 < len(location.siblings):
        return location.siblings[location.index + 1].id
    if location.index > 0:
        return location.siblings[location.index - 1].id
    return location.parent.id if location.parent is not None else None


def step_at_line(steps: Sequence[StepItem], line: int) -> str | None:
    """The step whose text holds 1-based ``line``: the last step starting at or before
    it, in source order."""
    best: StepItem | None = None
    for step in iter_steps(steps):
        if step.line is not None and step.line <= line and (
                best is None or best.line is None or step.line >= best.line):
            best = step
    return best.id if best is not None else None
