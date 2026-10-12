"""Stable error codes (docs/contract.md, "Errores y decisiones abiertas").

Codes are part of the protocol: clients branch on them, so a code is never
renamed or reused. Human-readable detail goes in ``ErrorInfo.message``.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    PARSE_ERROR = "parse_error"
    """The YAML is invalid; includes line and column."""
    VALIDATION_FAILED = "validation_failed"
    """A parameter is missing or has the wrong type, an id is duplicated, or a message is
    malformed."""
    UNKNOWN_STEP_TYPE = "unknown_step_type"
    """The step type is not in the registry."""
    SCRIPT_NOT_FOUND = "script_not_found"
    """The script_id does not exist or the file cannot be opened."""
    NOT_RUNNING = "not_running"
    """A control command was sent without an active run."""
    INVALID_STATE = "invalid_state"
    """The command is not allowed in the current state."""
    BUSY = "busy"
    """``run`` while another run is active."""
    CONFLICT = "conflict"
    """The script changed since the ``base_version`` sent with ``edit_script``."""
    TEMPLATE_MISSING = "template_missing"
    """A step's image file does not exist."""
    IMAGE_NOT_FOUND = "image_not_found"
    """``wait_image`` or an image-based step ran out of time; includes ``best_score``."""
    EXPRESSION_ERROR = "expression_error"
    """An expression or interpolation failed."""
    BACKEND_UNAVAILABLE = "backend_unavailable"
    """No backend fits the current session."""
    CAPABILITY_MISSING = "capability_missing"
    """The backend lacks something a step or profile needs."""
    INPUT_DENIED = "input_denied"
    """The system refused input or capture for lack of permissions."""
    INTERNAL_ERROR = "internal_error"
    """Unexpected engine failure; includes a traceback in ``details``."""
