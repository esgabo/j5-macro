"""Error helpers for step failures."""

from __future__ import annotations

import dataclasses
import traceback

from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import ErrorInfo
from j5_macro.script.model import Step


class RunFailed(Exception):
    """A step failure that ends the run (``on_error: stop`` or retries exhausted)."""

    def __init__(self, info: ErrorInfo) -> None:
        super().__init__(info.message)
        self.info = info


def internal_error(exc: BaseException) -> ErrorInfo:
    """``internal_error`` for an unexpected exception, with its traceback in details."""
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return ErrorInfo(code=ErrorCode.INTERNAL_ERROR.value,
                     message=f"{type(exc).__name__}: {exc}",
                     details={"traceback": trace})


def step_exception(exc: BaseException) -> ErrorInfo:
    """The error for an exception a step raised: ``input_denied`` when the system
    refused input (a backend raises ``PermissionError``), else ``internal_error``."""
    if isinstance(exc, PermissionError):
        return ErrorInfo(code=ErrorCode.INPUT_DENIED.value, message=str(exc))
    return internal_error(exc)


def at_step(info: ErrorInfo, step: Step, attempt: int) -> ErrorInfo:
    """``info`` located at ``step``, with ``details.attempt``."""
    details = {**(info.details or {}), "attempt": attempt}
    return dataclasses.replace(info, step_id=step.id, line=step.line, details=details)
