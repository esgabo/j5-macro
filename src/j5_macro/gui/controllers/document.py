"""The open script: loading it, keeping it current and editing it through the engine.

The GUI never parses or writes YAML itself (docs/contract.md, "Script management"): it
``load``s a file, reads it back with ``get_script`` and changes it with ``edit_script``.
When the file changes, on disk or through an edit, the engine emits ``script_changed``
and the document reloads, so every view always shows what is in the file.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import QObject, Signal

from j5_macro.client import EngineError
from j5_macro.gui.bridge import EngineBridge, ErrorCallback, ResultCallback
from j5_macro.gui.engine_thread import describe_error
from j5_macro.gui.models.script import ScriptState
from j5_macro.protocol import Event


class ScriptDocument(QObject):
    """The script open in the window (at most one)."""

    state_changed = Signal(object)
    """The new :class:`ScriptState` after every load and reload; None when closed."""
    load_failed = Signal(str, object)
    """``(message, errors)``: ``load`` refused the file; ``errors`` is the engine's
    list of problems (each with ``message``, ``code`` and ``line``)."""
    notice = Signal(str)
    """Something to tell the user in passing (the file was deleted on disk...)."""

    def __init__(self, bridge: EngineBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._script_id: str | None = None
        self._path: str | None = None
        self._state: ScriptState | None = None
        self._warnings: list[Mapping[str, Any]] = []
        self._token = 0
        self._stale = False
        bridge.event_received.connect(self._on_event)
        bridge.running_changed.connect(self._engine_running)
        bridge.engine_started.connect(self._engine_started)

    @property
    def script_id(self) -> str | None:
        return self._script_id

    @property
    def path(self) -> str | None:
        """The file asked for in :meth:`open`, even while it fails to load."""
        return self._path

    @property
    def state(self) -> ScriptState | None:
        return self._state

    def open(self, path: str) -> None:
        """Load the script at ``path`` and show it (``state_changed``)."""
        self._token += 1
        token = self._token
        self._path = path
        self._bridge.call("load", {"path": path},
                          on_result=lambda result: self._loaded(token, result),
                          on_error=lambda exc: self._load_failed(token, exc))

    def reopen(self) -> None:
        """Load the same file again; done on its own when a new engine starts, since
        script ids belong to the engine that gave them."""
        if self._path is not None:
            self.open(self._path)

    def reload(self) -> None:
        """Read the script again with ``get_script``."""
        if self._script_id is None:
            return
        token = self._token
        self._bridge.call("get_script", {"script_id": self._script_id},
                          on_result=lambda result: self._got(token, result),
                          on_error=lambda exc: self.notice.emit(
                              f"cannot read the script: {describe_error(exc)}"))

    def close(self) -> None:
        self._token += 1
        self._script_id = None
        self._path = None
        self._state = None
        self.state_changed.emit(None)

    def edit(self, edits: Sequence[Mapping[str, Any]], *,
             on_result: ResultCallback | None = None,
             on_error: ErrorCallback | None = None) -> None:
        """Send ``edit_script`` edits (docs/contract.md, "Edit kinds") based on the
        version shown. The reload comes with the engine's ``script_changed``; a
        ``conflict`` (the file changed meanwhile) reloads too, then reaches ``on_error``."""
        if self._script_id is None or self._state is None:
            raise RuntimeError("no script is open")

        def failed(exc: BaseException) -> None:
            if isinstance(exc, EngineError) and exc.code == "conflict":
                self.reload()
            if on_error is not None:
                on_error(exc)
            else:
                self.notice.emit(f"edit failed: {describe_error(exc)}")

        self._bridge.call("edit_script", {"script_id": self._script_id,
                                          "base_version": self._state.version,
                                          "edits": [dict(edit) for edit in edits]},
                          on_result=on_result, on_error=failed)

    # -- engine answers ------------------------------------------------------------------

    def _loaded(self, token: int, result: Mapping[str, Any]) -> None:
        if token != self._token:
            return
        self._script_id = str(result["script_id"])
        warnings = result.get("warnings")
        self._warnings = [w for w in warnings if isinstance(w, Mapping)] \
            if isinstance(warnings, list) else []
        self.reload()

    def _load_failed(self, token: int, exc: BaseException) -> None:
        if token != self._token:
            return
        self._script_id = None
        self._state = None
        errors: list[Mapping[str, Any]] = []
        if isinstance(exc, EngineError) and exc.error.details:
            raw = exc.error.details.get("errors")
            errors = [e for e in raw if isinstance(e, Mapping)] if isinstance(raw, list) else []
        self.state_changed.emit(None)
        self.load_failed.emit(describe_error(exc), errors)

    def _got(self, token: int, result: Mapping[str, Any]) -> None:
        if token != self._token or result.get("script_id") != self._script_id:
            return
        self._state = ScriptState.from_get_script(result, self._warnings)
        self.state_changed.emit(self._state)

    def _engine_running(self, running: bool) -> None:
        if not running:
            self._stale = self._path is not None

    def _engine_started(self, info: object) -> None:
        if self._stale:
            self._stale = False
            self.reopen()

    def _on_event(self, event: Event) -> None:
        if event.type != "script_changed" or event.run_id is not None:
            return
        if self._script_id is None or event.payload.get("script_id") != self._script_id:
            return
        if event.payload.get("source") == "deleted":
            self.notice.emit("the script file was deleted on disk; it stays open as it was")
            return
        self.reload()
