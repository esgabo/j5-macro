"""The :class:`~j5_macro.steps.spec.StepContext` given to executors and conditions."""

from __future__ import annotations

import random
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Protocol

from j5_macro.input.api import StepInput
from j5_macro.protocol.errors import ErrorCode
from j5_macro.runtime.resolve import evaluated
from j5_macro.runtime.seeding import derive_seed
from j5_macro.steps.spec import LogLevel, StepError
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


class RunContext:
    """One attempt of one step."""

    def __init__(self, host: ContextHost, *, step_id: str, iterations: list[int] | None,
                 attempt: int, depth: int, dry_run: bool, seed: int,
                 input_overrides: Mapping[str, Any] | None = None) -> None:
        self._host = host
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
        # Wired to the run's vision toolkit in milestone M3 (#22).
        raise StepError(ErrorCode.CAPABILITY_MISSING,
                        f"step '{self._step_id}' needs vision (milestone M3)")

    @property
    def input(self) -> StepInput:
        return self.input_for(None)

    def input_for(self, overrides: Mapping[str, Any] | None) -> StepInput:
        return self._host.step_input(self._step_id, self.rng, self._input_overrides,
                                     overrides)
