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
    display_scales: list[dict[str, Any]] | None = None
    """Each monitor the backend reports, ``{bbox: [x, y, w, h], scale}``; empty when it
    cannot tell. Omitted by engines that predate it."""
    stop_key: str | None = None
    """``hello`` only: the emergency stop key the engine watches, in backend key names
    joined with ``+`` (``ctrl+alt+shift+q``); None when it watches none."""


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
    store_as: dict[str, Any] | None = field(default=None, metadata=wire_name("as"))
    """The common ``as`` field, as ``{name, secret}``."""
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
    choices: list[Any] | None = None
    """The only values allowed (``button``: ``left``, ``right``, ``middle``), when the
    parameter has a fixed set; omitted otherwise."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultFieldInfo(Payload):
    """One field of a step type's result; ``optional`` fields are only present in
    some cases."""

    name: str
    type: str
    optional: bool = False
    doc: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class StepTypeInfo(Payload):
    name: str
    params: list[ParamInfo]
    doc: str = ""
    result: list[ResultFieldInfo] = field(default_factory=list)
    json_schema: dict[str, Any] = field(default_factory=dict, metadata=wire_name("schema"))
    kind: Literal["action", "control"] = "action"
    """``control`` for the block steps the interpreter runs itself (``if``, ``loop``)."""
    blocks: list[str] = field(default_factory=list)
    """The block slots holding steps, in path order (``if``: ``then``, ``else``)."""
    required_blocks: list[str] = field(default_factory=list)
    """The block slots a step of this type always has (``if``: ``then``)."""
    shorthand: str | None = None
    """The parameter the scalar form sets (``sleep: 2s`` is ``duration``)."""


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
    capture_scale: float | None = None
    """The script's ``capture_scale``, when given."""
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
    allow_exec: bool = False
    """Let ``exec`` steps start programs; without it they fail with capability_missing."""
    display_scale: float | None = None
    """The display scale of the whole run (1.0 = 100 %); None detects it per monitor."""
    strict: bool = False
    """Dry runs only: fail the step where a dry run would only warn that the real run
    fails (an image not found, a search that cannot be made, ``exec`` not allowed)."""

    def check(self, path: str) -> None:
        scale = self.display_scale
        if scale is not None and not 0 < scale < float("inf"):
            raise PayloadError(f"{path}.display_scale: must be a positive number")


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
    capture_scale: float | None = None
    valid: bool = True
    errors: list[ErrorInfo] = field(default_factory=list)
    vars: dict[str, Any] = field(default_factory=dict)
    """The script's ``vars``, as values."""
    input: dict[str, Any] = field(default_factory=dict)
    """The script's ``input`` profile (``mouse``, ``keyboard``), as written."""


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
    (``break``, ``timeout``, ``on_error``, ``input``, ``as``)."""

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
    capture_scale: float | None = None
    """The script's ``capture_scale``; it cannot be removed this way."""

    def check(self, path: str) -> None:
        scale = self.capture_scale
        if scale is not None and not 0 < scale < float("inf"):
            raise PayloadError(f"{path}.capture_scale: must be a positive number")


@dataclass(frozen=True, slots=True, kw_only=True)
class AddImageEdit(Payload):
    """Add the entry ``name`` to the script's ``images`` (``png``: base64 of a PNG file;
    ``scale``: its capture scale); an existing entry is replaced only with
    ``overwrite``."""

    op: Literal["add_image"] = "add_image"
    name: str
    png: str
    scale: float | None = None
    overwrite: bool = False

    def check(self, path: str) -> None:
        scale = self.scale
        if scale is not None and not 0 < scale < float("inf"):
            raise PayloadError(f"{path}.scale: must be a positive number")


@dataclass(frozen=True, slots=True, kw_only=True)
class RenameImageEdit(Payload):
    """Rename the entry ``name`` of ``images`` to ``new_name``, and every literal
    ``"#name"`` reference of the steps with it."""

    op: Literal["rename_image"] = "rename_image"
    name: str
    new_name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteImageEdit(Payload):
    """Remove the entry ``name`` of ``images`` (the section goes with its last entry)."""

    op: Literal["delete_image"] = "delete_image"
    name: str


Edit = (InsertEdit | UpdateEdit | MoveEdit | DeleteEdit | SetMetaEdit | AddImageEdit
        | RenameImageEdit | DeleteImageEdit)


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


# --- authoring tools (docs/contract.md, "Authoring tools") --------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class WindowRef(Payload):
    """A window as steps name it: the topmost visible one whose title contains
    ``title``."""

    title: str

    def check(self, path: str) -> None:
        if not self.title:
            raise PayloadError(f"{path}.title: must not be empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class WindowInfo(Payload):
    title: str
    bbox: list[int]
    """The client area on the desktop, ``[x, y, w, h]``."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ListWindowsResult(Result):
    windows: list[WindowInfo]
    """Topmost first."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ScreenshotPayload(Payload):
    path: str | None = None
    """None: a new file in the system's temporary directory."""
    window: WindowRef | None = None
    region: list[int] | None = None
    """``[x, y, w, h]``, relative to ``window`` when given."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ScreenshotResult(Result):
    path: str
    bbox: list[int]
    display_scale: float
    window: str | None = None
    window_bbox: list[int] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptureRegionPayload(Payload):
    """Without ``bbox`` the user selects the rectangle on the overlay. The destination
    is ``path`` or ``script_id`` with ``name``."""

    bbox: list[int] | None = None
    """``[x, y, w, h]``, relative to ``window`` when given."""
    window: WindowRef | None = None
    path: str | None = None
    script_id: str | None = None
    name: str | None = None
    overwrite: bool = False
    padding: int = 0
    transparent: bool = False

    def check(self, path: str) -> None:
        if (self.script_id is None) != (self.name is None):
            raise PayloadError(f"{path}: script_id and name go together")
        _exactly_one(path, path=self.path, script_id=self.script_id)
        if self.padding < 0:
            raise PayloadError(f"{path}.padding: must be 0 or more")


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptureWarning(Payload):
    code: str
    message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptureRegionResult(Result):
    bbox: list[int]
    display_scale: float
    path: str | None = None
    image_name: str | None = None
    version: str | None = None
    """The script's version after embedding the image."""
    scale: float | None = None
    """The embedded entry's ``scale``, when it got one."""
    window: str | None = None
    window_bbox: list[int] | None = None
    warnings: list[CaptureWarning] = field(default_factory=list)


@dataclass(frozen=True, slots=True, kw_only=True)
class PickPointPayload(Payload):
    window: WindowRef | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PickPointResult(Result):
    x: int
    y: int
    display_scale: float
    window: str | None = None
    window_area: list[int] | None = None
    """The window's client area on the desktop."""
    window_point: list[int] | None = None
    """The point relative to the window's client area."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FindImagePayload(Payload):
    image: str
    script_id: str | None = None
    threshold: float = 0.9
    scale: float = 1.0
    region: list[int] | None = None
    window: WindowRef | None = None
    display_scale: float | None = None
    show: bool = False
    show_ms: int = 3000

    def check(self, path: str) -> None:
        if not self.image:
            raise PayloadError(f"{path}.image: must not be empty")
        if not 0 <= self.threshold <= 1:
            raise PayloadError(f"{path}.threshold: must be between 0 and 1")
        for name, value in (("scale", self.scale), ("display_scale", self.display_scale)):
            if value is not None and not 0 < value < float("inf"):
                raise PayloadError(f"{path}.{name}: must be a positive number")
        if self.show_ms < 0:
            raise PayloadError(f"{path}.show_ms: must be 0 or more")


@dataclass(frozen=True, slots=True, kw_only=True)
class FindImageResult(Result):
    found: bool
    score: float
    region: list[int]
    scale: float
    display_scale: float
    capture_scale: float
    elapsed_ms: int
    bbox: list[int] | None = None
    center: list[int] | None = None
    window: str | None = None
    window_bbox: list[int] | None = None


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
    "list_windows": EmptyPayload,
    "screenshot": ScreenshotPayload,
    "capture_region": CaptureRegionPayload,
    "pick_point": PickPointPayload,
    "find_image": FindImagePayload,
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
    "list_windows": ListWindowsResult,
    "screenshot": ScreenshotResult,
    "capture_region": CaptureRegionResult,
    "pick_point": PickPointResult,
    "find_image": FindImageResult,
}
