"""Input planning: pure, seeded functions (docs/contract.md, "Plan, then execute").

Every planner takes a ``random.Random`` from the caller, draws one 63-bit seed from it and
plans with a fresh ``Random(seed)``. The plan reports that seed, so ``replan(seed)``
reproduces it exactly and the caller's generator advances by the same amount whatever the
strategy. Nothing here touches the system.

``direct`` and ``linear`` are planned here; ``human`` is delegated to
:mod:`j5_macro.input.human_mouse` and :mod:`j5_macro.input.human_keys`, which receive the
plan's own ``Random(seed)`` and never touch the caller's generator.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Literal

from j5_macro.backends.base import Button, Rect
from j5_macro.input import human_keys, human_mouse
from j5_macro.input.api import (
    ButtonAction,
    KeyAction,
    KeyboardProfile,
    KeyPlan,
    MousePlan,
    MouseProfile,
    ScrollAction,
    Strategy,
    TimedPoint,
)
from j5_macro.input.keys import parse_combo

# --- timing constants -------------------------------------------------------------------

LINEAR_SPEED_PX_S = 1200.0
"""Pointer speed of ``linear`` at ``speed: 1.0``, in pixels per second."""
LINEAR_MIN_MOVE_MS = 60.0
"""Shortest ``linear`` movement (any non-zero distance)."""
POINT_INTERVAL_MS = 8.0
"""Target spacing of planned points (~125 Hz); the actual spacing divides the duration
evenly."""
CHARS_PER_WORD = 5
"""Characters per word when converting ``wpm`` to a key interval."""
KEY_HOLD_FRACTION = 0.3
"""``linear`` typing holds each key this fraction of the key interval."""
COMBO_GAP_MS = 30.0
"""``linear`` delay between successive key events of a combination."""
CLICK_HOLD_MS = 40.0
"""``linear`` time a mouse button stays down per click."""
CLICK_GAP_MS = (50.0, 80.0)
"""``linear`` pause between the clicks of a multi-click (uniform, seeded)."""
SCROLL_NOTCH_MS = 50.0
"""``linear`` delay between scroll notches."""
DRAG_SETTLE_MS = 50.0
"""``linear`` pause after pressing and before releasing the button of a drag."""

def _derive(rng: random.Random) -> tuple[int, random.Random]:
    seed = rng.getrandbits(63)
    return seed, random.Random(seed)


# --- mouse ------------------------------------------------------------------------------


def plan_move(start: tuple[int, int], end: tuple[int, int], profile: MouseProfile,
              rng: random.Random, *, target: Rect | None = None,
              bounds: Rect | None = None) -> MousePlan:
    """Plan a pointer movement from ``start`` to ``end``.

    ``direct``: one point at t=0 on the target. ``linear``: a straight line at
    ``LINEAR_SPEED_PX_S * speed`` (at least ``LINEAR_MIN_MOVE_MS``), a point every
    ~``POINT_INTERVAL_MS``, the first one interval after the start and the last one
    exactly on ``end``. A zero-length movement is a single point at t=0.

    ``human``: see :func:`j5_macro.input.human_mouse.plan_points`. ``target`` is the area
    aimed at (an image match, a window): ``human`` lands somewhere inside it, so the
    plan's ``end`` may differ from the requested one; ``direct`` and ``linear`` ignore
    it. ``bounds`` is the virtual desktop: ``human`` keeps every point inside it.
    """
    seed, _ = _derive(rng)
    return replan_move(seed, start, end, profile, target=target, bounds=bounds)


def replan_move(seed: int, start: tuple[int, int], end: tuple[int, int],
                profile: MouseProfile, *, target: Rect | None = None,
                bounds: Rect | None = None) -> MousePlan:
    """The plan :func:`plan_move` produced with ``seed``."""
    strategy: Strategy = profile.strategy
    if profile.strategy == "human":
        landing, human_points = human_mouse.plan_points(
            start, end, profile, random.Random(seed), target=target, bounds=bounds)
        return MousePlan(strategy="human", seed=seed, start=start, end=landing,
                         points=human_points)
    sx, sy = start
    ex, ey = end
    distance = math.hypot(ex - sx, ey - sy)
    if strategy == "direct" or distance == 0:
        points: tuple[TimedPoint, ...] = (TimedPoint(0.0, ex, ey),)
    else:
        duration = max(LINEAR_MIN_MOVE_MS, distance / (LINEAR_SPEED_PX_S * profile.speed)
                       * 1000.0)
        count = max(1, math.ceil(duration / POINT_INTERVAL_MS))
        points = tuple(
            TimedPoint(duration * i / count,
                       round(sx + (ex - sx) * i / count),
                       round(sy + (ey - sy) * i / count))
            for i in range(1, count + 1))
    return MousePlan(strategy=strategy, seed=seed, start=start, end=end, points=points)


@dataclass(frozen=True, slots=True, kw_only=True)
class ClickPlan:
    """Button presses of a click, relative to its start (the pointer is already there)."""

    strategy: Strategy
    seed: int
    button: Button
    actions: tuple[ButtonAction, ...]

    @property
    def duration_ms(self) -> float:
        return self.actions[-1].t_ms if self.actions else 0.0


def plan_click(button: Button, clicks: int, profile: MouseProfile,
               rng: random.Random) -> ClickPlan:
    """``direct``: every down/up at t=0. ``linear``: each click held ``CLICK_HOLD_MS``,
    clicks separated by a seeded pause in ``CLICK_GAP_MS``."""
    if clicks < 1:
        raise ValueError("clicks must be at least 1")
    seed, r = _derive(rng)
    if profile.strategy == "human":
        return ClickPlan(strategy="human", seed=seed, button=button,
                         actions=human_mouse.plan_click_actions(clicks, profile, r))
    strategy: Strategy = profile.strategy
    actions: list[ButtonAction] = []
    t = 0.0
    for i in range(clicks):
        if i and strategy != "direct":
            t += r.uniform(*CLICK_GAP_MS)
        actions.append(ButtonAction(t, True))
        if strategy != "direct":
            t += CLICK_HOLD_MS
        actions.append(ButtonAction(t, False))
    return ClickPlan(strategy=strategy, seed=seed, button=button, actions=tuple(actions))


@dataclass(frozen=True, slots=True, kw_only=True)
class ScrollPlan:
    strategy: Strategy
    seed: int
    actions: tuple[ScrollAction, ...]

    @property
    def duration_ms(self) -> float:
        return self.actions[-1].t_ms if self.actions else 0.0


def plan_scroll(dx: int, dy: int, profile: MouseProfile, rng: random.Random) -> ScrollPlan:
    """``direct``: one ``scroll(dx, dy)``. ``linear``: one notch per axis at a time,
    ``SCROLL_NOTCH_MS`` apart (both axes advance together while both have notches left)."""
    seed, r = _derive(rng)
    if profile.strategy == "human":
        return ScrollPlan(strategy="human", seed=seed,
                          actions=human_mouse.plan_scroll_actions(dx, dy, profile, r))
    strategy: Strategy = profile.strategy
    if dx == 0 and dy == 0:
        actions: tuple[ScrollAction, ...] = ()
    elif strategy == "direct":
        actions = (ScrollAction(0.0, dx, dy),)
    else:
        sx = (dx > 0) - (dx < 0)
        sy = (dy > 0) - (dy < 0)
        actions = tuple(
            ScrollAction(i * SCROLL_NOTCH_MS, sx if i < abs(dx) else 0,
                         sy if i < abs(dy) else 0)
            for i in range(max(abs(dx), abs(dy))))
    return ScrollPlan(strategy=strategy, seed=seed, actions=actions)


# --- keyboard ---------------------------------------------------------------------------


def key_interval_ms(wpm: float) -> float:
    """Milliseconds between key presses at ``wpm`` words per minute."""
    return 60_000.0 / (wpm * CHARS_PER_WORD)


KeyKind = Literal["key", "char"]


TEXT_CONTROL_KEYS = {"\n": "enter", "\r": "enter", "\t": "tab", "\b": "backspace"}
"""The control characters ``type`` accepts and the key each one presses (``\\r\\n`` is one
``enter``). Every other control character is rejected (see :func:`find_control_character`)."""


def is_control_character(char: str) -> bool:
    """True for a C0 or C1 control character or DEL (Unicode category ``Cc``)."""
    code = ord(char)
    return code < 0x20 or 0x7F <= code <= 0x9F


def find_control_character(text: str) -> int | None:
    """Index of the first control character in ``text`` that typing cannot press, or None.

    The accepted ones are the keys of :data:`TEXT_CONTROL_KEYS`."""
    for index, char in enumerate(text):
        if is_control_character(char) and char not in TEXT_CONTROL_KEYS:
            return index
    return None


def text_units(text: str) -> list[tuple[KeyKind, str]]:
    """Split text into ``(kind, value)`` keystrokes: one ``char`` per code point, ``\\n``
    (and ``\\r\\n`` or a lone ``\\r``) as the ``enter`` key, ``\\t`` as ``tab`` and ``\\b`` as
    ``backspace``. Any other control character raises ``ValueError``."""
    bad = find_control_character(text)
    if bad is not None:
        raise ValueError(f"cannot type control character U+{ord(text[bad]):04X} "
                         f"at index {bad}")
    units: list[tuple[KeyKind, str]] = []
    i = 0
    while i < len(text):
        c = text[i]
        key = TEXT_CONTROL_KEYS.get(c)
        if key is not None:
            units.append(("key", key))
            if c == "\r" and text[i + 1:i + 2] == "\n":
                i += 1
        else:
            units.append(("char", c))
        i += 1
    return units


def plan_typing(text: str, profile: KeyboardProfile, rng: random.Random) -> KeyPlan:
    """``direct``: every keystroke down then up at t=0. ``linear``: keystroke ``i`` goes
    down at ``i * interval`` and up ``KEY_HOLD_FRACTION * interval`` later, with
    ``interval = key_interval_ms(wpm)``."""
    seed, r = _derive(rng)
    units = text_units(text)
    if profile.strategy == "human":
        return KeyPlan(strategy="human", seed=seed,
                       actions=human_keys.plan_typing_actions(units, profile, r),
                       chars=len(units), wpm=profile.wpm)
    strategy: Strategy = profile.strategy
    actions: list[KeyAction] = []
    if strategy == "direct":
        for kind, value in units:
            actions.append(KeyAction(0.0, kind, value, True))
            actions.append(KeyAction(0.0, kind, value, False))
        wpm = None
    else:
        interval = key_interval_ms(profile.wpm)
        hold = interval * KEY_HOLD_FRACTION
        for i, (kind, value) in enumerate(units):
            actions.append(KeyAction(i * interval, kind, value, True))
            actions.append(KeyAction(i * interval + hold, kind, value, False))
        wpm = profile.wpm
    return KeyPlan(strategy=strategy, seed=seed, actions=tuple(actions), chars=len(units),
                   wpm=wpm)


def plan_combo(combo: str, hold: float | None, profile: KeyboardProfile,
               rng: random.Random) -> KeyPlan:
    """Keys down in order (modifiers first), then up in reverse order.

    The last key goes down at ``t_down``; the first release happens ``hold`` seconds later
    when ``hold`` is given, otherwise after ``KEY_HOLD_FRACTION`` of the key interval
    (``linear``) or immediately (``direct``). ``linear`` spaces the other events
    ``COMBO_GAP_MS`` apart. ``chars`` is the number of keys in the combination.
    """
    parsed = parse_combo(combo)
    if hold is not None and (not math.isfinite(hold) or hold < 0):
        raise ValueError("hold must be a non-negative number of seconds")
    seed, r = _derive(rng)
    keys = parsed.keys
    if profile.strategy == "human":
        return KeyPlan(strategy="human", seed=seed,
                       actions=human_keys.plan_combo_actions(
                           keys, None if hold is None else hold * 1000.0, profile, r),
                       chars=len(keys), wpm=profile.wpm)
    strategy: Strategy = profile.strategy
    gap = 0.0 if strategy == "direct" else COMBO_GAP_MS
    actions = [KeyAction(i * gap, "key", key, True) for i, key in enumerate(keys)]
    t = actions[-1].t_ms
    if hold is not None:
        t += hold * 1000.0
    elif strategy != "direct":
        t += key_interval_ms(profile.wpm) * KEY_HOLD_FRACTION
    for i, key in enumerate(reversed(keys)):
        actions.append(KeyAction(t + i * gap, "key", key, False))
    return KeyPlan(strategy=strategy, seed=seed, actions=tuple(actions), chars=len(keys),
                   wpm=None if strategy == "direct" else profile.wpm)
