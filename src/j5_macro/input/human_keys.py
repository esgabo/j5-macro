"""The ``human`` keyboard strategy (docs/contract.md, "Strategies"; issue #31).

Pure functions of a seeded ``random.Random`` (the plan's own generator, created by
:mod:`j5_macro.input.planning`): the same arguments and seed always give the same
actions, and nothing here touches the system.

Typing: log-normal intervals around the WPM, longer pauses after spaces and punctuation,
variable hold times with slight overlap between keys, and optional typos corrected with
``backspace``.

Placeholder until #31 lands: a fixed interval from the WPM, as ``linear``.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Literal

from j5_macro.input.api import KeyAction, KeyboardProfile

Unit = tuple[Literal["key", "char"], str]
"""One keystroke of the text: ``("char", "a")`` or ``("key", "enter")``."""


def _interval_ms(wpm: float) -> float:
    return 60_000.0 / (wpm * 5)


def plan_typing_actions(units: Sequence[Unit], profile: KeyboardProfile,
                        r: random.Random) -> tuple[KeyAction, ...]:
    """Key events typing ``units`` (from ``planning.text_units``), sorted by ``t_ms``,
    the first at t=0.

    Invariants every implementation keeps: each unit goes down once and up once; a unit
    is never pressed while the same ``(kind, value)`` is still down; replaying the
    events (a ``char`` down types it, a ``backspace`` down deletes the previous
    character, ``enter``/``tab`` type ``\\n``/``\\t``) yields exactly the text.
    """
    interval = _interval_ms(profile.wpm)
    actions: list[KeyAction] = []
    for i, (kind, value) in enumerate(units):
        actions.append(KeyAction(i * interval, kind, value, True))
        actions.append(KeyAction(i * interval + interval * 0.3, kind, value, False))
    return tuple(actions)


def plan_combo_actions(keys: Sequence[str], hold_ms: float | None, profile: KeyboardProfile,
                       r: random.Random) -> tuple[KeyAction, ...]:
    """A combination such as ``("ctrl", "shift", "s")``: keys down in order, then up in
    reverse order, sorted by ``t_ms`` and starting at t=0. With ``hold_ms`` the first
    release comes exactly that long after the last key went down."""
    gap = 30.0
    actions = [KeyAction(i * gap, "key", key, True) for i, key in enumerate(keys)]
    t = actions[-1].t_ms + (hold_ms if hold_ms is not None else _interval_ms(profile.wpm) * 0.3)
    for i, key in enumerate(reversed(keys)):
        actions.append(KeyAction(t + i * gap, "key", key, False))
    return tuple(actions)
