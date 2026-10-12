"""A fake input session and helpers to run scripts with it (no display needed)."""

from __future__ import annotations

import random
import threading
from typing import Any

from runtime_helpers import REGISTRY, TIMEOUT, Recorder, ScaledClock, load

from j5_macro.backends.base import Button, Rect, UnknownKey
from j5_macro.backends.fake import FakeBackend
from j5_macro.input.api import (
    AbortSignal,
    EmergencyStop,
    InputEventSink,
    KeyboardProfile,
    MouseProfile,
)
from j5_macro.runtime import RunOptions, RunOutcome
from j5_macro.runtime.interpreter import create_run
from j5_macro.script.model import Script


class FakeSession:
    """Records every call with the profile it got. Behaviour hooks:

    - ``press("bad+key")`` raises UnknownKey;
    - ``press(..., hold=s)`` holds the keys and sleeps ``s`` through the abort signal;
    - a click at ``(0, 0)`` raises EmergencyStop (the corner failsafe);
    - ``type_text`` holds ``shift`` (left held on purpose) and emits ``input_state``.
    """

    def __init__(self, backend: Any, emit: InputEventSink, abort: AbortSignal) -> None:
        self.backend = backend
        self.emit = emit
        self.abort = abort
        self.calls: list[tuple[str, Any, Any]] = []
        self.rngs: list[random.Random] = []
        self._held: list[str] = []
        self._lock = threading.Lock()

    def _hold(self, *keys: str) -> None:
        with self._lock:
            self._held.extend(keys)
            held = list(self._held)
        self.emit("input_state", {"held": held})

    def move(self, x: int, y: int, *, profile: MouseProfile, rng: random.Random,
             target: Rect | None = None) -> None:
        self.calls.append(("move", (x, y), profile))
        self.rngs.append(rng)

    def click(self, x: int, y: int, *, button: Button, clicks: int, profile: MouseProfile,
              rng: random.Random, target: Rect | None = None) -> None:
        self.calls.append(("click", (x, y, button, clicks), profile))
        self.rngs.append(rng)
        if (x, y) == (0, 0):
            raise EmergencyStop("cursor pushed into the top-left corner")

    def drag(self, start: tuple[int, int], end: tuple[int, int], *, button: Button,
             profile: MouseProfile, rng: random.Random) -> None:
        self.calls.append(("drag", (start, end, button), profile))

    def scroll(self, dx: int, dy: int, *, profile: MouseProfile, rng: random.Random) -> None:
        self.calls.append(("scroll", (dx, dy), profile))

    def type_text(self, text: str, *, profile: KeyboardProfile, rng: random.Random) -> None:
        self.calls.append(("type_text", text, profile))
        self._hold("shift")

    def press(self, combo: str, *, hold: float | None, profile: KeyboardProfile,
              rng: random.Random) -> None:
        self.calls.append(("press", (combo, hold), profile))
        if combo == "bad+key":
            raise UnknownKey("unknown key 'bad'")
        if hold:
            self._hold(*combo.split("+"))
            try:
                self.abort.sleep(hold)
            finally:
                self.release_all("stop")
            self.abort.check()

    def find_window(self, title: str) -> Rect | None:
        self.calls.append(("find_window", title, None))
        result: Rect | None = self.backend.find_window(title)
        return result

    @property
    def held(self) -> list[str]:
        with self._lock:
            return list(self._held)

    def release_all(self, reason: str) -> list[str]:
        with self._lock:
            keys, self._held = self._held, []
        self.calls.append(("release_all", reason, None))
        if keys:
            self.emit("input_state", {"held": []})
            self.emit("keys_released", {"keys": keys, "reason": reason})
        return keys

    def named(self, name: str) -> list[tuple[str, Any, Any]]:
        return [call for call in self.calls if call[0] == name]


class SessionFactory:
    """A ``SessionFactory`` that remembers the sessions it made."""

    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []
        self.session_type: type[FakeSession] = FakeSession

    def __call__(self, backend: Any, emit: InputEventSink, abort: AbortSignal) -> FakeSession:
        session = self.session_type(backend, emit, abort)
        self.sessions.append(session)
        return session

    @property
    def session(self) -> FakeSession:
        assert len(self.sessions) == 1, f"{len(self.sessions)} sessions were created"
        return self.sessions[0]


def backend(**windows: Rect) -> FakeBackend:
    return FakeBackend(windows=dict(windows))


class InputRun:
    """A run with a fake session factory, on a worker thread."""

    def __init__(self, script: Script | str, *, factory: SessionFactory | None = None,
                 scale: float = 100.0, **options: Any) -> None:
        if isinstance(script, str):
            script = load(script)
        options.setdefault("backend", backend())
        self.factory = factory if factory is not None else SessionFactory()
        self.recorder = Recorder()
        self.controller = create_run(script, REGISTRY, self.recorder, RunOptions(**options),
                                     clock=ScaledClock(scale), session_factory=self.factory)
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


def run_input(script: Script | str, **options: Any) -> tuple[RunOutcome, InputRun]:
    """Run to the end with a fake session; returns the outcome and the run."""
    run = InputRun(script, **options)
    return run.join(), run
