"""The active run: starting and stopping it, and following its events.

One run at a time (docs/contract.md, "One run at a time"). :class:`RunController` feeds
the events of the active run to a :class:`~j5_macro.gui.models.run_log.RunLog` and
re-emits what views need as signals. It also keeps the breakpoints the user set on the
open script.

Extension point (run and debugging): pause, resume, ``step_*``, ``get_state``,
``set_variable``, ``evaluate``, sending breakpoints with ``set_breakpoints``, the params
prompt and the on-error and interference pauses belong here and in
:mod:`j5_macro.gui.views.run_panel`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from PySide6.QtCore import QObject, Signal

from j5_macro.gui.bridge import EngineBridge
from j5_macro.gui.controllers.document import ScriptDocument
from j5_macro.gui.engine_thread import describe_error
from j5_macro.gui.models.run_log import RunLog
from j5_macro.protocol import Event

MODES = ("normal", "dry", "step")


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
    run_finished = Signal(object)
    """The ``run_finished`` payload (``status``, ``duration_ms``, ``error``...)."""
    breakpoints_changed = Signal(object)
    """The new ``frozenset`` of step ids with a client breakpoint."""
    error = Signal(str)
    """A run could not start or a control command failed: a message for people."""

    def __init__(self, bridge: EngineBridge, document: ScriptDocument,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._document = document
        self._log = RunLog()
        self._requested = False
        self._breakpoints: frozenset[str] = frozenset()
        bridge.event_received.connect(self._on_event)
        bridge.running_changed.connect(self._engine_running)
        document.state_changed.connect(self._script_changed)

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

    # -- commands ------------------------------------------------------------------------

    def start(self, mode: str = "normal", **options: Any) -> None:
        """Run the open script. ``options`` are ``run``'s other fields (``strict``,
        ``params``, ``vars``, ``seed``, ``input_profile``, ``captures``, ``allow_exec``,
        ``display_scale``); ``None`` values are left out."""
        if mode not in MODES:
            raise ValueError(f"unknown run mode {mode!r}")
        script_id = self._document.script_id
        if script_id is None:
            self.error.emit("open a script first")
            return
        if self.active:
            self.error.emit("a run is already active")
            return
        payload: dict[str, Any] = {"script_id": script_id, "mode": mode}
        payload.update({key: value for key, value in options.items() if value is not None})
        self._log = RunLog()
        self._requested = True
        self.active_changed.emit(True)
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

    # -- breakpoints ---------------------------------------------------------------------

    @property
    def breakpoints(self) -> frozenset[str]:
        return self._breakpoints

    def set_breakpoints(self, step_ids: Iterable[str]) -> None:
        breakpoints = frozenset(step_ids)
        if breakpoints != self._breakpoints:
            self._breakpoints = breakpoints
            self.breakpoints_changed.emit(breakpoints)

    def toggle_breakpoint(self, step_id: str) -> None:
        self.set_breakpoints(self._breakpoints ^ {step_id})

    # -- engine --------------------------------------------------------------------------

    def _run_answered(self, result: Mapping[str, Any]) -> None:
        run_id = result.get("run_id")
        if self._log.run_id is None and isinstance(run_id, str):
            self._log.run_id = run_id

    def _run_refused(self, exc: BaseException) -> None:
        self._requested = False
        self.active_changed.emit(False)
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
        if event.type == "run_started":
            self.run_started.emit(event.run_id)
        self.event_logged.emit(line)
        if self._log.state != state_before:
            self.state_changed.emit(self._log.state)
        if self._log.current_step_id != step_before:
            self.current_step_changed.emit(self._log.current_step_id)
        if event.type == "run_finished":
            self._requested = False
            self.run_finished.emit(dict(event.payload))
            self.active_changed.emit(False)

    def _engine_running(self, running: bool) -> None:
        """The engine died or was restarted: no run is active any more."""
        if running or not self.active:
            return
        self._requested = False
        self._log.state = "finished"
        self._log.current_step_id = None
        self.current_step_changed.emit(None)
        self.state_changed.emit("finished")
        self.active_changed.emit(False)

    def _script_changed(self, state: object) -> None:
        if state is None:
            self.set_breakpoints(())
