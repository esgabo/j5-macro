"""Test doubles for the input session: a virtual clock, an abort signal and an event sink."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from j5_macro.backends.fake import FakeBackend
from j5_macro.input.session import MasterSession
from j5_macro.protocol.payloads.events import EVENT_PAYLOADS


class Stopped(BaseException):
    """Stands in for the interpreter's stop exception (a BaseException there too)."""


class VirtualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@dataclass
class FakeAbort:
    """Sleeping advances the virtual clock. With ``stop_at`` set, the first check or sleep
    reaching that time raises :class:`Stopped`. ``on_wake`` runs after every completed
    sleep (tests use it to act "while" the action is running)."""

    clock: VirtualClock
    stop_at: float | None = None
    on_wake: Callable[[float], None] | None = None
    sleeps: list[float] = field(default_factory=list)
    checks: int = 0

    def check(self) -> None:
        self.checks += 1
        if self.stop_at is not None and self.clock.now >= self.stop_at:
            raise Stopped()

    def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.sleeps.append(seconds)
        target = self.clock.now + seconds
        if self.stop_at is not None and target >= self.stop_at:
            self.clock.now = max(self.clock.now, self.stop_at)
            raise Stopped()
        self.clock.now = target
        if self.on_wake is not None:
            self.on_wake(self.clock.now)


class Recorder:
    """An event sink that validates every payload against the protocol classes."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, name: str, payload: dict[str, Any]) -> None:
        EVENT_PAYLOADS[name].from_json(payload)
        self.events.append((name, payload))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def of(self, name: str) -> list[dict[str, Any]]:
        return [payload for n, payload in self.events if n == name]


@dataclass
class Harness:
    session: MasterSession
    backend: FakeBackend
    clock: VirtualClock
    abort: FakeAbort
    events: Recorder

    def calls(self, *names: str) -> list[tuple[Any, ...]]:
        """Recorded backend calls as ``(name, *args)``, optionally filtered by name."""
        return [(c.name, *c.args) for c in self.backend.calls
                if not names or c.name in names]


def make(backend: FakeBackend | None = None, *, stop_at: float | None = None,
         on_wake: Callable[[float], None] | None = None) -> Harness:
    clock = VirtualClock()
    abort = FakeAbort(clock, stop_at=stop_at, on_wake=on_wake)
    events = Recorder()
    backend = backend or FakeBackend()
    session = MasterSession(backend, events, abort, clock=clock)
    return Harness(session, backend, clock, abort, events)
