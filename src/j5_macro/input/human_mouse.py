"""The ``human`` mouse strategy (docs/contract.md, "Strategies"; issue #29).

Pure functions of a seeded ``random.Random`` (the plan's own generator, created by
:mod:`j5_macro.input.planning`): the same arguments and seed always give the same result,
and nothing here touches the system or imports anything platform-specific.

Movement: a path shape (``windmouse`` or ``bezier``, both implemented here) gives the
geometry, a polyline from the start to the point aimed at. A shared timing layer then
gives the duration (Fitts' law, divided by ``speed``, with log-normal noise), a
bell-shaped speed profile (minimum jerk, peaking slightly before the middle), an
occasional overshoot with a pause and a corrective movement, and smoothed sideways
micro-jitter that fades out at both ends. The landing point is ``end``, or a Gaussian
point around it inside ``target``.
"""

from __future__ import annotations

import bisect
import math
import random
from collections.abc import Callable
from itertools import pairwise

from j5_macro.backends.base import Rect
from j5_macro.input.api import ButtonAction, MouseProfile, ScrollAction, TimedPoint

Point = tuple[float, float]

# --- movement timing --------------------------------------------------------------------

POINT_INTERVAL_MS = 8.0
"""Target spacing of planned points (~125 Hz), as in ``planning.POINT_INTERVAL_MS``."""
FITTS_A_MS = 20.0
"""Fitts' law intercept ``a`` of ``MT = a + b * log2(D / W + 1)``, at ``speed: 1.0``."""
FITTS_B_MS = 125.0
"""Fitts' law slope ``b``: 500 px to a 20 px target ~610 ms, 50 px ~245 ms."""
DEFAULT_TARGET_PX = 20.0
"""Target width ``W`` for Fitts' law when the movement has no target area."""
MIN_MOVE_MS = 30.0
"""Shortest movement (any non-zero distance), before dividing by ``speed``."""
DURATION_SIGMA = 0.08
"""Sigma of the log-normal noise multiplying every movement duration."""
SPEED_PEAK_RANGE = (0.42, 0.5)
"""Fraction of the movement time at which the speed peaks (uniform, seeded)."""

# --- landing ----------------------------------------------------------------------------

TARGET_SIGMA_FRACTION = 1.0 / 6.0
"""Landing sigma inside a target, as a fraction of its width (x) and height (y)."""
TARGET_INSET_PX = 2
"""The landing keeps this far from the target's border (less for tiny targets)."""
CORNER_CLEARANCE_PX = 3
"""Points other than the landing stay this far from the desktop corners on one axis, so
the corner failsafe (2 px) never fires on a planned point."""

# --- overshoot --------------------------------------------------------------------------

OVERSHOOT_MIN_DISTANCE_PX = 100.0
"""Movements shorter than this never overshoot."""
OVERSHOOT_FRACTION = (0.03, 0.08)
"""How far past the landing the main movement aims, as a fraction of the distance."""
OVERSHOOT_MAX_PX = 40.0
"""Upper bound of the overshoot distance."""
OVERSHOOT_SIDE_SIGMA = 0.3
"""Sideways error of the overshoot point, as a fraction of the overshoot distance."""
OVERSHOOT_PAUSE_MS = (30.0, 100.0)
"""Pause between the main movement and the correction (uniform, seeded)."""
CORRECTION_TARGET_PX = 12.0
"""Fitts' ``W`` of the corrective movement after an overshoot."""

# --- jitter -----------------------------------------------------------------------------

JITTER_KNOT_MS = 60.0
"""Spacing of the random knots of the sideways jitter, smoothly interpolated."""

# --- path shapes ------------------------------------------------------------------------

WIND_GRAVITY = 9.0
"""WindMouse gravity: pull towards the destination per step, before distance scaling."""
WIND_FORCE = 5.0
"""WindMouse wind: random push per step, before distance scaling (the classic default
is 3; 5 gives a visible, still modest curvature of a few percent of the distance)."""
WIND_MAX_STEP = 15.0
"""WindMouse step size limit, before distance scaling."""
WIND_DAMP_DISTANCE = 12.0
"""Below this distance WindMouse stops adding wind and shrinks its step limit."""
WIND_SCALE_RANGE = (0.5, 4.0)
"""Bounds of the WindMouse distance scale ``sqrt(D / WIND_SCALE_REF_PX)``."""
WIND_SCALE_REF_PX = 300.0
"""Distance at which the WindMouse constants are used unscaled."""
WIND_MAX_STEPS = 2000
"""Safety bound on WindMouse iterations."""
BEZIER_OFFSET_FRACTION = (0.05, 0.25)
"""Perpendicular offset of the Bézier control points, as a fraction of the distance."""
BEZIER_MAX_OFFSET_PX = 200.0
"""Upper bound of the Bézier control point offset."""
BEZIER_S_CURVE_P = 0.15
"""Probability that the two Bézier control points bend to opposite sides (S-curve)."""
BEZIER_STEP_PX = 4.0
"""Approximate spacing of the Bézier polyline before resampling in time."""
BEZIER_MAX_SEGMENTS = 300
"""Upper bound of Bézier polyline segments."""

# --- clicks, scroll, drag ---------------------------------------------------------------

CLICK_HOLD_MEDIAN_MS = 90.0
"""Median time a button stays down per click (log-normal)."""
CLICK_HOLD_SIGMA = 0.25
"""Sigma of the log-normal click hold time."""
CLICK_HOLD_RANGE_MS = (40.0, 200.0)
"""Clamp of the click hold time."""
CLICK_GAP_MEDIAN_MS = 95.0
"""Median pause between the release of a click and the next press of a multi-click."""
CLICK_GAP_SIGMA = 0.25
"""Sigma of the log-normal multi-click pause."""
CLICK_GAP_RANGE_MS = (60.0, 150.0)
"""Clamp of the multi-click pause, short enough to register as a double click."""
SCROLL_NOTCH_MEDIAN_MS = 70.0
"""Median delay between scroll notches at ``speed: 1.0`` (log-normal)."""
SCROLL_NOTCH_SIGMA = 0.3
"""Sigma of the log-normal scroll notch delay."""
SCROLL_NOTCH_RANGE_MS = (25.0, 200.0)
"""Clamp of the scroll notch delay at ``speed: 1.0``."""
SCROLL_BURST_P = 0.2
"""Probability that a notch follows the previous one in a quick burst."""
SCROLL_BURST_MS = (12.0, 30.0)
"""Delay of a burst notch (uniform, seeded), at ``speed: 1.0``."""
DRAG_SETTLE_MEDIAN_MS = 120.0
"""Median pause after pressing and before releasing the button of a drag (log-normal)."""
DRAG_SETTLE_SIGMA = 0.25
"""Sigma of the log-normal drag pause."""
DRAG_SETTLE_RANGE_MS = (80.0, 200.0)
"""Clamp of the drag pause."""


# --- helpers ----------------------------------------------------------------------------


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _lognormal(r: random.Random, median: float, sigma: float,
               bounds: tuple[float, float]) -> float:
    return _clamp(r.lognormvariate(math.log(median), sigma), *bounds)


def fitts_ms(distance: float, width: float, speed: float) -> float:
    """Noise-free movement time of Fitts' law for ``distance`` and target ``width``."""
    mt = FITTS_A_MS + FITTS_B_MS * math.log2(distance / max(width, 1.0) + 1.0)
    return max(MIN_MOVE_MS, mt) / speed


def _min_jerk(tau: float) -> float:
    return tau * tau * tau * (10.0 + tau * (-15.0 + 6.0 * tau))


def _inside(bounds: Rect | None, x: float, y: float, *, final: bool) -> Point:
    """Clamp into ``bounds``; non-final points also keep clear of the corners."""
    if bounds is None:
        return x, y
    x0, y0 = bounds.x, bounds.y
    x1, y1 = bounds.x + bounds.width - 1, bounds.y + bounds.height - 1
    x = _clamp(x, x0, x1)
    y = _clamp(y, y0, y1)
    m = CORNER_CLEARANCE_PX
    if not final and bounds.width > 2 * m + 2:
        near_x = x - x0 < m or x1 - x < m
        near_y = y - y0 < m or y1 - y < m
        if near_x and near_y:
            x = x0 + m if x - x0 < m else x1 - m
    return x, y


def _landing(end: tuple[int, int], target: Rect | None, bounds: Rect | None,
             r: random.Random) -> tuple[int, int]:
    x, y = float(end[0]), float(end[1])
    if target is not None:
        ix = min(TARGET_INSET_PX, max(0, (target.width - 1) // 2))
        iy = min(TARGET_INSET_PX, max(0, (target.height - 1) // 2))
        x = _clamp(r.gauss(x, target.width * TARGET_SIGMA_FRACTION),
                   target.x + ix, target.x + max(ix, target.width - 1 - ix))
        y = _clamp(r.gauss(y, target.height * TARGET_SIGMA_FRACTION),
                   target.y + iy, target.y + max(iy, target.height - 1 - iy))
    x, y = _inside(bounds, round(x), round(y), final=True)
    return int(x), int(y)


# --- path shapes ------------------------------------------------------------------------


def windmouse_path(start: Point, end: Point, r: random.Random) -> list[Point]:
    """Our implementation of the WindMouse algorithm: a polyline from ``start`` to
    ``end`` (both included) pulled by gravity and pushed by a randomly changing wind,
    with a step limit that shrinks near the destination. Constants scale with distance."""
    sx, sy = start
    ex, ey = end
    total = math.hypot(ex - sx, ey - sy)
    scale = _clamp(math.sqrt(total / WIND_SCALE_REF_PX), *WIND_SCALE_RANGE)
    gravity = WIND_GRAVITY * scale
    wind = WIND_FORCE * scale
    max_step = WIND_MAX_STEP * scale
    damp = WIND_DAMP_DISTANCE * scale
    sqrt3, sqrt5 = math.sqrt(3.0), math.sqrt(5.0)
    x, y = sx, sy
    vx = vy = wx = wy = 0.0
    points = [(x, y)]
    for _ in range(WIND_MAX_STEPS):
        dist = math.hypot(ex - x, ey - y)
        if dist < 1.0:
            break
        w = min(wind, dist)
        if dist >= damp:
            wx = wx / sqrt3 + (2.0 * r.random() - 1.0) * w / sqrt5
            wy = wy / sqrt3 + (2.0 * r.random() - 1.0) * w / sqrt5
        else:
            wx /= sqrt3
            wy /= sqrt3
            if max_step < 3.0:
                max_step = r.random() * 3.0 + 3.0
            else:
                max_step /= sqrt5
        vx += wx + gravity * (ex - x) / dist
        vy += wy + gravity * (ey - y) / dist
        v = math.hypot(vx, vy)
        if v > max_step:
            clip = max_step / 2.0 + r.random() * max_step / 2.0
            vx, vy = vx / v * clip, vy / v * clip
        x += vx
        y += vy
        points.append((x, y))
    points.append((ex, ey))
    return points


def bezier_path(start: Point, end: Point, r: random.Random) -> list[Point]:
    """A cubic Bézier from ``start`` to ``end`` (both included) whose control points are
    offset perpendicular to the straight line, usually to the same side."""
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    total = math.hypot(dx, dy)
    if total == 0:
        return [start, end]
    nx, ny = -dy / total, dx / total
    side = 1.0 if r.random() < 0.5 else -1.0
    o1 = side * min(BEZIER_MAX_OFFSET_PX, total * r.uniform(*BEZIER_OFFSET_FRACTION))
    o2 = o1 * r.uniform(0.3, 1.2)
    if r.random() < BEZIER_S_CURVE_P:
        o2 = -o2
    u1, u2 = r.uniform(0.2, 0.4), r.uniform(0.6, 0.8)
    c1 = (sx + dx * u1 + nx * o1, sy + dy * u1 + ny * o1)
    c2 = (sx + dx * u2 + nx * o2, sy + dy * u2 + ny * o2)
    n = int(_clamp(math.ceil(total / BEZIER_STEP_PX), 4, BEZIER_MAX_SEGMENTS))
    points = []
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        points.append((a * sx + b * c1[0] + c * c2[0] + d * ex,
                       a * sy + b * c1[1] + c * c2[1] + d * ey))
    points[-1] = end
    return points


PATHS: dict[str, Callable[[Point, Point, random.Random], list[Point]]] = {
    "windmouse": windmouse_path,
    "bezier": bezier_path,
}
"""Path shapes by ``MouseProfile.path``."""


def _keep_between(points: list[Point], start: Point, end: Point) -> list[Point]:
    """Clip each point's progress along start→end into [0, D], so a path never passes
    its aim point nor backs up behind its start (sideways deviation is kept)."""
    sx, sy = start
    dx, dy = end[0] - sx, end[1] - sy
    total = math.hypot(dx, dy)
    ux, uy = dx / total, dy / total
    out = []
    for x, y in points:
        along = (x - sx) * ux + (y - sy) * uy
        clipped = _clamp(along, 0.0, total)
        out.append((x + (clipped - along) * ux, y + (clipped - along) * uy))
    return out


# --- timing layer -----------------------------------------------------------------------


def _segment(start: Point, aim: Point, width: float, t0: float, profile: MouseProfile,
             r: random.Random, bounds: Rect | None, *, final: bool) -> list[TimedPoint]:
    """Timed points of one movement from ``start`` to ``aim`` beginning at ``t0``, the
    first one after ``t0`` and the last one exactly on ``aim``."""
    distance = math.hypot(aim[0] - start[0], aim[1] - start[1])
    duration = fitts_ms(distance, width, profile.speed) * r.lognormvariate(0.0,
                                                                          DURATION_SIGMA)
    path = _keep_between(PATHS[profile.path](start, aim, r), start, aim)
    cumulative = [0.0]
    for (ax, ay), (bx, by) in pairwise(path):
        cumulative.append(cumulative[-1] + math.hypot(bx - ax, by - ay))
    length = cumulative[-1]
    skew = math.log(0.5) / math.log(r.uniform(*SPEED_PEAK_RANGE))
    count = max(1, math.ceil(duration / POINT_INTERVAL_MS))

    knots_n = max(2, math.ceil(duration / JITTER_KNOT_MS)) + 1
    knots = [r.uniform(-1.0, 1.0) * profile.jitter for _ in range(knots_n)]
    nx, ny = -(aim[1] - start[1]) / distance, (aim[0] - start[0]) / distance

    points: list[TimedPoint] = []
    for i in range(1, count + 1):
        tau = i / count
        if i == count:
            x, y = aim
        else:
            s = _min_jerk(tau ** skew) * length
            k = min(bisect.bisect_right(cumulative, s), len(path) - 1)
            seg = cumulative[k] - cumulative[k - 1]
            f = (s - cumulative[k - 1]) / seg if seg > 0 else 0.0
            (ax, ay), (bx, by) = path[k - 1], path[k]
            x, y = ax + (bx - ax) * f, ay + (by - ay) * f
            if profile.jitter:
                u = tau * (knots_n - 1)
                j = min(int(u), knots_n - 2)
                w = (1.0 - math.cos(math.pi * (u - j))) / 2.0
                offset = (knots[j] * (1.0 - w) + knots[j + 1] * w) * math.sin(math.pi * tau)
                x, y = x + nx * offset, y + ny * offset
        is_last = final and i == count
        x, y = _inside(bounds, round(x), round(y), final=is_last)
        points.append(TimedPoint(t0 + duration * tau, int(x), int(y)))
    return points


def plan_points(start: tuple[int, int], end: tuple[int, int], profile: MouseProfile,
                r: random.Random, *, target: Rect | None = None,
                bounds: Rect | None = None) -> tuple[tuple[int, int], tuple[TimedPoint, ...]]:
    """Plan a human movement from ``start`` towards ``end``.

    Returns ``(landing, points)``. ``landing`` is where the movement ends: ``end`` itself
    without ``target``, otherwise a Gaussian point around ``end`` inside ``target`` (clear
    of its border); clamped into ``bounds`` when given. Points have integer coordinates
    and non-decreasing ``t_ms``, the first one after t=0 (``start`` is where the pointer
    already is), every one inside ``bounds`` when given (and, except the landing, clear of
    its corners), and the last one exactly on ``landing``. ``start == landing`` gives a
    single point at t=0.

    The duration follows Fitts' law with ``W`` the target's smaller side (or
    ``DEFAULT_TARGET_PX``). With probability ``profile.overshoot``, movements longer than
    ``OVERSHOOT_MIN_DISTANCE_PX`` aim a little past the landing, pause, and correct.
    """
    landing = _landing(end, target, bounds, r)
    if landing == start:
        return landing, (TimedPoint(0.0, *landing),)
    width = (min(target.width, target.height) if target is not None
             else DEFAULT_TARGET_PX)
    sx, sy = float(start[0]), float(start[1])
    lx, ly = float(landing[0]), float(landing[1])
    distance = math.hypot(lx - sx, ly - sy)

    aim: Point | None = None
    if distance > OVERSHOOT_MIN_DISTANCE_PX and r.random() < profile.overshoot:
        past = min(OVERSHOOT_MAX_PX, distance * r.uniform(*OVERSHOOT_FRACTION))
        side = r.gauss(0.0, past * OVERSHOOT_SIDE_SIGMA)
        ux, uy = (lx - sx) / distance, (ly - sy) / distance
        ox, oy = _inside(bounds, round(lx + ux * past - uy * side),
                         round(ly + uy * past + ux * side), final=False)
        if (ox, oy) != (lx, ly) and (ox, oy) != (sx, sy):
            aim = (ox, oy)

    if aim is None:
        points = _segment((sx, sy), (lx, ly), width, 0.0, profile, r, bounds, final=True)
    else:
        points = _segment((sx, sy), aim, width, 0.0, profile, r, bounds, final=False)
        t = points[-1].t_ms + r.uniform(*OVERSHOOT_PAUSE_MS)
        points += _segment((points[-1].x, points[-1].y), (lx, ly), CORRECTION_TARGET_PX, t,
                           profile, r, bounds, final=True)
    return landing, tuple(points)


# --- clicks, scroll, drag ---------------------------------------------------------------


def plan_click_actions(clicks: int, profile: MouseProfile,
                       r: random.Random) -> tuple[ButtonAction, ...]:
    """Button presses of a human click (``clicks`` >= 1): alternating down/up starting at
    t=0 with a down, log-normal hold times and, between the clicks of a multi-click,
    pauses short enough to register as a double click."""
    actions: list[ButtonAction] = []
    t = 0.0
    for i in range(clicks):
        if i:
            t += _lognormal(r, CLICK_GAP_MEDIAN_MS, CLICK_GAP_SIGMA, CLICK_GAP_RANGE_MS)
        actions.append(ButtonAction(t, True))
        t += _lognormal(r, CLICK_HOLD_MEDIAN_MS, CLICK_HOLD_SIGMA, CLICK_HOLD_RANGE_MS)
        actions.append(ButtonAction(t, False))
    return tuple(actions)


def plan_scroll_actions(dx: int, dy: int, profile: MouseProfile,
                        r: random.Random) -> tuple[ScrollAction, ...]:
    """Notches of a human scroll: one notch per axis at a time, the first at t=0, the
    sums of ``dx`` and ``dy`` over the actions equal to the arguments; empty for (0, 0).
    Delays are log-normal (divided by ``speed``) with occasional quick bursts."""
    sx = (dx > 0) - (dx < 0)
    sy = (dy > 0) - (dy < 0)
    actions: list[ScrollAction] = []
    t = 0.0
    for i in range(max(abs(dx), abs(dy))):
        if i:
            if r.random() < SCROLL_BURST_P:
                delay = r.uniform(*SCROLL_BURST_MS)
            else:
                delay = _lognormal(r, SCROLL_NOTCH_MEDIAN_MS, SCROLL_NOTCH_SIGMA,
                                   SCROLL_NOTCH_RANGE_MS)
            t += delay / profile.speed
        actions.append(ScrollAction(t, sx if i < abs(dx) else 0, sy if i < abs(dy) else 0))
    return tuple(actions)


def drag_settle_ms(profile: MouseProfile, r: random.Random) -> float:
    """Pause after pressing (and again before releasing) the button of a human drag."""
    return _lognormal(r, DRAG_SETTLE_MEDIAN_MS, DRAG_SETTLE_SIGMA, DRAG_SETTLE_RANGE_MS)
