"""Loaded scripts and the script commands: ``list_step_types``, ``load``, ``validate``
(docs/contract.md, "Commands" and "Script format and step registry")."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from j5_macro.engine.dispatch import CommandError
from j5_macro.engine.server import EngineServer
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import Command, ErrorInfo, JsonObject
from j5_macro.protocol.payloads import (
    Diagnostic,
    LoadPayload,
    LoadResult,
    ScriptRef,
    StepNode,
    ValidateResult,
)
from j5_macro.script.loader import LoadResult as LoaderResult
from j5_macro.script.loader import load_file, load_script
from j5_macro.script.model import Script, Step
from j5_macro.steps.registry import StepRegistry


@dataclass(slots=True)
class LoadedScript:
    script_id: str
    script: Script
    path: Path | None
    """The file it was loaded from; None for ``load`` with ``source``."""
    breakpoints: frozenset[str] = field(default_factory=frozenset)
    """Client breakpoints set with ``set_breakpoints``, by step id."""


class ScriptStore:
    """Scripts loaded in this engine session, by ``script_id`` (``s1``, ``s2``, ...)."""

    def __init__(self, registry: StepRegistry) -> None:
        self.registry = registry
        self._scripts: dict[str, LoadedScript] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def load_text(self, text: str, source_path: str | None = None) -> LoaderResult:
        return load_script(text, lookup=self.registry.spec, check=self.registry.check_params,
                           source_path=source_path)

    def load_path(self, path: Path) -> LoaderResult:
        return load_file(path, lookup=self.registry.spec, check=self.registry.check_params)

    def add(self, script: Script, path: Path | None) -> LoadedScript:
        with self._lock:
            self._counter += 1
            entry = LoadedScript(f"s{self._counter}", script, path)
            self._scripts[entry.script_id] = entry
            return entry

    def get(self, script_id: str) -> LoadedScript:
        with self._lock:
            entry = self._scripts.get(script_id)
        if entry is None:
            raise CommandError(ErrorCode.SCRIPT_NOT_FOUND, f"unknown script_id: {script_id!r}",
                               {"script_id": script_id})
        return entry

    def set_breakpoints(self, script_id: str, step_ids: list[str]) -> LoadedScript:
        entry = self.get(script_id)
        with self._lock:
            entry.breakpoints = frozenset(step_ids)
        return entry


# --- wire shapes ------------------------------------------------------------------------


def format_seconds(seconds: float) -> str:
    """A duration in the script syntax: ``2s``, ``1.5s`` or ``500ms``."""
    ms = round(seconds * 1000)
    if ms % 1000 == 0:
        return f"{ms // 1000}s"
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:g}s"


def step_node(step: Step) -> StepNode:
    return StepNode(
        id=step.id,
        type=step.type,
        path=list(step.path),
        line=step.line,
        params=step.params,
        breakpoint=step.breakpoint,
        on_error=step.on_error,
        timeout=None if step.timeout is None else format_seconds(step.timeout),
        input=step.input,
        blocks={slot: [step_node(child) for child in children]
                for slot, children in step.blocks.items()} or None,
    )


def diagnostic(info: ErrorInfo) -> Diagnostic:
    return Diagnostic(message=info.message, step_id=info.step_id, line=info.line)


def _warnings(result: LoaderResult) -> list[Diagnostic]:
    return [] if result.script is None else [diagnostic(w) for w in result.script.warnings]


# --- handlers ---------------------------------------------------------------------------


def install(server: EngineServer, store: ScriptStore) -> None:
    registry = store.registry

    def list_step_types(command: Command) -> JsonObject:
        return {"step_types": registry.describe()}

    def load(command: Command) -> JsonObject:
        payload = LoadPayload.from_json(command.payload)
        path: Path | None = None
        if payload.path is not None:
            # Absolute, so relative image paths in the script do not depend on the
            # engine's working directory changing later.
            path = Path(payload.path).absolute()
            result = store.load_path(path)
        else:
            assert payload.source is not None
            result = store.load_text(payload.source)
        if result.script is None:
            first = result.errors[0]
            raise CommandError(
                first.code,
                first.message if len(result.errors) == 1
                else f"{first.message} (and {len(result.errors) - 1} more errors)",
                {"errors": [e.to_json() for e in result.errors], "warnings": []},
            )
        entry = store.add(result.script, path)
        return LoadResult(
            script_id=entry.script_id,
            steps=[step_node(step) for step in result.script.steps],
            warnings=_warnings(result),
        ).to_json()

    def validate(command: Command) -> JsonObject:
        entry = store.get(ScriptRef.from_json(command.payload).script_id)
        if entry.path is not None:
            result = store.load_path(entry.path)
        else:
            result = store.load_text(entry.script.text)
        data = ValidateResult(ok=result.ok, errors=result.errors).to_json()
        # Not in ValidateResult yet; clients ignore unknown fields.
        data["warnings"] = [w.to_json() for w in _warnings(result)]
        return data

    server.register("list_step_types", list_step_types)
    server.register("load", load)
    server.register("validate", validate)

