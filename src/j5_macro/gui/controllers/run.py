"""The active run: starting it, debugging it and following its events.

One run at a time (docs/contract.md, "One run at a time"). :class:`RunController` feeds
the events of the active run to a :class:`~j5_macro.gui.models.run_log.RunLog` and
re-emits what views need as signals. It sends the control commands (``pause``,
``resume``, ``step_over``, ``step_into``, ``step_out``, ``stop``), reads the paused
run's variables with ``get_state`` and changes them with ``set_variable``, evaluates
expressions, and keeps the user's breakpoints, sent to the engine with
``set_breakpoints`` whenever they or the open script change.

Dialogs (the params prompt, the confirmation before real input) belong to the views;
this class only remembers what they need: the non-secret param values per script for
the session and whether to confirm runs that send real input.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, Signal

from j5_macro.client import EngineError
from j5_macro.gui.bridge import EngineBridge
from j5_macro.gui.controllers.document import ScriptDocument
from j5_macro.gui.engine_thread import describe_error
from j5_macro.gui.models.debugger import DebugActions, PauseInfo, debug_actions, pause_from_state
from j5_macro.gui.models.run_log import RunLog
from j5_macro.gui.models.run_options import RunKind, RunOptions, run_kind
from j5_macro.gui.models.script import ScriptState
from j5_macro.gui.models.variables import set_variable_payload
from j5_macro.protocol import Event

MODES = ("normal", "dry", "step")
STEP_COMMANDS = ("step_over", "step_into", "step_out")
CONFIRM_SETTING = "run/confirm_real_input"


class RunController(QObject):
    active_changed = Signal(bool)
    """A run started (True: from the ``run`` request on) or ended (False)."""
    run_started = Signal(str)
    """The run id, at ``run_started``."""
    event_logged = Signal(object)
    """A :class:`~j5_macro.gui.models.run_log.LogLine` per event of the active run."""
    current_step_changed = Signal(object)
    """The id of the step being run or paused at; None between runs."""
    state_changed = Signal(str)
    """``running``, ``paused``, ``stopping`` or ``finished``."""
    paused = Signal(object)
    """The :class:`~j5_macro.gui.models.debugger.PauseInfo` when the run pauses (again
    when ``get_state`` says where); None when it goes on or ends."""
    variables_changed = Signal(object)
    """``get_state``'s ``variables`` (secrets masked) at each pause; None when the run
    ends."""
    actions_changed = Signal(object)
    """The :class:`~j5_macro.gui.models.debugger.DebugActions` that apply now."""
    run_finished = Signal(object)
    """The ``run_finished`` payload (``status``, ``duration_ms``, ``error``...)."""
    breakpoints_changed = Signal(object)
    """The new ``frozenset`` of step ids with a client breakpoint."""
    error = Signal(str)
    """A run could not start or a control command failed: a message for people."""

    def __init__(self, bridge: EngineBridge, document: ScriptDocument,
                 parent: QObject | None = None, *, runs_dir: Path | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._document = document
        self._runs_dir = runs_dir
        self._log = RunLog()
        self._requested = False
        self._kind: RunKind | None = None
        self._pending = False
        self._skipping = False
        self._entry_breakpoint: str | None = None
        self._breakpoints: frozenset[str] = frozenset()
        self._breakpoints_by_script: dict[str, frozenset[str]] = {}
        self._script_key: str | None = None
        self._sent: tuple[str, frozenset[str]] | None = None
        self._variables: dict[str, Any] | None = None
        self._remembered: dict[str, dict[str, str]] = {}
        self._settings: QSettings | None = None
        self.confirm_real_input = True
        """Ask before a run that sends real input (the views ask; saved with
        :meth:`use_settings`)."""
        bridge.event_received.connect(self._on_event)
        bridge.running_changed.connect(self._engine_running)
        document.state_changed.connect(self._script_changed)

    # -- state ---------------------------------------------------------------------------

    @property
    def log(self) -> RunLog:
        """The log of the current (or last) run."""
        return self._log

    @property
    def active(self) -> bool:
        return self._requested or self._log.active

    @property
    def run_id(self) -> str | None:
        return self._log.run_id

    @property
    def kind(self) -> RunKind | None:
        """How the active (or last) run was started."""
        return self._kind

    @property
    def state(self) -> str:
        """``idle``, ``starting``, ``running``, ``paused``, ``stopping`` or
        ``finished``."""
        if self._requested and self._log.run_id is None:
            return "starting"
        if self._requested and self._log.state == "idle":
            return "starting"
        return self._log.state

    @property
    def pause(self) -> PauseInfo | None:
        return self._log.pause if self._log.state == "paused" else None

    @property
    def variables(self) -> dict[str, Any] | None:
        """The variables at the current pause (None before ``get_state`` answers)."""
        return self._variables

    @property
    def actions(self) -> DebugActions:
        state = self._document.state
        return debug_actions(engine_running=self._bridge.running,
                             can_run=state is not None and not self.active,
                             state=self.state, pending=self._pending)

    @property
    def runs_dir(self) -> Path | None:
        """Where the engine saves runs, when known (failure captures are found there)."""
        return self._runs_dir

    def capture_path(self, relative: str) -> Path | None:
        """The file of a capture of the active run (``step_failed.screenshot``)."""
        if self._runs_dir is None or self._log.run_id is None or not relative:
            return None
        return self._runs_dir / self._log.run_id / Path(*relative.split("/"))

    # -- preferences ---------------------------------------------------------------------

    def use_settings(self, settings: QSettings) -> None:
        """Read and keep the preferences (``confirm_real_input``) in ``settings``."""
        self._settings = settings
        value = settings.value(CONFIRM_SETTING, True)
        self.confirm_real_input = value not in (False, "false", "0", 0)

    def set_confirm_real_input(self, confirm: bool) -> None:
        self.confirm_real_input = confirm
        if self._settings is not None:
            self._settings.setValue(CONFIRM_SETTING, confirm)

    def remembered_params(self, script: ScriptState | None = None) -> dict[str, str]:
        """The non-secret param texts of the last run of this script, this session."""
        key = _script_key(script or self._document.state)
        return dict(self._remembered.get(key, {})) if key else {}

    def remember_params(self, texts: Mapping[str, str],
                        script: ScriptState | None = None) -> None:
        """Keep these texts (never pass secrets) for the next run of the script."""
        key = _script_key(script or self._document.state)
        if key:
            self._remembered[key] = dict(texts)

    # -- commands ------------------------------------------------------------------------

    def start(self, mode: str = "normal", *, params: Mapping[str, Any] | None = None,
              options: RunOptions | None = None, **extra: Any) -> None:
        """Run the open script. ``mode`` is a kind (``normal``, ``dry``, ``debug``,
        ``debug_dry``) or a ``run`` mode (``step`` is ``debug``). ``options`` and
        ``extra`` (other ``run`` fields such as ``strict``, ``vars``, ``seed``,
        ``input_profile``; ``None`` values are left out) complete the payload. Nothing
        given here is kept: secret params pass straight to the engine."""
        if mode not in MODES and mode not in ("debug", "debug_dry"):
            raise ValueError(f"unknown run mode {mode!r}")
        kind = run_kind(mode)
        state = self._document.state
        script_id = self._document.script_id
        if script_id is None or state is None:
            self.error.emit("open a script first")
            return
        if self.active:
            self.error.emit("a run is already active")
            return
        payload: dict[str, Any] = {"script_id": script_id, "mode": kind.mode}
        if params:
            payload["params"] = dict(params)
        if options is not None:
            payload.update(options.payload(kind))
        payload.update({key: value for key, value in extra.items() if value is not None})
        self._log = RunLog()
        self._kind = kind
        self._requested = True
        self._pending = False
        self._skipping = False
        self._variables = None
        self._entry_breakpoint = None
        self.active_changed.emit(True)
        self._actions_changed()
        if kind.stop_on_entry and kind.mode == "dry":
            first = state.steps[0].id if state.steps else None
            if first is not None and first not in self._breakpoints:
                self._entry_breakpoint = first
                self._send_breakpoints(self._breakpoints | {first}, force=True)
        self._bridge.call("run", payload, on_result=self._run_answered,
                          on_error=self._run_refused)

    def stop(self) -> None:
        """Stop the active run (``stop``, reason ``user``)."""
        run_id = self._log.run_id
        if run_id is None or not self._log.active:
            return
        self._bridge.call("stop", {"run_id": run_id, "reason": "user"},
                          on_error=lambda exc: self.error.emit(
                              f"stop failed: {describe_error(exc)}"))

    def pause_run(self) -> None:
        """Pause at the next atomic action (``pause``)."""
        self._control("pause", require="running")

    def resume(self) -> None:
        """Continue to the next breakpoint or the end; after an error or interference,
        retry the step (``resume``)."""
        self._control("resume")

    def step_over(self) -> None:
        self._control("step_over")

    def step_into(self) -> None:
        self._control("step_into")

    def step_out(self) -> None:
        self._control("step_out")

    def retry(self) -> None:
        """Run the failed or interrupted step again (``resume``)."""
        self.resume()

    def skip(self, carry_on: bool | None = None) -> None:
        """Skip the failed or interrupted step (``step_over``, which pauses after it).
        With ``carry_on`` (by default when the run was not started to debug) the run
        then continues on its own, as the CLI does."""
        if carry_on is None:
            carry_on = not (self._kind is not None and self._kind.debugging)
        if self._control("step_over"):
            self._skipping = carry_on

    def refresh_state(self) -> None:
        """Ask ``get_state`` for the current step and the variables."""
        run_id = self._log.run_id
        if run_id is None or not self._log.active:
            return
        self._bridge.call("get_state", {"run_id": run_id},
                          on_result=lambda result: self._got_state(run_id, result),
                          on_error=lambda exc: self.error.emit(
                              f"get_state failed: {describe_error(exc)}"))

    def set_variable(self, name: str, text: str, *, expression: bool,
                     on_done: Callable[[str | None], None] | None = None) -> None:
        """``set_variable`` with a value (JSON, else text) or an expression; then the
        variables are read again. ``on_done`` gets None, or the error message."""
        run_id = self._log.run_id
        if run_id is None or self.state != "paused":
            _call(on_done, "the run is not paused")
            return
        try:
            payload = set_variable_payload(run_id, name, text, expression=expression)
        except ValueError as exc:
            _call(on_done, str(exc))
            return

        def done(_: object) -> None:
            self.refresh_state()
            _call(on_done, None)

        self._bridge.call("set_variable", payload, on_result=done,
                          on_error=lambda exc: _call(on_done, _message(exc)))

    def evaluate(self, expression: str, on_result: Callable[[Any], None],
                 on_error: Callable[[str], None]) -> None:
        """``evaluate`` over the paused run's variables (secrets come back masked)."""
        run_id = self._log.run_id
        if run_id is None or self.state != "paused":
            on_error("the run is not paused")
            return
        if not expression.strip():
            on_error("type an expression")
            return
        self._bridge.call("evaluate", {"run_id": run_id, "expression": expression},
                          on_result=lambda result: on_result(result.get("value")),
                          on_error=lambda exc: on_error(_message(exc)))

    # -- breakpoints ---------------------------------------------------------------------

    @property
    def breakpoints(self) -> frozenset[str]:
        return self._breakpoints

    def set_breakpoints(self, step_ids: Iterable[str]) -> None:
        """The user's breakpoints on the open script; sent to the engine, so they apply
        to the next run and to the active one at once."""
        breakpoints = frozenset(step_ids)
        if breakpoints != self._breakpoints:
            self._breakpoints = breakpoints
            if self._script_key is not None:
                self._breakpoints_by_script[self._script_key] = breakpoints
            self.breakpoints_changed.emit(breakpoints)
        extra = {self._entry_breakpoint} if self._entry_breakpoint else set()
        self._send_breakpoints(breakpoints | extra)

    def toggle_breakpoint(self, step_id: str) -> None:
        self.set_breakpoints(self._breakpoints ^ {step_id})

    def _send_breakpoints(self, step_ids: frozenset[str], *, force: bool = False) -> None:
        script_id = self._document.script_id
        if script_id is None or not self._bridge.running:
            return
        if not force and self._sent == (script_id, step_ids):
            return
        self._sent = (script_id, step_ids)
        self._bridge.call("set_breakpoints",
                          {"script_id": script_id, "step_ids": sorted(step_ids)},
                          on_error=lambda exc: self.error.emit(
                              f"set_breakpoints failed: {describe_error(exc)}"))

    # -- engine --------------------------------------------------------------------------

    def _control(self, command: str, *, require: str = "paused") -> bool:
        run_id = self._log.run_id
        if run_id is None or self._log.state != require or self._pending:
            return False
        self._pending = True
        self._actions_changed()
        self._bridge.call(command, {"run_id": run_id},
                          on_error=lambda exc: self._control_failed(command, exc))
        return True

    def _control_failed(self, command: str, exc: BaseException) -> None:
        self._pending = False
        self._skipping = False
        self._actions_changed()
        if self._log.active:
            self.error.emit(f"{command} failed: {describe_error(exc)}")

    def _run_answered(self, result: Mapping[str, Any]) -> None:
        run_id = result.get("run_id")
        if self._log.run_id is None and isinstance(run_id, str):
            self._log.run_id = run_id

    def _run_refused(self, exc: BaseException) -> None:
        self._requested = False
        self._restore_entry_breakpoint()
        self.active_changed.emit(False)
        self._actions_changed()
        self.error.emit(f"the run could not start: {describe_error(exc)}")

    def _on_event(self, event: Event) -> None:
        if event.run_id is None:
            return
        if event.type == "run_started" and self._requested and self._log.run_id is None:
            self._log.run_id = event.run_id
        if event.run_id != self._log.run_id:
            return
        step_before, state_before = self._log.current_step_id, self._log.state
        line = self._log.apply(event)
        self.event_logged.emit(line)
        if event.type == "run_started":
            self.run_started.emit(event.run_id)
            self.state_changed.emit(self._log.state)
            self._actions_changed()
        if event.type == "state_changed":
            self._state_event(state_before)
        if self._log.current_step_id != step_before:
            self.current_step_changed.emit(self._log.current_step_id)
        if event.type == "run_finished":
            self._requested = False
            self._pending = False
            self._skipping = False
            self._variables = None
            self._restore_entry_breakpoint()
            if state_before == "paused":
                self.paused.emit(None)
            self.state_changed.emit(self._log.state)
            self.variables_changed.emit(None)
            self.run_finished.emit(dict(event.payload))
            self.active_changed.emit(False)
            self._actions_changed()

    def _state_event(self, state_before: str) -> None:
        self._pending = False
        state = self._log.state
        if state == "paused":
            if self._entry_breakpoint is not None:
                # The breakpoint a dry debug run starts with: this is the entry pause.
                self._restore_entry_breakpoint()
                pause = self._log.pause
                if pause is not None and pause.reason == "breakpoint":
                    self._log.pause = replace(pause, reason="step")
            if self._skipping and self._log.pause_reason == "step":
                # The pause that follows skipping a step: carry on without asking.
                self._skipping = False
                self.state_changed.emit(state)
                self._control("resume")
                return
            self._skipping = False
            self.state_changed.emit(state)
            self.paused.emit(self._log.pause)
            self.refresh_state()
        else:
            self._variables = None  # until the next pause; the views keep the last ones
            if state_before == "paused":
                self.paused.emit(None)
            if state != state_before:
                self.state_changed.emit(state)
        self._actions_changed()

    def _got_state(self, run_id: str, result: Mapping[str, Any]) -> None:
        if run_id != self._log.run_id or self._log.state != "paused":
            return
        variables = result.get("variables")
        self._variables = dict(variables) if isinstance(variables, Mapping) else {}
        step_id = result.get("step_id")
        if isinstance(step_id, str) and step_id != self._log.current_step_id:
            self._log.current_step_id = step_id
            self.current_step_changed.emit(step_id)
        pause = pause_from_state(self._log.pause, result)
        if pause is not self._log.pause:
            self._log.pause = pause
            self.paused.emit(pause)
        self.variables_changed.emit(dict(self._variables))

    def _restore_entry_breakpoint(self) -> None:
        if self._entry_breakpoint is None:
            return
        self._entry_breakpoint = None
        self._send_breakpoints(self._breakpoints, force=True)

    def _actions_changed(self) -> None:
        self.actions_changed.emit(self.actions)

    def _engine_running(self, running: bool) -> None:
        """The engine died or was restarted: no run is active any more, and the new
        engine needs the breakpoints again."""
        self._sent = None
        if running or not self.active:
            self._actions_changed()
            return
        was_paused = self._log.state == "paused"
        self._requested = False
        self._pending = False
        self._skipping = False
        self._entry_breakpoint = None
        self._variables = None
        self._log.state = "finished"
        self._log.current_step_id = None
        self._log.pause = None
        self.current_step_changed.emit(None)
        if was_paused:
            self.paused.emit(None)
        self.variables_changed.emit(None)
        self.state_changed.emit("finished")
        self.active_changed.emit(False)
        self._actions_changed()

    def _script_changed(self, state: ScriptState | None) -> None:
        key = _script_key(state)
        if key != self._script_key:
            self._script_key = key
            self.set_breakpoints(self._breakpoints_by_script.get(key, frozenset())
                                 if key else ())
        else:
            self._send_breakpoints(self._breakpoints)
        self._actions_changed()


def _script_key(state: ScriptState | None) -> str | None:
    """Identifies a script across reloads and engine restarts: its file, else its id."""
    if state is None:
        return None
    return os.path.normcase(os.path.abspath(state.path)) if state.path else state.script_id


def _message(exc: BaseException) -> str:
    if isinstance(exc, EngineError):
        return exc.error.message
    return describe_error(exc)


def _call(callback: Callable[[str | None], None] | None, message: str | None) -> None:
    if callback is not None:
        callback(message)
