"""Exceptions raised by the engine client."""

from __future__ import annotations

from collections.abc import Sequence

from j5_macro.protocol import ErrorInfo


class ClientError(Exception):
    """Base class for every client-side failure."""


class EngineError(ClientError):
    """The engine answered a command with ``ok: false``."""

    def __init__(self, command: str, error: ErrorInfo) -> None:
        super().__init__(f"{command} failed: [{error.code}] {error.message}")
        self.command = command
        self.error = error

    @property
    def code(self) -> str:
        return self.error.code


class EngineDied(ClientError):
    """The engine process exited (or closed stdout) without being asked to."""

    def __init__(self, exit_code: int | None, stderr_tail: Sequence[str]) -> None:
        self.exit_code = exit_code
        self.stderr_tail = list(stderr_tail)
        message = f"engine died (exit code {exit_code})"
        if self.stderr_tail:
            message += "; last stderr lines:\n" + "\n".join(self.stderr_tail[-10:])
        super().__init__(message)


class ClientClosed(ClientError):
    """The client was closed while the operation was pending or afterwards."""
