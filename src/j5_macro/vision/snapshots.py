"""Turning screen captures into templates and PNG files (docs/contract.md, "Authoring
tools": capturing).

Pure functions over pixels (``numpy`` arrays, BGR or BGRA, as OpenCV uses): cropping a
rectangle out of a frame, the "transparent background" mode, the flat-colour check and
PNG encoding. They need the ``vision`` extra.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from j5_macro.backends.base import Rect
from j5_macro.vision.api import Frame


def intersect(a: Rect, b: Rect) -> Rect | None:
    """The part of ``a`` inside ``b``; None when they do not overlap."""
    left, top = max(a.x, b.x), max(a.y, b.y)
    right = min(a.x + a.width, b.x + b.width)
    bottom = min(a.y + a.height, b.y + b.height)
    if right <= left or bottom <= top:
        return None
    return Rect(left, top, right - left, bottom - top)


def crop(frame: Frame, rect: Rect) -> Any:
    """The pixels of ``rect`` (desktop coordinates, inside the frame), as a copy."""
    left, top = rect.x - frame.origin[0], rect.y - frame.origin[1]
    region = frame.pixels[top:top + rect.height, left:left + rect.width]
    if region.shape[0] != rect.height or region.shape[1] != rect.width:
        raise ValueError(f"{rect} is not inside the captured area {frame.rect}")
    return np.array(region, dtype=np.uint8, copy=True)


def border_color(pixels: Any) -> tuple[int, ...]:
    """The most frequent colour of the outermost rows and columns of a BGR image (the
    first one in reading order on a tie)."""
    height, width = pixels.shape[:2]
    border = np.concatenate([
        pixels[0, :], pixels[height - 1, :], pixels[:, 0], pixels[:, width - 1],
    ]).reshape(-1, pixels.shape[2])
    colours, first, counts = np.unique(border, axis=0, return_index=True,
                                       return_counts=True)
    best = max(range(len(colours)), key=lambda i: (int(counts[i]), -int(first[i])))
    return tuple(int(v) for v in colours[best])


def transparent_background(pixels: Any) -> Any:
    """A BGRA copy of a BGR image where every pixel equal to its border colour is fully
    transparent. Raises ValueError when no pixel would stay opaque."""
    colour = np.array(border_color(pixels), dtype=np.uint8)
    background = (pixels == colour).all(axis=2)
    if background.all():
        raise ValueError("every pixel has the border colour: nothing would stay opaque")
    alpha = np.where(background, 0, 255).astype(np.uint8)
    return np.dstack([pixels, alpha])


def is_flat(pixels: Any) -> bool:
    """True when every visible pixel (alpha 128 or more, for a BGRA image) has the same
    colour: such a template never matches."""
    if pixels.shape[2] == 4:
        values = pixels[:, :, :3][pixels[:, :, 3] >= 128]
    else:
        values = pixels.reshape(-1, pixels.shape[2])
    return values.size == 0 or bool((values == values[0]).all())


def encode_png(pixels: Any) -> bytes:
    """A BGR or BGRA image as PNG bytes."""
    ok, encoded = cv2.imencode(".png", pixels)
    if not ok:
        raise ValueError("cannot encode the image as PNG")
    return bytes(encoded.tobytes())
