"""Strict dry runs (docs/contract.md, "Strict dry runs"): what a dry run only warns about
because the real run would fail there fails the step instead."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from runtime_helpers import run
from runtime_input_helpers import backend
from runtime_vision_helpers import FakeToolkit, VisionRun, hit, miss, with_images

from j5_macro.backends.base import Rect

BOX = Rect(300, 400, 40, 20)


def strict_run(tmp_path: Path, steps: str, matches: list[Any], **options: Any) -> VisionRun:
    script = with_images(f"steps:\n{steps}", tmp_path, "img/x.png")
    options.setdefault("mode", "dry")
    options.setdefault("strict", True)
    return VisionRun(script, FakeToolkit(matches), **options)


def failure(vision: VisionRun) -> dict[str, Any]:
    (failed,) = vision.recorder.of("step_failed")
    return dict(failed)


def warnings(recorder: Any) -> list[str]:
    return [log["message"] for log in recorder.of("log", level="warning")]


def test_an_image_not_found_fails_after_one_attempt(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: w\n    wait_image_in: {image: img/x.png, timeout: 5s}\n"
                        "  - id: after\n    log: next\n", [miss(0.42)], run_dir=tmp_path)
    outcome = vision.join()
    assert outcome.status == "failed"
    assert outcome.error is not None and outcome.error.code == "image_not_found"
    assert len(vision.searches()) == 1
    assert vision.clock.t == 0  # no waiting
    failed = failure(vision)
    assert failed["step_id"] == "w"
    assert failed["error"]["details"] == {"best_score": 0.42, "threshold": 0.9,
                                          "template": "img/x.png", "attempts": 1,
                                          "attempt": 1}
    assert "strict dry run" in failed["error"]["message"]
    assert failed["screenshot"] == "captures/0001-w-1.png"
    assert (tmp_path / "captures" / "0001-w-1.png").is_file()
    assert not vision.recorder.of("step_finished", step_id="after")
    assert vision.factory.sessions == []


def test_a_found_image_completes_as_a_dry_run(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: c\n    click: {image: img/x.png}\n",
                        [hit(0.95, BOX)], backend=backend())
    assert vision.join().status == "completed"
    result = vision.recorder.of("step_finished")[0]["result"]
    assert result["dry_run"] is True and result["found"] is True
    assert vision.factory.sessions == []


@pytest.mark.parametrize("steps", [
    "  - id: c\n    click: {image: img/x.png}\n",
    "  - id: c\n    drag: {from: {image: img/x.png}, to: {x: 1, y: 2}}\n",
    "  - id: d\n    drag: {from: {x: 1, y: 2}, to: {image: img/x.png}}\n",
])
def test_input_step_targets_must_be_found(tmp_path: Path, steps: str) -> None:
    vision = strict_run(tmp_path, steps, [miss(0.3)], backend=backend())
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "image_not_found"


def test_on_error_continue_applies(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: w\n    on_error: continue\n"
                        "    wait_image_in: img/x.png\n  - id: after\n    log: next\n",
                        [miss(0.3)])
    assert vision.join().status == "completed"
    assert failure(vision)["error"]["code"] == "image_not_found"
    assert vision.recorder.of("step_finished", step_id="after")


def test_a_missing_window_fails(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: c\n    click: {image: img/x.png, window: "
                        "{title: Ghost}}\n", [hit(0.95, BOX)], backend=backend())
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "window_not_found"
    assert warnings(vision.recorder) == []


def test_without_vision_the_search_fails(tmp_path: Path) -> None:
    script = with_images("steps:\n  - id: c\n    click: {image: img/x.png}\n", tmp_path,
                         "img/x.png")
    outcome, rec = run(script, mode="dry", strict=True)
    assert outcome.error is not None and outcome.error.code == "capability_missing"
    assert "vision" in outcome.error.message
    # Without strict, the same dry run only warns.
    outcome, rec = run(script, mode="dry")
    assert outcome.status == "completed"
    assert any("not searched" in message for message in warnings(rec))


IF_VISIBLE = """
  - id: look
    if:
      when: {image_visible: img/x.png}
      then:
        - id: yes
          log: seen
      else:
        - id: no
          log: unseen
"""


def test_image_visible_is_still_false_when_not_found(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, IF_VISIBLE, [miss(0.2)], run_dir=tmp_path)
    assert vision.join().status == "completed"
    assert vision.recorder.of("branch_taken")[0]["branch"] == "else"
    assert not (tmp_path / "captures").exists()


def test_image_visible_fails_when_it_cannot_search(tmp_path: Path) -> None:
    script = with_images(f"steps:\n{IF_VISIBLE}", tmp_path, "img/x.png")
    outcome, _ = run(script, mode="dry", strict=True)
    assert outcome.error is not None and outcome.error.code == "capability_missing"
    outcome, rec = run(script, mode="dry")
    assert outcome.status == "completed"
    assert rec.of("branch_taken")[0]["branch"] == "else"


def test_an_unknown_condition_fails() -> None:
    script = "steps:\n  - id: look\n    if:\n      when: {pixel_is_red: [1, 2]}\n" \
             "      then:\n        - log: red\n"
    outcome, _ = run(script, mode="dry", strict=True)
    assert outcome.error is not None and outcome.error.code == "expression_error"
    outcome, rec = run(script, mode="dry")
    assert outcome.status == "completed" and warnings(rec)


def test_strict_only_applies_to_dry_runs(tmp_path: Path) -> None:
    """The engine refuses strict outside dry runs; the interpreter ignores it there."""
    vision = strict_run(tmp_path, "  - id: w\n    wait_image_in: {image: img/x.png, "
                        "timeout: 0s}\n", [hit(0.95, BOX)], mode="normal")
    assert vision.join().status == "completed"


def test_the_while_cap_stays_a_warning() -> None:
    outcome, rec = run("steps:\n  - id: forever\n    loop: {while: 'true', "
                       "do: [{set: {name: x, value: 1}}]}\n", mode="dry", strict=True)
    assert outcome.status == "completed"
    assert any("'while' loop stopped" in message for message in warnings(rec))


# --- exec ---------------------------------------------------------------------------------


def exec_steps(wait: bool) -> str:
    command = json.dumps([sys.executable, "-c", "print('hi')"])
    return f"""
        steps:
          - id: prog
            as: out
            exec: {{command: {command}, wait: {json.dumps(wait)}}}
          - id: use
            log: "got ${{strip(out.stdout)}} ${{out.exit_code}}"
    """


def test_a_dry_exec_stands_in_for_the_output() -> None:
    outcome, rec = run(exec_steps(True), mode="dry", allow_exec=True, strict=True)
    assert outcome.status == "completed", rec.summary()
    (finished,) = rec.of("step_finished", step_id="prog")
    result = finished["result"]
    assert (result["exit_code"], result["stdout"], result["stderr"]) == (0, "", "")
    assert result["dry_run"] is True and "pid" not in result
    (logged,) = rec.of("step_finished", step_id="use")
    assert logged["result"]["message"] == "got  0"


def test_a_dry_exec_without_wait_has_no_pid() -> None:
    script = exec_steps(False).replace('log: "got ${strip(out.stdout)} ${out.exit_code}"',
                                       'log: "pid ${out.pid}"')
    outcome, rec = run(script, mode="dry", allow_exec=True)
    assert outcome.status == "completed", rec.summary()
    (finished,) = rec.of("step_finished", step_id="prog")
    assert finished["result"]["pid"] is None
    assert "stdout" not in finished["result"]


def test_a_strict_dry_exec_needs_allow_exec() -> None:
    outcome, rec = run(exec_steps(True), mode="dry", strict=True)
    assert outcome.error is not None and outcome.error.code == "capability_missing"
    (failed,) = rec.of("step_failed", step_id="prog")
    assert failed["error"]["details"] == {"capability": "exec", "attempt": 1}
    assert not rec.of("log", level="info")  # nothing described
    outcome, rec = run(exec_steps(True), mode="dry")
    assert outcome.status == "completed"
    assert any("--allow-exec" in message for message in warnings(rec))


# --- the waits ----------------------------------------------------------------------------

EDITOR = Rect(100, 200, 800, 600)


def test_wait_image_out_fails_when_its_image_is_visible(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: w\n    wait_image_out: {image: img/x.png, "
                        "timeout: 5s}\n", [hit(0.97, BOX)], run_dir=tmp_path)
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "still_visible"
    failed = failure(vision)
    assert failed["error"]["details"] == {"image": "img/x.png", "score": 0.97, "attempt": 1}
    assert "strict dry run" in failed["error"]["message"]
    assert failed["screenshot"] == "captures/0001-w-1.png"
    assert len(vision.searches()) == 1 and vision.clock.t == 0


def test_wait_image_out_passes_when_its_image_is_gone(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: w\n    wait_image_out: img/x.png\n",
                        [miss(0.2)], run_dir=tmp_path)
    assert vision.join().status == "completed"
    assert not (tmp_path / "captures").exists()


@pytest.mark.parametrize("step", ["wait_image_in", "wait_image_out"])
def test_image_waits_fail_when_their_window_is_missing(tmp_path: Path, step: str) -> None:
    vision = strict_run(tmp_path, f"  - id: w\n    {step}: {{image: img/x.png, "
                        "window: {title: Ghost}}\n", [miss(0.2)], backend=backend())
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "window_not_found"
    assert vision.searches() == [] and vision.clock.t == 0
    assert warnings(vision.recorder) == []


def test_wait_window_in_fails_without_the_window(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: w\n    wait_window_in: Mousepad\n", [],
                        backend=backend())
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "window_not_found"
    assert outcome.error.details == {"title": "Mousepad", "attempt": 1}
    assert "strict dry run" in outcome.error.message


def test_wait_window_out_fails_while_the_window_is_there(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: w\n    wait_window_out: Save\n", [],
                        backend=backend(**{"Save as": BOX}))
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "still_visible"
    assert outcome.error.details == {"title": "Save", "window": "Save as", "attempt": 1}


def test_satisfied_window_waits_complete(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: i\n    wait_window_in: Mousepad\n"
                        "  - id: o\n    wait_window_out: Save as\n", [],
                        backend=backend(**{"notes - Mousepad": EDITOR}))
    assert vision.join().status == "completed"
    result = vision.recorder.of("step_finished", step_id="i")[0]["result"]
    assert result["found"] is True and result["window"] == "notes - Mousepad"


def test_window_waits_without_a_backend_fail(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: w\n    wait_window_in: Mousepad\n", [],
                        backend=None, backend_reason="no X")
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "capability_missing"


def test_non_strict_dry_runs_only_report_unmet_waits(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: a\n    wait_image_in: img/x.png\n"
                        "  - id: b\n    wait_image_out: img/x.png\n"
                        "  - id: c\n    wait_window_in: Ghost\n"
                        "  - id: d\n    wait_window_out: Save\n",
                        [miss(0.2), hit(0.97, BOX)], strict=False,
                        backend=backend(**{"Save as": BOX}))
    assert vision.join().status == "completed"
    result = vision.recorder.of("step_finished", step_id="c")[0]["result"]
    assert result["found"] is False and result["window"] == "Ghost"


# --- x/y targets in a window ---------------------------------------------------------------


@pytest.mark.parametrize("steps", [
    "  - id: c\n    click: {x: 1, y: 2, window: {title: Ghost}}\n",
    "  - id: c\n    move: {x: 1, y: 2, window: {title: Ghost}}\n",
    "  - id: c\n    drag: {from: {x: 1, y: 2}, to: {x: 3, y: 4, window: {title: Ghost}}}\n",
])
def test_an_xy_target_needs_its_window(tmp_path: Path, steps: str) -> None:
    vision = strict_run(tmp_path, steps, [], backend=backend())
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "window_not_found"
    assert outcome.error.details == {"title": "Ghost", "attempt": 1}
    # Without strict, the same dry run warns and goes on.
    vision = strict_run(tmp_path, steps, [], backend=backend(), strict=False)
    assert vision.join().status == "completed"
    assert any("Ghost" in message for message in warnings(vision.recorder))


def test_an_xy_target_in_an_open_window_completes(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: c\n    click: {x: 1, y: 2, window: {title: Notes}}\n",
                        [], backend=backend(Notes=EDITOR))
    assert vision.join().status == "completed"
    assert warnings(vision.recorder) == []


# --- after the first input: checks of the screen only warn ---------------------------------

RELAXED = "strict checks relaxed after step"


def infos(recorder: Any) -> list[str]:
    return [log["message"] for log in recorder.of("log", level="info")]


def test_an_unmet_wait_before_any_input_fails(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: note\n    log: start\n"
                        "  - id: w\n    wait_image_in: img/x.png\n"
                        "  - id: c\n    click: {x: 1, y: 2}\n", [miss(0.3)],
                        backend=backend())
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "image_not_found"
    assert not any(RELAXED in message for message in infos(vision.recorder))


@pytest.mark.parametrize(("acting", "why"), [
    ("click: {x: 1, y: 2}", "input sent earlier (step 'act')"),
    ("type: hello", "input sent earlier (step 'act')"),
    ("key: ctrl+s", "input sent earlier (step 'act')"),
    ("scroll: {dy: -3}", "input sent earlier (step 'act')"),
    ("exec: {command: [mousepad]}", "a program started earlier (step 'act')"),
    ("poke: {}", "input sent earlier (step 'act')"),  # a plugin's step with effect: input
])
def test_an_unmet_wait_after_input_is_a_warning(tmp_path: Path, acting: str,
                                                why: str) -> None:
    vision = strict_run(tmp_path, f"  - id: act\n    {acting}\n"
                        "  - id: w\n    wait_image_in: {image: img/x.png, timeout: 5s}\n"
                        "  - id: after\n    log: next\n", [miss(0.42)], backend=backend(),
                        allow_exec=True, run_dir=tmp_path)
    outcome = vision.join()
    assert outcome.status == "completed", vision.recorder.summary()
    assert not vision.recorder.of("step_failed")
    (warning,) = warnings(vision.recorder)
    assert warning.startswith("dry-run: img/x.png not found (one attempt; best score 0.420")
    assert warning.endswith("; not a failure in a strict dry run because it may depend on "
                            f"{why}")
    assert vision.recorder.of("step_finished", step_id="after")
    assert not (tmp_path / "captures").exists()  # not a failure: nothing captured
    assert vision.factory.sessions == []


def test_every_unmet_check_after_input_warns(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: act\n    click: {x: 1, y: 2}\n"
                        "  - id: a\n    wait_image_in: {image: img/x.png, "
                        "window: {title: Ghost}}\n"
                        "  - id: b\n    wait_image_out: img/x.png\n"
                        "  - id: c\n    wait_window_in: Ghost\n"
                        "  - id: d\n    wait_window_out: Save\n"
                        "  - id: e\n    click: {x: 1, y: 2, window: {title: Ghost}}\n"
                        "  - id: f\n    click: {image: img/x.png}\n",
                        [hit(0.97, BOX), miss(0.2)], backend=backend(**{"Save as": BOX}))
    assert vision.join().status == "completed", vision.recorder.summary()
    logged = warnings(vision.recorder)
    assert len(logged) == 6
    assert all(message.endswith("may depend on input sent earlier (step 'act')")
               for message in logged)
    assert "image img/x.png not searched: no visible window" in logged[0]
    assert "img/x.png still visible (one attempt" in logged[1]
    assert "no visible window whose title contains 'Ghost';" in logged[2]
    assert "window 'Save as' still visible;" in logged[3]
    assert "no visible window whose title contains 'Ghost';" in logged[4]
    assert "img/x.png not found (one attempt" in logged[5]


def test_the_relax_is_logged_once_and_lasts(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: rows\n    loop:\n      times: 3\n      do:\n"
                        "        - id: w\n          wait_image_in: img/x.png\n"
                        "        - id: c\n          click: {x: 1, y: 2}\n",
                        [hit(0.95, BOX), miss(0.3)], backend=backend())
    assert vision.join().status == "completed", vision.recorder.summary()
    relaxed = [log for log in vision.recorder.of("log", level="info")
               if RELAXED in log["message"]]
    assert len(relaxed) == 1
    assert relaxed[0]["step_id"] == "c"
    assert relaxed[0]["message"].startswith("strict checks relaxed after step 'c': later "
                                            "checks of the screen may depend on the input")
    # The first iteration's wait was satisfied; the next two only warn.
    assert len(warnings(vision.recorder)) == 2


def test_a_plugin_wait_follows_the_same_rule(tmp_path: Path) -> None:
    """``ctx.vision.wait`` in a dry run: strict before the first input, a warning after."""
    vision = strict_run(tmp_path, "  - id: s\n    seek: img/x.png\n", [miss(0.3)])
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "image_not_found"
    vision = strict_run(tmp_path, "  - id: p\n    poke: {}\n  - id: s\n    seek: img/x.png\n",
                        [miss(0.3)])
    assert vision.join().status == "completed", vision.recorder.summary()
    (warning,) = warnings(vision.recorder)
    assert warning.endswith("may depend on input sent earlier (step 'p')")


def test_a_failed_input_step_does_not_relax(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: c\n    on_error: continue\n"
                        "    click: {image: img/x.png}\n"
                        "  - id: w\n    wait_image_in: img/x.png\n", [miss(0.3)],
                        backend=backend())
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "image_not_found"
    assert [failed["step_id"] for failed in vision.recorder.of("step_failed")] == ["c", "w"]
    assert not any(RELAXED in message for message in infos(vision.recorder))


def test_non_strict_dry_runs_do_not_relax(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: c\n    click: {x: 1, y: 2}\n"
                        "  - id: w\n    wait_image_in: img/x.png\n", [miss(0.3)],
                        backend=backend(), strict=False)
    assert vision.join().status == "completed"
    assert not any(RELAXED in message for message in infos(vision.recorder))
    assert warnings(vision.recorder) == []


def test_a_search_that_cannot_be_made_still_fails_after_input(tmp_path: Path) -> None:
    script = with_images("steps:\n  - id: c\n    type: hello\n"
                         "  - id: w\n    wait_image_in: img/x.png\n", tmp_path, "img/x.png")
    outcome, rec = run(script, mode="dry", strict=True)
    assert outcome.error is not None and outcome.error.code == "capability_missing"
    assert rec.of("step_failed")[0]["step_id"] == "w"
    assert any(RELAXED in message for message in infos(rec))


def test_window_waits_without_a_backend_still_fail_after_input(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: c\n    type: hello\n"
                        "  - id: w\n    wait_window_in: Mousepad\n", [],
                        backend=None, backend_reason="no X")
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "capability_missing"


def test_exec_without_allow_exec_still_fails_after_input() -> None:
    script = "steps:\n  - id: c\n    type: hello\n  - id: prog\n    exec: mousepad\n"
    outcome, rec = run(script, mode="dry", strict=True)
    assert outcome.error is not None and outcome.error.code == "capability_missing"
    assert rec.of("step_failed")[0]["step_id"] == "prog"


def test_an_unknown_condition_still_fails_after_input() -> None:
    script = "steps:\n  - id: c\n    type: hello\n  - id: look\n    if:\n" \
             "      when: {pixel_is_red: [1, 2]}\n      then:\n        - log: red\n"
    outcome, _ = run(script, mode="dry", strict=True)
    assert outcome.error is not None and outcome.error.code == "expression_error"


def test_a_missing_template_still_fails_after_input(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: c\n    type: hello\n"
                        "  - id: w\n    wait_image_in: img/missing.png\n", [hit(0.95, BOX)])
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "template_missing"


FILL_FORM = """
  - id: wait_form
    wait_image_in: {image: img/label.png, window: {title: Form}, timeout: 10s}
    as: label
  - id: click_name
    click: {at: "${label}", offset: [3.5w, 0]}
  - id: type_name
    type: Ana
  - id: submit
    click: {image: img/submit.png, window: {title: Form}}
  - id: confirm
    wait_image_in: {image: img/saved.png, window: {title: Form}, timeout: 3s}
    on_error: retry
  - id: report
    log: saved
"""


def test_a_form_that_confirms_its_input_passes(tmp_path: Path) -> None:
    """The shape of skills/j5-macro/examples/fill_rows.yaml: the label must be there
    before any input; the confirmation only appears after the submit, which a dry run
    does not send, so it only warns."""
    script = with_images(f"steps:\n{FILL_FORM}", tmp_path, "img/label.png",
                         "img/submit.png", "img/saved.png")
    form = backend(Form=Rect(0, 0, 800, 600))
    vision = VisionRun(script, FakeToolkit([hit(0.97, BOX), hit(0.96, BOX), miss(0.31)]),
                       mode="dry", strict=True, backend=form)
    outcome = vision.join()
    assert outcome.status == "completed", vision.recorder.summary()
    assert not vision.recorder.of("step_failed")
    (warning,) = warnings(vision.recorder)
    assert warning.startswith("dry-run: img/saved.png not found")
    assert warning.endswith("input sent earlier (step 'click_name')")
    (relaxed,) = [log for log in vision.recorder.of("log", level="info")
                  if RELAXED in log["message"]]
    assert relaxed["step_id"] == "click_name"
    assert vision.recorder.of("step_finished", step_id="report")
    assert vision.factory.sessions == []

    # Before any input the form must be on screen.
    vision = VisionRun(script, FakeToolkit([miss(0.2)]), mode="dry", strict=True,
                       backend=backend(Form=Rect(0, 0, 800, 600)))
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "image_not_found"
    assert vision.recorder.of("step_failed")[0]["step_id"] == "wait_form"
