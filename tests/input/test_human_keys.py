import itertools
import random
import statistics

import pytest

from j5_macro.input import KeyAction, KeyboardProfile
from j5_macro.input.human_keys import (
    COMBO_DOWN_GAP_MS,
    COMBO_HOLD_MS,
    COMBO_UP_GAP_MS,
    HOLD_MEDIAN_MS,
    QWERTY_NEIGHBOURS,
    TYPO_PROBABILITY,
    plan_combo_actions,
    plan_typing_actions,
)
from j5_macro.input.planning import key_interval_ms, plan_combo, plan_typing, text_units

TEXTS = [
    "",
    "a",
    "Hello, world!",
    "aaa  bb",
    "ñé€日本",
    "line one\nline two\n\ttabbed",
    "The quick brown fox jumps over the lazy dog. Then it rests; quietly: why? Who knows!",
    "Mississippi bookkeeper committee",
    "UPPER lower MiXeD 12345 !?.,;:",
    "ab\ncd\te",
]
SEEDS = range(300)


def plan(text: str, seed: int, **profile) -> tuple[KeyAction, ...]:
    return plan_typing_actions(text_units(text), KeyboardProfile(**profile),
                               random.Random(seed))


def replay(actions: tuple[KeyAction, ...]) -> str:
    buffer: list[str] = []
    for a in actions:
        if not a.down:
            continue
        if a.kind == "char":
            buffer.append(a.value)
        elif a.value == "enter":
            buffer.append("\n")
        elif a.value == "tab":
            buffer.append("\t")
        elif a.value == "backspace":
            assert buffer, "backspace on an empty buffer"
            buffer.pop()
        else:
            raise AssertionError(f"unexpected key {a.value!r}")
    return "".join(buffer)


def check_structure(actions: tuple[KeyAction, ...]) -> None:
    """Sorted from t=0; each down released later; never a unit pressed while down; at
    most two units down at once; key units never overlap anything."""
    times = [a.t_ms for a in actions]
    assert times == sorted(times)
    if actions:
        assert times[0] == 0.0
    down: list[tuple[str, str]] = []
    for a in actions:
        unit = (a.kind, a.value)
        if a.down:
            assert unit not in down, f"{unit} pressed while down"
            down.append(unit)
            assert len(down) <= 2
            if any(kind == "key" for kind, _ in down):
                assert len(down) == 1, f"key unit overlaps: {down}"
        else:
            assert unit in down, f"{unit} released while up"
            down.remove(unit)
    assert not down


def downs(actions: tuple[KeyAction, ...]) -> list[KeyAction]:
    return [a for a in actions if a.down]


# --- typing: correctness ----------------------------------------------------------------


@pytest.mark.parametrize("typos", [False, True])
@pytest.mark.parametrize("wpm", [40, 90, 200])
def test_replay_gives_the_text_and_structure_holds(typos, wpm):
    for text in TEXTS:
        for seed in SEEDS:
            actions = plan(text, seed, wpm=wpm, typos=typos)
            assert replay(actions) == text, (text, seed)
            check_structure(actions)


def test_variance_zero_with_typos_still_types_the_text():
    for text in TEXTS:
        for seed in range(100):
            actions = plan(text, seed, variance=0, typos=True)
            assert replay(actions) == text
            check_structure(actions)


def test_without_typos_each_unit_goes_down_once_in_order():
    text = TEXTS[6]
    for seed in range(50):
        actions = plan(text, seed, typos=False)
        assert [(a.kind, a.value) for a in downs(actions)] == text_units(text)


def test_corrections_never_backspace_over_a_key_unit():
    text = "ab\ncd\tef\ngh"
    for seed in range(2000):
        actions = plan(text, seed, typos=True)
        typed: list[str] = []
        for a in downs(actions):
            if a.value == "backspace":
                assert typed and typed.pop() not in ("enter", "tab")
            else:
                typed.append(a.value)


def test_rollover_happens_at_high_speed():
    overlaps = 0
    for seed in range(20):
        down = 0
        for a in plan("the quick brown fox jumps over", seed, wpm=150):
            down += 1 if a.down else -1
            overlaps += down == 2
    assert overlaps > 0


# --- typing: timing ---------------------------------------------------------------------


def test_mean_interval_matches_the_wpm():
    text = "abcdefghijklmnopqrstuvwxyz" * 40
    for wpm in (40, 60, 120):
        gaps: list[float] = []
        for seed in range(5):
            t = [a.t_ms for a in downs(plan(text, seed, wpm=wpm, variance=0.3))]
            gaps.extend(b - a for a, b in itertools.pairwise(t))
        assert statistics.fmean(gaps) == pytest.approx(key_interval_ms(wpm), rel=0.03)
        assert statistics.pstdev(gaps) > 0.1 * key_interval_ms(wpm)


def test_variance_zero_is_regular():
    text = "Hello, world. Another line!\nand\ttab"
    interval = key_interval_ms(75)
    actions = plan(text, 4, wpm=75, variance=0)
    for i, a in enumerate(downs(actions)):
        assert a.t_ms == pytest.approx(i * interval)
    assert plan(text, 4, wpm=75, variance=0) == plan(text, 5, wpm=75, variance=0)
    for a in actions:
        if not a.down:
            assert a.t_ms - max(d.t_ms for d in downs(actions) if d.t_ms <= a.t_ms
                                and (d.kind, d.value) == (a.kind, a.value)) <= HOLD_MEDIAN_MS


def test_pauses_after_punctuation_and_lines_are_longer():
    text = "ab. cd, ef; gh: ij! kl? mn\nop\tqr st uv wx"
    after: dict[str, list[float]] = {"letter": [], "space": [], "punct": [], "line": []}
    for seed in range(300):
        d = downs(plan(text, seed))
        for prev, nxt in itertools.pairwise(d):
            gap = nxt.t_ms - prev.t_ms
            if prev.kind == "key":
                after["line"].append(gap)
            elif prev.value in ".,;:!?":
                after["punct"].append(gap)
            elif prev.value == " ":
                after["space"].append(gap)
            else:
                after["letter"].append(gap)
    mean = {k: statistics.fmean(v) for k, v in after.items()}
    assert mean["letter"] < mean["space"] < mean["punct"] < mean["line"]
    assert mean["punct"] > mean["letter"] + 100


def test_typos_happen_at_roughly_the_documented_rate_and_only_when_on():
    text = "the quick brown fox jumps over the lazy dog " * 10
    letters = sum(c.isalpha() for c in text)
    typos = 0
    seeds = range(60)
    for seed in seeds:
        d = downs(plan(text, seed, typos=True))
        # Every correction starts with a run of backspaces: count the runs.
        typos += sum(1 for a, b in itertools.pairwise(d)
                     if b.value == "backspace" and a.value != "backspace")
        assert not any(a.value == "backspace" for a in downs(plan(text, seed)))
    rate = typos / (letters * len(seeds))
    assert rate == pytest.approx(TYPO_PROBABILITY, rel=0.25)


def test_typos_hit_a_neighbour_and_keep_the_case():
    text = "AbCdEfGhIj" * 20
    seen = 0
    for seed in range(100):
        typed: list[str] = []
        for a in downs(plan(text, seed, typos=True)):
            if a.value == "backspace":
                typed.pop()
                continue
            intended = text[len(typed)]
            if a.value != intended:
                assert a.value.lower() in QWERTY_NEIGHBOURS[intended.lower()]
                assert a.value.isupper() == intended.isupper()
                seen += 1
            typed.append(a.value)
    assert seen > 10


def test_typing_is_deterministic_and_seeds_differ():
    text = TEXTS[6]
    assert plan(text, 7, typos=True) == plan(text, 7, typos=True)
    assert plan(text, 7) != plan(text, 8)


def test_empty_text_has_no_actions():
    assert plan("", 1) == ()


# --- combinations -----------------------------------------------------------------------


def test_combo_order_and_human_gaps():
    keys = ("ctrl", "shift", "s")
    for seed in range(200):
        actions = plan_combo_actions(keys, None, KeyboardProfile(), random.Random(seed))
        assert [(a.value, a.down) for a in actions] == [
            ("ctrl", True), ("shift", True), ("s", True),
            ("s", False), ("shift", False), ("ctrl", False)]
        t = [a.t_ms for a in actions]
        assert t[0] == 0.0
        ranges = [COMBO_DOWN_GAP_MS, COMBO_DOWN_GAP_MS, COMBO_HOLD_MS, COMBO_UP_GAP_MS,
                  COMBO_UP_GAP_MS]
        for (lo, hi), a, b in zip(ranges, t, t[1:], strict=False):
            assert lo - 1e-9 <= b - a <= hi + 1e-9


def test_combo_hold_is_exact():
    for hold in (0.0, 37.5, 1500.0):
        actions = plan_combo_actions(("alt", "f4"), hold, KeyboardProfile(),
                                     random.Random(3))
        assert actions[2].t_ms - actions[1].t_ms == pytest.approx(hold, abs=1e-9)
        assert [a.t_ms for a in actions] == sorted(a.t_ms for a in actions)


def test_single_key_combo_and_determinism():
    a = plan_combo_actions(("escape",), None, KeyboardProfile(), random.Random(1))
    assert [(x.value, x.down) for x in a] == [("escape", True), ("escape", False)]
    assert a == plan_combo_actions(("escape",), None, KeyboardProfile(), random.Random(1))
    many = {plan_combo_actions(("ctrl", "c"), None, KeyboardProfile(), random.Random(s))
            for s in range(20)}
    assert len(many) > 1


# --- through planning -------------------------------------------------------------------


def test_plan_typing_dispatches_to_human():
    profile = KeyboardProfile(strategy="human", wpm=80, typos=True)
    text = "Hi there,\nfriend"
    p = plan_typing(text, profile, random.Random(11))
    assert p.strategy == "human" and p.chars == len(text_units(text)) and p.wpm == 80
    assert p == plan_typing(text, profile, random.Random(11))
    assert replay(p.actions) == text


def test_plan_combo_dispatches_to_human():
    profile = KeyboardProfile(strategy="human")
    p = plan_combo("ctrl+shift+s", 0.25, profile, random.Random(2))
    assert p.strategy == "human" and p.chars == 3
    assert p.actions[3].t_ms - p.actions[2].t_ms == pytest.approx(250.0, abs=1e-9)
    assert p == plan_combo("ctrl+shift+s", 0.25, profile, random.Random(2))
