"""The waits through the interpreter: wait_image_in, wait_image_out, wait_window_in and
wait_window_out, and window lookups that retry under a timeout."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from runtime_helpers import ScaledClock
from runtime_input_helpers import backend
from runtime_vision_helpers import FakeToolkit, JumpClock, VisionRun, hit, miss, with_images

from j5_macro.backends.base import Button, Capabilities, Rect, WindowInfo
from j5_macro.backends.fake import FakeBackend

BOX = Rect(300, 400, 40, 20)  # centre (320, 410)
EDITOR = Rect(100, 200, 800, 600)
EDITOR_TITLE = "notes.txt - Mousepad"


class Timeline(FakeBackend):
    """Windows that open and close on the run's clock: ``timeline`` maps a time in
    seconds to the windows from then on. Counts every window lookup."""

    def __init__(self, clock: Any, timeline: dict[float, dict[str, Rect]]) -> None:
        super().__init__()
        self.clock = clock
        self.timeline = timeline
        self.lookups = 0
        self.last_lookup = 0.0
        """The clock's time at the last lookup."""

    def _now(self) -> None:
        self.lookups += 1
        self.last_lookup = self.clock.now()
        current: dict[str, Rect] = {}
        for at, windows in sorted(self.timeline.items()):
            if self.clock.now() >= at:
                current = windows
        self.windows = dict(current)

    def find_window(self, title: str) -> Rect | None:
        self._now()
        return super().find_window(title)

    def list_windows(self) -> list[WindowInfo]:
        self._now()
        return super().list_windows()


def wait_run(tmp_path: Path, steps: str, matches: list[Any] | None = None, *,
             timeline: dict[float, dict[str, Rect]] | None = None,
             **options: Any) -> VisionRun:
    script = with_images(f"steps:\n{steps}", tmp_path, "img/x.png")
    clock = options.pop("clock", None) or JumpClock()
    if timeline is not None:
        options["backend"] = Timeline(clock, timeline)
    run = VisionRun(script, FakeToolkit(matches or [miss(0.1)]), clock=clock, **options)
    run.backend = options.get("backend")  # type: ignore[attr-defined]
    return run


def result(run: VisionRun, step_id: str) -> dict[str, Any]:
    finished = run.recorder.of("step_finished", step_id=step_id)
    assert finished, run.recorder.summary()
    return finished[0]["result"]


def failure(run: VisionRun) -> dict[str, Any]:
    failed = run.recorder.of("step_failed")
    assert len(failed) == 1, run.recorder.summary()
    return failed[0]


# --- wait_window_in ---------------------------------------------------------------------


def test_wait_window_in_waits_for_the_window_and_reports_its_full_title(
        tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_window_in: {title: Mousepad}\n"
                   "    as: editor\n"
                   "  - id: c\n    click: {x: 10, y: 20, window: {title: \"${editor.window}\"}}\n",
                   timeline={0.5: {EDITOR_TITLE: EDITOR}})
    assert run.join().status == "completed"
    assert result(run, "w") == {"window": EDITOR_TITLE, "bbox": [100, 200, 800, 600]}
    assert run.clock.t == pytest.approx(0.5)  # polls at 0, .25 and .5 s
    assert run.recorder.of("variable_changed", name="editor")[0]["new"] == {
        "window": EDITOR_TITLE, "bbox": [100, 200, 800, 600]}
    assert run.factory.session.named("click")[0][1] == (110, 220, Button.LEFT, 1)
    assert run.recorder.of("image_search") == []  # window polls emit no events


def test_wait_window_in_finds_an_open_window_at_once(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_window_in: Mousepad\n",
                   timeline={0: {"Other": BOX, EDITOR_TITLE: EDITOR}})
    assert run.join().status == "completed"
    assert result(run, "w")["window"] == EDITOR_TITLE
    assert run.clock.t == 0
    assert run.toolkit.created == 0  # windows need no vision


def test_wait_window_in_times_out(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_window_in: {title: Mousepad, timeout: 1s}\n",
                   timeline={})
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "window_not_found"
    assert outcome.error.details == {"title": "Mousepad", "timeout": 1.0, "attempt": 1}
    assert "appeared in 1s" in outcome.error.message
    assert run.clock.t == pytest.approx(1.0)
    assert run.backend.lookups == 5  # at 0, .25, .5, .75 and 1 s


def test_the_common_timeout_replaces_the_wait_timeout(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    timeout: 500ms\n"
                   "    wait_window_in: {title: Mousepad, timeout: 1m}\n", timeline={})
    outcome = run.join()
    assert outcome.error is not None and outcome.error.details is not None
    assert outcome.error.details["timeout"] == 0.5
    assert run.clock.t == pytest.approx(0.5)


@pytest.mark.parametrize("step", ["wait_window_in", "wait_window_out"])
@pytest.mark.parametrize("engine_backend", [
    None,
    FakeBackend(capabilities=Capabilities(pointer_position=True, move_abs=True,
                                          move_rel=True, char_input=True,
                                          screen_capture=False, windows=False)),
])
def test_window_waits_need_the_windows_capability(tmp_path: Path, step: str,
                                                  engine_backend: Any) -> None:
    run = wait_run(tmp_path, f"  - {step}: Mousepad\n", backend=engine_backend,
                   backend_reason="no X")
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "capability_missing"
    assert "window waits" in outcome.error.message


# --- wait_window_out --------------------------------------------------------------------


def test_wait_window_out_waits_until_the_window_closes(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_window_out: {title: Save as}\n    as: closed\n",
                   timeline={0: {"Save as": BOX, EDITOR_TITLE: EDITOR},
                             0.75: {EDITOR_TITLE: EDITOR}})
    assert run.join().status == "completed"
    assert result(run, "w") == {"gone": True, "waited_ms": 750}
    assert run.recorder.of("variable_changed", name="closed")[0]["new"] == {
        "gone": True, "waited_ms": 750}


def test_wait_window_out_succeeds_at_once_when_the_window_is_absent(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_window_out: Save as\n",
                   timeline={0: {EDITOR_TITLE: EDITOR}})
    assert run.join().status == "completed"
    assert result(run, "w") == {"gone": True, "waited_ms": 0}
    assert run.backend.lookups == 1


def test_wait_window_out_times_out_with_still_visible(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_window_out: {title: Save, timeout: 1s}\n",
                   timeline={0: {"Save as": BOX}})
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "still_visible"
    assert outcome.error.details == {"title": "Save", "window": "Save as", "timeout": 1.0,
                                     "attempt": 1}
    assert run.clock.t == pytest.approx(1.0)


# --- wait_image_in and windows that are still opening -----------------------------------


def test_wait_image_in_waits_for_its_window_then_for_the_image(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_image_in: {image: img/x.png, "
                   "window: {title: Mousepad}}\n", [miss(0.3), hit(0.95, BOX)],
                   timeline={0.5: {EDITOR_TITLE: EDITOR}})
    assert run.join().status == "completed"
    searches = run.searches()
    assert [s["attempt"] for s in searches] == [3, 4]  # polls 1 and 2 found no window
    assert [s["region"] for s in searches] == [[100, 200, 800, 600]] * 2
    assert result(run, "w")["window"] == "Mousepad"
    assert run.clock.t == pytest.approx(0.75)


def test_wait_image_in_with_a_window_but_no_image_is_image_not_found(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_image_in: {image: img/x.png, timeout: 1s, "
                   "window: {title: Mousepad}}\n", [miss(0.4)],
                   timeline={0.5: {EDITOR_TITLE: EDITOR}}, run_dir=tmp_path)
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "image_not_found"
    assert outcome.error.details is not None
    assert outcome.error.details["attempts"] == 5 and outcome.error.details["best_score"] == 0.4
    assert failure(run)["screenshot"] == "captures/0001-w-5.png"


def test_a_window_that_went_away_ends_like_the_image(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_image_in: {image: img/x.png, timeout: 1s, "
                   "window: {title: Mousepad}}\n", [miss(0.4)],
                   timeline={0: {EDITOR_TITLE: EDITOR}, 0.5: {}})
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "image_not_found"
    assert len(run.searches()) == 2


def test_click_waits_for_its_window_with_the_common_timeout(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: c\n    timeout: 2s\n"
                   "    click: {image: img/x.png, window: {title: Mousepad}}\n",
                   [hit(0.95, BOX)], timeline={0.25: {EDITOR_TITLE: EDITOR}})
    assert run.join().status == "completed"
    assert run.factory.session.named("click")[0][1] == (320, 410, Button.LEFT, 1)
    assert run.searches()[0]["attempt"] == 2


def test_a_region_next_to_a_kept_result_waits_for_its_window(tmp_path: Path) -> None:
    label = {"bbox": [110, 210, 40, 20], "window": "Mousepad", "window_bbox": [10, 10, 40, 20]}
    run = wait_run(tmp_path, "  - set: {name: label, value: " + repr(label).replace("'", "")
                   + "}\n  - id: w\n    wait_image_in: {image: img/x.png, "
                   "region: {at: \"${label}\", offset: [0, 40]}}\n",
                   [hit(0.95, BOX)], timeline={0.25: {EDITOR_TITLE: EDITOR}})
    assert run.join().status == "completed"
    assert run.searches()[0]["region"] == [110, 250, 40, 20]


# --- wait_image_out ---------------------------------------------------------------------


def test_wait_image_out_waits_until_the_image_is_gone(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_image_out: {image: img/x.png}\n    as: done\n",
                   [hit(0.97, BOX), hit(0.96, BOX), miss(0.2)], run_dir=tmp_path)
    assert run.join().status == "completed"
    assert result(run, "w") == {"gone": True, "waited_ms": 500}
    assert [s["found"] for s in run.searches()] == [True, True, False]
    assert not (tmp_path / "captures").exists()  # nothing failed
    assert run.recorder.of("variable_changed", name="done")[0]["new"]["gone"] is True


def test_wait_image_out_succeeds_at_once_when_the_image_is_absent(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_image_out: img/x.png\n", [miss(0.2)])
    assert run.join().status == "completed"
    assert result(run, "w") == {"gone": True, "waited_ms": 0}
    assert len(run.searches()) == 1


def test_wait_image_out_times_out_with_still_visible(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_image_out: {image: img/x.png, timeout: 1s}\n",
                   [hit(0.97, BOX)], run_dir=tmp_path)
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "still_visible"
    assert outcome.error.details == {"image": "img/x.png", "score": 0.97, "timeout": 1.0,
                                     "attempt": 1}
    assert len(run.searches()) == 5
    assert failure(run)["screenshot"] == "captures/0001-w-5.png"
    assert run.toolkit.saved == [(tmp_path / "captures" / "0001-w-5.png", BOX)]


def test_wait_image_out_needs_its_window(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_image_out: {image: img/x.png, timeout: 1s, "
                   "window: {title: Mousepad}}\n", [miss(0.2)], timeline={})
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "window_not_found"
    assert outcome.error.details == {"title": "Mousepad", "timeout": 1.0, "attempt": 1}
    assert run.searches() == []


def test_wait_image_out_is_satisfied_once_the_window_is_there(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_image_out: {image: \"img/x.png\", "
                   "window: {title: Mousepad}}\n", [miss(0.2)],
                   timeline={0.25: {EDITOR_TITLE: EDITOR}})
    assert run.join().status == "completed"
    assert result(run, "w") == {"gone": True, "waited_ms": 250}


# --- image_visible with a missing window ------------------------------------------------


def test_image_visible_is_false_when_the_window_is_missing(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: look\n    if:\n      when: {image_visible: "
                   "{image: img/x.png, window: {title: Ghost}}}\n      then: []\n",
                   [hit(0.99, BOX)], backend=backend())
    assert run.join().status == "completed"
    assert run.recorder.of("branch_taken")[0]["value"] is False
    assert run.searches() == []
    assert run.recorder.of("log", level="warning") == []


# --- pause and stop ---------------------------------------------------------------------


def test_pause_does_not_consume_a_window_wait(tmp_path: Path) -> None:
    # Scale 1000: the 100 s timeout lasts 0.1 s of real time.
    run = wait_run(tmp_path, "  - id: w\n    wait_window_in: {title: Ghost, timeout: 100s}\n",
                   timeline={}, clock=ScaledClock(1000.0), background=True)
    run.recorder.wait_count("step_started")
    run.controller.pause()
    run.recorder.wait_paused()
    time.sleep(0.3)  # 300 s of run time, three times the timeout
    run.controller.resume()
    outcome = run.join()
    assert outcome.error is not None and outcome.error.code == "window_not_found"
    # It gave up after 100 s of unpaused time: the 300 s paused did not count.
    assert run.backend.last_lookup >= 400


def test_stop_during_wait_image_out(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: w\n    wait_image_out: {image: img/x.png, timeout: 1h}\n",
                   [hit(0.97, BOX)], clock=ScaledClock(1000.0), background=True)
    run.recorder.wait_count("image_search", 2)
    run.controller.stop()
    assert run.join().status == "stopped"
    assert not run.recorder.of("step_failed")


# --- dry runs ---------------------------------------------------------------------------


def test_dry_runs_check_each_wait_once(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: wi\n    wait_window_in: Mousepad\n"
                   "  - id: wo\n    wait_window_out: {title: Save as, timeout: 3s}\n"
                   "  - id: ii\n    wait_image_in: {image: img/x.png, window: {title: Mousepad}}\n"
                   "  - id: io\n    wait_image_out: img/x.png\n",
                   [hit(0.97, BOX)], timeline={0: {EDITOR_TITLE: EDITOR}}, mode="dry")
    assert run.join().status == "completed"
    assert result(run, "wi") == {
        "dry_run": True, "found": True, "window": EDITOR_TITLE, "bbox": [100, 200, 800, 600],
        "description": "would wait up to 10s for a window whose title contains 'Mousepad' "
                       f"(found {EDITOR_TITLE!r})"}
    assert result(run, "wo") == {
        "dry_run": True, "found": False, "window": "Save as",
        "description": "would wait up to 3s for no window whose title contains 'Save as' "
                       "(not found)"}
    assert result(run, "ii")["found"] is True
    assert result(run, "io") == {
        "dry_run": True, "found": True, "score": 0.97, "bbox": [300, 400, 40, 20],
        "center": [320, 410],
        "description": "would wait up to 10s for image img/x.png to disappear "
                       "(found at (320, 410), score 0.970)"}
    assert len(run.searches()) == 2
    assert run.clock.t == 0
    assert run.factory.sessions == []


def test_dry_run_window_waits_without_a_backend_warn_and_go_on(tmp_path: Path) -> None:
    run = wait_run(tmp_path, "  - id: wi\n    wait_window_in: Mousepad\n", mode="dry",
                   backend=None, backend_reason="no X")
    assert run.join().status == "completed"
    warning = run.recorder.of("log", level="warning")[0]["message"]
    assert "window 'Mousepad' not searched" in warning
    assert result(run, "wi") == {
        "dry_run": True, "window": "Mousepad",
        "description": "would wait up to 10s for a window whose title contains 'Mousepad'"}
