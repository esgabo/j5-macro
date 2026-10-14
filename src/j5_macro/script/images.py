"""Embedded images: the ``images`` section of a script (docs/contract.md, "Script format
and step registry": embedded images).

A script carries templates as base64 PNG files under ``images``, and any ``image``
parameter refers to one with ``"#name"``. This module holds what the loader, the
editor and ``j5 pack``/``j5 unpack`` share: the limits, base64 decoding and wrapping,
the checks of an image's bytes, and :func:`image_slots`, which finds the image
references in a step's parameters. It imports neither OpenCV nor numpy: decoding the
pixels is tried only when the ``vision`` extra is installed.
"""

from __future__ import annotations

import base64
import binascii
import re
import struct
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from j5_macro.steps.spec import StepSpec

REFERENCE_PREFIX = "#"
"""``image: "#name"`` refers to the entry ``name`` of ``images``."""
NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
FORMATS = ("png",)
"""Keys of an entry that hold the image, one per encoding; only PNG for now."""
ENTRY_FIELDS = (*FORMATS, "scale")
MAX_IMAGE_BYTES = 1024 * 1024
"""Largest decoded image (1 MiB)."""
MAX_SCRIPT_BYTES = 8 * 1024 * 1024
"""Largest total of a script's decoded images (8 MiB)."""
WRAP = 76
"""Column at which the editor wraps base64 text."""

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_WHITESPACE = re.compile(r"\s+")
_IMAGE_PARAM_TYPES = ("string",)
_TARGET_PARAM_TYPES = ("object", "condition")
_ANCHOR_KEYS = ("after", "before")
"""Keys of an image target that may hold an image too: the anchors that choose among
several matches (a stored result there is a mapping, or text with ``${``)."""
_ANCHOR_PARAM_TYPES = ("string", "any")


def reference_name(value: Any) -> str | None:
    """The entry name of a literal reference (``"#name"`` gives ``name``); None for a
    path, an interpolated value or anything that is not a string."""
    if not isinstance(value, str) or not value.startswith(REFERENCE_PREFIX):
        return None
    if "${" in value:
        return None
    return value[len(REFERENCE_PREFIX):]


def decode_base64(text: str) -> bytes:
    """The bytes of base64 ``text``, ignoring whitespace (line breaks included);
    ``ValueError`` when it is not valid base64."""
    compact = _WHITESPACE.sub("", text)
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"not valid base64 ({exc})") from None


def compact_base64(text: str) -> str:
    """``text`` without whitespace: two encodings of the same data that differ only in
    line breaks compare equal."""
    return _WHITESPACE.sub("", text)


def wrap_base64(text: str, width: int = WRAP) -> str:
    """Base64 ``text`` (whitespace ignored) in lines of ``width`` characters, each
    ending with a line break; the empty string stays empty."""
    compact = compact_base64(text)
    return "".join(compact[i:i + width] + "\n" for i in range(0, len(compact), width))


def encode_base64(data: bytes, width: int = WRAP) -> str:
    """``data`` as base64 in lines of ``width`` characters (see :func:`wrap_base64`)."""
    return wrap_base64(base64.b64encode(data).decode("ascii"), width)


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height from a PNG's header, without decoding pixels; ``ValueError``
    when ``data`` does not start like a PNG file."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG file (wrong signature)")
    header = data[len(PNG_SIGNATURE):len(PNG_SIGNATURE) + 16]
    if len(header) < 16 or header[4:8] != b"IHDR":
        raise ValueError("not a PNG file (no IHDR header)")
    width, height = struct.unpack(">II", header[8:16])
    if width == 0 or height == 0:
        raise ValueError(f"the PNG header gives an empty size ({width}x{height})")
    return int(width), int(height)


PixelCheck = Callable[[bytes, str], object]
"""Decodes an image's pixels, raising ``ValueError`` when they are unreadable."""


def pixel_check() -> PixelCheck | None:
    """OpenCV's template decoder when the ``vision`` extra is installed, else None."""
    try:
        from j5_macro.vision.templates import decode_template_bytes
    except ImportError:
        return None
    return decode_template_bytes


def check_png(data: bytes, label: str, *, decode: bool = True) -> tuple[int, int]:
    """Check an embedded image's bytes and return its width and height.

    Raises ``ValueError`` with a message for people when it is over
    :data:`MAX_IMAGE_BYTES` or is not a PNG file, or, with ``decode`` and the
    ``vision`` extra installed, when its pixels cannot be decoded."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"{len(data):,} bytes once decoded, over the limit of "
                         f"{MAX_IMAGE_BYTES:,} bytes (1 MiB) per image")
    size = png_size(data)
    decoder = pixel_check() if decode else None
    if decoder is not None:
        decoder(data, label)
    return size


def image_slots(params: Mapping[str, Any],
                spec: StepSpec | None) -> Iterator[tuple[Mapping[str, Any], str, str]]:
    """Where a step's parameters hold an image: ``(mapping, key, param)`` for every
    string value, ``param`` being its dotted name for messages (``image``,
    ``from.image``, ``when.image_visible``, ``while.image_visible.image``).

    Looks at an ``image`` string parameter and the ``after``/``before`` anchors next to
    it, and inside mapping parameters of type ``object`` or ``condition`` at their
    ``image``, ``after`` and ``before`` keys and at ``image_visible``, either a string or
    a mapping with ``image`` (and anchors). Parameters the step type does not declare
    are skipped (the loader reports them)."""
    if spec is None:
        return
    for name, value in list(params.items()):
        param = spec.param(str(name))
        if param is None:
            continue
        if str(name) == "image" and param.type in _IMAGE_PARAM_TYPES:
            if isinstance(value, str):
                yield params, name, "image"
            continue
        if str(name) in _ANCHOR_KEYS and param.type in _ANCHOR_PARAM_TYPES:
            if isinstance(value, str) and "image" in params:
                yield params, name, str(name)
            continue
        if param.type not in _TARGET_PARAM_TYPES or not isinstance(value, Mapping):
            continue
        for key, inner in list(value.items()):
            where = f"{name}.{key}"
            if str(key) in ("image", *_ANCHOR_KEYS) and isinstance(inner, str):
                if str(key) == "image" or "image" in value:
                    yield value, key, where
            elif str(key) == "image_visible":
                if isinstance(inner, str):
                    yield value, key, where
                elif isinstance(inner, Mapping):
                    for image_key in [k for k in inner
                                      if str(k) in ("image", *_ANCHOR_KEYS)]:
                        if isinstance(inner[image_key], str):
                            yield inner, image_key, f"{where}.{image_key}"
