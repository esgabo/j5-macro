"""Vision through the interpreter: wait_image, image targets, image_visible, captures."""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import pytest
from runtime_helpers import load
from runtime_input_helpers import backend
from runtime_vision_helpers import (
    FakeToolkit,
    VisionRun,
    hit,
    miss,
    png_header,
    with_embedded,
    with_images,
)

from j5_macro.backends.base import Button, Capabilities, Rect
from j5_macro.backends.fake import FakeBackend

BOX = Rect(300, 400, 40, 20)  # centre (320, 410)
NOTES = Rect(100, 200, 800, 600)


def vision_run(tmp_path: Path, steps: str, matches: list[Any], *images: str,
               **options: Any) -> VisionRun:
    script = with_images(f"steps:\n{steps}", tmp_path, *(images or ("img/x.png",)))
    return VisionRun(script, FakeToolkit(matches), **options)


def step_failed(run: VisionRun) -> dict[str, Any]:
    failed = run.recorder.of("step_failed")
    assert len(failed) == 1, run.recorder.summary()
    return failed[0]


# --- wait_image -------------------------------------------------------------------------


def test_wait_image_found_on_the_third_attempt(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: w\n    wait_image: {image: img/x.png, timeout: 5s}\n",
                     [miss(0.2), miss(0.5), hit(0.97, BOX)], run_dir=tmp_path)
    assert run.join().status == "completed"
    assert run.recorder.summary()[1:] == [
        "step_started:w", "image_search", "image_search", "image_search", "step_finished:w"]
    searches = run.searches()
    assert [s["attempt"] for s in searches] == [1, 2, 3]
    assert [s["found"] for s in searches] == [False, False, True]
    assert searches[2] == {"template": "img/x.png", "threshold": 0.9, "found": True,
                           "best_score": 0.97, "bbox": [300, 400, 40, 20],
                           "region": [0, 0, 1920, 1080], "scale": 1.0,
                           "display_scale": 1.0, "capture_scale": 1.0, "attempt": 3,
                           "elapsed_ms": 0}
    assert run.recorder.of("step_finished")[0]["result"] == {
        "found": True, "score": 0.97, "bbox": [300, 400, 40, 20], "center": [320, 410]}
    assert run.clock.t == pytest.approx(0.5)  # two intervals of 0.25 s
    assert not (tmp_path / "captures").exists()  # on_failure: nothing failed
    assert run.toolkit.templates == [tmp_path / "img" / "x.png"]
    assert run.toolkit.capturer.closed


def test_wait_image_times_out(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: w\n    wait_image: {image: img/x.png, timeout: 1s, "
                     "threshold: 0.8}\n", [miss(0.42)], run_dir=tmp_path)
    outcome = run.join()
    assert outcome.status == "failed"
    assert run.recorder.summary()[1:] == ["step_started:w"] + ["image_search"] * 5 + [
        "step_failed:w"]
    searches = run.searches()
    assert [s["attempt"] for s in searches] == [1, 2, 3, 4, 5]  # at 0, .25, .5, .75, 1 s
    assert [s.get("screenshot") for s in searches] == [None] * 4 + ["captures/0001-w-5.png"]
    failed = step_failed(run)
    assert failed["screenshot"] == "captures/0001-w-5.png"
    error = failed["error"]
    assert error["code"] == "image_not_found"
    assert error["details"] == {"best_score": 0.42, "threshold": 0.8, "template": "img/x.png",
                                "attempts": 5, "attempt": 1}
    assert (tmp_path / "captures" / "0001-w-5.png").is_file()
    assert run.toolkit.saved == [(tmp_path / "captures" / "0001-w-5.png", Rect(5, 5, 10, 10))]


def test_captures_all_saves_every_attempt_with_the_match(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: w\n    wait_image: img/x.png\n",
                     [miss(0.3), hit(0.95, BOX)], run_dir=tmp_path, captures="all")
    assert run.join().status == "completed"
    paths = [s["screenshot"] for s in run.searches()]
    assert paths == ["captures/0001-w-1.png", "captures/0002-w-2.png"]
    assert [outline for _, outline in run.toolkit.saved] == [Rect(5, 5, 10, 10), BOX]
    assert sorted(p.name for p in (tmp_path / "captures").iterdir()) == [
        "0001-w-1.png", "0002-w-2.png"]


def test_nothing_is_saved_without_a_run_directory(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: w\n    wait_image: {image: img/x.png, timeout: 0s}\n",
                     [miss(0.3)], captures="all")
    assert run.join().status == "failed"
    assert "screenshot" not in run.searches()[0]
    assert "screenshot" not in step_failed(run)
    assert run.toolkit.saved == []


def test_a_capture_that_cannot_be_saved_only_warns(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: w\n    wait_image: {image: img/x.png, timeout: 0s}\n",
                     [miss(0.3)], run_dir=tmp_path / "gone")
    assert run.join().error.code == "image_not_found"  # type: ignore[union-attr]
    assert "cannot save captures" in run.recorder.of("log", level="warning")[0]["message"]
    assert "screenshot" not in step_failed(run)


def test_the_common_timeout_replaces_the_wait_image_timeout(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: w\n    timeout: 500ms\n"
                     "    wait_image: {image: img/x.png, timeout: 1m}\n", [miss(0.1)])
    assert run.join().status == "failed"
    assert len(run.searches()) == 3  # at 0, .25 and .5 s


def test_dry_run_searches_once_and_continues(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: w\n    wait_image: img/x.png\n  - id: after\n"
                     "    log: next\n", [miss(0.3)], mode="dry", run_dir=tmp_path)
    assert run.join().status == "completed"
    assert len(run.searches()) == 1
    result = run.recorder.of("step_finished", step_id="w")[0]["result"]
    assert result == {"dry_run": True, "found": False, "score": 0.3, "bbox": [5, 5, 10, 10],
                      "center": [10, 10], "description": "would wait up to 10s for image img/x.png "
                                     "(not found, best score 0.300)"}
    assert run.recorder.of("step_finished", step_id="after")
    assert not (tmp_path / "captures").exists()
    assert run.factory.sessions == []


# --- pause and stop ---------------------------------------------------------------------


def test_pause_does_not_consume_the_timeout(tmp_path: Path) -> None:
    # Scale 1000: the 100 s timeout lasts 0.1 s of real time.
    run = vision_run(tmp_path, "  - id: w\n    wait_image: {image: img/x.png, timeout: 100s}\n",
                     [miss(0.1)], background=True)
    run.recorder.wait_count("image_search")
    run.controller.pause()
    run.recorder.wait_paused()
    time.sleep(0.3)  # 300 s of run time, three times the timeout
    before = len(run.searches())
    run.controller.resume()
    run.recorder.wait_count("image_search", before + 3)
    assert not run.recorder.of("step_failed")
    run.controller.stop()
    outcome = run.join()
    assert outcome.status == "stopped"
    assert not run.recorder.of("step_failed")


def test_stop_during_a_wait(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: w\n    wait_image: {image: img/x.png, timeout: 1h}\n",
                     [miss(0.1)], background=True)
    run.recorder.wait_count("image_search", 2)
    run.controller.stop()
    assert run.join().status == "stopped"
    assert run.recorder.types()[-1] == "state_changed"
    assert run.toolkit.capturer.closed


# --- paths, windows and regions ---------------------------------------------------------


def test_images_resolve_against_the_current_directory_without_a_script_file(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "x.png").write_bytes(b"png")
    monkeypatch.chdir(tmp_path)
    toolkit = FakeToolkit([hit(0.99, BOX)])
    run = VisionRun(load("steps:\n  - wait_image: x.png\n"), toolkit)
    assert run.join().status == "completed"
    assert toolkit.templates == [tmp_path / "x.png"]


def test_missing_template(tmp_path: Path) -> None:
    script = with_images("steps:\n  - id: w\n    wait_image: img/none.png\n", tmp_path)
    run = VisionRun(script, FakeToolkit())
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "template_missing"
    assert outcome.error.details == {"template": "img/none.png", "attempt": 1,
                                     "path": str(tmp_path / "img" / "none.png")}
    assert run.searches() == []


@pytest.mark.parametrize(("params", "grab", "region"), [
    ("region: [10, 20, 30, 40], window: {title: Notes}", Rect(110, 220, 30, 40),
     [110, 220, 30, 40]),
    ("window: {title: Notes}", NOTES, [100, 200, 800, 600]),
    ("region: [10, 20, 30, 40]", Rect(10, 20, 30, 40), [10, 20, 30, 40]),
    ("threshold: 0.5", None, [0, 0, 1920, 1080]),
])
def test_window_and_region(tmp_path: Path, params: str, grab: Rect | None,
                           region: list[int]) -> None:
    run = vision_run(tmp_path, f"  - wait_image: {{image: img/x.png, {params}}}\n",
                     [hit(0.99, BOX)], backend=backend(**{"My Notes": NOTES}))
    assert run.join().status == "completed"
    assert run.toolkit.capturer.grabs == [grab]
    assert run.searches()[0]["region"] == region


def test_missing_window(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - wait_image: {image: img/x.png, window: {title: Ghost}}\n",
                     [hit(0.99, BOX)], backend=backend())
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "window_not_found"
    assert outcome.error.details == {"title": "Ghost", "attempt": 1}


@pytest.mark.parametrize("engine_backend", [
    None,
    FakeBackend(capabilities=Capabilities(pointer_position=True, move_abs=True,
                                          move_rel=True, char_input=True,
                                          screen_capture=True, windows=False)),
])
def test_window_searches_need_the_windows_capability(tmp_path: Path,
                                                     engine_backend: Any) -> None:
    run = vision_run(tmp_path, "  - wait_image: {image: img/x.png, window: {title: N}}\n",
                     [hit(0.99, BOX)], backend=engine_backend, backend_reason="no X")
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "capability_missing"
    assert run.toolkit.capturer.grabs == []


def test_full_screen_searches_work_without_a_backend(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - wait_image: img/x.png\n", [hit(0.99, BOX)])
    assert run.join().status == "completed"


def test_vision_unavailable_is_capability_missing(tmp_path: Path) -> None:
    script = with_images("steps:\n  - id: a\n    on_error: continue\n"
                         "    wait_image: img/x.png\n  - id: b\n    wait_image: img/x.png\n",
                         tmp_path, "img/x.png")
    toolkit = FakeToolkit()
    toolkit.error = RuntimeError("cannot open display :0")
    run = VisionRun(script, toolkit)
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "capability_missing"
    assert "the screen cannot be captured: cannot open display :0" in outcome.error.message
    assert toolkit.created == 1  # the failure is remembered for the rest of the run


# --- click and move on images -----------------------------------------------------------


def test_click_on_an_image(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: c\n    click: {image: img/x.png, button: right, "
                     "threshold: 0.7, scale: 1.25}\n", [hit(0.91, BOX)], backend=backend())
    assert run.join().status == "completed"
    session = run.factory.session
    assert session.named("click")[0][1] == (320, 410, Button.RIGHT, 1)
    assert session.targets == [BOX]
    assert run.toolkit.searches[0][2:] == (0.7, 1.25)
    assert run.recorder.of("step_finished")[0]["result"] == {
        "x": 320, "y": 410, "button": "right", "clicks": 1, "image": "img/x.png",
        "score": 0.91, "bbox": [300, 400, 40, 20], "center": [320, 410]}


def test_click_on_an_image_with_an_offset_aims_at_the_point(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - click: {image: img/x.png, offset: [100, -10]}\n",
                     [hit(0.91, BOX)], backend=backend())
    assert run.join().status == "completed"
    session = run.factory.session
    assert session.named("click")[0][1] == (420, 400, Button.LEFT, 1)
    assert session.targets == [None]


def test_move_to_an_image_in_a_window_region(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - move: {image: img/x.png, window: {title: Notes}, "
                     "region: [0, 0, 400, 300]}\n", [hit(0.91, BOX)],
                     backend=backend(Notes=NOTES))
    assert run.join().status == "completed"
    assert run.toolkit.capturer.grabs == [Rect(100, 200, 400, 300)]
    assert run.factory.session.named("move")[0][1] == (320, 410)
    assert run.factory.session.targets == [BOX]


def test_click_on_a_missing_image(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: c\n    click: {image: img/x.png}\n", [miss(0.61)],
                     backend=backend(), run_dir=tmp_path)
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "image_not_found"
    assert outcome.error.details is not None
    assert outcome.error.details["best_score"] == 0.61
    assert outcome.error.details["threshold"] == 0.9
    assert outcome.error.details["template"] == "img/x.png"
    assert len(run.searches()) == 1
    assert step_failed(run)["screenshot"] == "captures/0001-c-1.png"
    assert not run.factory.session.named("click")


def test_click_waits_for_its_image_with_the_common_timeout(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: c\n    timeout: 1s\n    click: {image: img/x.png}\n",
                     [miss(0.2), miss(0.3), hit(0.95, BOX)], backend=backend())
    assert run.join().status == "completed"
    assert len(run.searches()) == 3
    assert run.factory.session.named("click")[0][1] == (320, 410, Button.LEFT, 1)


def test_drag_between_images(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: d\n    drag: {from: {image: img/x.png}, "
                     "to: {x: 5, y: 6}}\n", [hit(0.95, BOX)], backend=backend())
    assert run.join().status == "completed"
    assert run.factory.session.named("drag")[0][1] == ((320, 410), (5, 6), Button.LEFT)
    result = run.recorder.of("step_finished")[0]["result"]
    assert result["from_bbox"] == [300, 400, 40, 20] and result["from_score"] == 0.95


def test_dry_run_click_describes_the_would_be_target(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: c\n    click: {image: img/x.png}\n",
                     [hit(0.95, BOX)], mode="dry", backend=backend())
    assert run.join().status == "completed"
    result = run.recorder.of("step_finished")[0]["result"]
    assert result["description"] == ("would click left at image img/x.png "
                                     "(found at (320, 410), score 0.950)")
    assert result["found"] is True and result["bbox"] == [300, 400, 40, 20]
    assert run.factory.sessions == []


def test_dry_run_without_the_window_warns_and_continues(tmp_path: Path) -> None:
    run = vision_run(tmp_path, "  - id: c\n    click: {image: img/x.png, window: "
                     "{title: Ghost}}\n", [hit(0.95, BOX)], mode="dry", backend=backend())
    assert run.join().status == "completed"
    warning = run.recorder.of("log", level="warning")[0]["message"]
    assert "not searched" in warning and "Ghost" in warning


# --- image_visible ----------------------------------------------------------------------

IF_VISIBLE = """
  - id: look
    if:
      when: {condition}
      then:
        - id: yes
          log: seen
      else:
        - id: no
          log: unseen
"""


@pytest.mark.parametrize(("condition", "threshold"), [
    ("{image_visible: img/x.png}", 0.9),
    ("{image_visible: {image: img/x.png, threshold: 0.6, region: [1, 2, 3, 4]}}", 0.6),
])
def test_image_visible(tmp_path: Path, condition: str, threshold: float) -> None:
    run = vision_run(tmp_path, IF_VISIBLE.format(condition=condition), [hit(0.93, BOX)])
    assert run.join().status == "completed"
    assert run.recorder.of("branch_taken")[0]["branch"] == "then"
    assert run.searches()[0]["threshold"] == threshold
    assert run.toolkit.searches[0][2] == threshold


def test_image_visible_false_is_not_a_failure(tmp_path: Path) -> None:
    run = vision_run(tmp_path, IF_VISIBLE.format(condition="{image_visible: img/x.png}"),
                     [miss(0.2)], run_dir=tmp_path)
    assert run.join().status == "completed"
    assert run.recorder.of("branch_taken")[0]["branch"] == "else"
    assert len(run.searches()) == 1
    assert not (tmp_path / "captures").exists()


def test_image_visible_in_a_window(tmp_path: Path) -> None:
    condition = "{image_visible: {image: img/x.png, window: {title: Notes}}}"
    run = vision_run(tmp_path, IF_VISIBLE.format(condition=condition), [hit(0.93, BOX)],
                     backend=backend(Notes=NOTES))
    assert run.join().status == "completed"
    assert run.toolkit.capturer.grabs == [NOTES]


def test_image_visible_rejects_unknown_keys(tmp_path: Path) -> None:
    condition = "{image_visible: {image: img/x.png, colour: red}}"
    run = vision_run(tmp_path, IF_VISIBLE.format(condition=condition), [hit(0.93, BOX)])
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "validation_failed"


def test_image_visible_in_a_dry_run_searches_once(tmp_path: Path) -> None:
    run = vision_run(tmp_path, IF_VISIBLE.format(condition="{image_visible: img/x.png}"),
                     [hit(0.93, BOX)], mode="dry")
    assert run.join().status == "completed"
    assert run.recorder.of("branch_taken")[0]["branch"] == "then"
    assert len(run.searches()) == 1


def test_region_outside_the_screen_is_a_validation_error(tmp_path: Path) -> None:
    script = with_images("steps:\n  - wait_image: {image: img/x.png, "
                         "region: [5000, 5000, 10, 10]}\n", tmp_path, "img/x.png")
    toolkit = FakeToolkit([hit(0.99, BOX)])

    def refuse(region: Rect | None = None) -> Any:
        raise ValueError("region (5000, 5000, 10, 10) is outside the virtual desktop")

    toolkit.capturer.grab = refuse  # type: ignore[method-assign]
    outcome = VisionRun(script, toolkit).join()
    assert outcome.error is not None and outcome.error.code == "validation_failed"
    assert outcome.error.details is not None and outcome.error.details["param"] == "region"


# --- embedded images --------------------------------------------------------------------


def test_embedded_images_are_decoded_once_per_run_and_reported_as_written(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = png_header(tail=b"pixels")
    script = with_embedded(
        "  - id: w\n    wait_image: \"#box\"\n"
        "  - id: c\n    click: {image: \"#box\"}\n"
        "  - id: d\n    drag: {from: {image: \"#box\"}, to: {image: img/x.png}}\n"
        "  - id: i\n    if:\n      when: {image_visible: {image: \"#box\"}}\n"
        "      then: []\n",
        monkeypatch, box=data)
    script = with_images(script, tmp_path, "img/x.png")
    toolkit = FakeToolkit([hit(0.97, BOX)])
    run = VisionRun(script, toolkit, backend=backend(), run_dir=tmp_path, captures="all")
    assert run.join().status == "completed"
    assert toolkit.decoded == [(data, "#box")]  # decoded once, then kept for the run
    assert toolkit.templates == [tmp_path / "img" / "x.png"]
    assert [s["template"] for s in run.searches()] == [
        "#box", "#box", "#box", "img/x.png", "#box"]
    assert [path for path, *_ in toolkit.searches] == [
        Path("#box"), Path("#box"), Path("#box"), tmp_path / "img" / "x.png", Path("#box")]
    # The image data is never copied to the events or the run's directory.
    assert len(toolkit.saved) == 5
    encoded = json.dumps(run.recorder.events)
    assert script.images["box"].format == "png"
    assert "iVBOR" not in encoded and "pixels" not in encoded


def test_an_interpolated_reference_is_resolved_when_the_step_runs(
        monkeypatch: pytest.MonkeyPatch) -> None:
    script = with_embedded("  - id: w\n    wait_image: \"${icon}\"\n", monkeypatch,
                           ok=png_header())
    script = dataclasses.replace(script, vars={"icon": "#ok"})
    run = VisionRun(script, FakeToolkit([hit(0.99, BOX)]))
    assert run.join().status == "completed"
    assert run.searches()[0]["template"] == "#ok"


@pytest.mark.parametrize("mode", ["normal", "dry"])
def test_an_unknown_embedded_name_at_run_time_is_template_missing(
        monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    script = with_embedded("  - id: w\n    wait_image: \"${icon}\"\n", monkeypatch,
                           ok=png_header())
    script = dataclasses.replace(script, vars={"icon": "#gone"})
    toolkit = FakeToolkit([hit(0.99, BOX)])
    toolkit.error = ImportError("no vision")  # checked before the toolkit is needed
    outcome = VisionRun(script, toolkit, mode=mode).join()
    assert outcome.error is not None and outcome.error.code == "template_missing"
    assert outcome.error.details == {"template": "#gone", "attempt": 1}  # no path
    assert "no embedded image named 'gone'" in outcome.error.message
    assert toolkit.created == 0


def test_an_unreadable_embedded_image_is_template_missing(
        monkeypatch: pytest.MonkeyPatch) -> None:
    script = with_embedded("  - id: w\n    wait_image: \"#bad\"\n", monkeypatch,
                           bad=png_header())
    bad = dataclasses.replace(script.images["bad"], data=b"GIF89a")
    script = dataclasses.replace(script, images={"bad": bad})
    outcome = VisionRun(script, FakeToolkit()).join()
    assert outcome.error is not None and outcome.error.code == "template_missing"
    assert outcome.error.details == {"template": "#bad", "attempt": 1}
    assert "#bad is not a readable image" in outcome.error.message


def test_a_toolkit_without_a_decoder_cannot_load_embedded_images(
        monkeypatch: pytest.MonkeyPatch) -> None:
    script = with_embedded("  - id: w\n    wait_image: \"#ok\"\n", monkeypatch,
                           ok=png_header())
    toolkit = FakeToolkit([hit(0.99, BOX)])
    toolkit.embedding = False
    outcome = VisionRun(script, toolkit).join()
    assert outcome.error is not None and outcome.error.code == "capability_missing"


def test_embedded_images_do_not_depend_on_the_working_directory(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # an empty directory: the image lives in the script
    script = with_embedded("  - id: w\n    wait_image: \"#ok\"\n", monkeypatch,
                           ok=png_header())
    assert script.source_path is None
    run = VisionRun(script, FakeToolkit([hit(0.99, BOX)]))
    assert run.join().status == "completed"
