"""Saved runs, as ``list_runs`` reports them (docs/contract.md, "Run history")."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    script_name: str = ""
    script_path: str | None = None
    mode: str = ""
    status: str | None = None
    """None while the run is active or when it never finished."""
    started: str = ""
    duration_ms: int | None = None
    warnings: int | None = None
    """Warning ``log`` events of a finished run; None while it runs or when unknown."""

    @property
    def duration_text(self) -> str:
        if self.duration_ms is None:
            return ""
        return f"{self.duration_ms / 1000:.1f} s"


def parse_run_list(result: Mapping[str, Any]) -> list[RunSummary]:
    raw = result.get("runs")
    runs: list[RunSummary] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping) or not isinstance(item.get("run_id"), str):
            continue
        duration = item.get("duration_ms")
        warnings = item.get("warnings")
        path = item.get("script_path")
        status = item.get("status")
        runs.append(RunSummary(
            run_id=str(item["run_id"]),
            script_name=str(item.get("script_name") or ""),
            script_path=path if isinstance(path, str) else None,
            mode=str(item.get("mode") or ""),
            status=status if isinstance(status, str) else None,
            started=str(item.get("started") or ""),
            duration_ms=duration if isinstance(duration, int) else None,
            warnings=warnings if isinstance(warnings, int) else None,
        ))
    return runs
