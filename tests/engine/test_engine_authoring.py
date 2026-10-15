"""The authoring tools through the engine (docs/contract.md, "Authoring tools"), on an
in-memory desktop: a real numpy image, the real matcher and PNG coding, a fake backend
for windows and display scales, and a scripted overlay."""

from __future__ import annotations

import base64
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from engine_harness import Engine, FakeFactory

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from j5_macro.backends.base import Rect  # noqa: E402
from j5_macro.backends.fake import FakeBackend  # noqa: E402
from j5_macro.backends.select import BackendChoice  # noqa: E402
from j5_macro.engine.backend import EngineBackend  # noqa: E402
from j5_macro.vision.api import Frame, VisionToolkit  # noqa: E402
from j5_macro.vision.capture import clip_region  # noqa: E402
from j5_macro.vision.drawing import save_png  # noqa: E402
from j5_macro.vision.matching import locate, locate_all  # noqa: E402
from j5_macro.vision.templates import TemplateCache, decode_template_bytes  # noqa: E402

DESKTOP = Rect(0, 0, 400, 300)
WINDOW = Rect(100, 80, 200, 150)
"""The client area of the window "Notes - editor"."""
ICON = Rect(150, 120, 24, 16)
"""A textured patch inside the window, on a white background."""


def desktop_pixels() -> Any:
    pixels = np.full((DESKTOP.height, DESKTOP.width, 3), 90, dtype=np.uint8)
    pixels[WINDOW.y:WINDOW.y + WINDOW.height, WINDOW.x:WINDOW.x + WINDOW.width] = 255
    rng = np.random.default_rng(7)
    patch = rng.integers(0, 255, (ICON.height, ICON.width, 3), dtype=np.uint8)
    pixels[ICON.y:ICON.y + ICON.height, ICON.x:ICON.x + ICON.width] = patch
    return pixels


class ArrayCapturer:
    def __init__(self) -> None:
        self.pixels = desktop_pixels()
        self.grabs: list[Rect | None] = []
        self.closed = False

    def grab(self, region: Rect | None = None) -> Frame:
        self.grabs.append(region)
        area = DESKTOP if region is None else clip_region(region, DESKTOP)
        part = self.pixels[area.y:area.y + area.height, area.x:area.x + area.width]
        return Frame(np.ascontiguousarray(part), (area.x, area.y))

    def close(self) -> None:
        self.closed = True


class Toolkit:
    """A :data:`ToolkitFactory` over :class:`ArrayCapturer`, or one that fails."""

    def __init__(self, error: Exception | None = None) -> None:
        self.capturer = ArrayCapturer()
        self.error = error

    def __call__(self) -> VisionToolkit:
        if self.error is not None:
            raise self.error
        return VisionToolkit(capturer=self.capturer, load_template=TemplateCache(),
                             locate=locate, save_image=save_png,
                             decode_template=decode_template_bytes, locate_all=locate_all)


class ScriptedOverlay:
    """An :class:`~j5_macro.engine.overlay.Overlay` that answers ``answer`` once
    ``release`` is set (at once by default), or None when cancelled."""

    def __init__(self, answer: Any = None, *, block: bool = False) -> None:
        self.answer = answer
        self.release = threading.Event()
        if not block:
            self.release.set()
        self.opened = threading.Event()
        self.cancelled = threading.Event()
        self.frames: list[Frame] = []
        self.shown: list[tuple[Rect | None, bool, str, int]] = []
        self.others: list[list[Rect]] = []

    def _wait(self, frame: Frame) -> Any:
        self.frames.append(frame)
        self.opened.set()
        while not self.release.wait(0.01):
            if self.cancelled.is_set():
                return None
        return None if self.cancelled.is_set() else self.answer

    def select_region(self, frame: Frame, hint: str = "") -> Rect | None:
        result: Rect | None = self._wait(frame)
        return result

    def pick_point(self, frame: Frame, hint: str = "") -> tuple[int, int] | None:
        result: tuple[int, int] | None = self._wait(frame)
        return result

    def show_match(self, frame: Frame, bbox: Rect | None, found: bool, label: str,
                   duration_ms: int, *, others: Sequence[Rect] = ()) -> None:
        self.shown.append((bbox, found, label, duration_ms))
        self.others.append(list(others))
        self._wait(frame)

    def cancel(self) -> None:
        self.cancelled.set()


@pytest.fixture
def fake() -> FakeBackend:
    return FakeBackend(bounds=DESKTOP, windows={"Notes - editor": WINDOW,
                                                "Desktop": DESKTOP})


@pytest.fixture
def toolkit() -> Toolkit:
    return Toolkit()


@pytest.fixture
def overlay() -> ScriptedOverlay:
    return ScriptedOverlay()


@pytest.fixture
def factory() -> FakeFactory:
    return FakeFactory()


@pytest.fixture
def engine(fake: FakeBackend, toolkit: Toolkit, overlay: ScriptedOverlay,
           factory: FakeFactory) -> Iterator[Engine]:
    eng = Engine(backend=EngineBackend(BackendChoice("fake", fake, "test")),
                 toolkit_factory=toolkit, overlay_factory=lambda: overlay,
                 run_factory=factory)
    yield eng
    eng.close()


def read_png(path: Path) -> Any:
    return cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)


def region_of(rect: Rect) -> Any:
    return desktop_pixels()[rect.y:rect.y + rect.height, rect.x:rect.x + rect.width]


SCRIPT = """\
# A script to embed captures in.
name: Embed
steps:
  - log: hello  # stays as it is
"""


# --- list_windows and screenshot --------------------------------------------------------


def test_list_windows_reports_titles_and_client_areas(engine: Engine) -> None:
    assert engine.ok("list_windows") == {"windows": [
        {"title": "Notes - editor", "bbox": [100, 80, 200, 150]},
        {"title": "Desktop", "bbox": [0, 0, 400, 300]}]}


def test_list_windows_needs_a_backend_with_windows() -> None:
    eng = Engine(backend=EngineBackend(BackendChoice(None, None, "no display")))
    try:
        error = eng.error("list_windows")
        assert error["code"] == "capability_missing"
        assert "no display" in error["message"]
    finally:
        eng.close()


def test_screenshot_of_a_window_region(engine: Engine, tmp_path: Path) -> None:
    out = tmp_path / "shot.png"
    result = engine.ok("screenshot", {"path": str(out), "window": {"title": "Notes"},
                                      "region": [10, 20, 50, 40]})
    assert result == {"path": str(out), "bbox": [110, 100, 50, 40], "display_scale": 1.0,
                      "window": "Notes - editor", "window_bbox": [10, 20, 50, 40]}
    assert (read_png(out) == region_of(Rect(110, 100, 50, 40))).all()


def test_screenshot_defaults_to_the_whole_desktop_in_a_temporary_file(
        engine: Engine) -> None:
    result = engine.ok("screenshot")
    path = Path(result["path"])
    try:
        assert path.parent.name == "j5-macro" and path.suffix == ".png"
        assert result["bbox"] == [0, 0, 400, 300]
        assert read_png(path).shape == (300, 400, 3)
    finally:
        path.unlink()


def test_screenshot_outside_the_desktop_and_unknown_windows(engine: Engine,
                                                             tmp_path: Path) -> None:
    error = engine.error("screenshot", {"path": str(tmp_path / "a.png"),
                                        "region": [1000, 1000, 10, 10]})
    assert error["code"] == "validation_failed"
    assert error["details"]["field"] == "region"
    error = engine.error("screenshot", {"window": {"title": "Nope"}})
    assert error["code"] == "window_not_found"
    assert error["details"] == {"title": "Nope"}


def test_without_vision_the_screen_tools_are_missing_a_capability(
        fake: FakeBackend, tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"png")
    eng = Engine(backend=EngineBackend(BackendChoice("fake", fake, "test")),
                 toolkit_factory=Toolkit(ImportError("no cv2")))
    try:
        for command, payload in (("screenshot", {}), ("pick_point", {}),
                                 ("find_image", {"image": str(tmp_path / "a.png")}),
                                 ("capture_region", {"path": "a.png"})):
            error = eng.error(command, payload)
            assert error["code"] == "capability_missing", command
            assert "no cv2" in error["message"]
    finally:
        eng.close()


# --- capture_region, non-interactive ----------------------------------------------------


def test_capture_a_window_relative_bbox_to_a_file(engine: Engine, tmp_path: Path) -> None:
    out = tmp_path / "icon.png"
    result = engine.ok("capture_region", {"bbox": [50, 40, 24, 16],
                                          "window": {"title": "Notes"}, "path": str(out)})
    assert result == {"bbox": [150, 120, 24, 16], "display_scale": 1.0, "path": str(out),
                      "window": "Notes - editor", "window_bbox": [50, 40, 24, 16],
                      "warnings": []}
    assert (read_png(out) == region_of(ICON)).all()


def test_capture_reports_the_topmost_window_the_rectangle_fits_in(engine: Engine,
                                                                   tmp_path: Path) -> None:
    inside = engine.ok("capture_region", {"bbox": [150, 120, 24, 16],
                                          "path": str(tmp_path / "a.png")})
    assert inside["window"] == "Notes - editor"
    assert inside["window_bbox"] == [50, 40, 24, 16]
    across = engine.ok("capture_region", {"bbox": [90, 70, 30, 30],
                                          "path": str(tmp_path / "b.png")})
    assert across["window"] == "Desktop"  # not inside Notes: the next one down


def test_capture_padding_is_clipped_to_the_desktop(engine: Engine, tmp_path: Path) -> None:
    result = engine.ok("capture_region", {"bbox": [2, 3, 10, 10], "padding": 5,
                                          "path": str(tmp_path / "a.png")})
    assert result["bbox"] == [0, 0, 17, 18]


def test_capture_refuses_to_overwrite_and_needs_a_directory(engine: Engine,
                                                            tmp_path: Path) -> None:
    out = tmp_path / "icon.png"
    out.write_bytes(b"old")
    error = engine.error("capture_region", {"bbox": [150, 120, 24, 16], "path": str(out)})
    assert error["code"] == "validation_failed"
    assert error["details"] == {"field": "path", "reason": "exists"}
    assert out.read_bytes() == b"old"
    engine.ok("capture_region", {"bbox": [150, 120, 24, 16], "path": str(out),
                                 "overwrite": True})
    assert read_png(out).shape == (16, 24, 3)
    error = engine.error("capture_region", {"bbox": [0, 0, 5, 5],
                                            "path": str(tmp_path / "no" / "a.png")})
    assert error["details"]["reason"] == "no_directory"


def test_capture_payload_problems(engine: Engine, tmp_path: Path) -> None:
    out = str(tmp_path / "a.png")
    for payload, field in (({"bbox": [0, 0, 0, 5], "path": out}, "bbox"),
                           ({"bbox": [0, 0, 5], "path": out}, "bbox"),
                           ({"bbox": [500, 500, 5, 5], "path": out}, "bbox")):
        error = engine.error("capture_region", payload)
        assert error["code"] == "validation_failed", payload
        assert error["details"]["field"] == field
    for payload in ({"bbox": [0, 0, 5, 5]}, {"bbox": [0, 0, 5, 5], "path": out,
                                              "script_id": "s1", "name": "a"},
                    {"bbox": [0, 0, 5, 5], "script_id": "s1"}):
        assert engine.error("capture_region", payload)["code"] == "validation_failed"


def test_transparent_background_and_flat_warning(engine: Engine, tmp_path: Path) -> None:
    # The icon on the window's white background, with 3 pixels of white around it.
    out = tmp_path / "cutout.png"
    result = engine.ok("capture_region", {"bbox": [147, 117, 30, 22], "path": str(out),
                                          "transparent": True})
    assert result["warnings"] == []
    image = read_png(out)
    assert image.shape == (22, 30, 4)
    assert (image[0, :, 3] == 0).all() and (image[3:19, 3:27, 3] > 0).any()
    # A plain white area is flat, and nothing would stay opaque once transparent.
    flat = engine.ok("capture_region", {"bbox": [250, 200, 20, 20],
                                        "path": str(tmp_path / "flat.png")})
    assert [w["code"] for w in flat["warnings"]] == ["flat"]
    error = engine.error("capture_region", {"bbox": [250, 200, 20, 20], "transparent": True,
                                            "path": str(tmp_path / "none.png")})
    assert error["details"]["reason"] == "transparent"


def test_embed_a_capture_in_a_loaded_script(engine: Engine, tmp_path: Path) -> None:
    script = tmp_path / "embed.yaml"
    script.write_text(SCRIPT, encoding="utf-8")
    script_id = engine.ok("load", {"path": str(script)})["script_id"]
    result = engine.ok("capture_region", {"bbox": [150, 120, 24, 16],
                                          "script_id": script_id, "name": "icon"})
    assert result["image_name"] == "icon"
    assert "scale" not in result  # display scale 1 = the script's capture scale
    text = script.read_text(encoding="utf-8")
    assert text.startswith(SCRIPT)  # comments and steps untouched, no ids written
    assert "images:\n  icon:\n    png: |\n" in text
    changed = engine.event("script_changed")
    assert changed["payload"]["source"] == "edit"
    assert changed["payload"]["version"] == result["version"]
    images = engine.ok("get_script", {"script_id": script_id})["images"]
    assert images == [{"name": "icon", "format": "png", "bytes": images[0]["bytes"],
                       "width": 24, "height": 16}]
    # The same name again needs overwrite; a bad name is refused.
    error = engine.error("capture_region", {"bbox": [0, 0, 5, 5], "script_id": script_id,
                                            "name": "icon"})
    assert error["details"] == {"field": "name", "reason": "exists"}
    error = engine.error("capture_region", {"bbox": [0, 0, 5, 5], "script_id": script_id,
                                            "name": "1st icon"})
    assert error["details"] == {"field": "name"}
    found = engine.ok("find_image", {"image": "#icon", "script_id": script_id})
    assert found["found"] is True and found["bbox"] == [150, 120, 24, 16]


def test_embedding_records_a_display_scale_that_differs(fake: FakeBackend,
                                                        tmp_path: Path) -> None:
    fake.monitors = [(DESKTOP, 1.5)]
    eng = Engine(backend=EngineBackend(BackendChoice("fake", fake, "test")),
                 toolkit_factory=Toolkit())
    try:
        script_id = eng.ok("load", {"source": SCRIPT})["script_id"]
        result = eng.ok("capture_region", {"bbox": [150, 120, 24, 16],
                                           "script_id": script_id, "name": "icon"})
        assert result["display_scale"] == 1.5 and result["scale"] == 1.5
        source = eng.ok("get_script", {"script_id": script_id})["source"]
        assert "    scale: 1.5\n" in source
        same = eng.ok("load", {"source": SCRIPT.replace("name: Embed",
                                                         "name: Embed\ncapture_scale: 1.5")})
        result = eng.ok("capture_region", {"bbox": [150, 120, 24, 16],
                                           "script_id": same["script_id"], "name": "icon"})
        assert "scale" not in result
        # A file cannot carry it: the result reports the display scale only.
        result = eng.ok("capture_region", {"bbox": [150, 120, 24, 16],
                                           "path": str(tmp_path / "a.png")})
        assert result["display_scale"] == 1.5 and "scale" not in result
    finally:
        eng.close()


# --- find_image -------------------------------------------------------------------------


def test_find_image_searches_like_a_step(engine: Engine, tmp_path: Path) -> None:
    (tmp_path / "img").mkdir()
    cv2.imwrite(str(tmp_path / "img" / "icon.png"), region_of(ICON))
    script = tmp_path / "s.yaml"
    script.write_text(SCRIPT, encoding="utf-8")
    script_id = engine.ok("load", {"path": str(script)})["script_id"]
    result = engine.ok("find_image", {"image": "img/icon.png", "script_id": script_id,
                                      "window": {"title": "Notes"}})
    assert result["found"] is True and result["score"] > 0.99
    assert result["bbox"] == [150, 120, 24, 16] and result["center"] == [162, 128]
    assert result["window"] == "Notes - editor"
    assert result["window_bbox"] == [50, 40, 24, 16]
    assert result["region"] == [100, 80, 200, 150]
    assert result["scale"] == 1.0 and result["capture_scale"] == 1.0
    # Outside the region the template is not found, and the best score says how close.
    missed = engine.ok("find_image", {"image": str(tmp_path / "img" / "icon.png"),
                                      "region": [0, 0, 90, 70]})
    assert missed["found"] is False and missed["score"] < 0.9
    assert "window" not in missed


def test_find_image_all_lists_every_match_in_reading_order(engine: Engine, toolkit: Toolkit,
                                                          overlay: ScriptedOverlay,
                                                          tmp_path: Path) -> None:
    cv2.imwrite(str(tmp_path / "icon.png"), region_of(ICON))
    # A second, slightly altered copy, outside the window and below it: it still matches,
    # but less well, so the best match does not depend on floating-point noise.
    copy = region_of(ICON).copy()
    copy[0, :3] = 0  # scores about 0.98
    pixels = toolkit.capturer.pixels
    pixels[250:250 + ICON.height, 20:20 + ICON.width] = copy
    image = str(tmp_path / "icon.png")
    plain = engine.ok("find_image", {"image": image})
    assert "matches" not in plain
    result = engine.ok("find_image", {"image": image, "all": True})
    assert result["found"] is True and result["bbox"] == plain["bbox"]
    assert [m["bbox"] for m in result["matches"]] == [[150, 120, 24, 16], [20, 250, 24, 16]]
    assert result["matches"][1]["center"] == [32, 258]
    assert 0.9 <= result["matches"][1]["score"] < result["matches"][0]["score"]
    in_window = engine.ok("find_image", {"image": image, "all": True,
                                         "window": {"title": "Notes"}})
    assert in_window["matches"] == [{"score": in_window["score"],
                                     "bbox": [150, 120, 24, 16], "center": [162, 128],
                                     "window_bbox": [50, 40, 24, 16]}]
    shown = engine.ok("find_image", {"image": image, "all": True, "show": True,
                                     "show_ms": 100})
    assert shown["found"] is True
    assert overlay.shown[-1][0] == ICON
    assert overlay.shown[-1][2].startswith("2 matches; found: score 1.000")
    assert overlay.others[-1] == [Rect(20, 250, 24, 16)]


def test_find_image_errors(engine: Engine, tmp_path: Path) -> None:
    error = engine.error("find_image", {"image": "#icon"})
    assert error["code"] == "validation_failed" and error["details"]["field"] == "image"
    error = engine.error("find_image", {"image": str(tmp_path / "missing.png")})
    assert error["code"] == "template_missing"
    script_id = engine.ok("load", {"source": SCRIPT})["script_id"]
    error = engine.error("find_image", {"image": "#icon", "script_id": script_id})
    assert error["code"] == "template_missing"
    error = engine.error("find_image", {"image": "a.png", "threshold": 2})
    assert error["code"] == "validation_failed"


def test_find_image_show_highlights_the_match(engine: Engine, overlay: ScriptedOverlay,
                                              tmp_path: Path) -> None:
    cv2.imwrite(str(tmp_path / "icon.png"), region_of(ICON))
    result = engine.ok("find_image", {"image": str(tmp_path / "icon.png"), "show": True,
                                      "show_ms": 500})
    assert result["found"] is True
    assert overlay.shown == [(ICON, True, overlay.shown[0][2], 500)]
    assert overlay.shown[0][2].startswith("found: score 1.000")


# --- the overlay ------------------------------------------------------------------------


def test_interactive_capture_saves_what_the_user_selected(fake: FakeBackend,
                                                          tmp_path: Path) -> None:
    overlay = ScriptedOverlay(Rect(150, 120, 24, 16))
    toolkit = Toolkit()
    eng = Engine(backend=EngineBackend(BackendChoice("fake", fake, "test")),
                 toolkit_factory=toolkit, overlay_factory=lambda: overlay)
    try:
        out = tmp_path / "icon.png"
        result = eng.ok("capture_region", {"path": str(out)})
        assert result["bbox"] == [150, 120, 24, 16]
        assert result["window"] == "Notes - editor"
        assert (read_png(out) == region_of(ICON)).all()
        assert toolkit.capturer.grabs == [None]  # the still the overlay showed
        assert overlay.frames[0].rect == DESKTOP
    finally:
        eng.close()


def test_pick_point_reports_the_window_under_it(fake: FakeBackend) -> None:
    overlay = ScriptedOverlay((250, 200))
    eng = Engine(backend=EngineBackend(BackendChoice("fake", fake, "test")),
                 toolkit_factory=Toolkit(), overlay_factory=lambda: overlay)
    try:
        assert eng.ok("pick_point") == {
            "x": 250, "y": 200, "display_scale": 1.0, "window": "Notes - editor",
            "window_area": [100, 80, 200, 150], "window_point": [150, 120]}
        overlay.answer = (20, 10)  # outside Notes: the desktop window
        assert eng.ok("pick_point")["window"] == "Desktop"
        # With a window, the point is reported relative to it, wherever it is.
        assert eng.ok("pick_point", {"window": {"title": "Notes"}})["window_point"] == \
            [-80, -70]
    finally:
        eng.close()


def test_escape_cancels(fake: FakeBackend, tmp_path: Path) -> None:
    overlay = ScriptedOverlay(None)
    eng = Engine(backend=EngineBackend(BackendChoice("fake", fake, "test")),
                 toolkit_factory=Toolkit(), overlay_factory=lambda: overlay)
    try:
        for command, payload in (("capture_region", {"path": str(tmp_path / "a.png")}),
                                 ("pick_point", {})):
            error = eng.error(command, payload)
            assert error["code"] == "invalid_state"
            assert error["details"] == {"reason": "cancelled"}
        assert not (tmp_path / "a.png").exists()
    finally:
        eng.close()


def test_one_overlay_at_a_time_and_no_runs_meanwhile(fake: FakeBackend,
                                                     factory: FakeFactory) -> None:
    overlay = ScriptedOverlay((5, 5), block=True)
    eng = Engine(backend=EngineBackend(BackendChoice("fake", fake, "test")),
                 toolkit_factory=Toolkit(), overlay_factory=lambda: overlay,
                 run_factory=factory)
    try:
        pending = eng.send("pick_point")
        assert overlay.opened.wait(5)
        # The engine keeps serving, refuses a second overlay and any run.
        assert eng.ok("list_windows")["windows"]
        error = eng.error("pick_point")
        assert error["code"] == "busy" and error["details"] == {"reason": "overlay"}
        error = eng.error("run", {"script_id": eng.load_source(), "mode": "normal"})
        assert error["code"] == "busy" and error["details"] == {"reason": "overlay"}
        # Non-interactive tools still work.
        assert eng.ok("screenshot", {"path": str(Path(eng.ok("screenshot")["path"]))})
        overlay.release.set()
        assert eng.response(pending)["result"]["x"] == 5
        # Released: a run may start again.
        eng.ok("run", {"script_id": eng.load_source(), "mode": "normal"})
    finally:
        eng.close()


def test_no_overlay_during_a_run(engine: Engine, factory: FakeFactory) -> None:
    factory.behaviour.block = True
    run_id = engine.start(engine.load_source())
    error = engine.error("pick_point")
    assert error["code"] == "busy" and error["details"] == {"run_id": run_id}
    # Without an overlay, the tools work during a run.
    assert engine.ok("list_windows")["windows"]
    engine.ok("stop", {"run_id": run_id})
    engine.wait_finished(run_id)


def test_without_tkinter_there_is_no_overlay(fake: FakeBackend, tmp_path: Path) -> None:
    def no_tk() -> Any:
        raise ImportError("the overlay needs tkinter")

    eng = Engine(backend=EngineBackend(BackendChoice("fake", fake, "test")),
                 toolkit_factory=Toolkit(), overlay_factory=no_tk)
    try:
        error = eng.error("pick_point")
        assert error["code"] == "capability_missing"
        assert error["details"] == {"capability": "overlay"}
        # The non-interactive forms still work.
        eng.ok("capture_region", {"bbox": [0, 0, 5, 5], "path": str(tmp_path / "a.png")})
    finally:
        eng.close()


def test_end_of_input_cancels_an_open_overlay(fake: FakeBackend) -> None:
    overlay = ScriptedOverlay((5, 5), block=True)
    eng = Engine(backend=EngineBackend(BackendChoice("fake", fake, "test")),
                 toolkit_factory=Toolkit(), overlay_factory=lambda: overlay)
    pending = eng.send("pick_point")
    assert overlay.opened.wait(5)
    eng.close()
    response = eng.response(pending)
    assert response["error"]["details"] == {"reason": "cancelled"}


def test_embedded_png_round_trips_through_base64(engine: Engine) -> None:
    script_id = engine.ok("load", {"source": SCRIPT})["script_id"]
    engine.ok("capture_region", {"bbox": [150, 120, 24, 16], "script_id": script_id,
                                 "name": "icon", "transparent": True})
    source = engine.ok("get_script", {"script_id": script_id})["source"]
    data = source.split("png: |\n", 1)[1]
    png = base64.b64decode("".join(line.strip() for line in data.splitlines()))
    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    assert image.shape == (16, 24, 4)
