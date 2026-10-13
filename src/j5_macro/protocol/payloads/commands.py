"""Command payloads and results (docs/contract.md, "Commands" and "Script management")."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from j5_macro.protocol.messages import ErrorInfo
from j5_macro.protocol.payloads.codec import Payload, PayloadError, wire_name

RunMode = Literal["normal", "step", "dry"]
Captures = Literal["on_failure", "all"]
RunState = Literal["running", "paused", "stopping"]
RunStatus = Literal["completed", "stopped", "failed"]
OnError = Literal["stop", "continue", "retry", "pause"]


def _exactly_one(at: str, **values: object) -> None:
    if sum(v is not None for v in values.values()) != 1:
        raise PayloadError(f"{at}: expected exactly one of {', '.join(values)}")


class Result(Payload):
    """Base for response results: validation errors are reported under ``result``."""

    __slots__ = ()
    _root: ClassVar[str] = "result"


@dataclass(frozen=True, slots=True, kw_only=True)
class EmptyPayload(Payload):
    """Commands without parameters (``list_step_types``)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class EmptyResult(Result):
    """Commands whose successful response carries no data."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RunRef(Payload):
    """Control commands that only name the run: pause, resume, step_*, get_state, delete_run."""

    run_id: str


# --- shared shapes ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Capabilities(Payload):
    """Engine capabilities, shared by the ``hello`` result and the ``ready`` event."""

    backend: str | None = None
    input_strategies: list[str] = field(default_factory=list)
    step_types: list[str] = field(default_factory=list)
    vision: bool | None = None
    """The vision toolkit and its extra are installed (image steps can search the
    screen); capturing may still fail at run time, with ``capability_missing``. Omitted
    by engines that predate it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class StepNode(Payload):
    """One step of a loaded script, as the engine reports it.

    ``params`` holds the type-specific parameters; ``blocks`` holds the child step lists of
    block steps, keyed by the YAML key (``then``, ``else``, ``do``).
    """

    id: str
    type: str
    path: list[int]
    line: int | None = None
    params: dict[str, Any] = field(default_factory=dict)
    breakpoint: bool = field(default=False, metadata=wire_name("break"))
    on_error: OnError | None = None
    timeout: str | None = None
    input: dict[str, Any] | None = None
    blocks: dict[str, list[StepNode]] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Diagnostic(Payload):
    """A non-fatal note about a script, e.g. a generated step id."""

    message: str
    step_id: str | None = None
    line: int | None = None


# --- execution and debugging ------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class HelloPayload(Payload):
    v_min: int
    v_max: int
    client: str


@dataclass(frozen=True, slots=True, kw_only=True)
class HelloResult(Result):
    v: int
    capabilities: Capabilities


@dataclass(frozen=True, slots=True, kw_only=True)
class ParamInfo(Payload):
    name: str
    type: str
    required: bool
    default: Any = None
    doc: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class StepTypeInfo(Payload):
    name: str
    params: list[ParamInfo]
    doc: str = ""
    json_schema: dict[str, Any] = field(default_factory=dict, metadata=wire_name("schema"))


@dataclass(frozen=True, slots=True, kw_only=True)
class ListStepTypesResult(Result):
    step_types: list[StepTypeInfo]


@dataclass(frozen=True, slots=True, kw_only=True)
class LoadPayload(Payload):
    """Exactly one of ``path`` (a file) or ``source`` (YAML text)."""

    path: str | None = None
    source: str | None = None

    def check(self, path: str) -> None:
        _exactly_one(path, path=self.path, source=self.source)


@dataclass(frozen=True, slots=True, kw_only=True)
class LoadResult(Result):
    script_id: str
    steps: list[StepNode]
    params: list[dict[str, Any]] = field(default_factory=list)
    """Declared params: ``name``, ``type``, ``secret``, ``required`` and optionally
    ``description`` and ``default`` (never for secrets)."""
    images: list[dict[str, Any]] = field(default_factory=list)
    """Embedded images: ``name``, ``format``, ``bytes``, ``width``, ``height`` and
    optionally ``scale``; never the data."""
    warnings: list[Diagnostic] = field(default_factory=list)


@dataclass(frozen=True, slots=True, kw_only=True)
class ScriptRef(Payload):
    script_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidateResult(Result):
    ok: bool
    errors: list[ErrorInfo] = field(default_factory=list)
    warnings: list[Diagnostic] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RunPayload(Payload):
    script_id: str
    mode: RunMode
    params: dict[str, Any] | None = None
    """Values for the script's declared params."""
    vars: dict[str, Any] | None = None
    """Overrides for the script's ``vars`` (declared names only)."""
    seed: int | None = None
    input_profile: dict[str, Any] | None = None
    captures: Captures = "on_failure"


@dataclass(frozen=True, slots=True, kw_only=True)
class RunResult(Result):
    run_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StopPayload(Payload):
    run_id: str
    reason: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SetBreakpointsPayload(Payload):
    script_id: str
    step_ids: list[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class GetStateResult(Result):
    run_id: str
    state: RunState
    step_id: str | None = None
    path: list[int] = field(default_factory=list)
    iterations: list[int] | None = None
    """Current iteration of each enclosing loop, outermost first; None outside loops."""
    line: int | None = None
    variables: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class SetVariablePayload(Payload):
    """``value`` as given, or the value of ``expression`` evaluated by the engine over
    the run's variables (unmasked, so a secret can be copied)."""

    run_id: str
    name: str
    value: Any = None
    expression: str | None = None

    def check(self, path: str) -> None:
        if self.expression is not None and self.value is not None:
            raise PayloadError(f"{path}: value and expression exclude each other")


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluatePayload(Payload):
    run_id: str
    expression: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluateResult(Result):
    value: Any


@dataclass(frozen=True, slots=True, kw_only=True)
class GetEventsPayload(Payload):
    """``run_id`` None asks for the engine-level stream."""

    run_id: str | None = None
    from_seq: int


@dataclass(frozen=True, slots=True, kw_only=True)
class GetEventsResult(Result):
    """Raw event objects (full envelopes), in ``seq`` order."""

    events: list[dict[str, Any]]


# --- script management ------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ListScriptsPayload(Payload):
    root: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ScriptInfo(Payload):
    path: str
    name: str
    modified: str
    """ISO 8601 UTC timestamp of the last change."""
    valid: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ListScriptsResult(Result):
    scripts: list[ScriptInfo]


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateScriptPayload(Payload):
    path: str
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateScriptResult(Result):
    script_id: str
    version: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GetScriptResult(Result):
    script_id: str
    version: str
    source: str
    name: str
    path: str | None
    steps: list[StepNode]
    params: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    """As in :class:`LoadResult`."""
    valid: bool = True
    errors: list[ErrorInfo] = field(default_factory=list)


Block = Literal["then", "else", "do"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Position(Payload):
    """Where a step goes: right after ``after_step_id``, or at ``index`` (the end by
    default) of the top-level list (no ``parent_step_id``) or of ``block`` of
    ``parent_step_id``."""

    after_step_id: str | None = None
    parent_step_id: str | None = None
    block: Block | None = None
    index: int | None = None

    def check(self, path: str) -> None:
        if self.after_step_id is not None and (
                self.parent_step_id is not None or self.block is not None
                or self.index is not None):
            raise PayloadError(f"{path}: after_step_id excludes parent_step_id, block "
                               "and index")
        if (self.parent_step_id is None) != (self.block is None):
            raise PayloadError(f"{path}: parent_step_id and block go together")
        if self.index is not None and self.index < 0:
            raise PayloadError(f"{path}.index: must be 0 or more")


@dataclass(frozen=True, slots=True, kw_only=True)
class InsertEdit(Payload):
    """Insert ``step`` (a YAML-shaped step mapping) at ``to``; the engine assigns its id
    unless the step has one."""

    op: Literal["insert"] = "insert"
    step: dict[str, Any]
    to: Position


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateEdit(Payload):
    """Replace the given ``params`` (``None`` removes one) and ``common`` fields
    (``break``, ``timeout``, ``on_error``, ``input``)."""

    op: Literal["update"] = "update"
    step_id: str
    params: dict[str, Any] = field(default_factory=dict)
    common: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MoveEdit(Payload):
    op: Literal["move"] = "move"
    step_id: str
    to: Position


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteEdit(Payload):
    op: Literal["delete"] = "delete"
    step_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SetMetaEdit(Payload):
    op: Literal["set_meta"] = "set_meta"
    name: str | None = None
    input: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    vars: dict[str, Any] | None = None
    images: dict[str, Any] | None = None
    """Embedded images by name, each ``{png: <base64>, scale?: number}``."""


Edit =InsertEdit | UpdateEdit | MoveEdit | DeleteEdit | SetMetaEdit


@dataclass(frozen=True, slots=True, kw_only=True)
class EditScriptPayload(Payload):
    script_id: str
    base_version: str
    edits: list[Edit]


@dataclass(frozen=True, slots=True, kw_only=True)
class EditScriptResult(Result):
    version: str
    valid: bool = True
    errors: list[ErrorInfo] = field(default_factory=list)


@dataclass(frozen=True, slots=True, kw_only=True)
class ListRunsPayload(Payload):
    script_id: str | None = None
    limit: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RunInfo(Payload):
    run_id: str
    status: RunStatus | None
    """None when the run never finished (e.g. the engine died)."""
    started: str
    """ISO 8601 UTC timestamp."""
    duration_ms: int | None = None
    script_name: str | None = None
    script_path: str | None = None
    mode: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ListRunsResult(Result):
    runs: list[RunInfo]


@dataclass(frozen=True, slots=True, kw_only=True)
class GetRunPayload(Payload):
    run_id: str
    from_seq: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class GetRunResult(Result):
    run_id: str
    run: dict[str, Any]
    """The run's ``run.json``."""
    events: list[dict[str, Any]]
    captures: list[str] = field(default_factory=list)
    """Paths relative to ``runs/<run_id>/``."""


COMMAND_PAYLOADS: dict[str, type[Payload]] = {
    "hello": HelloPayload,
    "list_step_types": EmptyPayload,
    "load": LoadPayload,
    "validate": ScriptRef,
    "run": RunPayload,
    "pause": RunRef,
    "resume": RunRef,
    "step_over": RunRef,
    "step_into": RunRef,
    "step_out": RunRef,
    "stop": StopPayload,
    "set_breakpoints": SetBreakpointsPayload,
    "get_state": RunRef,
    "set_variable": SetVariablePayload,
    "evaluate": EvaluatePayload,
    "get_events": GetEventsPayload,
    "list_scripts": ListScriptsPayload,
    "create_script": CreateScriptPayload,
    "get_script": ScriptRef,
    "edit_script": EditScriptPayload,
    "list_runs": ListRunsPayload,
    "get_run": GetRunPayload,
    "delete_run": RunRef,
}

COMMAND_RESULTS: dict[str, type[Payload]] = {
    "hello": HelloResult,
    "list_step_types": ListStepTypesResult,
    "load": LoadResult,
    "validate": ValidateResult,
    "run": RunResult,
    "pause": EmptyResult,
    "resume": EmptyResult,
    "step_over": EmptyResult,
    "step_into": EmptyResult,
    "step_out": EmptyResult,
    "stop": EmptyResult,
    "set_breakpoints": EmptyResult,
    "get_state": GetStateResult,
    "set_variable": EmptyResult,
    "evaluate": EvaluateResult,
    "get_events": GetEventsResult,
    "list_scripts": ListScriptsResult,
    "create_script": CreateScriptResult,
    "get_script": GetScriptResult,
    "edit_script": EditScriptResult,
    "list_runs": ListRunsResult,
    "get_run": GetRunResult,
    "delete_run": EmptyResult,
}
