"""Screen capture with mss.

``MssCapturer.grab(region)`` captures a rectangle of the virtual desktop (every screen;
coordinates may be negative on multi-monitor setups) as a BGR :class:`Frame` whose
``origin`` is the desktop position of its top-left pixel. Regions are clipped to the
virtual desktop; a region with nothing left after clipping is a ``ValueError``.

mss instances are not shareable between threads (X11 connections and Windows device
contexts belong to the thread that created them), so each thread lazily gets its own.

On Linux, mss 10's default XCB backend refuses some X servers that it could capture,
such as KasmVNC's Xvnc, which lists the root window's depth twice; mss's Xlib backend
captures them, so it is the fallback when the default backend cannot start.
"""

from __future__ import annotations

import contextlib
import sys
import threading
from typing import Any

import mss
import numpy as np
from mss.exception import ScreenShotError

from j5_macro.backends.base import Rect
from j5_macro.vision.api import Frame


def _new_mss() -> Any:
    factory = getattr(mss, "MSS", None)  # mss >= 10; mss.mss() is deprecated there
    if factory is None:  # pragma: no cover - mss 9
        return mss.mss()
    try:
        return factory()
    except ScreenShotError:
        if not sys.platform.startswith("linux"):
            raise
        with contextlib.suppress(Exception):
            return factory(backend="xlib")
        raise


def clip_region(region: Rect, bounds: Rect) -> Rect:
    """``region`` clipped to ``bounds``; ``ValueError`` when nothing remains."""
    if region.width <= 0 or region.height <= 0:
        raise ValueError(f"capture region must have a positive size, got {region}")
    left = max(region.x, bounds.x)
    top = max(region.y, bounds.y)
    right = min(region.x + region.width, bounds.x + bounds.width)
    bottom = min(region.y + region.height, bounds.y + bounds.height)
    if right <= left or bottom <= top:
        raise ValueError(f"capture region {region} is outside the virtual desktop {bounds}")
    return Rect(left, top, right - left, bottom - top)


class MssCapturer:
    """:class:`j5_macro.vision.api.Capturer` backed by mss. Thread-safe."""

    def __init__(self) -> None:
        self._local = threading.local()
        self._lock = threading.Lock()
        self._instances: list[Any] = []
        self._closed = False

    def _mss(self) -> Any:
        instance = getattr(self._local, "mss", None)
        if instance is not None:
            return instance
        with self._lock:
            if self._closed:
                raise RuntimeError("the screen capturer is closed")
            try:
                instance = _new_mss()
            except Exception as exc:
                raise RuntimeError(f"cannot open the screen for capture: {exc}") from exc
            self._instances.append(instance)
        self._local.mss = instance
        return instance

    def desktop(self) -> Rect:
        """The virtual desktop: the bounding box of every screen."""
        monitor = self._mss().monitors[0]
        return Rect(int(monitor["left"]), int(monitor["top"]),
                    int(monitor["width"]), int(monitor["height"]))

    def grab(self, region: Rect | None = None) -> Frame:
        if self._closed:
            raise RuntimeError("the screen capturer is closed")
        bounds = self.desktop()
        area = bounds if region is None else clip_region(region, bounds)
        try:
            shot = self._mss().grab({"left": area.x, "top": area.y,
                                     "width": area.width, "height": area.height})
        except Exception as exc:
            raise RuntimeError(f"screen capture of {area} failed: {exc}") from exc
        width, height = shot.size
        bgra = np.frombuffer(shot.raw, dtype=np.uint8).reshape(height, width, 4)
        return Frame(np.ascontiguousarray(bgra[:, :, :3]), (area.x, area.y))

    def close(self) -> None:
        """Release every thread's capture handle; later grabs raise ``RuntimeError``."""
        with self._lock:
            self._closed = True
            instances, self._instances = self._instances, []
        for instance in instances:  # best effort: some belong to other threads
            with contextlib.suppress(Exception):
                instance.close()
