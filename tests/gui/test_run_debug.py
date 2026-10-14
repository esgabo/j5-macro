"""Running and debugging from the window, against a real engine with the fake backend:
the params prompt, dry and strict runs, breakpoints and stepping, the on-error pause,
variables and evaluate."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from gui_support import WAIT, wait_for
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit

from j5_macro.gui.models.debugger import PauseInfo
from j5_macro.gui.views.step_tree import GUTTER

SECRET = "hunter2-very-secret"

PARAMS = """\
version: 1
name: Params
params:
  user:
    description: Who signs in
  password:
    secret: true
    description: The password
  retries:
    type: integer
    default: 2
  verbose:
    type: boolean
    default: false
steps:
  - id: greet
    log: "hello ${user}, ${retries} retries, verbose ${verbose}"
  - id: type_password
    type: "${password}"
  - id: echo
    log: "the password is ${password}"
"""

DEBUG = """\
version: 1
name: Debug
vars:
  n: 0
  token: abc
steps:
  - id: start
    log: start
  - id: repeat
    loop:
      times: 2
      do:
        - id: bump
          set: {name: n, expr: "n + 1"}
  - id: check
    if:
      when: "n == 5"
      then:
        - id: five
          log: "n is five"
      else:
        - id: other
          log: "n is not five"
  - id: done
    log: done
"""

FAILING = """\
version: 1
name: Failing
steps:
  - id: before
    log: before
  - id: broken
    on_error: pause
    set: {name: x, expr: "missing_variable + 1"}
  - id: after
    log: after
"""

STRICT = """\
version: 1
name: Strict
steps:
  - id: look
    wait_image_in: {image: target.png, timeout: 1s}
  - id: after
    log: after
"""


def write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def open_script(qtbot: Any, window: Any, path: Path) -> Any:
    window.open_target(str(path))
    wait_for(qtbot, lambda: window.ctx.document.state is not None
             and os.path.samefile(window.ctx.document.state.path, path))
    return window.ctx.document.state


def wait_paused(qtbot: Any, window: Any) -> Any:
    run = window.ctx.run
    wait_for(qtbot, lambda: run.state == "paused" and run.variables is not None
             and run.actions.step)
    return run.pause


def all_widget_texts(window: Any) -> str:
    texts: list[str] = []
    for edit in window.findChildren(QLineEdit):
        texts.append(edit.text())
    for label in window.findChildren(QLabel):
        texts.append(label.text())
    for combo in window.findChildren(QComboBox):
        texts.append(combo.currentText())
    return "\n".join(texts)


def test_params_prompt_secret_never_shown(qtbot: Any, window: Any, scripts: Path) -> None:
    path = write(scripts, "params.yaml", PARAMS)
    open_script(qtbot, window, path)
    panel = window.run_panel
    seen: list[Any] = []

    def fill(dialog: Any) -> bool:
        seen.append(dialog)
        assert dialog.field("password").echoMode() == QLineEdit.EchoMode.Password
        assert dialog.field("retries").text() == "2"  # the default
        assert not dialog.try_accept()  # user and password are required
        assert dialog.problem.isVisibleTo(dialog)
        assert dialog.error_text("user") == "required"
        assert dialog.error_text("retries") == ""
        dialog.set_text("user", "bob")
        dialog.set_text("password", SECRET)
        dialog.set_text("retries", "three")
        assert not dialog.try_accept()
        dialog.set_text("retries", "3")
        dialog.set_text("verbose", "true")
        dialog.set_kind("normal")
        assert dialog.try_accept()
        assert dialog.field("password").text() == ""  # cleared once the dialog is done
        return True

    panel.exec_dialog = fill
    with qtbot.waitSignal(window.ctx.run.run_finished, timeout=WAIT) as finished:
        panel.request("normal")
    assert finished.args[0]["status"] == "completed"
    lines = panel.log_lines()
    assert "[info] hello bob, 3 retries, verbose true" in lines
    assert "[info] the password is ***" in lines
    log_text = "\n".join(lines)
    assert SECRET not in log_text
    assert SECRET not in all_widget_texts(window)
    assert len(seen) == 1
    # Non-secret values are remembered for the next run of the script; secrets are not.
    assert window.ctx.run.remembered_params() == {"user": "bob", "retries": "3",
                                                  "verbose": "true"}

    def check_again(dialog: Any) -> bool:
        assert dialog.field("user").text() == "bob"
        assert dialog.field("password").text() == ""
        return False

    panel.exec_dialog = check_again
    panel.request("dry")
    assert not window.ctx.run.active


def test_confirmation_before_real_input(qtbot: Any, window: Any, scripts: Path,
                                       monkeypatch: Any) -> None:
    open_script(qtbot, window, scripts / "quick.yaml")
    panel = window.run_panel
    asked: list[str] = []
    monkeypatch.setattr(panel, "confirm_real_input",
                        lambda kind: asked.append(kind.key) or False)
    # The fake backend sends no real input: no question.
    with qtbot.waitSignal(window.ctx.run.run_finished, timeout=WAIT):
        panel.request("normal")
    assert asked == []
    # With a backend that does, the run waits for the answer (here: cancel).
    info = window.ctx.bridge.info
    monkeypatch.setattr(type(window.ctx.bridge), "info",
                        property(lambda self: type(info)(**{
                            **{f: getattr(info, f) for f in info.__dataclass_fields__},
                            "backend": "x11"})))
    panel.request("normal")
    panel.request("dry")
    assert asked == ["normal"]
    assert window.ctx.run.active  # the dry run needs no confirmation
    wait_for(qtbot, lambda: not window.ctx.run.active)
    window.ctx.run.confirm_real_input = False
    with qtbot.waitSignal(window.ctx.run.run_finished, timeout=WAIT):
        panel.request("normal")
    assert asked == ["normal"]


def test_strict_dry_run_fails_where_the_real_run_would(qtbot: Any, window: Any,
                                                       scripts: Path) -> None:
    from PySide6.QtGui import QColor, QImage

    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(200, 10, 10))
    assert image.save(str(scripts / "target.png"))
    open_script(qtbot, window, write(scripts, "strict.yaml", STRICT))
    panel = window.run_panel
    # The first search of the session loads OpenCV and grabs the real desktop, which
    # can take far longer than the GUI waits on a cold Windows runner.
    with qtbot.waitSignal(window.ctx.run.run_finished, timeout=4 * WAIT,
                          raising=False) as finished:
        panel.start("dry")
    assert finished.signal_triggered, "\n".join(panel.log_lines())
    # Not found, or the screen cannot be searched here: a plain dry run completes.
    assert finished.args[0]["status"] == "completed"
    warnings = finished.args[0]["warnings"]
    assert f"{warnings} warning" in panel.summary_label.text()
    panel.strict_box.setChecked(True)
    with qtbot.waitSignal(window.ctx.run.run_finished, timeout=WAIT) as finished:
        panel.request("dry")
    assert finished.args[0]["status"] == "failed"
    assert any(line.startswith("✗ look") for line in panel.log_lines())
    assert panel.summary_label.text().startswith("Failed")
    assert "after" not in "\n".join(panel.log_lines())


def test_breakpoint_step_over_variables_and_continue(qtbot: Any, window: Any,
                                                     scripts: Path) -> None:
    open_script(qtbot, window, write(scripts, "debug.yaml", DEBUG))
    run = window.ctx.run
    panel = window.run_panel
    tree = window.editor.tree
    tree.itemClicked.emit(tree.item_for("check"), GUTTER)
    assert run.breakpoints == frozenset({"check"})
    assert not panel.continue_action.isEnabled()

    panel.request("dry")
    pause = wait_paused(qtbot, window)
    assert pause.reason == "breakpoint" and pause.step_id == "check"
    assert pause.title == "Paused at a breakpoint, before 'check'"
    assert tree.current_step() == "check" and tree.is_paused()
    assert panel.pause_view.isVisible() and not panel.pause_view.retry_button.isVisible()
    assert panel.state_label.text() == "Paused (breakpoint)"
    for action in (panel.continue_action, panel.step_over_action, panel.step_into_action,
                   panel.step_out_action, panel.stop_action):
        assert action.isEnabled()
    assert not panel.pause_action.isEnabled() and not panel.run_action.isEnabled()
    assert panel.variables.values() == {"n": "2", "token": '"abc"'}

    # Change a variable: the if takes the other branch.
    panel.variables.select("n")
    assert panel.variables.value_edit.text() == "2"
    panel.variables.value_edit.setText("4")
    panel.variables.expression_box.setChecked(True)
    panel.variables.value_edit.setText("n + 3")
    panel.variables.apply_edit()
    wait_for(qtbot, lambda: panel.variables.values().get("n") == "5")

    # Evaluate.
    panel.variables.evaluate_edit.setText("n * 2")
    panel.variables.evaluate()
    wait_for(qtbot, lambda: panel.variables.result_label.text() == "n * 2 = 10")
    panel.variables.evaluate_edit.setText("nope +")
    panel.variables.evaluate()
    wait_for(qtbot, lambda: panel.variables.result_label.text() not in ("", "n * 2 = 10"))

    # Step over the if: it runs its branch and pauses before the next step.
    run.step_over()
    wait_for(qtbot, lambda: run.state == "paused" and run.pause is not None
             and run.pause.step_id == "done")
    assert run.pause.reason == "step"
    assert "[info] n is five" in panel.log_lines()
    assert tree.current_step() == "done"

    with qtbot.waitSignal(run.run_finished, timeout=WAIT) as finished:
        panel.continue_action.trigger()
    assert finished.args[0]["status"] == "completed"
    assert "[info] done" in panel.log_lines()
    assert not panel.pause_view.isVisible()
    assert panel.variables.values() == {}
    assert not tree.is_paused() and tree.current_step() is None


def test_debug_a_dry_run_pauses_at_the_first_step_and_steps_into(
        qtbot: Any, window: Any, scripts: Path) -> None:
    open_script(qtbot, window, write(scripts, "debug.yaml", DEBUG))
    run = window.ctx.run
    run.start("debug_dry")
    pause = wait_paused(qtbot, window)
    assert pause.reason == "step" and pause.step_id == "start"
    assert run.breakpoints == frozenset()  # the entry breakpoint is not the user's
    run.step_over()
    wait_for(qtbot, lambda: run.pause is not None and run.pause.step_id == "repeat")
    run.step_into()
    wait_for(qtbot, lambda: run.pause is not None and run.pause.step_id == "bump")
    run.step_out()
    wait_for(qtbot, lambda: run.pause is not None and run.pause.step_id == "check")
    with qtbot.waitSignal(run.run_finished, timeout=WAIT) as finished:
        run.resume()
    assert finished.args[0]["status"] == "completed"
    # The entry breakpoint was removed: a new dry run does not pause.
    with qtbot.waitSignal(run.run_finished, timeout=WAIT) as finished:
        window.run_panel.start("dry")
    assert finished.args[0]["status"] == "completed"


def test_stop_while_paused(qtbot: Any, window: Any, scripts: Path) -> None:
    open_script(qtbot, window, write(scripts, "debug.yaml", DEBUG))
    window.run_panel.start("debug")  # step mode: real input with the fake backend
    pause = wait_paused(qtbot, window)
    assert pause.step_id == "start"
    with qtbot.waitSignal(window.ctx.run.run_finished, timeout=WAIT) as finished:
        window.run_panel.stop_action.trigger()
    assert finished.args[0]["status"] == "stopped"
    assert window.run_panel.state_label.text() == "Finished: stopped"
    assert window.run_panel.run_action.isEnabled()


def test_on_error_pause_then_retry_and_skip(qtbot: Any, window: Any, scripts: Path) -> None:
    open_script(qtbot, window, write(scripts, "failing.yaml", FAILING))
    run = window.ctx.run
    panel = window.run_panel
    panel.request("dry")
    pause = wait_paused(qtbot, window)
    assert pause.reason == "error" and pause.step_id == "broken" and pause.recoverable
    assert "missing_variable" in pause.detail
    view = panel.pause_view
    assert view.isVisible() and view.retry_button.isVisible()
    assert "missing_variable" in view.detail.text()
    assert window.editor.tree.current_step() == "broken"

    # Retry: it fails again and pauses again.
    view.retry_button.click()
    wait_for(qtbot, lambda: sum(line.startswith("✗ broken") for line in panel.log_lines())
             == 2 and run.state == "paused" and run.actions.step)
    # Skip: not debugging, so the run carries on to the end.
    with qtbot.waitSignal(run.run_finished, timeout=WAIT) as finished:
        view.skip_button.click()
    assert finished.args[0]["status"] == "completed"
    lines = panel.log_lines()
    assert "↷ broken skipped (user)" in lines and "[info] after" in lines


def test_skip_while_debugging_stays_paused(qtbot: Any, window: Any, scripts: Path) -> None:
    open_script(qtbot, window, write(scripts, "failing.yaml", FAILING))
    run = window.ctx.run
    run.start("debug_dry")
    wait_paused(qtbot, window)
    run.resume()
    wait_for(qtbot, lambda: run.pause is not None and run.pause.reason == "error"
             and run.actions.step)
    run.skip()
    pause = wait_paused(qtbot, window)
    assert pause.reason == "step"
    assert pause.step_id == "after"
    with qtbot.waitSignal(run.run_finished, timeout=WAIT):
        run.stop()


def test_log_filter_clear_and_summary(qtbot: Any, window: Any, scripts: Path) -> None:
    open_script(qtbot, window, scripts / "quick.yaml")
    panel = window.run_panel
    with qtbot.waitSignal(window.ctx.run.run_finished, timeout=WAIT):
        panel.start("normal")
    lines = panel.log_lines()
    assert any(line.startswith("keyboard plan (") for line in lines)
    panel.set_filter("info")
    visible = panel.visible_lines()
    assert len(visible) < len(lines)
    assert not any(line.startswith("keyboard plan") for line in visible)
    panel.set_filter("error")
    assert panel.visible_lines() == []
    panel.set_filter("debug")
    assert panel.visible_lines() == lines
    summary = panel.summary_label.text()
    assert summary.startswith("Completed in ") and "0 warnings" in summary
    assert "seed " in summary
    panel.clear_log()
    assert panel.log_lines() == []


def test_breakpoints_are_sent_and_follow_the_script(qtbot: Any, window: Any,
                                                    scripts: Path) -> None:
    debug = write(scripts, "debug.yaml", DEBUG)
    open_script(qtbot, window, debug)
    run = window.ctx.run
    run.toggle_breakpoint("done")
    open_script(qtbot, window, scripts / "quick.yaml")
    assert run.breakpoints == frozenset()
    open_script(qtbot, window, debug)
    assert run.breakpoints == frozenset({"done"})
    window.run_panel.start("dry")
    pause = wait_paused(qtbot, window)
    assert pause.step_id == "done"
    # Removing it while paused applies to the active run too.
    run.toggle_breakpoint("done")
    with qtbot.waitSignal(run.run_finished, timeout=WAIT):
        run.resume()


def test_pause_view_shows_interference_and_a_capture(qtbot: Any, window: Any,
                                                      tmp_path: Path) -> None:
    from PySide6.QtGui import QColor, QImage

    view = window.run_panel.pause_view
    view.show_pause(PauseInfo(reason="interference", step_id="move_it",
                              interference={"expected": [10, 20], "actual": [300, 40],
                                            "action": "pause"}))
    assert view.isVisible() and view.retry_button.isVisible()
    assert view.title.text() == "The mouse was moved during 'move_it'"
    assert "(300, 40)" in view.detail.text()

    run = window.ctx.run
    assert run.runs_dir == tmp_path / "runs"
    run.log.run_id = "r-20261014-120000-abcd"
    capture = tmp_path / "runs" / run.log.run_id / "captures" / "0001-look-1.png"
    capture.parent.mkdir(parents=True)
    image = QImage(80, 40, QImage.Format.Format_RGB32)
    image.fill(QColor(30, 120, 200))
    assert image.save(str(capture))
    view.show_pause(PauseInfo(reason="error", step_id="look",
                              error={"code": "image_not_found", "message": "not found"},
                              screenshot="captures/0001-look-1.png"))
    assert view.capture.isVisible() and view.capture_path == capture
    assert view.detail.text() == "not found (image_not_found)"
    view.show_pause(None)
    assert not view.isVisible()
    run.log.run_id = None
