"""Choosing the input backend for the current session.

``J5_BACKEND`` overrides the choice: ``auto`` (default), ``x11``, ``windows``, ``fake``
(records input, touches nothing) or ``none``.

``auto`` picks by platform and session type: on Linux, a Wayland session gets no backend
yet (even with ``DISPLAY`` set, XWayland would only reach X11 applications), and an X11
session (``DISPLAY`` set) gets the X11 backend. Selection never raises: when no backend
is available, the choice carries a human-readable reason.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass

from j5_macro.backends.base import Backend

BACKEND_ENV = "J5_BACKEND"

WAYLAND_REASON = ("Wayland is not supported yet; log in with an X11 session "
                  "(e.g. Xfce, or 'Ubuntu on Xorg')")
WINDOWS_REASON = "the Windows backend arrives in milestone M6"
XLIB_MISSING_REASON = ("the X11 backend needs python-xlib: "
                       "pip install 'j5-macro[x11]'")

_UNIX_X11_PLATFORMS = ("linux", "freebsd", "openbsd", "netbsd")


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
    if wanted == "windows":
        return BackendChoice(None, None, WINDOWS_REASON)
    if wanted == "x11":
        return _x11(env, f"selected by {BACKEND_ENV}=x11")
    if wanted != "auto":
        return BackendChoice(
            None, None,
            f"unknown {BACKEND_ENV} value {wanted!r} (use auto, x11, windows, fake or none)")
    return _auto(env, platform)


def is_wayland_session(env: Mapping[str, str]) -> bool:
    return (env.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"
            or bool(env.get("WAYLAND_DISPLAY", "").strip()))


def _auto(env: Mapping[str, str], platform: str) -> BackendChoice:
    if platform == "win32":
        return BackendChoice(None, None, WINDOWS_REASON)
    if platform == "darwin":
        return BackendChoice(None, None, "macOS is not supported")
    if not platform.startswith(_UNIX_X11_PLATFORMS):
        return BackendChoice(None, None, f"no input backend for platform {platform!r}")
    if is_wayland_session(env):
        return BackendChoice(None, None, WAYLAND_REASON)
    if not env.get("DISPLAY", "").strip():
        return BackendChoice(None, None, "no graphical session: DISPLAY is not set")
    return _x11(env, "X11 session")


def _x11(env: Mapping[str, str], why: str) -> BackendChoice:
    display_name = env.get("DISPLAY", "").strip()
    if not display_name:
        return BackendChoice(None, None, "the X11 backend needs DISPLAY to be set")
    try:
        backend = _open_x11(display_name)
    except ImportError as exc:
        if (exc.name or "").partition(".")[0] == "Xlib":
            return BackendChoice(None, None, XLIB_MISSING_REASON)
        return BackendChoice(None, None, f"cannot load the X11 backend: {exc}")
    except Exception as exc:  # any connection failure becomes a reason, never a crash
        detail = str(exc).strip() or type(exc).__name__
        return BackendChoice(None, None,
                             f"cannot connect to the X display {display_name!r}: {detail}")
    return BackendChoice("x11", backend, f"{why} (DISPLAY={display_name})")


def _open_x11(display_name: str) -> Backend:
    """Import and open the X11 backend; a separate function so tests can replace it."""
    from j5_macro.backends.x11 import X11Backend

    return X11Backend(display_name)
