"""Key names, characters and virtual-key codes on Windows: pure functions, testable on any
platform.

Physical keys are sent as virtual-key codes (with their scan code, which some
applications read instead). A printable ASCII key name is the key that types that
character: the backend asks the current layout first (``VkKeyScanW``), like the X11
backend looks the character up in the keyboard mapping, and falls back to the US layout
below. Characters typed with ``char_down`` never go through a layout: they are sent as
Unicode packets (``KEYEVENTF_UNICODE``), one per UTF-16 code unit.
"""

from __future__ import annotations

import unicodedata

from j5_macro.backends.base import UnknownKey

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SPACE = 0x20

NAMED_VKS: dict[str, int] = {
    # Modifiers: always the left-hand key.
    "ctrl": 0xA2,  # VK_LCONTROL
    "shift": 0xA0,  # VK_LSHIFT
    "alt": 0xA4,  # VK_LMENU
    "super": 0x5B,  # VK_LWIN
    # Named keys.
    "enter": VK_RETURN,
    "tab": VK_TAB,
    "escape": 0x1B,
    "backspace": VK_BACK,
    "delete": 0x2E,
    "insert": 0x2D,
    "space": VK_SPACE,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,  # VK_PRIOR
    "pagedown": 0x22,  # VK_NEXT
    "capslock": 0x14,  # VK_CAPITAL
    "menu": 0x5D,  # VK_APPS
    "printscreen": 0x2C,  # VK_SNAPSHOT
    **{f"f{n}": 0x70 + n - 1 for n in range(1, 25)},  # VK_F1 = 0x70 ... VK_F24 = 0x87
}

EXTENDED_VKS = frozenset({
    0x5B, 0x5C,  # VK_LWIN, VK_RWIN
    0x5D,  # VK_APPS
    0x2E, 0x2D,  # VK_DELETE, VK_INSERT
    0x26, 0x28, 0x25, 0x27,  # arrows
    0x24, 0x23, 0x21, 0x22,  # VK_HOME, VK_END, VK_PRIOR, VK_NEXT
    0x2C,  # VK_SNAPSHOT
    0xA3, 0xA5,  # VK_RCONTROL, VK_RMENU
    0x6F,  # VK_DIVIDE
    0x90,  # VK_NUMLOCK
})
"""Keys sent with ``KEYEVENTF_EXTENDEDKEY``: without it, an arrow would arrive as the
number pad key that shares its scan code."""

_US_OEM: dict[str, int] = {
    ";": 0xBA, ":": 0xBA,  # VK_OEM_1
    "=": 0xBB, "+": 0xBB,  # VK_OEM_PLUS
    ",": 0xBC, "<": 0xBC,  # VK_OEM_COMMA
    "-": 0xBD, "_": 0xBD,  # VK_OEM_MINUS
    ".": 0xBE, ">": 0xBE,  # VK_OEM_PERIOD
    "/": 0xBF, "?": 0xBF,  # VK_OEM_2
    "`": 0xC0, "~": 0xC0,  # VK_OEM_3
    "[": 0xDB, "{": 0xDB,  # VK_OEM_4
    "\\": 0xDC, "|": 0xDC,  # VK_OEM_5
    "]": 0xDD, "}": 0xDD,  # VK_OEM_6
    "'": 0xDE, '"': 0xDE,  # VK_OEM_7
    " ": VK_SPACE,
}
_US_SHIFTED_DIGITS = ")!@#$%^&*("
"""The shifted digit row of a US keyboard: index ``n`` is shift + ``n``."""

CONTROL_CHAR_VKS: dict[str, int] = {
    "\n": VK_RETURN,
    "\r": VK_RETURN,
    "\t": VK_TAB,
    "\b": VK_BACK,
}
"""Control characters ``type`` may send, typed with their physical key."""

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
HOTKEY_MODIFIERS: dict[str, int] = {
    "alt": MOD_ALT, "ctrl": MOD_CONTROL, "shift": MOD_SHIFT, "super": MOD_WIN,
}


def is_ascii_key(key: str) -> bool:
    return len(key) == 1 and key.isascii() and key.isprintable()


def us_vk(char: str) -> int:
    """The virtual key that types the printable ASCII ``char`` on a US layout (shift
    level ignored: ``A`` and ``!`` are the ``a`` and ``1`` keys)."""
    if not is_ascii_key(char):
        raise UnknownKey(f"unknown key {char!r}")
    if char.isalpha():
        return ord(char.upper())
    if char.isdigit():
        return ord(char)
    index = _US_SHIFTED_DIGITS.find(char)
    if index >= 0:
        return ord("0") + index
    return _US_OEM[char]


def named_vk(key: str) -> int | None:
    """The virtual key of a named key (``ctrl``, ``f5``), or None for any other name."""
    return NAMED_VKS.get(key)


def check_key(key: str) -> None:
    """Raise :class:`UnknownKey` unless ``key`` is a valid physical key name."""
    if named_vk(key) is None and not is_ascii_key(key):
        raise UnknownKey(f"unknown key {key!r}")


def char_units(char: str) -> list[int]:
    """The UTF-16 code units that type ``char`` as Unicode packets (two for a character
    outside the Basic Multilingual Plane)."""
    if len(char) != 1:
        raise UnknownKey(f"not a single character: {char!r}")
    code = ord(char)
    if 0xD800 <= code <= 0xDFFF:
        raise UnknownKey(f"cannot type a lone surrogate {char!r}")
    if unicodedata.category(char) == "Cc":
        raise UnknownKey(f"cannot type control character {char!r}")
    if code < 0x10000:
        return [code]
    code -= 0x10000
    return [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]
