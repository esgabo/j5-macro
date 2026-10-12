"""Template matching with OpenCV.

Scores are normalised cross-correlation coefficients (``cv2.TM_CCOEFF_NORMED``): the
Pearson correlation between the template and each frame window, computed on the
mean-subtracted colour channels. That makes the score insensitive to uniform brightness
and contrast changes but sensitive to the pattern itself; 1 is a perfect match.

- **Unmasked templates**: ``TM_CCOEFF_NORMED``.
- **Masked templates** (PNG with transparency): ``TM_CCOEFF_NORMED`` with the mask, so
  only opaque pixels count (both means and correlation are taken over them). It keeps
  the same meaning as the unmasked score, so one ``threshold`` works for both.
  ``TM_CCORR_NORMED`` was rejected: without mean subtraction it is a cosine similarity
  of raw intensities, which stays high (often > 0.85) on any plain bright area and makes
  the default 0.9 threshold unreliable.

Scores are reported in [0, 1]: negative correlations (inverted patterns) count as 0.
A window or template with no variation has no defined correlation; it scores 0 (OpenCV
returns NaN, or 1 for a flat unmasked template), so a flat template never matches.
"""

from __future__ import annotations

import time
from typing import Any

import cv2
import numpy as np

from j5_macro.backends.base import Rect
from j5_macro.vision.api import Frame, Match, Template

METHOD = cv2.TM_CCOEFF_NORMED
_VALID_LIMIT = 1.0 + 1e-3
"""Coefficients beyond this are numerical noise from near-zero denominators."""


def _bgr(pixels: Any) -> Any:
    if pixels.ndim == 2:
        return cv2.cvtColor(pixels, cv2.COLOR_GRAY2BGR)
    if pixels.shape[2] == 4:
        return np.ascontiguousarray(pixels[:, :, :3])
    return pixels


def scaled_template(template: Template, scale: float) -> tuple[Any, Any | None]:
    """The template's pixels and mask resized by ``scale``."""
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError(f"scale must be a positive number, got {scale}")
    pixels, mask = _bgr(template.pixels), template.mask
    if scale == 1.0:
        return pixels, mask
    width, height = template.size
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    pixels = cv2.resize(pixels, size, interpolation=interpolation)
    if mask is not None:
        mask = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
    return pixels, mask


def _is_flat(pixels: Any, mask: Any | None) -> bool:
    values = pixels.reshape(-1, pixels.shape[2]) if mask is None else pixels[mask > 0]
    return values.size == 0 or bool((values == values[0]).all())


def score_map(template: Template, frame: Frame, scale: float = 1.0) -> tuple[Any, int, int]:
    """Scores in [0, 1] for every position of the scaled template in the frame (``None``
    when it does not fit), plus the scaled template's width and height."""
    pixels, mask = scaled_template(template, scale)
    height, width = pixels.shape[:2]
    image = _bgr(frame.pixels)
    if height > image.shape[0] or width > image.shape[1]:
        return None, width, height
    if _is_flat(pixels, mask):
        rows, cols = image.shape[0] - height + 1, image.shape[1] - width + 1
        return np.zeros((rows, cols), dtype=np.float32), width, height
    if mask is None:
        scores = cv2.matchTemplate(image, pixels, METHOD)
    else:
        scores = cv2.matchTemplate(image, pixels, METHOD, mask=mask)
    scores[~np.isfinite(scores) | (np.abs(scores) > _VALID_LIMIT)] = 0.0
    np.clip(scores, 0.0, 1.0, out=scores)
    return scores, width, height


def locate(template: Template, frame: Frame, threshold: float, scale: float = 1.0) -> Match:
    """:data:`j5_macro.vision.api.Locator`: the best location of ``template`` (resized
    by ``scale``) in ``frame``. ``bbox`` is in desktop coordinates."""
    started = time.perf_counter()
    scores, width, height = score_map(template, frame, scale)
    if scores is None:
        return Match(found=False, score=0.0, bbox=None,
                     elapsed_ms=(time.perf_counter() - started) * 1000)
    _, best, _, (x, y) = cv2.minMaxLoc(scores)
    score = float(best)
    bbox = Rect(frame.origin[0] + int(x), frame.origin[1] + int(y), width, height)
    return Match(found=score >= threshold, score=score, bbox=bbox,
                 elapsed_ms=(time.perf_counter() - started) * 1000)


def locate_all(template: Template, frame: Frame, threshold: float, scale: float = 1.0, *,
               max_results: int = 100, min_distance: int = 0) -> list[Match]:
    """Every match at or above ``threshold``, best first: matches never overlap, and
    their top-left corners are at least ``min_distance`` pixels apart (Chebyshev
    distance). At most ``max_results`` are returned; each one's ``elapsed_ms`` is the
    time of the whole search."""
    started = time.perf_counter()
    scores, width, height = score_map(template, frame, scale)
    if scores is None or max_results <= 0:
        return []
    found: list[tuple[float, int, int]] = []
    while len(found) < max_results:
        _, best, _, (x, y) = cv2.minMaxLoc(scores)
        if best < threshold or best < 0.0:  # suppressed positions hold -1
            break
        found.append((float(best), int(x), int(y)))
        for reach_x, reach_y in ((width, height), (min_distance, min_distance)):
            if reach_x > 0 and reach_y > 0:
                scores[max(0, y - reach_y + 1):y + reach_y,
                       max(0, x - reach_x + 1):x + reach_x] = -1.0
    elapsed = (time.perf_counter() - started) * 1000
    ox, oy = frame.origin
    return [Match(found=True, score=score, bbox=Rect(ox + x, oy + y, width, height),
                  elapsed_ms=elapsed) for score, x, y in found]
