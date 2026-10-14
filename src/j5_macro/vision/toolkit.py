"""Builds the :class:`~j5_macro.vision.api.VisionToolkit` (the
:data:`~j5_macro.vision.api.ToolkitFactory`).

This module imports without the ``vision`` extra; OpenCV, numpy and mss are imported
when :func:`create_toolkit` runs.
"""

from __future__ import annotations

from j5_macro.backends.base import Rect
from j5_macro.vision.api import VisionToolkit

INSTALL_HINT = 'install the vision extra: pip install "j5-macro[vision]"'


def preload() -> bool:
    """Import OpenCV, numpy and mss now, if installed; True when they are.

    On Windows the engine calls this on its main thread before it starts reading
    commands: loading their DLLs later, from a run's thread, hangs while the main thread
    is blocked reading the stdin pipe (synchronous I/O on a standard handle serializes
    with the DLLs' start-up code), so the first image search of a run never returned.
    """
    try:
        import j5_macro.vision.capture
        import j5_macro.vision.drawing
        import j5_macro.vision.matching
        import j5_macro.vision.templates  # noqa: F401
    except ImportError:
        return False
    return True


def create_toolkit() -> VisionToolkit:
    """Screen capture, cached template loading, matching and PNG saving.

    Raises ``ImportError`` (with an install hint) when OpenCV, numpy or mss are missing
    and ``RuntimeError`` when the screen cannot be captured (e.g. no display).
    """
    try:
        from j5_macro.vision.capture import MssCapturer
        from j5_macro.vision.drawing import save_png
        from j5_macro.vision.matching import locate
        from j5_macro.vision.templates import TemplateCache, decode_template_bytes
    except ImportError as exc:
        raise ImportError(f"image matching needs OpenCV, numpy and mss ({exc}); "
                          f"{INSTALL_HINT}") from exc

    capturer = MssCapturer()
    try:
        desktop = capturer.desktop()
        capturer.grab(Rect(desktop.x, desktop.y, 1, 1))
    except Exception as exc:
        capturer.close()
        message = str(exc) if isinstance(exc, RuntimeError) else f"screen capture failed: {exc}"
        raise RuntimeError(message) from exc
    return VisionToolkit(capturer=capturer, load_template=TemplateCache(),
                         locate=locate, save_image=save_png,
                         decode_template=decode_template_bytes)
