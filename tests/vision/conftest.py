"""Vision test setup.

Tests that need OpenCV, numpy and mss call ``pytest.importorskip`` at module level, so
they are skipped when the ``vision`` extra is not installed. Tests marked ``x11`` capture
a real X screen (run them under ``xvfb-run -a``) and are skipped without one.
"""

from __future__ import annotations

import functools
import os
from typing import Any

import pytest

_keepalive: list[Any] = []
"""Keeps one connection open: Xvfb resets whenever its last client disconnects."""


@functools.cache
def x11_unavailable_reason() -> str | None:
    try:
        from Xlib import error as xerror
        from Xlib.display import Display
    except ImportError:
        return "python-xlib is not installed"
    name = os.environ.get("DISPLAY")
    if not name:
        return "DISPLAY is not set (run under xvfb-run)"
    try:
        display = Display(name)
    except (xerror.DisplayError, xerror.ConnectionClosedError, OSError) as exc:
        return f"no X server reachable at {name!r}: {exc}"
    _keepalive.append(display)
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    here = os.path.dirname(__file__)
    for item in items:
        if item.get_closest_marker("x11") is None or not str(item.path).startswith(here):
            continue
        reason = x11_unavailable_reason()
        if reason is not None:
            if os.environ.get("J5_REQUIRE_X11"):  # set in CI, where Xvfb must be there
                raise pytest.UsageError(f"J5_REQUIRE_X11 is set but {reason}")
            item.add_marker(pytest.mark.skip(reason=reason))


def pytest_unconfigure(config: pytest.Config) -> None:
    while _keepalive:
        _keepalive.pop().close()
