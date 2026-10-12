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


def require_int(payload: JsonObject, name: str) -> int:
    """Return ``payload[name]`` if it is an integer, else raise ``validation_failed``."""
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandError(ErrorCode.VALIDATION_FAILED, f"'{name}' must be an integer",
                           {"field": name})
    return value


def require_str(payload: JsonObject, name: str, *, nullable: bool = False) -> str | None:
    """Return ``payload[name]`` if it is a string (or null when allowed)."""
    if name not in payload:
        raise CommandError(ErrorCode.VALIDATION_FAILED, f"'{name}' is required",
                           {"field": name})
    value = payload[name]
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        kind = "a string or null" if nullable else "a string"
        raise CommandError(ErrorCode.VALIDATION_FAILED, f"'{name}' must be {kind}",
                           {"field": name})
    return value
