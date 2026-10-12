"""Engine server: reads commands, dispatches them and writes responses and events."""

from __future__ import annotations

import threading
import traceback
from collections import deque
from collections.abc import Callable

from j5_macro.engine.dispatch import CommandError, Handler, HandlerSpec
from j5_macro.engine.events import EventSequencer
from j5_macro.engine.transport import LineTransport
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import (
    PROTOCOL_VERSION,
    Command,
    ErrorInfo,
    Event,
    JsonObject,
    ProtocolError,
    Response,
    decode,
    encode,
)
from j5_macro.protocol.payloads import PayloadError

EVENT_BUFFER_SIZE = 1000
"""Engine-level events kept in memory for ``get_events``."""


class EngineServer:
    """Serves one client over a :class:`LineTransport` until end of input.

    Handlers are registered per command type with :meth:`register`; the core
    commands (``hello``, ``get_events`` and the not-yet-implemented ones) are
    installed by :func:`j5_macro.engine.commands.install` (see ``app.create_server``).
    """

    def __init__(self, transport: LineTransport, events: EventSequencer | None = None) -> None:
        self.transport = transport
        self.events = events if events is not None else EventSequencer()
        self.negotiated_version: int | None = None
        self._handlers: dict[str, HandlerSpec] = {}
        self._buffer: deque[Event] = deque(maxlen=EVENT_BUFFER_SIZE)
        self._emit_lock = threading.Lock()
        self.ready_payload: JsonObject = {
            "v": PROTOCOL_VERSION, "backend": None,
            "capabilities": {"backend": None, "input_strategies": [], "step_types": []},
        }
        """Payload of the ``ready`` event; command modules fill in the capabilities."""
        self._after_response: list[Callable[[], None]] = []
        self._shutdown_hooks: list[Callable[[], None]] = []
        self._ready_hooks: list[Callable[[], None]] = []

    # -- registration -----------------------------------------------------

    def register(self, type: str, handler: Handler, *, requires_hello: bool = True) -> None:
        """Register (or replace) the handler for a command type."""
        self._handlers[type] = HandlerSpec(handler, requires_hello)

    def is_registered(self, type: str) -> bool:
        return type in self._handlers

    def after_response(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` once the response to the command being handled is written.

        Only for handlers on the protocol thread; e.g. ``run`` starts its worker here so
        no run event can precede the ``run`` response on the wire.
        """
        self._after_response.append(callback)

    def on_ready(self, hook: Callable[[], None]) -> None:
        """Run ``hook`` right after ``ready`` is emitted (e.g. startup warnings, which
        must not precede ``ready``)."""
        self._ready_hooks.append(hook)

    def on_shutdown(self, hook: Callable[[], None]) -> None:
        """Run ``hook`` at end of input, before :meth:`serve` returns."""
        self._shutdown_hooks.append(hook)

    # -- output -----------------------------------------------------------

    def emit(self, type: str, payload: JsonObject | None = None) -> Event:
        """Emit an engine-level event (``run_id: null``) and keep it for ``get_events``.

        Stamping, buffering and writing happen under one lock, so the wire order
        always matches ``seq`` even with several emitting threads.
        """
        with self._emit_lock:
            event = self.events.next(type, payload)
            self._buffer.append(event)
            self.transport.write_line(encode(event))
        return event

    def buffered_events(self, from_seq: int) -> list[Event]:
        with self._emit_lock:
            return [event for event in self._buffer if event.seq >= from_seq]

    def log(self, level: str, message: str) -> None:
        self.emit("log", {"level": level, "message": message})

    def respond(self, response: Response) -> None:
        self.transport.write_line(encode(response))

    # -- loop -------------------------------------------------------------

    def serve(self, ready_payload: JsonObject | None = None) -> int:
        """Emit ``ready``, then handle lines until end of input. Returns the exit code.

        ``ready_payload`` defaults to :attr:`ready_payload`. At end of input the
        shutdown hooks run (e.g. stopping an active run) before returning.
        """
        self.emit("ready", ready_payload if ready_payload is not None else self.ready_payload)
        try:
            for ready_hook in self._ready_hooks:
                ready_hook()
            while (line := self.transport.read_line()) is not None:
                if line.strip():
                    self.handle_line(line)
        finally:
            for hook in self._shutdown_hooks:
                hook()
        return 0

    def handle_line(self, line: str) -> None:
        """Handle one input line; a line that is not a command becomes a ``log`` event."""
        try:
            message = decode(line)
        except ProtocolError as exc:
            self.log("error", f"ignored malformed line: {exc}")
            return
        if not isinstance(message, Command):
            kind = type(message).__name__.lower()
            self.log("error", f"ignored line: expected kind 'command', got {kind!r}")
            return
        if not isinstance(message.type, str) or not isinstance(message.id, str):
            self.log("error", "ignored malformed command: 'type' and 'id' must be strings")
            return
        try:
            self.respond(self.dispatch(message))
        finally:
            callbacks, self._after_response = self._after_response, []
            for callback in callbacks:
                callback()

    def dispatch(self, command: Command) -> Response:
        """Run the handler for ``command`` and build its response."""
        try:
            result = self._invoke(command)
        except CommandError as exc:
            return Response(type=command.type, id=command.id, ok=False, error=exc.info)
        except PayloadError as exc:
            return Response(type=command.type, id=command.id, ok=False, error=exc.error)
        except Exception as exc:
            info = ErrorInfo(
                code=ErrorCode.INTERNAL_ERROR.value,
                message=f"unexpected error handling '{command.type}': {exc}",
                details={"traceback": traceback.format_exc()},
            )
            return Response(type=command.type, id=command.id, ok=False, error=info)
        return Response(type=command.type, id=command.id, ok=True, result=result)

    def _invoke(self, command: Command) -> JsonObject:
        spec = self._handlers.get(command.type)
        if spec is None:
            raise CommandError(ErrorCode.VALIDATION_FAILED,
                               f"unknown command type: {command.type!r}")
        if spec.requires_hello and self.negotiated_version is None:
            raise CommandError(ErrorCode.INVALID_STATE,
                               f"'{command.type}' requires a successful 'hello' first",
                               {"reason": "hello_required"})
        if not isinstance(command.payload, dict):
            raise CommandError(ErrorCode.VALIDATION_FAILED, "'payload' must be an object",
                               {"field": "payload"})
        return spec.handler(command)
