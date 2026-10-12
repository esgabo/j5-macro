"""Releasing what a dead engine held (docs/contract.md, "Transport and message format":
the client supervises the engine).

The engine reports held keys and buttons with ``input_state``. If it dies with
something held, the CLI selects a backend in its own process and releases each entry:
``button:<name>`` with ``button_up``, a single character with ``char_up`` and any other
name with ``key_up``, newest first.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from j5_macro.backends.base import Backend, Button
from j5_macro.backends.select import BackendChoice, select_backend

BUTTON_PREFIX = "button:"


@dataclass(slots=True)
class ReleaseReport:
    released: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    """``(entry, why)`` for each entry that could not be released."""
    backend: str | None = None
    reason: str | None = None
    """Why nothing could be released (no backend), if so."""

    def message(self) -> str:
        """One line for the user."""
        if self.reason is not None:
            held = ", ".join(entry for entry, _ in self.failed)
            return f"the engine held {held} and they could not be released: {self.reason}"
        parts = []
        if self.released:
            parts.append(f"released what the engine held: {', '.join(self.released)} "
                         f"(backend {self.backend})")
        if self.failed:
            failures = "; ".join(f"{entry}: {why}" for entry, why in self.failed)
            parts.append(f"could not release {failures}")
        return "; ".join(parts)


def release_one(backend: Backend, entry: str) -> None:
    """Release one ``input_state.held`` entry; ValueError for one it cannot name."""
    if entry.startswith(BUTTON_PREFIX):
        backend.button_up(Button(entry[len(BUTTON_PREFIX):]))
    elif len(entry) == 1:
        backend.char_up(entry)
    elif entry:
        backend.key_up(entry)
    else:
        raise ValueError("empty entry")


def release_held(held: Sequence[str],
                 select: Callable[[], BackendChoice] = select_backend) -> ReleaseReport:
    """Release ``held`` (in reverse press order) with a backend of this process."""
    report = ReleaseReport()
    if not held:
        return report
    try:
        choice = select()
    except Exception as exc:
        choice = BackendChoice(None, None, f"cannot select a backend: {exc}")
    backend = choice.backend
    if backend is None:
        report.reason = f"no input backend ({choice.reason})"
        report.failed = [(entry, report.reason) for entry in held]
        return report
    report.backend = choice.name or backend.name
    try:
        for entry in reversed(held):
            try:
                release_one(backend, entry)
            except Exception as exc:
                report.failed.append((entry, str(exc) or type(exc).__name__))
            else:
                report.released.append(entry)
    finally:
        try:
            backend.close()
        except Exception as exc:
            report.failed.append(("backend", f"close failed: {exc}"))
    return report
