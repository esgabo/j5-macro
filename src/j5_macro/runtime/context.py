"""The :class:`~j5_macro.steps.spec.StepContext` given to executors and conditions."""

from __future__ import annotations

import random
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from j5_macro.input.api import StepInput
from j5_macro.runtime.resolve import evaluated
from j5_macro.runtime.seeding import derive_seed
from j5_macro.steps.spec import LogLevel
from j5_macro.vision.api import StepVision


class ContextHost(Protocol):
    """What a context needs from the interpreter."""

    @property
    def variables(self) -> Mapping[str, Any]: ...

    def assign(self, name: str, value: Any, step_id: str | None) -> None: ...

    def log(self, level: LogLevel, message: str, step_id: str | None) -> None: ...

    def wait(self, seconds: float, depth: int) -> None: ...

    def check_stop(self) -> None: ...

    def step_input(self, step_id: str, rng: random.Random,
                   *overrides: Mapping[str, Any] | None) -> StepInput: ...

    def step_vision(self, context: RunContext) -> StepVision: ...


class RunContext:
    """One attempt of one step."""

    def __init__(self, host: ContextHost, *, step_id: str, iterations: list[int] | None,
                 attempt: int, depth: int, dry_run: bool, seed: int,
                 input_overrides: Mapping[str, Any] | None = None,
                 timeout: float | None = None, script_dir: Path | None = None,
                 allow_exec: bool = False) -> None:
        self._host = host
        self.timeout = timeout
        """The step's common ``timeout`` field in seconds (None when not set)."""
        self._script_dir = script_dir
        self._allow_exec = allow_exec
        self._input_overrides = input_overrides
        self._step_id = step_id
        self._iterations = iterations
        self._attempt = attempt
        self._depth = depth
        self._dry_run = dry_run
        self._seed = seed
        self._rng: random.Random | None = None
        self.pause_requested = False
        self.pause_message: str | None = None

    @property
    def step_id(self) -> str:
        return self._step_id

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def script_dir(self) -> Path:
        return self._script_dir if self._script_dir is not None else Path.cwd()

    @property
    def allow_exec(self) -> bool:
        return self._allow_exec

    @property
    def rng(self) -> random.Random:
        if self._rng is None:
            self._rng = random.Random(
                derive_seed(self._seed, self._step_id, self._iterations, self._attempt))
        return self._rng

    @property
    def variables(self) -> Mapping[str, Any]:
        return MappingProxyType(dict(self._host.variables))

    def set_variable(self, name: str, value: Any) -> None:
        self._host.assign(name, value, self._step_id)

    def evaluate(self, expression: str) -> Any:
        return evaluated(expression, self._host.variables)

    def log(self, level: LogLevel, message: str) -> None:
        self._host.log(level, message, self._step_id)

    def wait(self, seconds: float) -> None:
        self._host.wait(seconds, self._depth)

    def request_pause(self, message: str | None = None) -> None:
        self.pause_requested = True
        self.pause_message = message

    def check_stop(self) -> None:
        self._host.check_stop()

    @property
    def vision(self) -> StepVision:
        return self._host.step_vision(self)

    @property
    def input(self) -> StepInput:
        return self.input_for(None)

    def input_for(self, overrides: Mapping[str, Any] | None) -> StepInput:
        return self._host.step_input(self._step_id, self.rng, self._input_overrides,
                                     overrides)
