"""The ``human`` mouse strategy (docs/contract.md, "Strategies"; issue #29).

Pure functions of a seeded ``random.Random`` (the plan's own generator, created by
:mod:`j5_macro.input.planning`): the same arguments and seed always give the same result,
and nothing here touches the system or imports anything platform-specific.

Movement: a path shape (``windmouse`` or ``bezier``, both implemented here) gives the
geometry; a shared timing layer gives the duration (Fitts' law), a bell-shaped speed
profile, an occasional overshoot with a correction, and micro-jitter.

Placeholder until #29 lands: straight lines at a fixed pace, landing on the target's
requested point.
"""

from __future__ import annotations

import math
import random

from j5_macro.backends.base import Rect
from j5_macro.input.api import ButtonAction, MouseProfile, ScrollAction, TimedPoint

_PLACEHOLDER_SPEED_PX_S = 1200.0
_PLACEHOLDER_STEP_MS = 8.0


def plan_points(start: tuple[int, int], end: tuple[int, int], profile: MouseProfile,
                r: random.Random, *, target: Rect | None = None,
                bounds: Rect | None = None) -> tuple[tuple[int, int], tuple[TimedPoint, ...]]:
    """Plan a human movement from ``start`` towards ``end``.

    Returns ``(landing, points)``. ``landing`` is where the movement ends: ``end`` itself
    without ``target``, otherwise a point inside ``target`` drawn around ``end``. Points
    have non-decreasing ``t_ms``, the first one after t=0 (``start`` is where the pointer
    already is), every one inside ``bounds`` when given, and the last one exactly on
    ``landing``. ``start == landing`` gives a single point at t=0.
    """
    sx, sy = start
    ex, ey = end
    distance = math.hypot(ex - sx, ey - sy)
    if distance == 0:
        return end, (TimedPoint(0.0, ex, ey),)
    duration = max(60.0, distance / (_PLACEHOLDER_SPEED_PX_S * profile.speed) * 1000.0)
    count = max(1, math.ceil(duration / _PLACEHOLDER_STEP_MS))
    return end, tuple(
        TimedPoint(duration * i / count, round(sx + (ex - sx) * i / count),
                   round(sy + (ey - sy) * i / count))
        for i in range(1, count + 1))


def plan_click_actions(clicks: int, profile: MouseProfile,
                       r: random.Random) -> tuple[ButtonAction, ...]:
    """Button presses of a human click (``clicks`` >= 1): alternating down/up starting at
    t=0 with a down, variable hold times and pauses between the clicks of a multi-click."""
    actions: list[ButtonAction] = []
    t = 0.0
    for i in range(clicks):
        if i:
            t += 65.0
        actions.append(ButtonAction(t, True))
        t += 40.0
        actions.append(ButtonAction(t, False))
    return tuple(actions)


def plan_scroll_actions(dx: int, dy: int, profile: MouseProfile,
                        r: random.Random) -> tuple[ScrollAction, ...]:
    """Notches of a human scroll: one notch per axis at a time, the first at t=0, the
    sums of ``dx`` and ``dy`` over the actions equal to the arguments; empty for (0, 0)."""
    sx = (dx > 0) - (dx < 0)
    sy = (dy > 0) - (dy < 0)
    return tuple(ScrollAction(i * 50.0, sx if i < abs(dx) else 0, sy if i < abs(dy) else 0)
                 for i in range(max(abs(dx), abs(dy))))


def drag_settle_ms(profile: MouseProfile, r: random.Random) -> float:
    """Pause after pressing (and again before releasing) the button of a human drag."""
    return 50.0
