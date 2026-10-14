"""The display scale of runs: templates resized by display / capture scale, pixel
offsets that follow it, offsets in units of the target's size (docs/contract.md,
"Script format and step registry": offsets, display scale)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from runtime_vision_helpers import (
    FakeToolkit,
    VisionRun,
    hit,
    png_header,
    with_embedded,
    with_images,
)

from j5_macro.backends.base import Rect
from j5_macro.backends.fake import FakeBackend
from j5_macro.runtime.display import DisplayScale, monitor_scales, run_display_scale

BOX = Rect(300, 400, 40, 20)  # centre (320, 410)
PRIMARY = Rect(0, 0, 1920, 1080)
SECOND = Rect(1920, 0, 1920, 1080)


def scaled(*monitors: tuple[Rect, float], **windows: Rect) -> FakeBackend:
    return FakeBackend(windows=dict(windows), monitors=list(monitors))


def vision(tmp_path: Path, script: str, backend: FakeBackend | None = None,
           matches: list[Any] | None = None, **options: Any) -> VisionRun:
    loaded = with_images(script, tmp_path, "img/x.png")
    toolkit = FakeToolkit(matches if matches is not None else [hit(0.97, BOX)])
    if backend is not None:
        options["backend"] = backend
    run = VisionRun(loaded, toolkit, **options)
    assert run.join().status == "completed", run.recorder.summary()
    return run


def scales(run: VisionRun) -> list[float]:
    """The scale each template was searched at."""
    return [search[3] for search in run.toolkit.searches]


def clicks(run: VisionRun) -> list[tuple[int, int]]:
    return [(call[1][0], call[1][1]) for call in run.factory.session.named("click")]


WAIT = "steps:\n  - id: w\n    wait_image_in: img/x.png\n"


# --- templates --------------------------------------------------------------------------


def test_templates_follow_the_detected_display_scale(tmp_path: Path) -> None:
    run = vision(tmp_path, WAIT, scaled((PRIMARY, 1.5)))
    assert scales(run) == [1.5]
    (search,) = run.searches()
    assert (search["scale"], search["display_scale"], search["capture_scale"]) == (
        1.5, 1.5, 1.0)


def test_capture_scale_divides_the_display_scale(tmp_path: Path) -> None:
    run = vision(tmp_path, "capture_scale: 1.5\n" + WAIT, scaled((PRIMARY, 1.5)))
    assert scales(run) == [1.0]
    assert run.searches()[0]["capture_scale"] == 1.5


def test_the_step_scale_multiplies_on_top(tmp_path: Path) -> None:
    run = vision(tmp_path, "steps:\n  - wait_image_in: {image: img/x.png, scale: 2}\n",
                 scaled((PRIMARY, 1.5)))
    assert scales(run) == [3.0]
    assert run.searches()[0]["scale"] == 3.0


def test_without_a_backend_the_display_scale_is_1(tmp_path: Path) -> None:
    run = vision(tmp_path, "capture_scale: 2\n" + WAIT)
    assert scales(run) == [0.5]
    assert run.searches()[0]["display_scale"] == 1.0


def test_the_run_display_scale_replaces_detection(tmp_path: Path) -> None:
    run = vision(tmp_path, WAIT, scaled((PRIMARY, 1.5)), display_scale=1.25)
    assert scales(run) == [1.25]


def test_a_failing_detection_counts_as_1(tmp_path: Path) -> None:
    class Broken(FakeBackend):
        def display_scale(self, x: int, y: int) -> float | None:
            raise OSError("no monitor")

    run = vision(tmp_path, WAIT, Broken())
    assert scales(run) == [1.0]


def test_each_search_uses_the_monitor_it_looks_at(tmp_path: Path) -> None:
    backend = scaled((PRIMARY, 1.0), (SECOND, 2.0), Notes=Rect(2000, 100, 800, 600))
    run = vision(tmp_path, """
steps:
  - wait_image_in: {image: img/x.png, window: {title: Notes}}
  - wait_image_in: {image: img/x.png, region: [100, 100, 200, 200]}
  - wait_image_in: {image: img/x.png, region: [2000, 100, 200, 200]}
  - wait_image_in: img/x.png
""", backend)
    # The window and the last region are on the second monitor; the whole desktop is
    # searched at the primary monitor's scale.
    assert scales(run) == [2.0, 1.0, 2.0, 1.0]


def test_embedded_images_carry_their_capture_scale(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    script = with_embedded("""\
  - wait_image_in: "#big"
  - wait_image_in: "#plain"
""", monkeypatch, big=png_header(), plain=png_header())
    images = {**script.images, "big": dataclasses.replace(script.images["big"], scale=1.5)}
    script = dataclasses.replace(script, capture_scale=1.25, images=images)
    toolkit = FakeToolkit([hit(0.97, BOX)])
    run = VisionRun(script, toolkit, backend=scaled((PRIMARY, 1.5)))
    assert run.join().status == "completed"
    assert scales(run) == [1.0, 1.2]
    assert [s["capture_scale"] for s in run.searches()] == [1.5, 1.25]


# --- offsets ----------------------------------------------------------------------------


def test_pixel_offsets_of_image_targets_follow_the_template(tmp_path: Path) -> None:
    run = vision(tmp_path, """
steps:
  - click: {image: img/x.png, offset: [10, -4]}
""", scaled((PRIMARY, 1.5)))
    assert clicks(run) == [(335, 404)]
    assert run.factory.session.targets == [None]


def test_size_units_follow_the_match_not_the_scale(tmp_path: Path) -> None:
    run = vision(tmp_path, """
steps:
  - click: {image: img/x.png, offset: [0.5w, -1h]}
  - click: {image: img/x.png, offset: [1h, 0]}
""", scaled((PRIMARY, 1.5)))
    assert clicks(run) == [(340, 390), (340, 410)]


def test_offsets_of_at_targets_use_the_script_capture_scale(tmp_path: Path) -> None:
    run = vision(tmp_path, """
capture_scale: 0.5
steps:
  - id: find
    wait_image_in: img/x.png
    as: label
  - click: {at: "${label}", offset: [10, 5]}
  - click: {at: "${label}", offset: [1w, 0.5h]}
  - click: {at: "${label}", offset: ["${-1}", 0]}
""", FakeBackend(), display_scale=1.5)
    # display 1.5 / capture 0.5: pixels count three times.
    assert clicks(run) == [(350, 425), (360, 420), (317, 410)]


def test_offsets_of_x_y_targets_are_never_scaled(tmp_path: Path) -> None:
    run = vision(tmp_path, "steps:\n  - click: {x: 100, y: 100, offset: [10, 0]}\n",
                 FakeBackend(), display_scale=2.0)
    assert clicks(run) == [(110, 100)]


def test_interpolated_size_units_on_x_y_targets_fail(tmp_path: Path) -> None:
    loaded = with_images("vars: {dx: 2w}\nsteps:\n  - id: c\n"
                         "    click: {x: 1, y: 1, offset: [\"${dx}\", 0]}\n",
                         tmp_path, "img/x.png")
    run = VisionRun(loaded, FakeToolkit(), backend=FakeBackend())
    assert run.join().status == "failed"
    (failed,) = run.recorder.of("step_failed")
    assert failed["error"]["code"] == "validation_failed"
    assert failed["error"]["details"]["param"] == "offset"
    assert "x/y target" in failed["error"]["message"]


def test_regions_at_a_result_scale_their_pixels_and_take_size_units(tmp_path: Path) -> None:
    run = vision(tmp_path, """
steps:
  - id: find
    wait_image_in: img/x.png
    as: label
  - wait_image_in: {image: img/x.png, region: {at: "${label}", offset: [0, 1h], size: [2w, 20]}}
  - wait_image_in: {image: img/x.png, region: {at: "${label}", offset: [10, 0]}}
""", display_scale=2.0)
    grabs = run.toolkit.capturer.grabs
    # 2w = 80; 20 px at display 2 = 40; centred 1h (20) below the label's centre.
    assert grabs[1] == Rect(280, 410, 80, 40)
    # The default size is the label's own; the 10 px offset counts twice.
    assert grabs[2] == Rect(320, 400, 40, 20)


def test_drag_points_take_units_and_scale(tmp_path: Path) -> None:
    run = vision(tmp_path, """
steps:
  - id: find
    wait_image_in: img/x.png
    as: label
  - drag: {from: {at: "${label}", offset: [-0.5w, 0]}, to: {image: img/x.png, offset: [10, 0]}}
""", scaled((PRIMARY, 2.0)))
    (drag,) = run.factory.session.named("drag")
    assert drag[1][:2] == ((300, 410), (340, 410))


def test_dry_runs_scale_their_searches(tmp_path: Path) -> None:
    run = vision(tmp_path, WAIT, scaled((PRIMARY, 1.5)), mode="dry")
    assert scales(run) == [1.5]
    assert run.factory.sessions == []


# --- the display scale object -----------------------------------------------------------


def test_display_scale_sources() -> None:
    assert run_display_scale(1.25, scaled((PRIMARY, 2.0))) == (1.25, "run")
    assert run_display_scale(None, scaled((PRIMARY, 2.0))) == (2.0, "detected")
    assert run_display_scale(None, None) == (1.0, "default")
    blind = FakeBackend()
    blind.capabilities = blind.capabilities.__class__(
        pointer_position=True, move_abs=True, move_rel=True, char_input=True,
        screen_capture=False)
    assert run_display_scale(None, blind) == (1.0, "default")
    assert monitor_scales(blind) == []


def test_display_scale_ignores_nonsense() -> None:
    weird = scaled((PRIMARY, float("nan")))
    assert DisplayScale(backend=weird).at((5, 5)) == 1.0
    assert monitor_scales(weird) == []
    assert DisplayScale(override=-1, capture_scale=0).override is None
    assert DisplayScale(capture_scale=0).capture_scale == 1.0
    two = DisplayScale(backend=scaled((PRIMARY, 1.0), (SECOND, 2.0)), capture_scale=0.5)
    assert two.at() == 1.0 and two.at((2500, 10)) == 2.0
    assert two.ratio_at((2500, 10)) == 4.0
    assert two.ratio_at((2500, 10), capture_scale=2.0) == 1.0


def test_monitor_scales_report_every_monitor() -> None:
    assert monitor_scales(scaled((PRIMARY, 1.0), (SECOND, 1.5))) == [
        {"bbox": [0, 0, 1920, 1080], "scale": 1.0},
        {"bbox": [1920, 0, 1920, 1080], "scale": 1.5}]
    assert monitor_scales(None) == []

