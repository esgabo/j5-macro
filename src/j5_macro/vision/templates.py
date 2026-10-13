"""Template loading: PNG, JPEG or BMP files as BGR pixels plus an optional mask.

A PNG alpha channel becomes a binary mask: pixels with alpha < 128 are ignored when
matching. Files are decoded from bytes so non-ASCII paths work on Windows too, and images
embedded in a script are decoded the same way (:func:`decode_template_bytes`).
:class:`TemplateCache` keeps recently used templates, keyed by path and modification
time, so editing an image on disk is picked up by the next search.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from j5_macro.vision.api import Template

ALPHA_CUTOFF = 128
"""Pixels whose alpha is below this are transparent (ignored when matching)."""

DEFAULT_CACHE_SIZE = 64


def _to_uint8(pixels: Any) -> Any:
    if pixels.dtype == np.uint8:
        return pixels
    if pixels.dtype == np.uint16:
        return (pixels // 257).astype(np.uint8)
    raise ValueError(f"unsupported pixel type {pixels.dtype}")


def decode_template(data: bytes, path: Path) -> Template:
    """Decodes an encoded image into a :class:`Template`; ``ValueError`` if unreadable."""
    if not data:
        raise ValueError(f"{path} is empty, not an image")
    try:
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    except cv2.error as exc:
        raise ValueError(f"{path} is not a readable image: {exc}") from exc
    if image is None or image.size == 0:
        raise ValueError(f"{path} is not a readable image (expected PNG, JPEG or BMP)")
    image = _to_uint8(image)
    mask = None
    if image.ndim == 2:
        pixels = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 2:  # gray + alpha
        pixels = cv2.cvtColor(np.ascontiguousarray(image[:, :, 0]), cv2.COLOR_GRAY2BGR)
        mask = image[:, :, 1]
    elif image.shape[2] == 4:
        pixels = np.ascontiguousarray(image[:, :, :3])
        mask = image[:, :, 3]
    elif image.shape[2] == 3:
        pixels = image
    else:
        raise ValueError(f"{path} has an unsupported number of channels: {image.shape[2]}")
    if mask is not None:
        opaque = mask >= ALPHA_CUTOFF
        if not opaque.any():
            raise ValueError(f"{path} is fully transparent")
        mask = None if opaque.all() else np.where(opaque, 255, 0).astype(np.uint8)
    pixels.setflags(write=False)  # cached templates are shared
    if mask is not None:
        mask.setflags(write=False)
    return Template(path=path, pixels=pixels, mask=mask)


def decode_template_bytes(data: bytes, label: str) -> Template:
    """Decodes an image embedded in a script (:data:`j5_macro.vision.api.TemplateDecoder`);
    ``label`` (``#name``) names it in messages and becomes the template's ``path``."""
    return decode_template(data, Path(label))


def read_template(path: Path) -> Template:
    """Loads a template without caching. ``FileNotFoundError`` when ``path`` is missing,
    ``ValueError`` when it is not a readable image."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"template image not found: {path}")
    if not path.is_file():
        raise ValueError(f"{path} is not a file")
    return decode_template(path.read_bytes(), path)


class TemplateCache:
    """A thread-safe LRU :data:`j5_macro.vision.api.TemplateLoader`, keyed by path and
    modification time."""

    def __init__(self, maxsize: int = DEFAULT_CACHE_SIZE) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self.maxsize = maxsize
        self._entries: OrderedDict[str, tuple[int, int, Template]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def __call__(self, path: Path) -> Template:
        path = Path(path)
        key = os.path.abspath(path)
        try:
            stat = path.stat()
        except FileNotFoundError:
            with self._lock:
                self._entries.pop(key, None)
            raise FileNotFoundError(f"template image not found: {path}") from None
        stamp = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[:2] == stamp:
                self._entries.move_to_end(key)
                self.hits += 1
                return entry[2]
        template = read_template(path)
        with self._lock:
            self.misses += 1
            self._entries[key] = (*stamp, template)
            self._entries.move_to_end(key)
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)
        return template

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
