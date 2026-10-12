"""Typed payloads for every command, result and event (docs/contract.md).

Each payload is a frozen dataclass with ``from_json`` (validating, raises
:class:`PayloadError` with code ``validation_failed``) and ``to_json``.
"""

from __future__ import annotations

from typing import Any

from j5_macro.protocol.payloads.codec import (
    Payload,
    PayloadError,
    wire_name,
)
from j5_macro.protocol.payloads.commands import (
    COMMAND_PAYLOADS,
    COMMAND_RESULTS,
    Capabilities,
    Captures,
    CreateScriptPayload,
    CreateScriptResult,
    DeleteEdit,
    Diagnostic,
    Edit,
    EditScriptPayload,
    EditScriptResult,
    EmptyPayload,
    EmptyResult,
    EvaluatePayload,
    EvaluateResult,
    GetEventsPayload,
    GetEventsResult,
    GetRunPayload,
    GetRunResult,
    GetScriptResult,
    GetStateResult,
    HelloPayload,
    HelloResult,
    InsertEdit,
    ListRunsPayload,
    ListRunsResult,
    ListScriptsPayload,
    ListScriptsResult,
    ListStepTypesResult,
    LoadPayload,
    LoadResult,
    MoveEdit,
    OnError,
    ParamInfo,
    Position,
    Result,
    RunInfo,
    RunMode,
    RunPayload,
    RunRef,
    RunResult,
    RunState,
    RunStatus,
    ScriptInfo,
    ScriptRef,
    SetBreakpointsPayload,
    SetMetaEdit,
    SetVariablePayload,
    StepNode,
    StepTypeInfo,
    StopPayload,
    UpdateEdit,
    ValidateResult,
)
from j5_macro.protocol.payloads.events import (
    EVENT_PAYLOADS,
    BranchTakenEvent,
    BreakpointHitEvent,
    Device,
    FinishReason,
    ImageSearchEvent,
    InputExecutedEvent,
    InputPlannedEvent,
    InputStateEvent,
    KeysReleasedEvent,
    LogEvent,
    LogLevel,
    LoopIterationEvent,
    ReadyEvent,
    RunFinishedEvent,
    RunStartedEvent,
    ScriptChangedEvent,
    StateChangedEvent,
    StateReason,
    StepEvent,
    StepFailedEvent,
    StepFinishedEvent,
    StepSkippedEvent,
    StepStartedEvent,
    Strategy,
    TriggerFiredEvent,
    UserInterferenceEvent,
    VariableChangedEvent,
)


def parse_command_payload(type: str, data: Any) -> Payload:
    """Validate a command payload; an unknown command type is a validation error."""
    cls = COMMAND_PAYLOADS.get(type)
    if cls is None:
        raise PayloadError(f"type: unknown command {type!r}")
    return cls.from_json(data)


def parse_command_result(type: str, data: Any) -> Payload:
    """Validate the ``result`` of a successful response to command ``type``."""
    cls = COMMAND_RESULTS.get(type)
    if cls is None:
        raise PayloadError(f"type: unknown command {type!r}")
    return cls.from_json(data)


def parse_event_payload(type: str, data: Any) -> Payload | None:
    """Validate an event payload; returns None for unknown event types (clients ignore them)."""
    cls = EVENT_PAYLOADS.get(type)
    if cls is None:
        return None
    return cls.from_json(data)


__all__ = [
    "COMMAND_PAYLOADS",
    "COMMAND_RESULTS",
    "EVENT_PAYLOADS",
    "BranchTakenEvent",
    "BreakpointHitEvent",
    "Capabilities",
    "Captures",
    "CreateScriptPayload",
    "CreateScriptResult",
    "DeleteEdit",
    "Device",
    "Diagnostic",
    "Edit",
    "EditScriptPayload",
    "EditScriptResult",
    "EmptyPayload",
    "EmptyResult",
    "EvaluatePayload",
    "EvaluateResult",
    "FinishReason",
    "GetEventsPayload",
    "GetEventsResult",
    "GetRunPayload",
    "GetRunResult",
    "GetScriptResult",
    "GetStateResult",
    "HelloPayload",
    "HelloResult",
    "ImageSearchEvent",
    "InputExecutedEvent",
    "InputPlannedEvent",
    "InputStateEvent",
    "InsertEdit",
    "KeysReleasedEvent",
    "ListRunsPayload",
    "ListRunsResult",
    "ListScriptsPayload",
    "ListScriptsResult",
    "ListStepTypesResult",
    "LoadPayload",
    "LoadResult",
    "LogEvent",
    "LogLevel",
    "LoopIterationEvent",
    "MoveEdit",
    "OnError",
    "ParamInfo",
    "Payload",
    "PayloadError",
    "Position",
    "ReadyEvent",
    "Result",
    "RunFinishedEvent",
    "RunInfo",
    "RunMode",
    "RunPayload",
    "RunRef",
    "RunResult",
    "RunStartedEvent",
    "RunState",
    "RunStatus",
    "ScriptChangedEvent",
    "ScriptInfo",
    "ScriptRef",
    "SetBreakpointsPayload",
    "SetMetaEdit",
    "SetVariablePayload",
    "StateChangedEvent",
    "StateReason",
    "StepEvent",
    "StepFailedEvent",
    "StepFinishedEvent",
    "StepNode",
    "StepSkippedEvent",
    "StepStartedEvent",
    "StepTypeInfo",
    "StopPayload",
    "Strategy",
    "TriggerFiredEvent",
    "UpdateEdit",
    "UserInterferenceEvent",
    "ValidateResult",
    "VariableChangedEvent",
    "parse_command_payload",
    "parse_command_result",
    "parse_event_payload",
    "wire_name",
]
