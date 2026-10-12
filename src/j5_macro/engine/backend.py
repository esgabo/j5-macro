"""The engine's input backend: reporting it, the emergency stop key and closing it.

docs/contract.md, "Mouse and keyboard masters" (Safety) and "Transport and message
format" (Handshake). The engine selects one backend at startup (``select_backend``),
reports it in ``ready``, ``hello`` and ``run_started``, hands it to every run through
``RunOptions.backend``, watches the emergency stop key with it, and closes it when it
shuts down.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from j5_macro.backends.base import MODIFIER_KEYS, NAMED_KEYS, Backend
from j5_macro.backends.select import BackendChoice

STOP_KEY_ENV = "J5_STOP_KEY"
DEFAULT_STOP_KEY = "ctrl+alt+shift+q"
INPUT_STRATEGIES = ("direct", "linear", "human")
"""Strategies offered when a backend exists (``human`` plans like ``linear`` until M5)."""

KEY_ALIASES = {
    "control": "ctrl", "ctl": "ctrl", "option": "alt", "opt": "alt",
    "cmd": "super", "command": "super", "win": "super", "windows": "super", "meta": "super",
    "esc": "escape", "return": "enter", "del": "delete", "ins": "insert",
    "pgup": "pageup", "pgdn": "pagedown", "pgdown": "pagedown", "spacebar": "space",
}


def parse_combo(text: str) -> list[str]:
    """``ctrl+alt+shift+q`` → ``["ctrl", "alt", "shift", "q"]`` in backend key names
    (aliases resolved, lowercase, order kept). Raises ValueError for a bad combination.

    A small local parser so the engine does not depend on the input layer's; it accepts
    the same key names (docs/contract.md, "Mouse and keyboard masters").
    """
    parts = [part.strip().lower() for part in text.split("+")]
    if not text.strip() or any(not part for part in parts):
        raise ValueError(f"invalid key combination {text!r}")
    keys: list[str] = []
    for part in parts:
        key = KEY_ALIASES.get(part, part)
        named = key in MODIFIER_KEYS or key in NAMED_KEYS
        printable = len(key) == 1 and key.isascii() and key.isprintable() and key != " "
        if not (named or printable):
            raise ValueError(f"unknown key {part!r} in {text!r}")
        if key in keys:
            raise ValueError(f"key {key!r} appears twice in {text!r}")
        keys.append(key)
    if all(key in MODIFIER_KEYS for key in keys):
        raise ValueError(f"{text!r} has only modifiers; add a key such as q")
    return keys


def stop_key_from(option: str | None, env: Mapping[str, str] | None = None) -> str:
    """The stop key: ``--stop-key``, else ``J5_STOP_KEY``, else the default."""
    env = os.environ if env is None else env
    if option:
        return option
    return env.get(STOP_KEY_ENV, "").strip() or DEFAULT_STOP_KEY


class EngineBackend:
    """The selected backend (or why there is none) and the emergency stop watch."""

    def __init__(self, choice: BackendChoice, stop_key: str = DEFAULT_STOP_KEY) -> None:
        self.choice = choice
        self.stop_key = stop_key
        self._unwatch: Callable[[], None] | None = None

    @property
    def backend(self) -> Backend | None:
        return self.choice.backend

    @property
    def name(self) -> str | None:
        if self.choice.backend is None:
            return None
        return self.choice.name or self.choice.backend.name

    @property
    def reason(self) -> str:
        return self.choice.reason

    @property
    def input_strategies(self) -> list[str]:
        return list(INPUT_STRATEGIES) if self.backend is not None else []

    def watch_stop_key(self, on_press: Callable[[], None]) -> str:
        """Start watching the stop key; returns a message saying what happened (for a
        ``log``). Never raises: a missing capability or a failure only disables it."""
        backend = self.backend
        if backend is None:
            return "no emergency stop key: there is no input backend"
        if not backend.capabilities.hotkey:
            return (f"no emergency stop key: the {self.name} backend cannot watch global "
                    "hotkeys; push the cursor into a screen corner to stop a run")
        try:
            keys = parse_combo(self.stop_key)
            self._unwatch = backend.watch_hotkey(keys, on_press)
        except Exception as exc:
            return f"no emergency stop key: cannot watch {self.stop_key!r}: {exc}"
        return f"emergency stop key: {'+'.join(keys)}"

    def close(self) -> list[str]:
        """Stop watching and close the backend; returns problems to log."""
        problems: list[str] = []
        unwatch, self._unwatch = self._unwatch, None
        if unwatch is not None:
            try:
                unwatch()
            except Exception as exc:
                problems.append(f"cannot stop watching the emergency stop key: {exc}")
        backend = self.backend
        if backend is not None:
            try:
                backend.close()
            except Exception as exc:
                problems.append(f"cannot close the {self.name} backend: {exc}")
        return problems
