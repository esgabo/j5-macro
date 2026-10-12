"""How step types are declared, validated and executed.

This module is the seam between the loader, the step registry, the built-in steps and
the interpreter. It has no dependencies on any of them.

- A step type is a :class:`StepSpec`: its parameters (:class:`ParamSpec`), the names of
  its child block slots, and an optional shorthand parameter for the scalar YAML form
  (``sleep: 2s`` means ``sleep: {duration: 2s}``).
- :func:`validate_params` checks a parameter mapping against a spec at load time.
- Action steps are run by an :data:`Executor`, which talks to the interpreter only
  through :class:`StepContext`. Control steps (``if``, ``loop``) have no executor; the
  interpreter handles them.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import ErrorInfo

ParamType = Literal[
    "string", "integer", "number", "boolean", "duration", "expression", "condition",
    "any", "object", "list",
]
"""Parameter types. ``condition`` is an expression string or a one-key mapping naming a
predefined condition (``{image_visible: img/x.png}``)."""

StepKind = Literal["action", "control"]
LogLevel = Literal["debug", "info", "warning", "error"]

_INTERPOLATION = "${"


@dataclass(frozen=True, slots=True, kw_only=True)
class ParamSpec:
    name: str
    type: ParamType
    required: bool = False
    default: Any = None
    doc: str = ""
    choices: tuple[Any, ...] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StepSpec:
    name: str
    doc: str
    params: tuple[ParamSpec, ...] = ()
    blocks: tuple[str, ...] = ()
    """Child block slots in path order, e.g. ``("then", "else")``."""
    shorthand: str | None = None
    """Parameter that receives the value of the scalar form (``sleep: 2s``)."""
    kind: StepKind = "action"
    required_blocks: tuple[str, ...] = ()

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)


StepLookup = Callable[[str], StepSpec | None]
"""What the loader needs from the registry: a spec by step type name."""


# --- validation -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParamProblem:
    param: str
    message: str


def validate_params(spec: StepSpec, params: Mapping[str, Any]) -> tuple[dict[str, Any],
                                                                      list[ParamProblem]]:
    """Check ``params`` against ``spec`` and fill in defaults.

    Returns the defaulted parameters and the problems found (empty when valid).
    Strings containing ``${`` are accepted for any scalar type: they are interpolated
    and re-checked at run time. Block slots are not parameters and must not be passed.
    """
    problems: list[ParamProblem] = []
    known = {p.name for p in spec.params}
    for name in params:
        if name not in known:
            problems.append(ParamProblem(name, f"unknown parameter for '{spec.name}'"))
    result: dict[str, Any] = {}
    for p in spec.params:
        if p.name not in params or params[p.name] is None:
            if p.required:
                problems.append(ParamProblem(p.name, "missing required parameter"))
            elif p.default is not None:
                result[p.name] = p.default
            continue
        value = params[p.name]
        message = check_value(p, value)
        if message is not None:
            problems.append(ParamProblem(p.name, message))
        result[p.name] = value
    return result, problems


def check_value(param: ParamSpec, value: Any) -> str | None:
    """Return a problem description, or None if ``value`` fits ``param``."""
    if isinstance(value, str) and _INTERPOLATION in value and param.type not in (
        "object", "list"):
        return None  # checked again after interpolation
    message = _check_type(param.type, value)
    if message is None and param.choices is not None and value not in param.choices:
        options = ", ".join(map(str, param.choices))
        message = f"expected one of {options}"
    return message


def _check_type(kind: ParamType, value: Any) -> str | None:
    match kind:
        case "any":
            return None
        case "string" | "expression":
            return None if isinstance(value, str) else "expected a string"
        case "boolean":
            return None if isinstance(value, bool) else "expected true or false"
        case "integer":
            ok = isinstance(value, int) and not isinstance(value, bool)
            return None if ok else "expected an integer"
        case "number":
            ok = isinstance(value, int | float) and not isinstance(value, bool)
            return None if ok else "expected a number"
        case "object":
            return None if isinstance(value, Mapping) else "expected a mapping"
        case "list":
            return None if isinstance(value, list) else "expected a list"
        case "duration":
            try:
                parse_duration(value)
            except ValueError as exc:
                return str(exc)
            return None
        case "condition":
            if isinstance(value, str):
                return None
            if isinstance(value, Mapping) and len(value) == 1:
                return None
            return "expected an expression or a single predefined condition"
    return f"unsupported parameter type {kind!r}"


# --- durations --------------------------------------------------------------------------

_DURATION = re.compile(
    r"^\s*(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|h)\s*"
    r"(?:(?:±|\+-)\s*(?P<variance>\d+(?:\.\d+)?)\s*%)?\s*$"
)
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


@dataclass(frozen=True, slots=True)
class Duration:
    """A duration with optional relative variance (``2s±20%`` → 2.0 s, variance 0.2)."""

    seconds: float
    variance: float = 0.0

    def sample(self, rng: random.Random) -> float:
        """A concrete duration in seconds, uniformly within the variance, never negative."""
        if self.variance == 0:
            return self.seconds
        low = self.seconds * (1 - self.variance)
        high = self.seconds * (1 + self.variance)
        return max(0.0, rng.uniform(low, high))


def parse_duration(value: Any) -> Duration:
    """Parse ``2s``, ``500ms``, ``1.5m``, ``1h``, ``2s±20%`` (or ``2s+-20%``).

    A bare non-negative number means seconds. Raises ValueError otherwise.
    """
    if isinstance(value, bool):
        raise ValueError("expected a duration such as 2s or 500ms")
    if isinstance(value, int | float):
        if value < 0:
            raise ValueError("a duration cannot be negative")
        return Duration(float(value))
    if not isinstance(value, str):
        raise ValueError("expected a duration such as 2s or 500ms")
    match = _DURATION.match(value)
    if match is None:
        raise ValueError(f"invalid duration {value!r}: expected e.g. 2s, 500ms, 2s±20%")
    seconds = float(match["amount"]) * _UNIT_SECONDS[match["unit"]]
    variance = float(match["variance"]) / 100 if match["variance"] else 0.0
    if variance > 1:
        raise ValueError(f"invalid duration {value!r}: variance above 100%")
    return Duration(seconds, variance)


# --- execution --------------------------------------------------------------------------


class StepError(Exception):
    """A step failed in an expected way; the interpreter applies ``on_error``."""

    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.info = ErrorInfo(code=code.value, message=message, details=details)


class StepContext(Protocol):
    """What an executor may do. Implemented by the interpreter."""

    @property
    def step_id(self) -> str: ...

    @property
    def dry_run(self) -> bool:
        """True in dry-run mode: executors must not send any input."""
        ...

    @property
    def rng(self) -> random.Random:
        """Seeded per step and attempt; the only source of randomness executors may use."""
        ...

    @property
    def variables(self) -> Mapping[str, Any]: ...

    def set_variable(self, name: str, value: Any) -> None:
        """Set a variable; the interpreter emits ``variable_changed``."""
        ...

    def evaluate(self, expression: str) -> Any:
        """Evaluate an expression over the current variables (raises StepError)."""
        ...

    def log(self, level: LogLevel, message: str) -> None: ...

    def wait(self, seconds: float) -> None:
        """Sleep, interruptibly: honours ``stop`` immediately and ``pause`` by resuming
        with the remaining time."""
        ...

    def request_pause(self, message: str | None = None) -> None:
        """Pause the run after this step, as a breakpoint written in the script."""
        ...


Executor = Callable[[StepContext, dict[str, Any]], dict[str, Any] | None]
"""Runs an action step with its interpolated parameters; returns ``step_finished.result``."""


@dataclass(frozen=True, slots=True)
class StepType:
    """A registered step type: its spec and, for action steps, its executor."""

    spec: StepSpec
    executor: Executor | None = field(default=None)
