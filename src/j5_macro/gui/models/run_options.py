"""How a run is started from the GUI: its kind, its options and the ``run`` payload.

The four kinds map to ``run``'s ``mode`` (docs/contract.md, "Commands"):

| Kind | ``mode`` | Pauses at the first step | Sends input |
| --- | --- | --- | --- |
| ``dry`` | ``dry`` | no | no |
| ``normal`` | ``normal`` | no | yes |
| ``debug`` | ``step`` | yes | yes |
| ``debug_dry`` | ``dry`` | yes, with a breakpoint on the first step | no |

``dry`` mode cannot start paused, so a dry debug run adds a breakpoint on the first step
for the start of the run and removes it at the first pause (as the DAP adapter does).
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from j5_macro.engine.persistence import APP_DIR_NAME, RUNS_DIR_ENV, platform_state_dir

CAPTURES = ("on_failure", "all")


@dataclass(frozen=True, slots=True)
class RunKind:
    key: str
    mode: str
    """``run``'s ``mode``."""
    label: str
    sends_input: bool
    stop_on_entry: bool = False

    @property
    def debugging(self) -> bool:
        return self.stop_on_entry


KINDS: dict[str, RunKind] = {
    "dry": RunKind("dry", "dry", "Dry run", sends_input=False),
    "normal": RunKind("normal", "normal", "Run", sends_input=True),
    "debug": RunKind("debug", "step", "Debug", sends_input=True, stop_on_entry=True),
    "debug_dry": RunKind("debug_dry", "dry", "Debug a dry run", sends_input=False,
                         stop_on_entry=True),
}
"""Every kind, in the order the run dialog lists them."""

NO_INPUT_BACKENDS = frozenset({"fake", "none"})
"""Backends that never move the real mouse or keyboard (no confirmation needed)."""


def run_kind(key: str) -> RunKind:
    """The kind named ``key`` (also accepts a ``mode``: ``step`` is ``debug``)."""
    if key == "step":
        return KINDS["debug"]
    try:
        return KINDS[key]
    except KeyError:
        raise ValueError(f"unknown run kind {key!r}") from None


@dataclass(frozen=True, slots=True)
class RunOptions:
    """``run``'s options besides ``params``; the defaults are the engine's."""

    strict: bool = False
    """Dry runs only (``strict``)."""
    allow_exec: bool = False
    seed: int | None = None
    """None: the engine picks one (reported in ``run_started``)."""
    captures: str = "on_failure"
    display_scale: float | None = None
    """None: detected by the engine."""

    def payload(self, kind: RunKind) -> dict[str, Any]:
        """The fields to add to ``run``'s payload (defaults left out)."""
        payload: dict[str, Any] = {}
        if self.strict and kind.mode == "dry":
            payload["strict"] = True
        if self.allow_exec:
            payload["allow_exec"] = True
        if self.seed is not None:
            payload["seed"] = self.seed
        if self.captures != "on_failure":
            payload["captures"] = self.captures
        if self.display_scale is not None:
            payload["display_scale"] = self.display_scale
        return payload

    def after_run(self) -> RunOptions:
        """What the next run starts with: everything but ``allow_exec``, which is asked
        for every run since ``exec`` starts programs."""
        return replace(self, allow_exec=False)


def parse_seed(text: str) -> int | None:
    """A seed field: '' for none, else a whole number."""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return int(stripped)
    except ValueError:
        raise ValueError("the seed must be a whole number") from None


def parse_display_scale(text: str) -> float | None:
    """A display scale field: '' to detect it, else a positive number (``1.5`` or
    ``150%``)."""
    stripped = text.strip()
    if not stripped:
        return None
    percent = stripped.endswith("%")
    try:
        value = float(stripped.rstrip("%").strip())
    except ValueError:
        raise ValueError("the display scale must be a positive number, such as 1.5 or "
                         "150%") from None
    if percent:
        value /= 100
    if not (value > 0 and math.isfinite(value)):
        raise ValueError("the display scale must be a positive number")
    return value


def needs_confirmation(kind: RunKind, backend: str | None, confirm: bool) -> bool:
    """Whether to ask before starting: a run that sends real input (not with the fake
    backend, nor without one), unless the user turned the question off."""
    return confirm and kind.sends_input and backend is not None \
        and backend not in NO_INPUT_BACKENDS


def confirmation_text(stop_key: str | None) -> str:
    """The question before a run that sends real input."""
    if stop_key:
        stop = (f"To stop it at any time press <b>{stop_key}</b> (the emergency stop key) "
                "or push the mouse into a corner of the screen.")
    else:
        stop = ("<b>The engine watches no emergency stop key</b>; push the mouse into a "
                "corner of the screen to stop it.")
    return ("This run moves the mouse and types on this computer. Do not touch the "
            f"mouse or the keyboard while it runs.<br><br>{stop}")


def engine_runs_dir(cmd: Sequence[str] | None, env: Mapping[str, str] | None,
                    cwd: str | None = None) -> Path:
    """The runs directory of an engine started with ``cmd`` (its ``--runs-dir``) and
    ``env`` (``$J5_RUNS_DIR``; the GUI's own environment when None), else the default,
    resolved as the engine does. Captures in events are relative to
    ``<runs dir>/<run_id>``."""
    args = list(cmd or ())
    option: str | None = None
    for index, arg in enumerate(args):
        if arg == "--runs-dir" and index + 1 < len(args):
            option = args[index + 1]
        elif arg.startswith("--runs-dir="):
            option = arg.split("=", 1)[1]
    if not option:
        option = (env if env is not None else os.environ).get(RUNS_DIR_ENV) or None
    path = Path(option) if option else platform_state_dir() / APP_DIR_NAME / "runs"
    if not path.is_absolute():
        path = Path(cwd or os.getcwd()) / path
    return path
