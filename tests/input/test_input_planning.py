import math
import random

import pytest

from j5_macro.backends import Button
from j5_macro.input import KeyAction, KeyboardProfile, MouseProfile, TimedPoint
from j5_macro.input.planning import (
    CLICK_HOLD_MS,
    COMBO_GAP_MS,
    SCROLL_NOTCH_MS,
    ScrollAction,
    plan_click,
    plan_combo,
    plan_move,
    plan_scroll,
    plan_typing,
    replan_move,
    text_units,
)

LINEAR = MouseProfile(strategy="linear")
DIRECT = MouseProfile(strategy="direct")
KB_LINEAR = KeyboardProfile(strategy="linear", wpm=60)
KB_DIRECT = KeyboardProfile(strategy="direct")


# --- seeds ------------------------------------------------------------------------------


def test_plans_are_deterministic_for_a_seed_and_differ_across_seeds():
    a = plan_move((0, 0), (500, 300), LINEAR, random.Random(1))
    b = plan_move((0, 0), (500, 300), LINEAR, random.Random(1))
    c = plan_move((0, 0), (500, 300), LINEAR, random.Random(2))
    assert a == b
    assert a.seed != c.seed
    clicks = [plan_click(Button.LEFT, 3, LINEAR, random.Random(s)).actions for s in (1, 1, 2)]
    assert clicks[0] == clicks[1] != clicks[2]


def test_reported_seed_reproduces_the_plan():
    plan = plan_move((10, 10), (900, 700), LINEAR, random.Random(42))
    assert replan_move(plan.seed, (10, 10), (900, 700), LINEAR) == plan


@pytest.mark.parametrize("profile", [DIRECT, LINEAR, MouseProfile()])
def test_every_planner_draws_exactly_one_seed(profile):
    rng, reference = random.Random(7), random.Random(7)
    plan_move((0, 0), (100, 100), profile, rng)
    reference.getrandbits(63)
    assert rng.random() == reference.random()


# --- mouse ------------------------------------------------------------------------------


def test_direct_move_is_one_point_on_the_target():
    plan = plan_move((0, 0), (300, 200), DIRECT, random.Random(1))
    assert plan.points == (TimedPoint(0.0, 300, 200),)
    assert plan.strategy == "direct"
    assert plan.duration_ms == 0


def test_linear_move_constant_speed_and_endpoints():
    plan = plan_move((100, 100), (1300, 100), LINEAR, random.Random(1))
    assert plan.duration_ms == pytest.approx(1000.0)  # 1200 px at 1200 px/s
    assert plan.points[-1] == TimedPoint(pytest.approx(1000.0), 1300, 100)
    times = [p.t_ms for p in plan.points]
    assert times == sorted(times)
    assert times[0] > 0
    gaps = [b - a for a, b in zip([0.0, *times], times, strict=False)]
    assert all(g == pytest.approx(gaps[0]) for g in gaps)
    assert gaps[0] <= 8.0
    for p in plan.points:  # on the line at the position its time dictates
        assert p.y == 100
        assert abs(p.x - (100 + 1.2 * p.t_ms)) <= 1


def test_linear_move_diagonal_and_speed():
    slow = plan_move((0, 0), (300, 400), LINEAR, random.Random(1))
    fast = plan_move((0, 0), (300, 400), MouseProfile(strategy="linear", speed=2.0),
                     random.Random(1))
    assert slow.duration_ms == pytest.approx(500 / 1200 * 1000)
    assert fast.duration_ms == pytest.approx(slow.duration_ms / 2)
    for p in slow.points:
        assert abs(p.x * 4 - p.y * 3) <= 7  # stays on the segment (rounding)


def test_linear_move_has_a_minimum_duration():
    plan = plan_move((0, 0), (5, 0), LINEAR, random.Random(1))
    assert plan.duration_ms == pytest.approx(60.0)
    assert plan.points[-1].x == 5


def test_zero_length_move_is_a_single_point():
    plan = plan_move((5, 5), (5, 5), LINEAR, random.Random(1))
    assert plan.points == (TimedPoint(0.0, 5, 5),)


def test_human_move_reports_human_and_replans_from_its_seed():
    human = plan_move((0, 0), (600, 0), MouseProfile(), random.Random(3))
    assert human.strategy == "human"
    assert human.points[-1] == TimedPoint(human.duration_ms, *human.end)
    assert replan_move(human.seed, (0, 0), (600, 0), MouseProfile()) == human


def test_click_plans():
    direct = plan_click(Button.LEFT, 2, DIRECT, random.Random(1))
    assert [(a.t_ms, a.down) for a in direct.actions] == [
        (0, True), (0, False), (0, True), (0, False)]
    linear = plan_click(Button.RIGHT, 3, LINEAR, random.Random(1))
    downs = [a.t_ms for a in linear.actions if a.down]
    ups = [a.t_ms for a in linear.actions if not a.down]
    assert downs[0] == 0
    for down, up in zip(downs, ups, strict=True):
        assert up - down == pytest.approx(CLICK_HOLD_MS)
    assert all(50 <= nxt - up <= 80 for up, nxt in zip(ups, downs[1:], strict=False))
    with pytest.raises(ValueError):
        plan_click(Button.LEFT, 0, LINEAR, random.Random(1))


def test_scroll_plans():
    assert plan_scroll(0, 3, DIRECT, random.Random(1)).actions == (ScrollAction(0, 0, 3),)
    linear = plan_scroll(-2, 3, LINEAR, random.Random(1))
    assert linear.actions == (
        ScrollAction(0, -1, 1), ScrollAction(SCROLL_NOTCH_MS, -1, 1),
        ScrollAction(2 * SCROLL_NOTCH_MS, 0, 1))
    assert plan_scroll(0, 0, LINEAR, random.Random(1)).actions == ()


# --- keyboard ---------------------------------------------------------------------------


def test_text_units_cover_unicode_and_control_characters():
    assert text_units("ñ日😀a\nb\tc\r\nd\re") == [
        ("char", "ñ"), ("char", "日"), ("char", "😀"), ("char", "a"), ("key", "enter"),
        ("char", "b"), ("key", "tab"), ("char", "c"), ("key", "enter"), ("char", "d"),
        ("key", "enter"), ("char", "e")]


def test_direct_typing_presses_everything_at_once():
    plan = plan_typing("ñ\n", KB_DIRECT, random.Random(1))
    assert plan.actions == (
        KeyAction(0, "char", "ñ", True), KeyAction(0, "char", "ñ", False),
        KeyAction(0, "key", "enter", True), KeyAction(0, "key", "enter", False))
    assert plan.chars == 2
    assert plan.wpm is None


def test_linear_typing_fixed_interval_from_wpm():
    plan = plan_typing("日本語", KB_LINEAR, random.Random(1))
    # 60 wpm * 5 chars/word = 300 chars/min -> 200 ms per key, held 30% (60 ms)
    assert [(a.t_ms, a.value, a.down) for a in plan.actions] == [
        (0, "日", True), (pytest.approx(60), "日", False),
        (200, "本", True), (pytest.approx(260), "本", False),
        (400, "語", True), (pytest.approx(460), "語", False)]
    assert plan.wpm == 60
    assert plan.chars == 3
    assert plan.duration_ms == pytest.approx(460)


def test_human_typing_reports_human_and_is_reproducible():
    human = plan_typing("hello", KeyboardProfile(wpm=60), random.Random(5))
    assert human.strategy == "human" and human.chars == 5 and human.wpm == 60
    assert human == plan_typing("hello", KeyboardProfile(wpm=60), random.Random(5))


def test_combo_plan_order_and_aliases():
    plan = plan_combo("Control+Shift+Esc", None, KB_DIRECT, random.Random(1))
    assert [(a.value, a.down) for a in plan.actions] == [
        ("ctrl", True), ("shift", True), ("escape", True),
        ("escape", False), ("shift", False), ("ctrl", False)]
    assert all(a.t_ms == 0 and a.kind == "key" for a in plan.actions)
    assert plan.chars == 3


def test_linear_combo_timing_and_hold():
    plan = plan_combo("ctrl+s", None, KB_LINEAR, random.Random(1))
    assert [a.t_ms for a in plan.actions] == [
        0, COMBO_GAP_MS, pytest.approx(COMBO_GAP_MS + 60), pytest.approx(2 * COMBO_GAP_MS + 60)]
    held = plan_combo("ctrl+s", 0.5, KB_LINEAR, random.Random(1))
    assert [a.t_ms for a in held.actions] == [
        0, COMBO_GAP_MS, COMBO_GAP_MS + 500, 2 * COMBO_GAP_MS + 500]
    direct = plan_combo("alt+tab", 1.0, KB_DIRECT, random.Random(1))
    assert [a.t_ms for a in direct.actions] == [0, 0, 1000, 1000]


@pytest.mark.parametrize("hold", [-1.0, math.inf, math.nan])
def test_combo_rejects_bad_hold(hold):
    with pytest.raises(ValueError, match="hold"):
        plan_combo("ctrl+s", hold, KB_LINEAR, random.Random(1))


def test_combo_rejects_bad_combo():
    with pytest.raises(ValueError, match="not a modifier"):
        plan_combo("s+ctrl", None, KB_LINEAR, random.Random(1))
