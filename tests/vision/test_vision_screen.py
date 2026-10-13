"""Capture a real X screen (Xvfb in CI) and find known content on it."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")
pytest.importorskip("mss")

from j5_macro.backends.base import Rect  # noqa: E402
from j5_macro.vision.api import Frame, Template  # noqa: E402
from j5_macro.vision.capture import MssCapturer  # noqa: E402
from j5_macro.vision.drawing import save_png  # noqa: E402
from j5_macro.vision.matching import locate  # noqa: E402
from j5_macro.vision.toolkit import create_toolkit  # noqa: E402

pytestmark = pytest.mark.x11

WINDOW = Rect(300, 200, 120, 90)
CELL = 15
COLOURS = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255), (255, 0, 255),
           (255, 255, 0), (40, 40, 40), (200, 120, 30)]  # BGR


def pattern() -> Any:
    """The window's content: a grid of solid cells with a fixed pseudo-random layout."""
    rows, cols = WINDOW.height // CELL, WINDOW.width // CELL
    picks = np.random.default_rng(42).integers(0, len(COLOURS), (rows, cols))
    grid = np.array(COLOURS, dtype=np.uint8)[picks]
    return np.repeat(np.repeat(grid, CELL, axis=0), CELL, axis=1)


@pytest.fixture
def painted_window() -> Iterator[Any]:
    from Xlib import X
    from Xlib.display import Display

    display = Display(os.environ.get("DISPLAY"))
    screen = display.screen()
    window = screen.root.create_window(
        WINDOW.x, WINDOW.y, WINDOW.width, WINDOW.height, 0, screen.root_depth,
        X.InputOutput, X.CopyFromParent, background_pixel=screen.black_pixel,
        override_redirect=True, event_mask=X.ExposureMask | X.StructureNotifyMask)
    window.map()
    gc = window.create_gc()
    content = pattern()

    def paint() -> None:
        for row in range(0, WINDOW.height, CELL):
            for col in range(0, WINDOW.width, CELL):
                b, g, r = (int(v) for v in content[row, col])
                gc.change(foreground=(r << 16) | (g << 8) | b)
                window.fill_rectangle(gc, col, row, CELL, CELL)
        display.sync()

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:  # wait until mapped, then paint
        if display.pending_events():
            if display.next_event().type == X.MapNotify:
                break
        else:
            time.sleep(0.01)
    paint()
    try:
        yield paint
    finally:
        window.destroy()
        display.sync()
        display.close()


def find(capturer: MssCapturer, template: Template, region: Rect | None = None) -> Any:
    deadline = time.monotonic() + 3
    while True:
        frame = capturer.grab(region)
        match = locate(template, frame, 0.95)
        if match.found or time.monotonic() > deadline:
            return frame, match
        time.sleep(0.05)


def test_full_screen_and_region_captures_find_the_window(painted_window: Any) -> None:
    content = pattern()
    inner = (2 * CELL, 1 * CELL)  # a template from inside the window
    template = Template(Path("t.png"), content[CELL:4 * CELL, 2 * CELL:6 * CELL].copy())
    expected = Rect(WINDOW.x + inner[0], WINDOW.y + inner[1], 4 * CELL, 3 * CELL)
    capturer = MssCapturer()
    try:
        desktop = capturer.desktop()
        frame, match = find(capturer, template)
        assert frame.rect == desktop
        assert frame.pixels.shape == (desktop.height, desktop.width, 3)
        assert match.found and match.score > 0.99, match
        assert match.bbox == expected

        region = Rect(250, 150, 300, 250)
        frame, match = find(capturer, template, region)
        assert frame.origin == (250, 150)
        assert frame.rect == region
        assert match.bbox == expected

        frame = capturer.grab(Rect(WINDOW.x, WINDOW.y, WINDOW.width, WINDOW.height))
        assert np.array_equal(frame.pixels, content)
    finally:
        capturer.close()


def test_regions_are_clipped_to_the_desktop(painted_window: Any) -> None:
    capturer = MssCapturer()
    try:
        desktop = capturer.desktop()
        frame = capturer.grab(Rect(desktop.x - 50, desktop.y - 30, 100, 80))
        assert frame.rect == Rect(desktop.x, desktop.y, 50, 50)
        right = desktop.x + desktop.width
        frame = capturer.grab(Rect(right - 10, desktop.y, 40, 10))
        assert frame.rect == Rect(right - 10, desktop.y, 10, 10)
        with pytest.raises(ValueError, match="outside the virtual desktop"):
            capturer.grab(Rect(right + 5, 0, 10, 10))
        with pytest.raises(ValueError, match="positive size"):
            capturer.grab(Rect(0, 0, 0, 10))
    finally:
        capturer.close()
    with pytest.raises(RuntimeError, match="closed"):
        capturer.grab()


def test_capture_from_several_threads(painted_window: Any) -> None:
    capturer = MssCapturer()
    errors: list[BaseException] = []
    expected = pattern()

    def worker() -> None:
        try:
            for _ in range(5):
                frame = capturer.grab(WINDOW)
                assert np.array_equal(frame.pixels, expected)
        except BaseException as exc:
            errors.append(exc)

    try:
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
    finally:
        capturer.close()
    assert not errors, errors


def test_a_script_finds_its_embedded_template_on_screen(painted_window: Any) -> None:
    import cv2

    from j5_macro.runtime import RunOptions
    from j5_macro.runtime.interpreter import create_run
    from j5_macro.script.images import encode_base64
    from j5_macro.script.loader import load_script
    from j5_macro.steps import default_registry

    ok, encoded = cv2.imencode(".png", pattern())
    assert ok
    block = encode_base64(encoded.tobytes()).replace("\n", "\n      ").rstrip()
    text = ("steps:\n  - id: w\n    wait_image: {image: \"#window\", timeout: 3s}\n"
            f"images:\n  window:\n    png: |\n      {block}\n")
    registry = default_registry()
    result = load_script(text, lookup=registry.spec, check=registry.check_params)
    assert result.script is not None, result.errors
    events: list[tuple[str, dict[str, Any]]] = []
    outcome = create_run(result.script, registry, lambda t, p: events.append((t, p)),
                         RunOptions()).run()
    assert outcome.status == "completed", outcome
    searches = [p for t, p in events if t == "image_search"]
    assert searches[-1]["template"] == "#window" and searches[-1]["found"]
    assert searches[-1]["bbox"] == [WINDOW.x, WINDOW.y, WINDOW.width, WINDOW.height]


def test_toolkit_end_to_end(painted_window: Any, tmp_path: Path) -> None:
    path = tmp_path / "window.png"
    save_png(Frame(pattern()), path)
    toolkit = create_toolkit()
    try:
        template = toolkit.load_template(path)
        frame = toolkit.capturer.grab()
        match = toolkit.locate(template, frame, 0.9, 1.0)
        assert match.found, match
        assert match.bbox == WINDOW
        toolkit.save_image(frame, tmp_path / "shot.png", match.bbox)
        assert (tmp_path / "shot.png").stat().st_size > 0
    finally:
        toolkit.capturer.close()
