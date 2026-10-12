"""Choosing the input backend for the current session.

``J5_BACKEND`` overrides the choice: ``auto`` (default), ``x11``, ``windows``, ``fake``
(records input, touches nothing) or ``none``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass

from j5_macro.backends.base import Backend

BACKEND_ENV = "J5_BACKEND"


@dataclass(frozen=True, slots=True)
class BackendChoice:
    """The selected backend, or None with the reason no backend is available."""

    name: str | None
    backend: Backend | None
    reason: str


def select_backend(env: Mapping[str, str] | None = None,
                   platform: str | None = None) -> BackendChoice:
    """Pick a backend from ``J5_BACKEND``, the platform and the session type."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    wanted = env.get(BACKEND_ENV, "auto").strip().lower() or "auto"
    if wanted == "none":
        return BackendChoice(None, None, f"disabled by {BACKEND_ENV}=none")
    if wanted == "fake":
        from j5_macro.backends.fake import FakeBackend

        return BackendChoice("fake", FakeBackend(), f"selected by {BACKEND_ENV}=fake")
    if wanted not in ("auto", "x11", "windows"):
        return BackendChoice(None, None, f"unknown {BACKEND_ENV} value {wanted!r}")
    # Real backends are added by milestone M2 (X11, #13/#14) and M6 (Windows).
    return BackendChoice(None, None, "no input backend is available yet")
