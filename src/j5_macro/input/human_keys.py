"""The ``human`` keyboard strategy (docs/contract.md, "Strategies"; issue #31).

Pure functions of a seeded ``random.Random`` (the plan's own generator, created by
:mod:`j5_macro.input.planning`): the same arguments and seed always give the same
actions, and nothing here touches the system.

Typing happens in two passes. First the units of the text become *strokes*: the units
themselves plus, with ``typos`` on, wrong neighbouring letters and the ``backspace``
presses and retyped characters that correct them. Then the strokes are timed:

- **Intervals** between key-downs are log-normal with ``sigma = variance``. The median is
  ``base / exp(sigma² / 2)`` so the *mean* matches ``base = 60000 / (wpm * 5)``, clamped
  to ``INTERVAL_CLAMP`` times the base. Pauses come on top of that: after spaces, after
  punctuation, after ``enter``/``tab``, rare "thinking" pauses between words, and a
  reaction pause before correcting a typo.
- **Hold times** are log-normal around ``HOLD_MEDIAN_MS``, independent of the interval,
  so at high speeds a key goes down before the previous one is up (rollover). Overlap is
  limited: at most two units are down at once, ``key`` units (``enter``, ``tab``,
  ``backspace``) never overlap anything, and a unit is released before the same unit is
  pressed again.

With ``variance: 0`` timing is fully regular: every interval is exactly the base (as
``linear``), every hold is ``HOLD_MEDIAN_MS`` (capped by the overlap rules), there are no
pauses, and a typo's reaction pause is the middle of ``TYPO_REACTION_MS``. Typos depend
only on ``typos``.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Literal

from j5_macro.input.api import KeyAction, KeyboardProfile

Unit = tuple[Literal["key", "char"], str]
"""One keystroke of the text: ``("char", "a")`` or ``("key", "enter")``."""

# --- tunable constants ------------------------------------------------------------------

CHARS_PER_WORD = 5
"""Characters per word when converting ``wpm`` to the base interval."""
INTERVAL_CLAMP = (0.35, 3.0)
"""Bounds of a sampled key-down interval, as multiples of the base interval."""
HOLD_MEDIAN_MS = 95.0
"""Median time a key stays down (also the fixed hold with ``variance: 0``)."""
HOLD_SIGMA = 0.25
"""Log-normal sigma of hold times (when ``variance`` > 0)."""
HOLD_CLAMP_MS = (50.0, 180.0)
"""Bounds of a sampled hold time, before the overlap rules shorten it."""
RELEASE_LEAD_MS = 6.0
"""How long before the next key-down a key is released when it must not overlap it
(never more than half the gap between the two key-downs)."""
SPACE_PAUSE_MS = 40.0
"""Mean extra pause after a space (uniform in 0.5x to 1.5x)."""
PUNCTUATION_PAUSE_MS = 180.0
"""Mean extra pause after one of ``PUNCTUATION`` (uniform in 0.5x to 1.5x)."""
LINE_PAUSE_MS = 350.0
"""Mean extra pause after ``enter`` or ``tab`` (uniform in 0.5x to 1.5x)."""
PUNCTUATION = frozenset(".,;:!?")
"""Characters followed by ``PUNCTUATION_PAUSE_MS``."""
THINK_PROBABILITY = 0.05
"""Chance of a "thinking" pause after a space, ``enter`` or ``tab``."""
THINK_PAUSE_MS = (250.0, 700.0)
"""Range (uniform) of a thinking pause."""
TYPO_PROBABILITY = 0.03
"""Chance, per ASCII letter of the text, of hitting a neighbouring QWERTY key instead
(``typos`` on only)."""
TYPO_NOTICE_LATE_PROBABILITY = 0.4
"""Chance a typo is noticed only after one or two more correct characters (otherwise it
is noticed at once). Never across a ``key`` unit or past the end of the text."""
TYPO_REACTION_MS = (200.0, 500.0)
"""Range (uniform) of the pause between noticing a typo and the first ``backspace``."""
COMBO_DOWN_GAP_MS = (40.0, 90.0)
"""Bounds of the log-normal gap between the key-downs of a combination."""
COMBO_HOLD_MS = (80.0, 140.0)
"""Bounds of the log-normal hold of a combination's last key (without ``hold``)."""
COMBO_UP_GAP_MS = (15.0, 50.0)
"""Bounds of the log-normal gap between the key-ups of a combination."""
COMBO_SIGMA = 0.15
"""Log-normal sigma of combination timings (when ``variance`` > 0); the median is the
geometric middle of each range."""

_QWERTY_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")


def _qwerty_neighbours() -> dict[str, str]:
    """Adjacent letters on a staggered QWERTY layout (same row, and the row above and
    below, shifted half a key to the right per row)."""
    where = {c: (row, col) for row, keys in enumerate(_QWERTY_ROWS)
             for col, c in enumerate(keys)}
    result: dict[str, str] = {}
    for c, (row, col) in where.items():
        near = [(row, col - 1), (row, col + 1), (row - 1, col), (row - 1, col + 1),
                (row + 1, col - 1), (row + 1, col)]
        result[c] = "".join(_QWERTY_ROWS[r][k] for r, k in near
                            if 0 <= r < len(_QWERTY_ROWS) and 0 <= k < len(_QWERTY_ROWS[r]))
    return result


QWERTY_NEIGHBOURS = _qwerty_neighbours()
"""Lowercase ASCII letter -> the letters next to it on a QWERTY keyboard."""

_BACKSPACE: Unit = ("key", "backspace")


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return min(max(value, bounds[0]), bounds[1])


def _lognormal(r: random.Random, median: float, sigma: float,
               bounds: tuple[float, float]) -> float:
    if sigma <= 0:
        return _clamp(median, bounds)
    return _clamp(median * math.exp(r.gauss(0.0, sigma)), bounds)


def _uniform(r: random.Random, bounds: tuple[float, float], regular: bool) -> float:
    return (bounds[0] + bounds[1]) / 2 if regular else r.uniform(*bounds)


# --- typing -----------------------------------------------------------------------------


def _strokes(units: Sequence[Unit], typos: bool,
             r: random.Random) -> list[tuple[Unit, bool]]:
    """The units to press, in order, each flagged when a typo reaction pause comes
    before it."""
    strokes: list[tuple[Unit, bool]] = []
    i = 0
    while i < len(units):
        kind, value = units[i]
        lower = value.lower()
        if (typos and kind == "char" and lower in QWERTY_NEIGHBOURS
                and r.random() < TYPO_PROBABILITY):
            wrong = r.choice(QWERTY_NEIGHBOURS[lower])
            if value.isupper():
                wrong = wrong.upper()
            late = r.randint(1, 2) if r.random() < TYPO_NOTICE_LATE_PROBABILITY else 0
            # Only correct characters may follow before noticing: no key units, and
            # nothing past the end of the text.
            following = 0
            while (following < late and i + 1 + following < len(units)
                   and units[i + 1 + following][0] == "char"):
                following += 1
            strokes.append((("char", wrong), False))
            strokes.extend((unit, False) for unit in units[i + 1:i + 1 + following])
            for k in range(following + 1):
                strokes.append((_BACKSPACE, k == 0))
            strokes.extend((unit, False) for unit in units[i:i + 1 + following])
            i += following + 1
        else:
            strokes.append((units[i], False))
            i += 1
    return strokes


def _pause_after(unit: Unit, r: random.Random) -> float:
    kind, value = unit
    if kind == "key" and value in ("enter", "tab"):
        pause = LINE_PAUSE_MS * r.uniform(0.5, 1.5)
    elif kind == "char" and value in PUNCTUATION:
        return PUNCTUATION_PAUSE_MS * r.uniform(0.5, 1.5)
    elif kind == "char" and value == " ":
        pause = SPACE_PAUSE_MS * r.uniform(0.5, 1.5)
    else:
        return 0.0
    if r.random() < THINK_PROBABILITY:
        pause += r.uniform(*THINK_PAUSE_MS)
    return pause


def plan_typing_actions(units: Sequence[Unit], profile: KeyboardProfile,
                        r: random.Random) -> tuple[KeyAction, ...]:
    """Key events typing ``units`` (from ``planning.text_units``), sorted by ``t_ms``,
    the first at t=0 (see the module docstring for the timing model).

    Invariants: every stroke goes down once and up later; a unit is never pressed while
    the same ``(kind, value)`` is still down; at most two units are down at once and
    ``key`` units never overlap anything; replaying the events (a ``char`` down types
    it, a ``backspace`` down deletes the previous character, ``enter``/``tab`` type
    ``\\n``/``\\t``) yields exactly the text, typos included.
    """
    strokes = _strokes(units, profile.typos, r)
    if not strokes:
        return ()
    base = 60_000.0 / (profile.wpm * CHARS_PER_WORD)
    sigma = max(0.0, profile.variance)
    regular = sigma == 0
    median = base / math.exp(sigma * sigma / 2)
    bounds = (base * INTERVAL_CLAMP[0], base * INTERVAL_CLAMP[1])

    downs = [0.0]
    for k in range(1, len(strokes)):
        gap = _lognormal(r, median, sigma, bounds)
        if not regular:
            gap += _pause_after(strokes[k - 1][0], r)
        if strokes[k][1]:
            gap += _uniform(r, TYPO_REACTION_MS, regular)
        downs.append(downs[-1] + gap)

    actions: list[KeyAction] = []
    for k, ((kind, value), _) in enumerate(strokes):
        hold = _lognormal(r, HOLD_MEDIAN_MS, 0.0 if regular else HOLD_SIGMA, HOLD_CLAMP_MS)
        up = downs[k] + hold
        if k + 1 < len(strokes):
            nxt = strokes[k + 1][0]
            if kind == "key" or nxt[0] == "key" or nxt == (kind, value):
                up = min(up, _release_before(downs[k], downs[k + 1]))
        if k + 2 < len(strokes):
            up = min(up, _release_before(downs[k], downs[k + 2]))
        actions.append(KeyAction(downs[k], kind, value, True))
        actions.append(KeyAction(up, kind, value, False))
    actions.sort(key=lambda a: a.t_ms)
    return tuple(actions)


def _release_before(down: float, next_down: float) -> float:
    """Latest release of a key pressed at ``down`` that must be up before
    ``next_down``: ``RELEASE_LEAD_MS`` earlier, but never in the first half of the gap."""
    return next_down - min(RELEASE_LEAD_MS, (next_down - down) / 2)


# --- combinations -----------------------------------------------------------------------


def _combo_time(r: random.Random, bounds: tuple[float, float], regular: bool) -> float:
    return _lognormal(r, math.sqrt(bounds[0] * bounds[1]), 0.0 if regular else COMBO_SIGMA,
                      bounds)


def plan_combo_actions(keys: Sequence[str], hold_ms: float | None, profile: KeyboardProfile,
                       r: random.Random) -> tuple[KeyAction, ...]:
    """A combination such as ``("ctrl", "shift", "s")``: keys down in order with gaps
    in ``COMBO_DOWN_GAP_MS``, the last one held for ``COMBO_HOLD_MS`` (or exactly
    ``hold_ms`` when given), then up in reverse order with gaps in ``COMBO_UP_GAP_MS``.
    Sorted by ``t_ms`` and starting at t=0. ``variance: 0`` uses the geometric middle of
    each range."""
    if not keys:
        return ()
    regular = profile.variance <= 0
    t = 0.0
    actions: list[KeyAction] = []
    for i, key in enumerate(keys):
        if i:
            t += _combo_time(r, COMBO_DOWN_GAP_MS, regular)
        actions.append(KeyAction(t, "key", key, True))
    t += hold_ms if hold_ms is not None else _combo_time(r, COMBO_HOLD_MS, regular)
    for i, key in enumerate(reversed(keys)):
        if i:
            t += _combo_time(r, COMBO_UP_GAP_MS, regular)
        actions.append(KeyAction(t, "key", key, False))
    return tuple(actions)
