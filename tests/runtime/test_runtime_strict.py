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
    vision = strict_run(tmp_path, "  - id: w\n    wait_image: {image: img/x.png, timeout: 5s}\n"
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
    "  - id: c\n    move: {x: 1, y: 2}\n  - id: d\n    drag: {from: {x: 1, y: 2}, "
    "to: {image: img/x.png}}\n",
])
def test_input_step_targets_must_be_found(tmp_path: Path, steps: str) -> None:
    vision = strict_run(tmp_path, steps, [miss(0.3)], backend=backend())
    outcome = vision.join()
    assert outcome.error is not None and outcome.error.code == "image_not_found"


def test_on_error_continue_applies(tmp_path: Path) -> None:
    vision = strict_run(tmp_path, "  - id: w\n    on_error: continue\n"
                        "    wait_image: img/x.png\n  - id: after\n    log: next\n",
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
    vision = strict_run(tmp_path, "  - id: w\n    wait_image: {image: img/x.png, "
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
