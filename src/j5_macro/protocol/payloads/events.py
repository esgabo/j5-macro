"""Event payloads (docs/contract.md, "Events")."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from j5_macro.protocol.messages import ErrorInfo
from j5_macro.protocol.payloads.codec import Payload, wire_name
from j5_macro.protocol.payloads.commands import Capabilities, RunMode, RunState, RunStatus

Device = Literal["mouse", "keyboard"]
Strategy = Literal["direct", "linear", "human"]
StateReason = Literal["breakpoint", "step", "user", "error", "interference"]
FinishReason = Literal["user", "interference"]
LogLevel = Literal["debug", "info", "warning", "error"]


@dataclass(frozen=True, slots=True, kw_only=True)
class StepEvent(Payload):
    """Fields shared by step events.

    ``path`` is the static position in the script (list index and block slot,
    alternating); ``iterations`` holds the current iteration of each enclosing loop,
    outermost first (omitted outside loops), so the same step in different loop
    iterations is distinguishable.
    """

    step_id: str
    path: list[int]
    iterations: list[int] | None = None
    line: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadyEvent(Payload):
    v: int
    backend: str | None = None
    capabilities: Capabilities


@dataclass(frozen=True, slots=True, kw_only=True)
class ScriptChangedEvent(Payload):
    script_id: str
    version: str | None
    """None when the file was deleted."""
    source: Literal["disk", "edit", "deleted"]
    valid: bool = True
    errors: list[ErrorInfo] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RunStartedEvent(Payload):
    script_id: str
    script_name: str | None = None
    """The script's ``name``; optional for engines that predate it."""
    mode: RunMode
    seed: int
    backend: str
    input_profile: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class StateChangedEvent(Payload):
    state: RunState
    reason: StateReason | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RunFinishedEvent(Payload):
    status: RunStatus
    reason: FinishReason | None = None
    duration_ms: int
    error: ErrorInfo | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StepStartedEvent(StepEvent):
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class StepFinishedEvent(StepEvent):
    duration_ms: int
    result: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StepSkippedEvent(StepEvent):
    reason: Literal["user"]


@dataclass(frozen=True, slots=True, kw_only=True)
class StepFailedEvent(StepEvent):
    error: ErrorInfo
    screenshot: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class BreakpointHitEvent(StepEvent):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class BranchTakenEvent(StepEvent):
    condition: Any
    """Expression text or a predefined condition mapping such as ``{image_visible: ...}``."""
    value: Any
    branch: Literal["then", "else"]


@dataclass(frozen=True, slots=True, kw_only=True)
class LoopIterationEvent(StepEvent):
    index: int
    total: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class VariableChangedEvent(Payload):
    name: str
    old: Any
    new: Any
    step_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageSearchEvent(Payload):
    """Rectangles (``bbox``, ``region``) are ``[x, y, width, height]`` in desktop pixels."""

    template: str
    threshold: float
    found: bool
    best_score: float
    bbox: list[int] | None = None
    region: list[int]
    attempt: int
    elapsed_ms: int
    screenshot: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class InputPlannedEvent(Payload):
    """Summary fields depend on the device: mouse sets from/to/duration_ms, keyboard
    chars/wpm. Points are ``[x, y]``."""

    device: Device
    strategy: Strategy
    seed: int
    start: list[int] | None = field(default=None, metadata=wire_name("from"))
    to: list[int] | None = None
    duration_ms: int | None = None
    chars: int | None = None
    wpm: float | None = None
    plan_ref: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class InputExecutedEvent(Payload):
    device: Device
    actual_duration_ms: int
    aborted: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class InputStateEvent(Payload):
    held: list[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class UserInterferenceEvent(Payload):
    """``expected`` and ``actual`` are ``[x, y]`` pointer positions."""

    expected: list[int]
    actual: list[int]
    action: Literal["pause", "stop"]


@dataclass(frozen=True, slots=True, kw_only=True)
class KeysReleasedEvent(Payload):
    keys: list[str]
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LogEvent(Payload):
    level: LogLevel
    message: str
    step_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TriggerFiredEvent(Payload):
    trigger_id: str
    kind: str


EVENT_PAYLOADS: dict[str, type[Payload]] = {
    "ready": ReadyEvent,
    "script_changed": ScriptChangedEvent,
    "run_started": RunStartedEvent,
    "state_changed": StateChangedEvent,
    "run_finished": RunFinishedEvent,
    "step_started": StepStartedEvent,
    "step_finished": StepFinishedEvent,
    "step_skipped": StepSkippedEvent,
    "step_failed": StepFailedEvent,
    "breakpoint_hit": BreakpointHitEvent,
    "branch_taken": BranchTakenEvent,
    "loop_iteration": LoopIterationEvent,
    "variable_changed": VariableChangedEvent,
    "image_search": ImageSearchEvent,
    "input_planned": InputPlannedEvent,
    "input_executed": InputExecutedEvent,
    "input_state": InputStateEvent,
    "user_interference": UserInterferenceEvent,
    "keys_released": KeysReleasedEvent,
    "log": LogEvent,
    "trigger_fired": TriggerFiredEvent,
}
