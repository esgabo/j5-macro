"""Input layer interfaces (docs/contract.md, "Mouse and keyboard masters").

Three layers, each depending only on the one below:

- **Backend** (``j5_macro.backends``): platform primitives.
- **Masters** (``j5_macro.input``): one :class:`InputSession` per run plans every action
  with a strategy (a pure, seeded function producing a :class:`MousePlan` or
  :class:`KeyPlan`), executes the plan against the backend on a monotonic clock, tracks
  held keys and buttons, and reports ``input_planned`` / ``input_executed`` /
  ``input_state`` / ``keys_released`` events.
- **Steps** see a :class:`StepInput` through ``StepContext.input``: the session bound to
  the step's random generator and effective input profile.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from j5_macro.backends.base import Button, Rect

Strategy = Literal["direct", "linear", "human"]
MousePath = Literal["windmouse", "bezier"]
InputEventSink = Callable[[str, dict[str, Any]], None]
"""Publishes ``input_planned``, ``input_executed``, ``input_state``, ``keys_released``,
``user_interference`` and ``log`` events (payloads as in ``protocol.payloads.events``)."""


# --- profiles ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class MouseProfile:
    strategy: Strategy = "human"
    speed: float = 1.0
    """Speed multiplier; 1.0 is a comfortable human pace."""
    path: MousePath = "windmouse"
    """Path shape for the ``human`` strategy."""
    overshoot: float = 0.1
    jitter: float = 1.0
    interference: Literal["pause", "stop", "off"] = "pause"
    tolerance: int = 20


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyboardProfile:
    strategy: Strategy = "human"
    wpm: float = 60.0
    variance: float = 0.3
    typos: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class InputProfile:
    mouse: MouseProfile = field(default_factory=MouseProfile)
    keyboard: KeyboardProfile = field(default_factory=KeyboardProfile)

    def merged(self, overrides: Mapping[str, Any] | None) -> InputProfile:
        """A copy with ``{mouse: {...}, keyboard: {...}}`` overrides applied
        (script ``input`` → run ``input_profile`` → step ``input``). Raises ValueError
        for unknown keys or bad values."""
        if not overrides:
            return self
        unknown = set(overrides) - {"mouse", "keyboard"}
        if unknown:
            raise ValueError(_unknown("keys", unknown))
        return InputProfile(
            mouse=_override(self.mouse, overrides.get("mouse")),
            keyboard=_override(self.keyboard, overrides.get("keyboard")),
        )


def _override(profile: Any, values: Any) -> Any:
    if values is None:
        return profile
    if not isinstance(values, Mapping):
        raise ValueError("input profile sections must be mappings")
    fields = {f for f in profile.__dataclass_fields__}
    unknown = set(values) - fields
    if unknown:
        raise ValueError(_unknown("fields", unknown))
    current = {name: getattr(profile, name) for name in fields}
    current.update(values)
    result = type(profile)(**current)
    _validate(result)
    return result


SEED_HINT = ("the seed is not an input profile setting: it belongs to the whole run "
             "(the run command's seed, j5 run --seed), and each step attempt derives its "
             "own sub-seed from it")


def _unknown(kind: str, names: set[str]) -> str:
    message = f"unknown input profile {kind}: {', '.join(sorted(names))}"
    return f"{message}; {SEED_HINT}" if "seed" in names else message


def _validate(profile: MouseProfile | KeyboardProfile) -> None:
    if profile.strategy not in ("direct", "linear", "human"):
        raise ValueError(f"unknown strategy {profile.strategy!r}")
    if isinstance(profile, MouseProfile):
        if profile.speed <= 0:
            raise ValueError("mouse speed must be positive")
        if profile.path not in ("windmouse", "bezier"):
            raise ValueError(f"unknown mouse path {profile.path!r}")
        if not 0 <= profile.overshoot <= 1:
            raise ValueError("mouse overshoot must be between 0 and 1")
        if profile.interference not in ("pause", "stop", "off"):
            raise ValueError(f"unknown interference action {profile.interference!r}")
        if isinstance(profile.tolerance, bool) or not isinstance(profile.tolerance, int | float) \
                or profile.tolerance < 0:
            raise ValueError("mouse tolerance must be a number of pixels, 0 or more")
    elif profile.wpm <= 0:
        raise ValueError("keyboard wpm must be positive")


# --- plans ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimedPoint:
    """Where the cursor should be ``t_ms`` milliseconds after the movement starts."""

    t_ms: float
    x: int
    y: int


@dataclass(frozen=True, slots=True, kw_only=True)
class MousePlan:
    strategy: Strategy
    seed: int
    start: tuple[int, int]
    end: tuple[int, int]
    points: tuple[TimedPoint, ...]
    """Non-empty, ``t_ms`` non-decreasing, last point at ``end``."""

    @property
    def duration_ms(self) -> float:
        return self.points[-1].t_ms if self.points else 0.0


@dataclass(frozen=True, slots=True)
class KeyAction:
    """Press (``down``) or release a physical ``key`` or a layout-independent ``char``."""

    t_ms: float
    kind: Literal["key", "char"]
    value: str
    down: bool


@dataclass(frozen=True, slots=True)
class ButtonAction:
    """Press (``down``) or release the click's button ``t_ms`` after the click starts."""

    t_ms: float
    down: bool


@dataclass(frozen=True, slots=True)
class ScrollAction:
    """Scroll ``dx``/``dy`` notches ``t_ms`` after the scroll starts."""

    t_ms: float
    dx: int
    dy: int


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyPlan:
    strategy: Strategy
    seed: int
    actions: tuple[KeyAction, ...]
    chars: int
    wpm: float | None = None

    @property
    def duration_ms(self) -> float:
        return self.actions[-1].t_ms if self.actions else 0.0


# --- runtime hooks ----------------------------------------------------------------------


class EmergencyStop(Exception):
    """Raised by a master when the user triggers the failsafe mid-action (cursor pushed
    into a screen corner). The master has already released what it held; the
    interpreter stops the run with reason ``user``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class UserInterference(Exception):
    """Raised by the mouse master when the real pointer strays more than the profile's
    ``tolerance`` from where the master put it during a movement (the user moved the
    mouse). The master has already released what the action held and emitted
    ``user_interference``; the interpreter pauses the run (``action == "pause"``) or
    stops it with reason ``interference`` (``"stop"``)."""

    def __init__(self, expected: tuple[int, int], actual: tuple[int, int],
                 action: Literal["pause", "stop"]) -> None:
        super().__init__(f"pointer moved by the user: expected {expected}, got {actual}")
        self.expected = expected
        self.actual = actual
        self.action = action


class AbortSignal(Protocol):
    """How masters notice ``stop`` without ever pausing mid-action.

    Implemented by the interpreter. Both methods raise the interpreter's own stop
    exception, which masters must let propagate (after releasing what they hold).
    """

    def check(self) -> None:
        """Raise if the run must stop now."""
        ...

    def sleep(self, seconds: float) -> None:
        """Sleep, returning early (by raising) only when the run must stop."""
        ...


class InputSession(Protocol):
    """One per run, shared by every step. Thread-safe for ``release_all``/``held``."""

    def move(self, x: int, y: int, *, profile: MouseProfile, rng: random.Random,
             target: Rect | None = None) -> None:
        """Move the cursor to (x, y). ``target`` is the area being aimed at (e.g. an
        image match); human strategies use it for the landing point and duration."""
        ...

    def click(self, x: int, y: int, *, button: Button, clicks: int, profile: MouseProfile,
              rng: random.Random, target: Rect | None = None) -> None: ...

    def drag(self, start: tuple[int, int], end: tuple[int, int], *, button: Button,
             profile: MouseProfile, rng: random.Random) -> None: ...

    def scroll(self, dx: int, dy: int, *, profile: MouseProfile, rng: random.Random) -> None: ...

    def type_text(self, text: str, *, profile: KeyboardProfile, rng: random.Random) -> None: ...

    def press(self, combo: str, *, hold: float | None, profile: KeyboardProfile,
              rng: random.Random) -> None:
        """Press a combination like ``ctrl+shift+s``: modifiers down in order, the key,
        then release in reverse order. ``hold`` keeps everything down that many seconds."""
        ...

    def find_window(self, title: str) -> Rect | None: ...

    @property
    def held(self) -> list[str]:
        """Held keys, characters and buttons (``button:left``), in press order."""
        ...

    def release_all(self, reason: str) -> list[str]:
        """Release everything held (emits ``keys_released`` if anything was held)."""
        ...


SessionFactory = Callable[[Any, InputEventSink, AbortSignal], InputSession]
"""``(backend, emit, abort) -> InputSession``; implemented by
``j5_macro.input.session.create_session``."""


class StepInput(Protocol):
    """What an input step executor sees as ``ctx.input``: the run's session bound to the
    step's seeded ``rng`` and effective profile."""

    @property
    def profile(self) -> InputProfile: ...

    def move(self, x: int, y: int, *, target: Rect | None = None) -> None: ...

    def click(self, x: int, y: int, *, button: Button = Button.LEFT, clicks: int = 1,
              target: Rect | None = None) -> None: ...

    def drag(self, start: tuple[int, int], end: tuple[int, int], *,
             button: Button = Button.LEFT) -> None: ...

    def scroll(self, dx: int, dy: int) -> None: ...

    def type_text(self, text: str) -> None: ...

    def press(self, combo: str, *, hold: float | None = None) -> None: ...

    def find_window(self, title: str) -> Rect | None: ...
