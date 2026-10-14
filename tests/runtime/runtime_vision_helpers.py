"""A fake vision toolkit with scripted matches, and a clock that jumps instead of
sleeping, to test image steps without a display, numpy or OpenCV."""

from __future__ import annotations

import base64
import dataclasses
import struct
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from runtime_helpers import REGISTRY, TIMEOUT, Recorder, ScaledClock, load
from runtime_input_helpers import SessionFactory

from j5_macro.backends.base import Rect
from j5_macro.runtime import RunOptions, RunOutcome
from j5_macro.runtime.interpreter import create_run
from j5_macro.script import images as embedded
from j5_macro.script.model import Script
from j5_macro.vision.api import Frame, Match, Template, VisionToolkit

DESKTOP = Rect(0, 0, 1920, 1080)
NEAR_MISS = Rect(5, 5, 10, 10)
"""Where :func:`miss` places its best (rejected) candidate."""


class Pixels:
    """An opaque stand-in for a numpy image: only ``shape`` (height, width, 3)."""

    def __init__(self, width: int, height: int) -> None:
        self.shape = (height, width, 3)


def hit(score: float, bbox: Rect) -> Match:
    return Match(found=True, score=score, bbox=bbox)


def miss(score: float, bbox: Rect | None = NEAR_MISS) -> Match:
    return Match(found=False, score=score, bbox=bbox)


class FakeCapturer:
    def __init__(self, desktop: Rect = DESKTOP) -> None:
        self.desktop = desktop
        self.grabs: list[Rect | None] = []
        self.closed = False

    def grab(self, region: Rect | None = None) -> Frame:
        self.grabs.append(region)
        area = region if region is not None else self.desktop
        return Frame(Pixels(area.width, area.height), (area.x, area.y))

    def close(self) -> None:
        self.closed = True


class FakeToolkit:
    """Builds a :class:`VisionToolkit` whose locator returns ``matches`` in order (the
    last one repeats) and records every call."""

    def __init__(self, matches: Iterable[Match] = ()) -> None:
        self.matches = list(matches) or [miss(0.1)]
        self.capturer = FakeCapturer()
        self.templates: list[Path] = []
        self.searches: list[tuple[Path, tuple[int, int], float, float]] = []
        self.saved: list[tuple[Path, Rect | None]] = []
        self.decoded: list[tuple[bytes, str]] = []
        self.created = 0
        self.error: Exception | None = None
        self.embedding = True
        """Whether the toolkit can load embedded images (``decode_template``)."""

    def __call__(self) -> VisionToolkit:
        """The :data:`ToolkitFactory`."""
        self.created += 1
        if self.error is not None:
            raise self.error
        return VisionToolkit(capturer=self.capturer, load_template=self.load_template,
                             locate=self.locate, save_image=self.save_image,
                             decode_template=self.decode_template if self.embedding
                             else None)

    def decode_template(self, data: bytes, label: str) -> Template:
        self.decoded.append((data, label))
        if not data.startswith(b"\x89PNG"):
            raise ValueError("not an image")
        return Template(Path(label), Pixels(10, 10))

    def load_template(self, path: Path) -> Template:
        self.templates.append(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        return Template(path, Pixels(10, 10))

    def locate(self, template: Template, frame: Frame, threshold: float,
               scale: float) -> Match:
        index = min(len(self.searches), len(self.matches) - 1)
        self.searches.append((template.path, frame.origin, threshold, scale))
        return self.matches[index]

    def save_image(self, frame: Frame, path: Path, outline: Rect | None) -> None:
        path.write_bytes(b"\x89PNG fake")
        self.saved.append((path, outline))


class JumpClock:
    """Time only moves when the run waits: each wait advances it by its timeout."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def wait(self, cond: threading.Condition, timeout: float | None) -> None:
        if timeout is None:
            cond.wait()
        else:
            self.t += timeout


def with_images(script: Script | str, directory: Path, *names: str) -> Script:
    """The script as if loaded from ``directory/script.yaml``, with the image files."""
    if isinstance(script, str):
        script = load(script)
    for name in names:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
    return dataclasses.replace(script, source_path=str(directory / "script.yaml"))


def png_header(width: int = 10, height: int = 10, tail: bytes = b"") -> bytes:
    """The start of a PNG file: enough for the loader when it does not decode pixels
    (see :func:`with_embedded`)."""
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00" + tail)


def with_embedded(steps: str, monkeypatch: pytest.MonkeyPatch,
                  **images: bytes) -> Script:
    """A script with ``steps`` and ``images`` embedded (base64 of each value). The
    loader's pixel check is turned off, so the fake headers of :func:`png_header` load
    whether or not the vision extra is installed."""
    monkeypatch.setattr(embedded, "pixel_check", lambda: None)
    section = "".join(f"  {name}: {{png: {base64.b64encode(data).decode()}}}\n"
                      for name, data in images.items())
    return load(f"steps:\n{steps}images:\n{section}")


class VisionRun:
    """A run with a fake toolkit (and a fake input session), on this thread with a
    jumping clock, or on a worker thread with a scaled clock (``background``); or with
    the given ``clock``."""

    def __init__(self, script: Script, toolkit: FakeToolkit, *, background: bool = False,
                 scale: float = 1000.0, conditions: Any = None, clock: Any = None,
                 **options: Any) -> None:
        self.toolkit = toolkit
        self.recorder = Recorder()
        self.factory = SessionFactory()
        if clock is None:
            clock = ScaledClock(scale) if background else JumpClock()
        self.clock: Any = clock
        self.controller = create_run(script, REGISTRY, self.recorder, RunOptions(**options),
                                     clock=self.clock, session_factory=self.factory,
                                     vision_factory=toolkit, conditions=conditions)
        self.outcome: RunOutcome | None = None
        self._thread: threading.Thread | None = None
        if background:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            self._run()

    def _run(self) -> None:
        self.outcome = self.controller.run()

    def join(self, timeout: float = TIMEOUT) -> RunOutcome:
        if self._thread is not None:
            self._thread.join(timeout)
            assert not self._thread.is_alive(), f"run did not end: {self.recorder.summary()}"
        assert self.outcome is not None
        return self.outcome

    def searches(self) -> list[dict[str, Any]]:
        return self.recorder.of("image_search")
