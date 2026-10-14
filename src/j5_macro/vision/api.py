"""Vision interfaces (docs/contract.md, "Script format and step registry": images; "Events":
``image_search``).

Two layers:

- **Toolkit** (``j5_macro.vision``): capture the screen (:class:`Capturer`), load
  templates (:data:`TemplateLoader`) and locate a template in a frame (:data:`Locator`).
  Pure functions plus one capture primitive; no knowledge of runs or events.
- **Steps** see a :class:`StepVision` through ``StepContext.vision``: single searches and
  waits that emit ``image_search`` events, save captures by the run's ``captures``
  policy, honour dry-run, stop and pause, and resolve image paths and windows.

Pixels are ``numpy.ndarray`` (``uint8``, height x width x 3, BGR, as OpenCV uses), typed
as ``Any`` here so this module imports without numpy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from j5_macro.backends.base import Rect, WindowInfo

DEFAULT_THRESHOLD = 0.9
"""Minimum match score of image searches when a step gives no ``threshold``."""
DEFAULT_SCALE = 1.0
"""Template scale of image searches when a step gives no ``scale`` (the display scale
ratio applies on top)."""
DEFAULT_INTERVAL = 0.25
"""Seconds between attempts of a wait."""


@dataclass(frozen=True, slots=True)
class Frame:
    """A capture. ``origin`` is the desktop position of the top-left pixel."""

    pixels: Any
    origin: tuple[int, int] = (0, 0)

    @property
    def rect(self) -> Rect:
        height, width = self.pixels.shape[:2]
        return Rect(self.origin[0], self.origin[1], int(width), int(height))


@dataclass(frozen=True, slots=True)
class Template:
    """An image to look for. ``mask`` (same size, single channel) marks the pixels that
    count, from a PNG's alpha channel; None means every pixel counts."""

    path: Path
    """The image file; for an image embedded in the script, ``#name``."""
    pixels: Any
    mask: Any | None = None

    @property
    def size(self) -> tuple[int, int]:
        height, width = self.pixels.shape[:2]
        return int(width), int(height)


@dataclass(frozen=True, slots=True, kw_only=True)
class Match:
    """The best location of a template in a frame, found or not."""

    found: bool
    score: float
    """Best similarity in [0, 1], even when below the threshold."""
    bbox: Rect | None
    """Desktop rectangle of the best location (None if the template cannot fit)."""
    elapsed_ms: float = 0.0
    frame: Rect | None = None
    """Client area of the window searched in, when the search had a ``window``, as it
    was for this attempt (set by ``StepVision``, not by the locator)."""
    display_ratio: float = 1.0
    """The display scale / capture scale ratio the template was resized by, besides the
    step's ``scale`` (set by ``StepVision``); pixel offsets from the match use it."""


class Capturer(Protocol):
    def grab(self, region: Rect | None = None) -> Frame:
        """Capture ``region`` of the virtual desktop (all of it when None)."""
        ...

    def close(self) -> None: ...


TemplateLoader = Callable[[Path], Template]
"""Loads (and may cache) a template; raises ``FileNotFoundError`` when missing and
``ValueError`` when the file is not a readable image."""

TemplateDecoder = Callable[[bytes, str], Template]
"""``decode(data, label)``: a template from an encoded image embedded in a script
(``label`` is ``#name``, for messages); raises ``ValueError`` when it is not readable."""

Locator = Callable[[Template, Frame, float, float], Match]
"""``locate(template, frame, threshold, scale) -> Match``: best location of the
template, scaled by ``scale``, in the frame."""

ImageSaver = Callable[[Frame, Path, Rect | None], None]
"""Writes a frame as PNG, optionally outlining a rectangle (desktop coordinates)."""


@dataclass(frozen=True, slots=True)
class VisionToolkit:
    capturer: Capturer
    load_template: TemplateLoader
    locate: Locator
    save_image: ImageSaver
    decode_template: TemplateDecoder | None = None
    """Loads embedded images; None for a toolkit that only reads files."""


ToolkitFactory = Callable[[], VisionToolkit]
"""Builds the toolkit; implemented by ``j5_macro.vision.toolkit.create_toolkit``. Raises
``ImportError`` when the ``vision`` extra is not installed and ``RuntimeError`` when the
screen cannot be captured."""


class StepVision(Protocol):
    """What step executors and conditions see as ``ctx.vision``: image searches and
    the window lookups of the waits, each polling every :data:`DEFAULT_INTERVAL`
    seconds of unpaused time, interruptible by ``stop`` and suspended by ``pause``."""

    def find(self, image: str, *, threshold: float = DEFAULT_THRESHOLD,
             scale: float = DEFAULT_SCALE, region: list[int] | dict[str, Any] | None = None,
             window: dict[str, Any] | None = None) -> Match:
        """One search (one ``image_search`` event). ``image`` is relative to the script's
        directory, or ``#name`` for an image embedded in the script; ``region`` is
        ``[x, y, w, h]``, relative to ``window`` when given, or ``{at, offset?, size?}``
        next to a stored image result. The match's ``frame`` is the client area of
        ``window``, when given.
        Raises StepError: ``template_missing``, ``window_not_found``,
        ``capability_missing`` (no vision)."""
        ...

    def wait(self, image: str, *, timeout: float, threshold: float = DEFAULT_THRESHOLD,
             scale: float = DEFAULT_SCALE, region: list[int] | dict[str, Any] | None = None,
             window: dict[str, Any] | None = None,
             interval: float = DEFAULT_INTERVAL) -> Match:
        """Search until found or ``timeout`` seconds pass (interruptible like any wait;
        a single attempt in dry runs). Raises StepError ``image_not_found`` with
        ``details.best_score`` when it times out. The step's common ``timeout`` field,
        when set, replaces ``timeout``; ``timeout=0`` makes a single attempt that fails
        when the image is not found. While waiting (a timeout above 0), a missing
        ``window`` is "not found yet": ``window_not_found`` (with ``details.timeout``)
        only when it never appeared."""
        ...

    def wait_gone(self, image: str, *, timeout: float,
                  threshold: float = DEFAULT_THRESHOLD, scale: float = DEFAULT_SCALE,
                  region: list[int] | dict[str, Any] | None = None,
                  window: dict[str, Any] | None = None,
                  interval: float = DEFAULT_INTERVAL) -> float:
        """Search until the image is not found, outside dry runs; returns the unpaused
        seconds waited. A missing ``window`` is "not gone yet". Raises StepError
        ``still_visible`` on timeout (``window_not_found`` when the window never
        appeared). The step's common ``timeout`` replaces ``timeout``."""
        ...

    def find_window(self, title: str) -> WindowInfo | None:
        """One look for the topmost visible window whose title contains ``title``: its
        full title and client area, or None. No event. Raises StepError
        ``capability_missing`` without a backend with the ``windows`` capability."""
        ...

    def wait_window(self, title: str, *, timeout: float,
                    interval: float = DEFAULT_INTERVAL) -> WindowInfo:
        """Look for the window until it is there, outside dry runs. Raises StepError
        ``window_not_found`` (``details.timeout``) on timeout. The step's common
        ``timeout`` replaces ``timeout``."""
        ...

    def wait_window_gone(self, title: str, *, timeout: float,
                         interval: float = DEFAULT_INTERVAL) -> float:
        """Look for the window until none matches, outside dry runs; returns the
        unpaused seconds waited. Raises StepError ``still_visible`` on timeout. The
        step's common ``timeout`` replaces ``timeout``."""
        ...
