"""Edits of the editor, one at a time.

``edit_script`` names the version it is based on, and the document only knows the new
version once it has reloaded (after the engine's ``script_changed``). Edits sent in a
row (typing in a field, then dragging a step) would otherwise conflict, so the queue
sends the next edit only when the document shows the version the last one produced.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from j5_macro.client import ClientError, EngineError
from j5_macro.gui.bridge import EngineBridge
from j5_macro.gui.controllers.document import ScriptDocument
from j5_macro.gui.engine_thread import describe_error
from j5_macro.gui.models.script import ScriptState

RELOAD_WAIT_MS = 3000
"""How long to wait for the reload before sending the next edit anyway."""
ANY_VERSION = ""


@dataclass(slots=True)
class _Pending:
    edits: list[dict[str, Any]]
    on_result: Callable[[Mapping[str, Any]], None] | None
    on_error: Callable[[str], None] | None
    retried: bool = False


class EditQueue(QObject):
    """Sends ``edit_script`` edits through the :class:`ScriptDocument`, in order."""

    failed = Signal(str)
    """An edit was refused (the message), when it has no ``on_error`` of its own."""
    idle_changed = Signal(bool)

    def __init__(self, document: ScriptDocument, parent: QObject | None = None, *,
                 bridge: EngineBridge | None = None) -> None:
        super().__init__(parent)
        self._document = document
        self._generation = 0
        self._queue: deque[_Pending] = deque()
        self._busy = False
        self._waiting_for: str | None = None
        self._idle_callbacks: list[Callable[[], None]] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RELOAD_WAIT_MS)
        self._timer.timeout.connect(self._reloaded)
        document.state_changed.connect(self._state_changed)
        if bridge is not None:
            bridge.engine_started.connect(lambda _: self.engine_restarted())

    def engine_restarted(self) -> None:
        """A new engine: the answer to an edit sent to the old one never comes (it is
        dropped), so stop waiting for it; the reload shows whether it was applied. Edits
        still queued are sent to the new engine (once it has the script again)."""
        if not self._busy:
            return
        self._generation += 1
        self._timer.stop()
        self._waiting_for = None
        self._finish()

    @property
    def idle(self) -> bool:
        """Nothing is being sent or waiting."""
        return not self._busy and not self._queue

    def submit(self, edits: Sequence[Mapping[str, Any]], *,
               on_result: Callable[[Mapping[str, Any]], None] | None = None,
               on_error: Callable[[str], None] | None = None) -> None:
        """Send ``edits`` (one ``edit_script``) after the ones already queued."""
        if not edits:
            return
        self._queue.append(_Pending([dict(e) for e in edits], on_result, on_error))
        self._next()

    def when_idle(self, callback: Callable[[], None]) -> None:
        """Call ``callback`` once every edit sent so far is applied and reloaded (now,
        when nothing is pending)."""
        if self.idle:
            callback()
        else:
            self._idle_callbacks.append(callback)

    def clear(self) -> None:
        """Forget the edits not sent yet (the script was closed)."""
        self._queue.clear()

    def _next(self) -> None:
        if self._busy or not self._queue:
            if not self._busy:
                self.idle_changed.emit(True)
                callbacks, self._idle_callbacks = self._idle_callbacks, []
                for callback in callbacks:
                    callback()
            return
        if self._document.state is None:
            self._queue.clear()
            return
        pending = self._queue.popleft()
        self._busy = True
        self.idle_changed.emit(False)
        generation = self._generation

        def done(result: Mapping[str, Any]) -> None:
            if generation != self._generation:
                return
            version = result.get("version")
            state = self._document.state
            if pending.on_result is not None:
                pending.on_result(result)
            if isinstance(version, str) and (state is None or state.version != version):
                self._waiting_for = version
                self._timer.start()
            else:
                self._finish()

        def failed(exc: BaseException) -> None:
            if generation != self._generation:
                return
            if not pending.retried and _engine_restarting(exc):
                # A new engine: the document opens the file again (script ids belong to
                # the engine), then the edit is sent again, once.
                pending.retried = True
                self._queue.appendleft(pending)
                self._waiting_for = ANY_VERSION
                self._timer.start()
                return
            message = describe_error(exc)
            if pending.on_error is not None:
                pending.on_error(message)
            else:
                self.failed.emit(message)
            if isinstance(exc, EngineError) and exc.code == "conflict":
                self._waiting_for = ANY_VERSION  # the document reloads: wait for it
                self._timer.start()
            else:
                self._finish()

        try:
            self._document.edit(pending.edits, on_result=done, on_error=failed)
        except RuntimeError as exc:  # the script was closed meanwhile
            self._busy = False
            self.failed.emit(str(exc))
            self._next()

    def _state_changed(self, state: ScriptState | None) -> None:
        if self._waiting_for is None:
            return
        if state is None or self._waiting_for in (ANY_VERSION, state.version):
            self._reloaded()

    def _reloaded(self) -> None:
        self._timer.stop()
        self._waiting_for = None
        self._finish()

    def _finish(self) -> None:
        self._busy = False
        self._next()


def _engine_restarting(exc: BaseException) -> bool:
    """The edit failed because the engine was replaced (its script ids are gone) or is
    not running yet."""
    if isinstance(exc, EngineError):
        return exc.code == "script_not_found"
    return isinstance(exc, ClientError)
