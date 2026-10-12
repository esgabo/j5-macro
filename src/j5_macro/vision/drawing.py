"""Saving captures as PNG, optionally outlining a rectangle (e.g. the best match)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from j5_macro.backends.base import Rect
from j5_macro.vision.api import Frame

HIGHLIGHT_COLOR = (0, 0, 255)  # BGR red
HIGHLIGHT_THICKNESS = 2


def save_png(frame: Frame, path: Path, highlight: Rect | None = None) -> None:
    """:data:`j5_macro.vision.api.ImageSaver`: writes ``frame`` to ``path`` as PNG,
    creating parent directories. ``highlight`` (desktop coordinates) is outlined with a
    2-pixel red line along the inside of its edges, so it stays visible when it touches
    the edges of the frame; the parts outside the frame are not drawn."""
    pixels = frame.pixels
    if highlight is not None and highlight.width > 0 and highlight.height > 0:
        pixels = np.array(pixels, dtype=np.uint8, copy=True)
        left = highlight.x - frame.origin[0]
        top = highlight.y - frame.origin[1]
        right, bottom = left + highlight.width - 1, top + highlight.height - 1
        for inset in range(HIGHLIGHT_THICKNESS):
            if left + inset > right - inset or top + inset > bottom - inset:
                break
            cv2.rectangle(pixels, (left + inset, top + inset), (right - inset, bottom - inset),
                          HIGHLIGHT_COLOR, 1)
    ok, encoded = cv2.imencode(".png", pixels)
    if not ok:
        raise ValueError(f"cannot encode the capture as PNG for {path}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded.tobytes())
