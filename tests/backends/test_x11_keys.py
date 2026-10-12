"""Pure keysym helpers of the X11 backend: no display needed."""

from __future__ import annotations

import pytest

from j5_macro.backends.base import MODIFIER_KEYS, NAMED_KEYS, UnknownKey
from j5_macro.backends.x11.keys import (
    char_keysym,
    key_keysym,
    keycode_anywhere,
    keycode_at_level0,
    remap_row,
    spare_keycodes,
)


def test_every_contract_key_name_has_a_keysym() -> None:
    for name in (*MODIFIER_KEYS, *NAMED_KEYS):
        assert key_keysym(name) > 0


@pytest.mark.parametrize(("key", "keysym"), [
    ("ctrl", 0xFFE3), ("shift", 0xFFE1), ("alt", 0xFFE9), ("super", 0xFFEB),
    ("enter", 0xFF0D), ("pageup", 0xFF55), ("printscreen", 0xFF61), ("f1", 0xFFBE),
    ("f12", 0xFFC9), ("f24", 0xFFD5), ("a", 0x61), ("A", 0x41), ("/", 0x2F), ("1", 0x31),
])
def test_key_keysyms(key: str, keysym: int) -> None:
    assert key_keysym(key) == keysym


@pytest.mark.parametrize("key", ["", "esc", "ñ", "ctrl+a", "F1", "\n"])
def test_unknown_keys(key: str) -> None:
    with pytest.raises(UnknownKey):
        key_keysym(key)


@pytest.mark.parametrize(("char", "keysym"), [
    ("a", 0x61), ("A", 0x41), ("!", 0x21), (" ", 0x20), ("ñ", 0xF1), ("é", 0xE9),
    (chr(0xA0), 0xA0), ("€", 0x10020AC), ("日", 0x10065E5), ("😀", 0x101F600),
    ("\n", 0xFF0D), ("\t", 0xFF09),
])
def test_char_keysyms(char: str, keysym: int) -> None:
    assert char_keysym(char) == keysym


@pytest.mark.parametrize("char", ["", "ab", "\x00", "\x1b", "\x7f", "\ud800"])
def test_untypeable_chars(char: str) -> None:
    with pytest.raises(UnknownKey):
        char_keysym(char)


MAPPING = [
    [0, 0, 0, 0],            # keycode 8: spare
    [0x61, 0x41, 0, 0],      # 9: a A
    [0x31, 0x21, 0, 0],      # 10: 1 !
    [0x41, 0, 0, 0],         # 11: a single "A" is treated by X as "a A"
    [0, 0, 0, 0],            # 12: spare
    [0xFFE3, 0, 0, 0],       # 13: Control_L
]


def test_level0_lookup() -> None:
    assert keycode_at_level0(MAPPING, 8, 0x61) == 9
    assert keycode_at_level0(MAPPING, 8, 0x31) == 10
    assert keycode_at_level0(MAPPING, 8, 0x21) is None  # only at the shift level
    assert keycode_at_level0(MAPPING, 8, 0x41) is None  # keycode 11 types "a"
    assert keycode_at_level0([[0x41, 0x41]], 8, 0x41) == 8


def test_any_level_lookup_prefers_lower_columns() -> None:
    assert keycode_anywhere(MAPPING, 8, 0x21) == 10
    assert keycode_anywhere(MAPPING, 8, 0x41) == 11  # column 0 beats keycode 9's column 1
    assert keycode_anywhere(MAPPING, 8, 0xFFE3) == 13
    assert keycode_anywhere(MAPPING, 8, 0xFFFF) is None


def test_spare_keycodes_highest_first() -> None:
    assert spare_keycodes(MAPPING, 8) == [12, 8]


def test_remap_row_sets_both_shift_levels() -> None:
    assert remap_row(0x41, 4) == [0x41, 0x41, 0, 0]
    assert remap_row(0x10020AC, 7) == [0x10020AC, 0x10020AC, 0, 0, 0, 0, 0]
    assert remap_row(0x61, 1) == [0x61, 0x61]
