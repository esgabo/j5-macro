"""Command handler types shared by the server and the command modules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import Command, ErrorInfo, JsonObject

Handler = Callable[[Command], JsonObject]
"""Takes a command and returns the response ``result``, or raises :class:`CommandError`."""


@dataclass(frozen=True, slots=True)
class HandlerSpec:
    handler: Handler
    requires_hello: bool = True


class CommandError(Exception):
    """An expected failure: the server turns it into an ``ok: false`` response."""

    def __init__(self, code: ErrorCode, message: str, details: JsonObject | None = None) -> None:
        super().__init__(message)
        self.info = ErrorInfo(code=code.value, message=message, details=details)

