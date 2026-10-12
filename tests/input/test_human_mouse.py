"""The ``human`` mouse planner (issue #29): pure, seeded, testable without a display."""

import math
import random
import time
from itertools import pairwise

import pytest

from j5_macro.backends.base import Rect
from j5_macro.input import MouseProfile, TimedPoint
from j5_macro.input.human_mouse import (
    CLICK_GAP_RANGE_MS,
    CLICK_HOLD_RANGE_MS,
    DRAG_SETTLE_RANGE_MS,
    OVERSHOOT_MAX_PX,
    bezier_path,
    drag_settle_ms,
    fitts_ms,
    plan_click_actions,
    plan_points,
    plan_scroll_actions,
    windmouse_path,
)
from j5_macro.input.planning import plan_move, replan_move
from j5_macro.input.session import in_corner

PATHS = ("windmouse", "bezier")
SEEDS = range(200)
SCREEN = Rect(0, 0, 1920, 1080)
EPS = 1e-6


def profile(path="windmouse", **kw):
    return MouseProfile(strategy="human", path=path, **kw)


def plan(start, end, prof, seed, **kw):
    return plan_points(start, end, prof, random.Random(seed), **kw)


def overshoots(start, landing, points, tolerance=1.0):
    """Whether any point goes past ``landing`` along the start→landing direction."""
    sx, sy = start
    dx, dy = landing[0] - sx, landing[1] - sy
    d = math.hypot(dx, dy)
    return any(((p.x - sx) * dx + (p.y - sy) * dy) / d > d + tolerance for p in points)


def max_deviation(start, end, points):
    sx, sy = start
    dx, dy = end[0] - sx, end[1] - sy
    d = math.hypot(dx, dy)
    return max(abs((p.x - sx) * dy - (p.y - sy) * dx) / d for p in points)


def check_invariants(start, landing, points, bounds=None):
    assert points
    assert all(isinstance(p.x, int) and isinstance(p.y, int) for p in points)
    assert all(a.t_ms <= b.t_ms for a, b in pairwise(points))
    assert (points[-1].x, points[-1].y) == landing
    if start == landing:
        assert points == (TimedPoint(0.0, *landing),)
    else:
        assert points[0].t_ms > 0
    if bounds is not None:
        assert all(bounds.contains(p.x, p.y) for p in points)


# --- movement invariants ----------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("end", [(900, 500), (100, 100), (103, 101), (101, 100),
                                 (1900, 1000), (0, 0), (100, 900)])
def test_invariants_hold_for_many_seeds(path, end):
    start = (100, 100)
    prof = profile(path, overshoot=0.5, jitter=3.0)
    for seed in SEEDS:
        landing, points = plan(start, end, prof, seed, bounds=SCREEN)
        assert landing == end
        check_invariants(start, landing, points, SCREEN)


@pytest.mark.parametrize("path", PATHS)
def test_zero_length_move_is_a_single_point_at_t0(path):
    assert plan((5, 7), (5, 7), profile(path), 1) == ((5, 7), (TimedPoint(0.0, 5, 7),))


@pytest.mark.parametrize("path", PATHS)
def test_points_stay_inside_bounds_and_clear_of_corners(path):
    bounds = Rect(-1920, 0, 3840, 1080)
    prof = profile(path, overshoot=1.0, jitter=5.0)
    for seed in SEEDS:
        # Towards the bottom-right corner: the overshoot would leave the desktop.
        landing, points = plan((-1000, 200), (1910, 1075), prof, seed, bounds=bounds)
        check_invariants((-1000, 200), landing, points, bounds)
        assert not any(in_corner(bounds, p.x, p.y) for p in points[:-1])
        # From a corner along the edge.
        landing, points = plan((-1920, 0), (0, 0), prof, seed, bounds=bounds)
        check_invariants((-1920, 0), landing, points, bounds)
        assert not any(in_corner(bounds, p.x, p.y) for p in points)


@pytest.mark.parametrize("path", PATHS)
def test_landing_outside_bounds_is_clamped(path):
    landing, points = plan((10, 10), (5000, -50), profile(path), 1, bounds=SCREEN)
    assert landing == (1919, 0)
    check_invariants((10, 10), landing, points, SCREEN)


@pytest.mark.parametrize("path", PATHS)
def test_very_short_moves_are_sane(path):
    prof = profile(path, overshoot=1.0, jitter=1.0)
    for seed in SEEDS:
        landing, points = plan((50, 50), (53, 52), prof, seed)
        check_invariants((50, 50), landing, points)
        assert len(points) <= 12
        assert points[-1].t_ms < 100
        assert all(abs(p.x - 51) <= 4 and abs(p.y - 51) <= 4 for p in points)


@pytest.mark.parametrize("path", PATHS)
def test_long_moves_plan_fast_with_a_sane_number_of_points(path):
    prof = profile(path, overshoot=1.0, jitter=2.0)
    for seed in range(20):
        t0 = time.perf_counter()
        _, points = plan((0, 0), (3000, 1200), prof, seed)
        assert time.perf_counter() - t0 < 0.02
        assert 50 < len(points) < 400


@pytest.mark.parametrize("path", PATHS)
def test_deterministic_for_a_seed_and_different_across_seeds(path):
    prof = profile(path)
    args = ((10, 20), (800, 600), prof)
    assert plan(*args, 7) == plan(*args, 7)
    assert plan(*args, 7, target=Rect(780, 590, 40, 20)) == \
        plan(*args, 7, target=Rect(780, 590, 40, 20))
    paths = {plan(*args, seed)[1] for seed in range(20)}
    assert len(paths) == 20


# --- landing ----------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
def test_landing_is_spread_inside_the_target_and_off_its_border(path):
    target = Rect(400, 300, 60, 24)
    landings = set()
    for seed in SEEDS:
        landing, points = plan((0, 0), target.center, profile(path), seed, target=target)
        check_invariants((0, 0), landing, points)
        x, y = landing
        assert target.x < x < target.x + target.width - 1
        assert target.y < y < target.y + target.height - 1
        landings.add(landing)
    assert len(landings) > 30
    xs = [x for x, _ in landings]
    assert min(xs) < target.center[0] < max(xs)


def test_tiny_targets_are_still_hit():
    for w, h in ((1, 1), (2, 2), (3, 5)):
        target = Rect(100, 100, w, h)
        for seed in range(50):
            landing, _ = plan((0, 0), (100, 100), profile(), seed, target=target)
            assert target.contains(*landing)


# --- timing -----------------------------------------------------------------------------


def median_duration(distance, prof, target=None, seeds=range(60)):
    durations = sorted(
        plan((0, 0), (distance, 0), prof, s, target=target)[1][-1].t_ms for s in seeds)
    return durations[len(durations) // 2]


@pytest.mark.parametrize("path", PATHS)
def test_duration_follows_fitts_law(path):
    prof = profile(path, overshoot=0)
    assert 500 <= median_duration(500, prof, Rect(490, -10, 20, 20)) <= 800
    assert 150 <= median_duration(50, prof) <= 300
    short, mid, long = (median_duration(d, prof) for d in (50, 500, 2000))
    assert short < mid < long
    big = median_duration(500, prof, Rect(400, -100, 200, 200))
    small = median_duration(500, prof, Rect(495, -5, 10, 10))
    assert big < small
    fast = median_duration(500, profile(path, overshoot=0, speed=2.0))
    assert fast < mid * 0.6
    assert fitts_ms(500, 20, 1.0) == pytest.approx(2 * fitts_ms(500, 20, 2.0))


@pytest.mark.parametrize("path", PATHS)
def test_points_are_about_8_ms_apart(path):
    _, points = plan((0, 0), (700, 300), profile(path, overshoot=0), 3)
    gaps = [b.t_ms - a.t_ms for a, b in pairwise(points)]
    assert all(4 <= g <= 8.0001 for g in gaps)


@pytest.mark.parametrize("path", PATHS)
def test_speed_is_bell_shaped(path):
    prof = profile(path, overshoot=0, jitter=0)
    peaks = []
    for seed in range(100):
        _, points = plan((0, 0), (1200, 400), prof, seed)
        pts = [(0.0, 0, 0), *((p.t_ms, p.x, p.y) for p in points)]
        speeds = [math.hypot(b[1] - a[1], b[2] - a[2]) / (b[0] - a[0])
                  for a, b in pairwise(pts)]
        n = len(speeds)
        # Smooth over a few samples, then find the peak.
        smooth = [sum(speeds[max(0, i - 3):i + 4]) / len(speeds[max(0, i - 3):i + 4])
                  for i in range(n)]
        peaks.append(smooth.index(max(smooth)) / n)
        tenth = max(1, n // 10)
        middle = sum(speeds[n // 3:2 * n // 3]) / len(speeds[n // 3:2 * n // 3])
        assert sum(speeds[:tenth]) / tenth < middle / 2
        assert sum(speeds[-tenth:]) / tenth < middle / 2
    assert sum(0.3 <= p <= 0.65 for p in peaks) >= 90
    assert sum(peaks) / len(peaks) < 0.52


# --- path shape -------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
def test_long_moves_deviate_from_the_straight_line(path):
    prof = profile(path, overshoot=0, jitter=0)
    curved = sum(max_deviation((0, 0), (800, 300), plan((0, 0), (800, 300), prof, s)[1]) > 5
                 for s in SEEDS)
    assert curved >= 0.8 * len(SEEDS)


def test_path_shapes_start_and_end_exactly():
    for fn in (windmouse_path, bezier_path):
        for seed in range(50):
            pts = fn((0.0, 0.0), (321.0, -77.0), random.Random(seed))
            assert pts[0] == (0.0, 0.0) and pts[-1] == (321.0, -77.0)
            assert len(pts) < 2000


def test_path_choice_changes_the_geometry():
    a = plan((0, 0), (800, 300), profile("windmouse"), 5)
    b = plan((0, 0), (800, 300), profile("bezier"), 5)
    assert a[1] != b[1]


# --- overshoot and jitter ---------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("probability", [0.0, 0.3, 1.0])
def test_overshoot_frequency_matches_the_probability(path, probability):
    prof = profile(path, overshoot=probability, jitter=0)
    hits = 0
    for seed in range(400):
        landing, points = plan((0, 0), (900, 200), prof, seed)
        if overshoots((0, 0), landing, points):
            hits += 1
            assert max(math.hypot(p.x - landing[0], p.y - landing[1])
                       for p in points[-20:]) <= OVERSHOOT_MAX_PX * 2
    if probability == 0:
        assert hits == 0
    else:
        assert abs(hits / 400 - probability) < 0.08


@pytest.mark.parametrize("path", PATHS)
def test_overshoot_never_happens_on_short_moves_or_at_zero(path):
    for seed in SEEDS:
        landing, points = plan((0, 0), (60, 30), profile(path, overshoot=1.0, jitter=0), seed)
        assert not overshoots((0, 0), landing, points)
        landing, points = plan((0, 0), (1500, -900), profile(path, overshoot=0, jitter=0),
                               seed)
        assert not overshoots((0, 0), landing, points)


@pytest.mark.parametrize("path", PATHS)
def test_overshoot_pauses_then_corrects(path):
    _, points = plan((0, 0), (900, 0), profile(path, overshoot=1.0, jitter=0), 11)
    gaps = [b.t_ms - a.t_ms for a, b in pairwise(points)]
    assert 30 < max(gaps) <= 108.0001
    assert max(p.x for p in points) > 900


@pytest.mark.parametrize("path", PATHS)
def test_jitter_changes_the_path_and_zero_adds_none(path):
    calm = profile(path, overshoot=0, jitter=0)
    shaky = profile(path, overshoot=0, jitter=6.0)
    differ = 0
    for seed in range(50):
        a = plan((0, 0), (700, 200), calm, seed)
        b = plan((0, 0), (700, 200), shaky, seed)
        assert a[0] == b[0] and a[1][-1] == b[1][-1]
        differ += a[1] != b[1]
    assert differ == 50


# --- clicks, scroll, drag ---------------------------------------------------------------


@pytest.mark.parametrize("clicks", [1, 2, 3])
def test_click_actions(clicks):
    for seed in SEEDS:
        actions = plan_click_actions(clicks, profile(), random.Random(seed))
        assert len(actions) == 2 * clicks
        assert actions[0].t_ms == 0
        assert [a.down for a in actions] == [True, False] * clicks
        hold_lo, hold_hi = CLICK_HOLD_RANGE_MS
        gap_lo, gap_hi = CLICK_GAP_RANGE_MS
        for down, up in zip(actions[::2], actions[1::2], strict=True):
            assert hold_lo - EPS <= up.t_ms - down.t_ms <= hold_hi + EPS
        for up, down in zip(actions[1::2], actions[2::2], strict=False):
            assert gap_lo - EPS <= down.t_ms - up.t_ms <= gap_hi + EPS
    holds = {plan_click_actions(1, profile(), random.Random(s))[1].t_ms for s in range(50)}
    assert len(holds) > 40
    assert 70 < sorted(holds)[len(holds) // 2] < 110
    assert plan_click_actions(2, profile(), random.Random(4)) == \
        plan_click_actions(2, profile(), random.Random(4))


@pytest.mark.parametrize(("dx", "dy"), [(0, 0), (0, 5), (0, -7), (3, 0), (-2, 6), (4, -4)])
def test_scroll_actions(dx, dy):
    for seed in range(50):
        actions = plan_scroll_actions(dx, dy, profile(), random.Random(seed))
        assert sum(a.dx for a in actions) == dx and sum(a.dy for a in actions) == dy
        assert all(abs(a.dx) <= 1 and abs(a.dy) <= 1 for a in actions)
        assert len(actions) == max(abs(dx), abs(dy))
        if actions:
            assert actions[0].t_ms == 0
            assert all(a.t_ms < b.t_ms for a, b in pairwise(actions))


def test_scroll_cadence_varies_and_follows_speed():
    def gaps(prof, seeds=range(30)):
        out = []
        for s in seeds:
            acts = plan_scroll_actions(0, 20, prof, random.Random(s))
            out += [b.t_ms - a.t_ms for a, b in pairwise(acts)]
        return sorted(out)

    normal = gaps(profile())
    assert 50 < normal[len(normal) // 2] < 90
    assert min(normal) < 35  # bursts
    assert len(set(normal)) > len(normal) * 0.9
    fast = gaps(profile(speed=2.0))
    assert fast[len(fast) // 2] < normal[len(normal) // 2] * 0.6


def test_drag_settle():
    values = [drag_settle_ms(profile(), random.Random(s)) for s in SEEDS]
    assert all(DRAG_SETTLE_RANGE_MS[0] <= v <= DRAG_SETTLE_RANGE_MS[1] for v in values)
    assert len(set(values)) > 100
    assert drag_settle_ms(profile(), random.Random(3)) == \
        drag_settle_ms(profile(), random.Random(3))


# --- through planning -------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
def test_plan_move_reports_the_landing_and_replans_from_its_seed(path):
    prof = profile(path, overshoot=0.5)
    target = Rect(600, 400, 80, 30)
    for seed in range(30):
        mp = plan_move((10, 10), target.center, prof, random.Random(seed), target=target,
                       bounds=SCREEN)
        assert mp.strategy == "human"
        assert target.contains(*mp.end)
        assert (mp.points[-1].x, mp.points[-1].y) == mp.end
        assert mp.duration_ms == mp.points[-1].t_ms
        assert replan_move(mp.seed, (10, 10), target.center, prof, target=target,
                           bounds=SCREEN) == mp
