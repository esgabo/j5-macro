"""Key names, characters and keysyms on X11: pure functions, testable without a display."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from j5_macro.backends.base import UnknownKey

NO_SYMBOL = 0
UNICODE_KEYSYM_OFFSET = 0x01000000
"""Keysyms for Unicode code points above Latin-1 are ``0x01000000 + codepoint``."""

_NAMED_KEYSYMS: dict[str, int] = {
    # Modifiers: always the left-hand key.
    "ctrl": 0xFFE3,  # Control_L
    "shift": 0xFFE1,  # Shift_L
    "alt": 0xFFE9,  # Alt_L
    "super": 0xFFEB,  # Super_L
    # Named keys.
    "enter": 0xFF0D,  # Return
    "tab": 0xFF09,  # Tab
    "escape": 0xFF1B,  # Escape
    "backspace": 0xFF08,  # BackSpace
    "delete": 0xFFFF,  # Delete
    "insert": 0xFF63,  # Insert
    "space": 0x0020,  # space
    "up": 0xFF52,  # Up
    "down": 0xFF54,  # Down
    "left": 0xFF51,  # Left
    "right": 0xFF53,  # Right
    "home": 0xFF50,  # Home
    "end": 0xFF57,  # End
    "pageup": 0xFF55,  # Prior
    "pagedown": 0xFF56,  # Next
    "capslock": 0xFFE5,  # Caps_Lock
    "menu": 0xFF67,  # Menu
    "printscreen": 0xFF61,  # Print
    **{f"f{n}": 0xFFBE + n - 1 for n in range(1, 25)},  # F1 = 0xFFBE ... F24
}

_CONTROL_CHAR_KEYSYMS: dict[str, int] = {
    "\n": 0xFF0D,  # Return
    "\r": 0xFF0D,  # Return
    "\t": 0xFF09,  # Tab
    "\b": 0xFF08,  # BackSpace
}

NUM_LOCK_KEYSYM = 0xFF7F
SCROLL_LOCK_KEYSYM = 0xFF14


def key_keysym(key: str) -> int:
    """The keysym of a physical key named as in the contract (``ctrl``, ``f5``, ``a``, ``/``).

    Printable ASCII characters map to their US-layout keysym, which equals the code point.
    """
    keysym = _NAMED_KEYSYMS.get(key)
    if keysym is not None:
        return keysym
    if len(key) == 1 and key.isascii() and key.isprintable():
        return ord(key)
    raise UnknownKey(f"unknown key {key!r}")


def char_keysym(char: str) -> int:
    """The keysym that types ``char``: Latin-1 keysyms equal the code point, the rest are
    Unicode keysyms. A few control characters map to their keys (newline to Return)."""
    if len(char) != 1:
        raise UnknownKey(f"not a single character: {char!r}")
    keysym = _CONTROL_CHAR_KEYSYMS.get(char)
    if keysym is not None:
        return keysym
    code = ord(char)
    if unicodedata.category(char) == "Cc":
        raise UnknownKey(f"cannot type control character {char!r}")
    if 0x20 <= code <= 0x7E or 0xA0 <= code <= 0xFF:
        return code
    if 0xD800 <= code <= 0xDFFF:
        raise UnknownKey(f"cannot type a lone surrogate {char!r}")
    return UNICODE_KEYSYM_OFFSET + code


Mapping = Sequence[Sequence[int]]
"""A core keyboard mapping: row ``i`` holds the keysyms of keycode ``min_keycode + i``."""


def keycode_at_level0(mapping: Mapping, min_keycode: int, keysym: int) -> int | None:
    """The lowest keycode whose first keysym (no modifiers, first group) is ``keysym``.

    X treats a group holding a single alphabetic keysym ``K`` as ``lower(K) upper(K)``,
    so an uppercase letter alone in its group types the lowercase one and does not count.
    """
    for index, row in enumerate(mapping):
        if not row or row[0] != keysym:
            continue
        second = row[1] if len(row) > 1 else NO_SYMBOL
        if second == NO_SYMBOL and _is_upper(keysym):
            continue
        return min_keycode + index
    return None


def _is_upper(keysym: int) -> bool:
    if keysym < 0x100:
        code = keysym
    elif keysym >= UNICODE_KEYSYM_OFFSET:
        code = keysym - UNICODE_KEYSYM_OFFSET
    else:
        return False
    if code > 0x10FFFF:
        return False
    char = chr(code)
    return char.isupper() and char.lower() != char


def keycode_anywhere(mapping: Mapping, min_keycode: int, keysym: int) -> int | None:
    """The keycode that carries ``keysym`` at the lowest column (any shift level or group)."""
    best: tuple[int, int] | None = None
    for index, row in enumerate(mapping):
        for column, value in enumerate(row):
            if value == keysym:
                if best is None or column < best[0]:
                    best = (column, min_keycode + index)
                break
    return None if best is None else best[1]


def spare_keycodes(mapping: Mapping, min_keycode: int) -> list[int]:
    """Keycodes without any keysym, highest first (the least likely to be real keys)."""
    spare = [min_keycode + i for i, row in enumerate(mapping) if not any(row)]
    return sorted(spare, reverse=True)


def remap_row(keysym: int, width: int) -> list[int]:
    """The row to install on a spare keycode so that it types ``keysym`` at every shift
    level: with ``keysym`` alone, X would treat an uppercase letter as ``lower upper``."""
    row = [keysym, keysym]
    return (row + [NO_SYMBOL] * width)[: max(width, 2)]
