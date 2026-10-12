"""Key names and key combinations as scripts write them.

A combination is a ``+``-separated list of parts, case-insensitive, with optional spaces
around each part: ``ctrl+shift+s``, ``Alt + F4``, ``super+e``. Every part but the last
must be a modifier (``ctrl``, ``shift``, ``alt``, ``super``); the last part is the key
being pressed and may itself be a modifier (``shift`` alone, ``ctrl+shift``).

The last part is either a named key (see ``j5_macro.backends.base``), an alias of one
(``esc``, ``return``, ``del``, ``pgup``, ``arrowup``...), or a single printable ASCII
character, which names the *physical* key that types that character on a US layout
(``ctrl+s``, ``ctrl+/``). Because ``+`` separates parts, the ``+`` key is written
``plus`` (``ctrl+plus``); likewise ``minus`` is accepted for ``-``. Characters outside
ASCII are not keys: type them with the ``type`` step instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from j5_macro.backends.base import MODIFIER_KEYS, NAMED_KEYS

ALIASES: dict[str, str] = {
    # modifiers
    "control": "ctrl", "ctl": "ctrl",
    "cmd": "super", "command": "super", "win": "super", "windows": "super", "meta": "super",
    "option": "alt", "opt": "alt",
    # named keys
    "esc": "escape",
    "return": "enter",
    "del": "delete",
    "ins": "insert",
    "bksp": "backspace",
    "spacebar": "space",
    "pgup": "pageup", "page_up": "pageup",
    "pgdn": "pagedown", "pgdown": "pagedown", "page_down": "pagedown",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "caps": "capslock", "caps_lock": "capslock",
    "prtsc": "printscreen", "prtscr": "printscreen", "print": "printscreen",
    "apps": "menu", "contextmenu": "menu",
    # characters that cannot be written literally in a combination
    "plus": "+",
    "minus": "-",
}
"""Alias → canonical key name. Canonical names map to themselves."""

_KNOWN = frozenset(MODIFIER_KEYS) | frozenset(NAMED_KEYS)


@dataclass(frozen=True, slots=True)
class Combo:
    """A parsed combination: ``modifiers`` in the order written, then ``key``."""

    modifiers: tuple[str, ...]
    key: str

    @property
    def keys(self) -> tuple[str, ...]:
        """Every key in press order."""
        return (*self.modifiers, self.key)


def normalize_key(name: str) -> str:
    """The canonical backend name for one key (alias resolved, lowercased).

    Raises ValueError for anything that is not a known key or a single printable ASCII
    character.
    """
    raw = name.strip()
    if not raw:
        raise ValueError("empty key name")
    if len(raw) == 1:
        if raw.isascii() and raw.isprintable() and raw != " ":
            return raw.lower()
        raise ValueError(
            f"{raw!r} is not a key name: only ASCII characters name physical keys; "
            "type other characters with the type step")
    lowered = raw.lower()
    if lowered in _KNOWN:
        return lowered
    if lowered in ALIASES:
        return ALIASES[lowered]
    raise ValueError(f"unknown key {raw!r}")


def parse_combo(combo: str) -> Combo:
    """Parse ``ctrl+shift+s`` into a :class:`Combo`. Raises ValueError with a message
    meant for the script author."""
    if not combo.strip():
        raise ValueError("empty key combination")
    parts = combo.split("+")
    if any(not part.strip() for part in parts):
        raise ValueError(
            f"bad key combination {combo!r}: empty part (write the + key as 'plus', "
            "e.g. 'ctrl+plus')")
    names: list[str] = []
    for part in parts:
        try:
            names.append(normalize_key(part))
        except ValueError as exc:
            raise ValueError(f"bad key combination {combo!r}: {exc}") from None
    *modifiers, key = names
    for raw, name in zip(parts, modifiers, strict=False):
        if name not in MODIFIER_KEYS:
            raise ValueError(
                f"bad key combination {combo!r}: {raw.strip()!r} is not a modifier; only "
                "the last part may be a regular key")
    if len(set(names)) != len(names):
        raise ValueError(f"bad key combination {combo!r}: a key is repeated")
    return Combo(tuple(modifiers), key)
