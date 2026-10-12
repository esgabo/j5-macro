"""Helpers for the interpreter tests: scripts from YAML, an event recorder that validates
every payload, a fast clock and a background runner."""

from __future__ import annotations

import json
import textwrap
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.payloads import parse_event_payload
from j5_macro.runtime import RunOptions, RunOutcome
from j5_macro.runtime.interpreter import create_run
from j5_macro.runtime.resolve import ConditionFn
from j5_macro.script.loader import load_script
from j5_macro.script.model import Script
from j5_macro.steps import (
    ParamSpec,
    StepContext,
    StepError,
    StepRegistry,
    StepSpec,
    StepType,
    default_registry,
)

TIMEOUT = 2.0
Event = tuple[str, dict[str, Any]]


def _rand(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    return {"value": ctx.rng.random()}


def _fail(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    raise StepError(ErrorCode(params["code"]), "failed on purpose", {"why": "test"})


def _boom(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("kaput")


def _flaky(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    """Fails until the variable ``tries`` reaches ``succeed_at``; counts its calls there."""
    tries = ctx.variables.get("tries", 0) + 1
    ctx.set_variable("tries", tries)
    if tries < params["succeed_at"]:
        raise StepError(ErrorCode.IMAGE_NOT_FOUND, f"not yet ({tries})")
    return {"tries": tries}


TEST_STEPS = (
    StepType(StepSpec(name="rand", doc="Random number from ctx.rng."), _rand),
    StepType(StepSpec(name="fail", doc="Always fails.", shorthand="code", params=(
        ParamSpec(name="code", type="string", default="image_not_found"),)), _fail),
    StepType(StepSpec(name="boom", doc="Raises a non-StepError exception."), _boom),
    StepType(StepSpec(name="flaky", doc="Fails a few times.", shorthand="succeed_at", params=(
        ParamSpec(name="succeed_at", type="integer", default=2),)), _flaky),
)


def registry() -> StepRegistry:
    reg = default_registry()
    for step_type in TEST_STEPS:
        reg.register(step_type)
    return reg


REGISTRY = registry()


def load(text: str) -> Script:
    result = load_script(textwrap.dedent(text), lookup=REGISTRY.spec,
                         check=REGISTRY.check_params)
    assert result.script is not None, result.errors
    return result.script


class ScaledClock:
    """A clock running ``scale`` times faster than real time."""

    def __init__(self, scale: float = 100.0) -> None:
        self.scale = scale
        self._origin = time.monotonic()

    def now(self) -> float:
        return (time.monotonic() - self._origin) * self.scale

    def wait(self, cond: threading.Condition, timeout: float | None) -> None:
        cond.wait(None if timeout is None else timeout / self.scale)


class Recorder:
    """The event sink: validates each payload with the typed classes and records it."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self._cond = threading.Condition()

    def __call__(self, type_: str, payload: dict[str, Any]) -> None:
        parsed = parse_event_payload(type_, payload)
        assert parsed is not None, f"unknown event {type_}"
        assert parsed.to_json() == payload
        json.dumps(payload)
        with self._cond:
            self.events.append((type_, payload))
            self._cond.notify_all()

    def of(self, type_: str, **match: Any) -> list[dict[str, Any]]:
        with self._cond:
            return [p for t, p in self.events if t == type_
                    and all(p.get(k) == v for k, v in match.items())]

    def types(self) -> list[str]:
        with self._cond:
            return [t for t, _ in self.events]

    def summary(self) -> list[str]:
        """Compact event list: ``type:step_id`` or ``state_changed:state/reason``."""
        out = []
        with self._cond:
            for t, p in self.events:
                if t == "state_changed":
                    out.append(f"{t}:{p['state']}/{p.get('reason')}")
                elif "step_id" in p:
                    out.append(f"{t}:{p['step_id']}")
                else:
                    out.append(t)
        return out

    def wait_for(self, predicate: Callable[[], bool], timeout: float = TIMEOUT) -> None:
        deadline = time.monotonic() + timeout
        with self._cond:
            while not predicate():
                left = deadline - time.monotonic()
                assert left > 0, f"timed out; events so far: {self.summary()}"
                self._cond.wait(left)

    def wait_count(self, type_: str, count: int = 1, **match: Any) -> None:
        self.wait_for(lambda: len(self.of(type_, **match)) >= count)

    def wait_paused(self, count: int = 1) -> None:
        self.wait_count("state_changed", count, state="paused")


def run(script: Script | str, *, conditions: Mapping[str, ConditionFn] | None = None,
        **options: Any) -> tuple[RunOutcome, Recorder]:
    """Run to completion on this thread."""
    if isinstance(script, str):
        script = load(script)
    recorder = Recorder()
    controller = create_run(script, REGISTRY, recorder, RunOptions(**options),
                            conditions=conditions, clock=ScaledClock())
    return controller.run(), recorder


class Background:
    """A run on a worker thread, as the engine does it."""

    def __init__(self, script: Script | str, *, scale: float = 100.0, **options: Any) -> None:
        if isinstance(script, str):
            script = load(script)
        self.recorder = Recorder()
        self.clock = ScaledClock(scale)
        self.controller = create_run(script, REGISTRY, self.recorder, RunOptions(**options),
                                     clock=self.clock)
        self.outcome: RunOutcome | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self.outcome = self.controller.run()

    def join(self, timeout: float = TIMEOUT) -> RunOutcome:
        self._thread.join(timeout)
        assert not self._thread.is_alive(), f"run did not end: {self.recorder.summary()}"
        assert self.outcome is not None
        return self.outcome
