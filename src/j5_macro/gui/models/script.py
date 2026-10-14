"""Scripts: the entries of ``list_scripts`` and the open script as ``get_script`` reports
it (docs/contract.md, "Script management")."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from j5_macro.gui.models.steps import StepItem, parse_steps


@dataclass(frozen=True, slots=True)
class ScriptEntry:
    """One script found by ``list_scripts``."""

    path: str
    name: str
    modified: str = ""
    valid: bool = True

    def relative_to(self, root: str) -> str:
        """The path relative to ``root`` (with ``/``), or as it is when outside it."""
        try:
            relative = os.path.relpath(self.path, root)
        except ValueError:  # another drive on Windows
            return self.path
        if relative.startswith(".."):
            return self.path
        return relative.replace(os.sep, "/")


def parse_script_list(result: Mapping[str, Any]) -> list[ScriptEntry]:
    """The ``list_scripts`` result, in the engine's order."""
    raw = result.get("scripts")
    entries: list[ScriptEntry] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            continue
        path = str(item["path"])
        entries.append(ScriptEntry(
            path=path,
            name=str(item.get("name") or os.path.splitext(os.path.basename(path))[0]),
            modified=str(item.get("modified") or ""),
            valid=item.get("valid") is not False,
        ))
    return entries


@dataclass(frozen=True, slots=True)
class ScriptState:
    """The open script: a ``get_script`` result. While the file is invalid, ``steps``,
    ``name``, ``params`` and ``images`` are those of the last valid version (the one the
    engine keeps) and ``errors`` says what is wrong with the current text."""

    script_id: str
    version: str
    source: str
    name: str
    path: str | None
    steps: tuple[StepItem, ...] = ()
    params: tuple[Mapping[str, Any], ...] = ()
    images: tuple[Mapping[str, Any], ...] = ()
    capture_scale: float | None = None
    valid: bool = True
    errors: tuple[Mapping[str, Any], ...] = ()
    warnings: tuple[Mapping[str, Any], ...] = field(default=())
    """The warnings of the last ``load`` (``get_script`` does not report them)."""

    @classmethod
    def from_get_script(cls, result: Mapping[str, Any],
                        warnings: Sequence[Mapping[str, Any]] = ()) -> ScriptState:
        path = result.get("path")
        scale = result.get("capture_scale")
        return cls(
            script_id=str(result.get("script_id", "")),
            version=str(result.get("version", "")),
            source=str(result.get("source", "")),
            name=str(result.get("name", "")),
            path=path if isinstance(path, str) else None,
            steps=tuple(parse_steps(result.get("steps"))),
            params=_mappings(result.get("params")),
            images=_mappings(result.get("images")),
            capture_scale=float(scale) if isinstance(scale, int | float) else None,
            valid=result.get("valid") is not False,
            errors=_mappings(result.get("errors")),
            warnings=tuple(warnings),
        )

    @property
    def title(self) -> str:
        """The script's name, else its file name."""
        if self.name:
            return self.name
        return os.path.basename(self.path) if self.path else self.script_id


def format_problem(problem: Mapping[str, Any]) -> str:
    """``line 12: message (code)`` for an error or warning of the engine."""
    message = str(problem.get("message", ""))
    code = problem.get("code")
    line = problem.get("line")
    text = f"line {line}: {message}" if isinstance(line, int) else message
    return f"{text} ({code})" if code else text


def _mappings(raw: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))
