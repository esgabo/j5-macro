"""The display scale of an X11 session (docs/contract.md, "Display scale").

X11 has no per-monitor scale that applications agree on. Desktops (Xfce, GNOME, KDE)
publish the font DPI they want as ``Xft.dpi`` in the X resource database, the root
window's ``RESOURCE_MANAGER`` property (what ``xrdb -query`` prints), and toolkits
scale with it; GTK also follows ``GDK_SCALE``. The scale is therefore ``Xft.dpi / 96``,
else ``GDK_SCALE``, else 1, for every monitor.

Pure functions over the resource string and the environment, so they are tested
without a display.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

BASE_DPI = 96.0
"""The DPI of a 100 % display."""
GDK_SCALE_ENV = "GDK_SCALE"

_XFT_DPI = re.compile(r"^\s*Xft\.dpi\s*:\s*(\S+)\s*$", re.MULTILINE)


def _positive(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def xft_dpi(resources: str | None) -> float | None:
    """``Xft.dpi`` in a resource database string, None when absent or not a positive
    number. The last entry wins, as in ``xrdb``."""
    if not resources:
        return None
    values = _XFT_DPI.findall(resources)
    return _positive(values[-1]) if values else None


def display_scale_from(resources: str | None, env: Mapping[str, str]) -> float:
    """``Xft.dpi / 96``, else ``GDK_SCALE``, else 1.0."""
    dpi = xft_dpi(resources)
    if dpi is not None:
        return round(dpi / BASE_DPI, 4)
    scale = _positive(env.get(GDK_SCALE_ENV, "").strip())
    return scale if scale is not None else 1.0
