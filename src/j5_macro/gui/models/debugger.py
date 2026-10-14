"""Debugging state without Qt: which controls apply in each state, and why a run paused.

docs/contract.md, "Run states and debugging semantics": a run is ``running``,
``paused`` or ``stopping`` until ``run_finished``. A pause has a reason: ``breakpoint``
(also after a ``pause:`` step), ``step`` (a step command finished, or a ``step`` mode run
starting), ``user`` (``pause``), ``error`` (a step with ``on_error: pause`` failed:
``resume`` retries it, ``step_over`` skips it) or ``interference`` (the user moved the
mouse during a movement, with the profile's ``interference: pause``; the same choices).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

RECOVERABLE_REASONS = frozenset({"error", "interference"})
"""Pauses where the current step did not complete: Retry (``resume``) or Skip
(``step_over``)."""


@dataclass(frozen=True, slots=True)
class DebugActions:
    """What the run controls allow now."""

    run: bool = False
    """Start a run (Run, Dry run, Debug)."""
    pause: bool = False
    resume: bool = False
    step: bool = False
    """Step over, into and out."""
    stop: bool = False
    inspect: bool = False
    """Read and change variables, evaluate expressions (only while paused)."""


def debug_actions(*, engine_running: bool, can_run: bool, state: str,
                  pending: bool = False) -> DebugActions:
    """The controls for a run ``state`` (``idle``, ``starting``, ``running``,
    ``paused``, ``stopping`` or ``finished``). ``can_run``: a valid script is open;
    ``pending``: a control command was sent and the engine has not changed state yet,
    so another one would be refused."""
    if not engine_running:
        return DebugActions()
    if state in ("idle", "finished"):
        return DebugActions(run=can_run)
    if state == "starting":
        return DebugActions(stop=True)
    if state == "running":
        return DebugActions(pause=not pending, stop=True)
    if state == "paused":
        go = not pending
        return DebugActions(resume=go, step=go, stop=True, inspect=True)
    return DebugActions()  # stopping


@dataclass(frozen=True, slots=True)
class PauseInfo:
    """Why the run is paused and where."""

    reason: str | None
    step_id: str | None = None
    """The step the run is paused before (or at, for an error or interference)."""
    error: Mapping[str, Any] | None = None
    """The failed step's error (``code``, ``message``, ``details``), reason ``error``."""
    screenshot: str | None = None
    """The failure capture, relative to the run's directory, reason ``error``."""
    interference: Mapping[str, Any] | None = field(default=None)
    """The ``user_interference`` payload, reason ``interference``."""

    @property
    def recoverable(self) -> bool:
        """The step did not complete: offer Retry and Skip."""
        return self.reason in RECOVERABLE_REASONS

    @property
    def title(self) -> str:
        step = f"'{self.step_id}'" if self.step_id else "the first step"
        if self.reason == "breakpoint":
            return f"Paused at a breakpoint, before {step}"
        if self.reason == "step":
            return f"Paused before {step}"
        if self.reason == "user":
            return f"Paused before {step}"
        if self.reason == "error":
            return f"Step {step} failed"
        if self.reason == "interference":
            return f"The mouse was moved during {step}"
        return f"Paused before {step}"

    @property
    def detail(self) -> str:
        """More about the pause, for the pause box ('' when there is nothing more)."""
        if self.reason == "error":
            message = ""
            if self.error is not None:
                message = str(self.error.get("message") or "")
                code = self.error.get("code")
                if code:
                    message = f"{message} ({code})" if message else str(code)
            return message
        if self.reason == "interference" and self.interference is not None:
            expected = _point(self.interference.get("expected"))
            actual = _point(self.interference.get("actual"))
            if expected and actual:
                return (f"The pointer was expected at {expected} but was at {actual}. "
                        "Held keys and buttons were released.")
            return "Held keys and buttons were released."
        return ""

    @property
    def hint(self) -> str:
        """What the buttons do."""
        if self.reason == "error":
            return ("Retry runs the step again, Skip goes on after it, Stop ends the run "
                    "as failed.")
        if self.reason == "interference":
            return ("Retry runs the step again from where the pointer is now, Skip goes on "
                    "after it, Stop ends the run.")
        return ""


def pause_from_state(previous: PauseInfo | None, state: Mapping[str, Any]) -> PauseInfo | None:
    """Update a pause with ``get_state``'s answer (the step it is paused at)."""
    if previous is None:
        return None
    step_id = state.get("step_id")
    if not isinstance(step_id, str) or step_id == previous.step_id:
        return previous
    return PauseInfo(reason=previous.reason, step_id=step_id, error=previous.error,
                     screenshot=previous.screenshot, interference=previous.interference)


def _point(value: object) -> str:
    if isinstance(value, list | tuple) and len(value) == 2:
        return f"({value[0]}, {value[1]})"
    return ""
