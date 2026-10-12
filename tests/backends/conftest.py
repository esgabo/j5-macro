"""Backend test setup: tests marked ``x11`` need python-xlib and a reachable X server
(run them under ``xvfb-run -a``); they are skipped otherwise."""

from __future__ import annotations

import functools
import os
from collections.abc import Iterator
from typing import Any

import pytest

_keepalive: list[Any] = []
"""The connection opened by the availability check stays open for the whole session:
Xvfb resets (and drops clients connecting mid-reset) whenever its last client leaves,
which would otherwise race between tests."""


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
    if not display.has_extension("XTEST"):
        display.close()
        return "the X server has no XTEST extension"
    _keepalive.append(display)
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if item.get_closest_marker("x11") is None:
            continue
        reason = x11_unavailable_reason()
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))


def pytest_unconfigure(config: pytest.Config) -> None:
    while _keepalive:
        _keepalive.pop().close()


@pytest.fixture
def x11_backend() -> Iterator[Any]:
    from j5_macro.backends.x11 import X11Backend

    backend = X11Backend(os.environ.get("DISPLAY"), remap_grace_seconds=0)
    try:
        yield backend
    finally:
        backend.close()


@pytest.fixture
def observer() -> Iterator[Any]:
    from x11_harness import Observer

    obs = Observer()
    try:
        yield obs
    finally:
        obs.close()
