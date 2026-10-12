"""The run's input session: plans every action, executes it, tracks held input.

See docs/contract.md, "Mouse and keyboard masters". One :class:`MasterSession` exists per
run. Each action is planned with a pure seeded planner (:mod:`j5_macro.input.planning`),
reported with ``input_planned``, replayed against a monotonic clock and closed with
``input_executed``. Waiting goes through the run's :class:`AbortSignal`, so a ``stop``
interrupts an action between two primitives (never pausing it): whatever the action
pressed is released and the signal's exception propagates.

Held keys, characters and buttons are tracked in press order. Physical keys appear by
name (``ctrl``), characters as ``char:<c>`` (``char:ñ``) and buttons as
``button:<name>`` (``button:left``). Every change emits ``input_state``.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from typing import Any

from j5_macro.backends.base import Backend, Button, Rect
from j5_macro.input.api import (
    AbortSignal,
    EmergencyStop,
    InputEventSink,
    InputSession,
    KeyAction,
    KeyboardProfile,
    KeyPlan,
    MousePlan,
    MouseProfile,
    Strategy,
)
from j5_macro.input.planning import (
    DRAG_SETTLE_MS,
    HUMAN_FALLBACK_WARNING,
    plan_click,
    plan_combo,
    plan_move,
    plan_scroll,
    plan_typing,
)
from j5_macro.protocol.payloads.events import (
    InputExecutedEvent,
    InputPlannedEvent,
    InputStateEvent,
    KeysReleasedEvent,
    LogEvent,
)

CORNER_MARGIN_PX = 2
"""The failsafe triggers when the real pointer is this close to a corner of the desktop."""
FAILSAFE_MESSAGE = "cursor moved into a screen corner"
FAILSAFE_REASON = "failsafe"
"""``keys_released`` reason when the corner failsafe fires."""
ABORTED_REASON = "aborted"
"""``keys_released`` reason when an action is interrupted (stop, backend error)."""

Clock = Callable[[], float]


def create_session(backend: Any, emit: InputEventSink, abort: AbortSignal, *,
                   clock: Clock = time.monotonic) -> InputSession:
    """The :data:`~j5_macro.input.api.SessionFactory`: a new session for one run."""
    return MasterSession(backend, emit, abort, clock=clock)


def in_corner(bounds: Rect, x: int, y: int, margin: int = CORNER_MARGIN_PX) -> bool:
    """Whether (x, y) is within ``margin`` pixels of a corner of ``bounds``."""
    xs = (bounds.x, bounds.x + bounds.width - 1)
    ys = (bounds.y, bounds.y + bounds.height - 1)
    return (any(abs(x - cx) <= margin for cx in xs)
            and any(abs(y - cy) <= margin for cy in ys))


class MasterSession:
    """The mouse and keyboard masters of one run (implements :class:`InputSession`)."""

    def __init__(self, backend: Backend, emit: InputEventSink, abort: AbortSignal, *,
                 clock: Clock = time.monotonic) -> None:
        self._backend = backend
        self._emit = emit
        self._abort = abort
        self._clock = clock
        self._lock = threading.RLock()
        self._held: list[tuple[str, Callable[[], None]]] = []
        self._position: tuple[int, int] | None = None
        """Last position the session sent the pointer to (or read from the backend)."""
        self._warned_human = False

    # --- InputSession: mouse ---------------------------------------------------------

    def move(self, x: int, y: int, *, profile: MouseProfile, rng: random.Random,
             target: Rect | None = None) -> None:
        self._warn_if_human(profile.strategy)
        self._action(lambda pressed: self._move_to(x, y, profile, rng, force=True))

    def click(self, x: int, y: int, *, button: Button, clicks: int, profile: MouseProfile,
              rng: random.Random, target: Rect | None = None) -> None:
        self._warn_if_human(profile.strategy)

        def body(pressed: list[str]) -> None:
            self._move_to(x, y, profile, rng)
            plan = plan_click(button, clicks, profile, rng)
            start = self._clock()
            for action in plan.actions:
                self._wait_until(start, action.t_ms)
                if action.down:
                    self._press_button(button, pressed)
                else:
                    self._release_button(button)

        self._action(body)

    def drag(self, start: tuple[int, int], end: tuple[int, int], *, button: Button,
             profile: MouseProfile, rng: random.Random) -> None:
        self._warn_if_human(profile.strategy)
        settle = 0.0 if profile.strategy == "direct" else DRAG_SETTLE_MS / 1000.0

        def body(pressed: list[str]) -> None:
            self._move_to(*start, profile, rng)
            self._press_button(button, pressed)
            self._sleep(settle)
            self._move_to(*end, profile, rng, force=True)
            self._sleep(settle)
            self._release_button(button)

        self._action(body)

    def scroll(self, dx: int, dy: int, *, profile: MouseProfile, rng: random.Random) -> None:
        self._warn_if_human(profile.strategy)
        plan = plan_scroll(dx, dy, profile, rng)

        def body(pressed: list[str]) -> None:
            self._emit_planned(InputPlannedEvent(
                device="mouse", strategy=plan.strategy, seed=plan.seed,
                duration_ms=round(plan.duration_ms)))
            start = self._clock()
            aborted = True
            try:
                for action in plan.actions:
                    self._wait_until(start, action.t_ms)
                    self._backend.scroll(action.dx, action.dy)
                aborted = False
            finally:
                self._emit_executed("mouse", start, aborted)

        self._action(body)

    # --- InputSession: keyboard ------------------------------------------------------

    def type_text(self, text: str, *, profile: KeyboardProfile, rng: random.Random) -> None:
        self._warn_if_human(profile.strategy)
        plan = plan_typing(text, profile, rng)
        self._action(lambda pressed: self._execute_keys(plan, pressed))

    def press(self, combo: str, *, hold: float | None, profile: KeyboardProfile,
              rng: random.Random) -> None:
        plan = plan_combo(combo, hold, profile, rng)
        self._warn_if_human(profile.strategy)
        self._action(lambda pressed: self._execute_keys(plan, pressed))

    # --- InputSession: other ---------------------------------------------------------

    def find_window(self, title: str) -> Rect | None:
        if not self._backend.capabilities.windows:
            return None
        return self._backend.find_window(title)

    @property
    def held(self) -> list[str]:
        with self._lock:
            return [token for token, _ in self._held]

    def release_all(self, reason: str) -> list[str]:
        """Release everything held, most recent first. Returns the released tokens in
        release order. Safe to call from any thread, also while an action runs."""
        with self._lock:
            return self._release_tokens([token for token, _ in self._held], reason)

    # --- actions ---------------------------------------------------------------------

    def _action(self, body: Callable[[list[str]], None]) -> None:
        """Run one action; on any exception release what it pressed, then re-raise."""
        pressed: list[str] = []
        try:
            body(pressed)
        except BaseException as exc:
            reason = FAILSAFE_REASON if isinstance(exc, EmergencyStop) else ABORTED_REASON
            with self._lock:
                self._release_tokens(pressed, reason)
            raise

    def _warn_if_human(self, strategy: Strategy) -> None:
        if strategy != "human":
            return
        with self._lock:
            if self._warned_human:
                return
            self._warned_human = True
        self._emit("log", LogEvent(level="warning", message=HUMAN_FALLBACK_WARNING).to_json())

    def _wait_until(self, start: float, t_ms: float) -> None:
        delay = start + t_ms / 1000.0 - self._clock()
        self._sleep(delay)

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._abort.sleep(seconds)
        else:
            self._abort.check()

    def _emit_planned(self, event: InputPlannedEvent) -> None:
        self._emit("input_planned", event.to_json())

    def _emit_executed(self, device: Any, start: float, aborted: bool) -> None:
        elapsed = max(0.0, self._clock() - start)
        self._emit("input_executed", InputExecutedEvent(
            device=device, actual_duration_ms=round(elapsed * 1000.0),
            aborted=aborted).to_json())

    # --- mouse execution -------------------------------------------------------------

    def _current_position(self) -> tuple[int, int] | None:
        """The pointer position movements start from."""
        backend = self._backend
        if backend.capabilities.pointer_position:
            real = backend.pointer_position()
            if real is not None:
                self._position = (int(real[0]), int(real[1]))
                return self._position
        if self._position is None and not backend.capabilities.move_abs:
            # Relative-only and blind: push the pointer to the top-left corner of the
            # desktop once so later deltas start from a known place.
            bounds = backend.screen_bounds()
            backend.move_rel(-bounds.width, -bounds.height)
            self._position = (bounds.x, bounds.y)
        return self._position

    def _move_to(self, x: int, y: int, profile: MouseProfile, rng: random.Random, *,
                 force: bool = False) -> None:
        """Plan and execute a movement to (x, y). Without ``force`` nothing is emitted or
        sent when the pointer is already there (the plan is still drawn, so the caller's
        generator advances the same way)."""
        current = self._current_position()
        start = current if current is not None else (x, y)
        plan = plan_move(start, (x, y), profile, rng)
        if current == (x, y) and not force:
            return
        self._execute_move(plan)

    def _execute_move(self, plan: MousePlan) -> None:
        self._emit_planned(InputPlannedEvent(
            device="mouse", strategy=plan.strategy, seed=plan.seed, start=list(plan.start),
            to=list(plan.end), duration_ms=round(plan.duration_ms)))
        backend = self._backend
        bounds = backend.screen_bounds()
        failsafe = (backend.capabilities.pointer_position
                    and not in_corner(bounds, *plan.end))
        start = self._clock()
        aborted = True
        try:
            for i, point in enumerate(plan.points):
                self._wait_until(start, point.t_ms)
                if i and failsafe:
                    self._check_corner(bounds)
                self._send_pointer(point.x, point.y)
            aborted = False
        finally:
            self._emit_executed("mouse", start, aborted)

    def _send_pointer(self, x: int, y: int) -> None:
        backend = self._backend
        if backend.capabilities.move_abs:
            backend.move_abs(x, y)
        else:
            cx, cy = self._position if self._position is not None else (x, y)
            if (x, y) != (cx, cy):
                backend.move_rel(x - cx, y - cy)
        self._position = (x, y)

    def _check_corner(self, bounds: Rect) -> None:
        real = self._backend.pointer_position()
        if real is None or not in_corner(bounds, *real):
            return
        if self._position is not None and in_corner(bounds, *self._position):
            return  # we put it there ourselves
        self.release_all(FAILSAFE_REASON)
        raise EmergencyStop(FAILSAFE_MESSAGE)

    def _press_button(self, button: Button, pressed: list[str]) -> None:
        self._press(f"button:{button.value}", lambda: self._backend.button_down(button),
                    lambda: self._backend.button_up(button), pressed)

    def _release_button(self, button: Button) -> None:
        self._release(f"button:{button.value}")

    # --- keyboard execution ----------------------------------------------------------

    def _execute_keys(self, plan: KeyPlan, pressed: list[str]) -> None:
        self._emit_planned(InputPlannedEvent(
            device="keyboard", strategy=plan.strategy, seed=plan.seed, chars=plan.chars,
            wpm=plan.wpm))
        start = self._clock()
        aborted = True
        try:
            for action in plan.actions:
                self._wait_until(start, action.t_ms)
                self._key_action(action, pressed)
            aborted = False
        finally:
            self._emit_executed("keyboard", start, aborted)

    def _key_action(self, action: KeyAction, pressed: list[str]) -> None:
        backend = self._backend
        value = action.value
        if action.kind == "key":
            token = value
            if action.down:
                self._press(token, lambda: backend.key_down(value),
                            lambda: backend.key_up(value), pressed)
            else:
                self._release(token)
        else:
            token = f"char:{value}"
            if action.down:
                self._press(token, lambda: backend.char_down(value),
                            lambda: backend.char_up(value), pressed)
            else:
                self._release(token)

    # --- held state ------------------------------------------------------------------

    def _press(self, token: str, down: Callable[[], None], up: Callable[[], None],
               pressed: list[str]) -> None:
        with self._lock:
            down()
            if any(held == token for held, _ in self._held):
                return
            self._held.append((token, up))
            pressed.append(token)
            self._emit_state()

    def _release(self, token: str) -> None:
        """Release ``token`` if it is still held (``release_all`` may have done it)."""
        with self._lock:
            for i, (held, up) in enumerate(self._held):
                if held == token:
                    up()
                    del self._held[i]
                    self._emit_state()
                    return

    def _release_tokens(self, tokens: list[str], reason: str) -> list[str]:
        """Release the still-held ``tokens``, last pressed first; emits ``keys_released``
        and ``input_state`` if anything was released. Caller holds the lock."""
        wanted = set(tokens)
        targets = [(token, up) for token, up in self._held if token in wanted]
        if not targets:
            return []
        released: list[str] = []
        for token, up in reversed(targets):
            try:
                up()
            except Exception as exc:
                self._emit("log", LogEvent(
                    level="warning", message=f"could not release {token}: {exc}").to_json())
            released.append(token)
        self._held = [(token, up) for token, up in self._held if token not in wanted]
        self._emit("keys_released", KeysReleasedEvent(keys=released, reason=reason).to_json())
        self._emit_state()
        return released

    def _emit_state(self) -> None:
        self._emit("input_state",
                   InputStateEvent(held=[token for token, _ in self._held]).to_json())
