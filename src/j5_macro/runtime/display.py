"""The display scale of a run (docs/contract.md, "Script format and step registry":
display scale).

Two scales meet here:

- the *display scale*, the one the run acts on now: the run's ``display_scale`` when
  the client gave one, else what the backend detects at a point (per monitor), else 1;
- the *capture scale*, the one the script's templates and pixel distances were taken
  at: the script's ``capture_scale`` (1 by default); an embedded image's ``scale``
  replaces it for that template (see ``RunVision.capture_scale``).

Templates are resized, and pixel offsets multiplied, by ``display / capture``.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from j5_macro.backends.base import Backend

DEFAULT_SCALE = 1.0
PRIMARY_POINT = (0, 0)
"""Where the primary monitor is: a search of the whole desktop uses its scale."""

ScaleSource = Literal["run", "detected", "default"]


def valid_scale(value: Any) -> float | None:
    """``value`` as a scale (a finite number greater than 0), else None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def detect(backend: Backend | None, point: tuple[int, int]) -> float | None:
    """What the backend reports at ``point``; None when it cannot tell or fails."""
    if backend is None or not backend.capabilities.display_scale:
        return None
    try:
        return valid_scale(backend.display_scale(point[0], point[1]))
    except Exception:
        return None


def monitor_scales(backend: Backend | None) -> list[dict[str, Any]]:
    """``ready``/``hello``'s ``display_scales``: each monitor as ``{bbox, scale}``."""
    if backend is None or not backend.capabilities.display_scale:
        return []
    try:
        monitors = backend.display_scales()
    except Exception:
        return []
    return [{"bbox": [rect.x, rect.y, rect.width, rect.height], "scale": scale}
            for rect, scale in monitors if valid_scale(scale) is not None]


def run_display_scale(override: float | None,
                      backend: Backend | None) -> tuple[float, ScaleSource]:
    """``run_started``'s ``display_scale`` and ``display_scale_source``: the run's
    value, else the primary monitor's detected scale, else 1."""
    if override is not None:
        return override, "run"
    detected = detect(backend, PRIMARY_POINT)
    if detected is not None:
        return detected, "detected"
    return DEFAULT_SCALE, "default"


class DisplayScale:
    """The display and capture scales of one run."""

    def __init__(self, *, override: float | None = None, backend: Backend | None = None,
                 capture_scale: float | None = None) -> None:
        self.override = valid_scale(override)
        self._backend = backend
        self.capture_scale = valid_scale(capture_scale) or DEFAULT_SCALE
        """The script's ``capture_scale``, 1 by default."""

    def at(self, point: tuple[int, int] | None = None) -> float:
        """The display scale at a desktop point (the primary monitor's for None)."""
        if self.override is not None:
            return self.override
        detected = detect(self._backend, PRIMARY_POINT if point is None else point)
        return DEFAULT_SCALE if detected is None else detected

    def ratio_at(self, point: tuple[int, int] | None = None,
                 capture_scale: float | None = None) -> float:
        """``display / capture`` at ``point``: by default with the script's capture
        scale."""
        capture = capture_scale if capture_scale is not None else self.capture_scale
        return self.at(point) / capture
