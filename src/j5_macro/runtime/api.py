"""Interface between the engine (wire protocol, run lifecycle) and the interpreter.

The engine owns everything about a run that is visible on the wire or on disk: the
``run_id``, event stamping (``seq``, ``ts``, ``t_ms``), persistence, ``run_started``
and ``run_finished``, and the one-run-at-a-time rule. The interpreter owns everything
about executing the script. They meet here:

- The engine calls a :data:`RunFactory` to get a :class:`RunController`, starts
  :meth:`RunController.run` on a worker thread, and forwards control commands to it
  from the protocol thread.
- The interpreter reports what happens through a :data:`RunEventSink`, passing the
  event type and its payload; the engine stamps and publishes it.
- The engine masks secrets in what it publishes. Secret params are known when the run
  starts; results stored as secrets during the run reach it through
  :attr:`RunOptions.add_secrets` (a :data:`SecretSink`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from j5_macro.backends.base import Backend
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import ErrorInfo
from j5_macro.script.model import Script
from j5_macro.steps.registry import StepRegistry

RunMode = Literal["normal", "step", "dry"]
RunStatus = Literal["completed", "stopped", "failed"]

RunEventSink = Callable[[str, dict[str, Any]], None]
"""Publish one run-level event: ``sink("step_started", {...payload...})``.

Payloads follow ``j5_macro.protocol.payloads.events``. Thread-safe on the engine side."""

SecretSink = Callable[[list[str]], None]
"""Register values as secrets of the run from now on: the engine masks them in every
event emitted after the call and in every answer (``get_state``, ``evaluate``, the
error in ``run.json``). Called on the worker thread; thread-safe on the engine side."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RunOptions:
    mode: RunMode = "normal"
    seed: int = 0
    variables: dict[str, Any] = field(default_factory=dict)
    """Overrides for the script's ``vars`` (the ``run`` command's ``vars``)."""
    breakpoints: frozenset[str] = frozenset()
    """Client breakpoints by step id; ``break: true`` steps are added by the interpreter."""
    input_profile: dict[str, Any] | None = None
    """Overrides for the script's ``input`` profile (e.g. ``direct`` for fast runs)."""
    captures: Literal["on_failure", "all"] = "on_failure"
    run_dir: Path | None = None
    """Where the run keeps its artifacts (``captures/``); None keeps nothing on disk.
    Events reference these files relative to it."""
    backend: Backend | None = None
    """The engine's input backend; None means input steps fail with capability_missing
    (dry runs never touch it)."""
    backend_reason: str | None = None
    """Why there is no backend (as the engine reported it), for capability_missing."""
    add_secrets: SecretSink | None = None
    """Where the interpreter registers the strings of a result stored with
    ``as: {secret: true}``, before emitting its ``step_finished``. None (no engine, e.g.
    in tests) masks nothing."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RunOutcome:
    """How :meth:`RunController.run` ended; the engine turns it into ``run_finished``."""

    status: RunStatus
    reason: Literal["user", "interference"] | None = None
    error: ErrorInfo | None = None


class ControlError(Exception):
    """A control command cannot be applied now (e.g. ``step_over`` while running)."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.info = ErrorInfo(code=code.value, message=message)


class RunController(Protocol):
    """One run of one script. All methods except :meth:`run` are thread-safe and return
    quickly; they are called from the engine's protocol thread while :meth:`run` blocks
    on a worker thread."""

    def run(self) -> RunOutcome:
        """Execute the script to the end, a ``stop`` or a fatal error. Never raises for
        script problems; unexpected exceptions become a ``failed`` outcome."""
        ...

    def pause(self) -> None:
        """Pause at the next atomic action; interrupts waits (they resume with the
        remaining time). No-op when already paused."""
        ...

    def resume(self) -> None:
        """Continue until the next breakpoint or the end. ``invalid_state`` if not paused."""
        ...

    def step_over(self) -> None: ...

    def step_into(self) -> None: ...

    def step_out(self) -> None:
        """Debugger stepping; ``invalid_state`` unless paused (see the contract's
        run states section for the semantics with nested blocks)."""
        ...

    def stop(self, reason: Literal["user", "interference"] = "user") -> None:
        """Abort as soon as possible, even mid-action; :meth:`run` then returns
        ``stopped``. Idempotent."""
        ...

    def set_breakpoints(self, step_ids: Iterable[str]) -> None:
        """Replace the client breakpoints (script ``break: true`` steps stay)."""
        ...

    def state(self) -> dict[str, Any]:
        """Snapshot in the ``get_state`` result shape (without ``run_id``)."""
        ...

    def set_variable(self, name: str, value: Any) -> None:
        """Only while paused, else ``invalid_state``."""
        ...

    def evaluate(self, expression: str) -> Any:
        """Only while paused, else ``invalid_state``; ``expression_error`` on failure."""
        ...


RunFactory = Callable[[Script, StepRegistry, RunEventSink, RunOptions], RunController]
"""Creates a run; implemented by ``j5_macro.runtime.interpreter.create_run``."""
