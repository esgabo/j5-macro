"""The main window against a real engine with the fake input backend."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from gui_support import WAIT, wait_for
from PySide6.QtCore import Qt

from j5_macro.gui.views.step_tree import BREAKPOINT_MARK, GUTTER


def open_script(qtbot: Any, window: Any, path: Path) -> Any:
    window.open_target(str(path))
    wait_for(qtbot, lambda: window.ctx.document.state is not None
             and os.path.samefile(window.ctx.document.state.path, path))
    return window.ctx.document.state


def tree_rows(tree: Any) -> list[tuple[int, str, str]]:
    """``(depth, step or block name, type)`` for every row, depth first."""
    rows: list[tuple[int, str, str]] = []

    def visit(item: Any, depth: int) -> None:
        rows.append((depth, item.text(1), item.text(2)))
        for index in range(item.childCount()):
            visit(item.child(index), depth + 1)

    for index in range(tree.topLevelItemCount()):
        visit(tree.topLevelItem(index), 0)
    return rows


def test_opens_a_folder_and_a_script(qtbot: Any, window: Any, scripts: Path) -> None:
    window.open_target(str(scripts))
    wait_for(qtbot, lambda: len(window.script_list.entries) == 3)
    names = [window.script_list.list.topLevelItem(i).text(0) for i in range(3)]
    assert sorted(names) == ["Bad", "Quick", "Slow"]
    assert "backend: fake" in window.engine_label.text()
    assert "stop key: ctrl+alt+shift+q" in window.engine_label.text()
    assert "ctrl+alt+shift+q" in window.run_panel.stop_key_label.text()

    quick = next(i for i in range(3)
                 if window.script_list.list.topLevelItem(i).text(0) == "Quick")
    window.script_list.list.itemActivated.emit(window.script_list.list.topLevelItem(quick), 0)
    wait_for(qtbot, lambda: window.ctx.document.state is not None)
    state = window.ctx.document.state
    assert state.name == "Quick" and state.valid
    assert window.windowTitle().startswith("Quick")
    assert tree_rows(window.editor.tree) == [
        (0, "hello", "log"),
        (0, "repeat", "loop"),
        (1, "do", ""),
        (2, "bump", "set"),
        (2, "check", "if"),
        (3, "then", ""),
        (4, "second", "log"),
        (3, "else", ""),
        (4, "first", "log"),
        (0, "press", "key"),
    ]
    assert window.editor.yaml_view.toPlainText() == (scripts / "quick.yaml").read_text(
        encoding="utf-8")
    window.editor.tree.select_step("check")
    assert window.editor.form.step is not None and window.editor.form.step.id == "check"
    assert window.run_panel.run_button.isEnabled()
    assert not window.run_panel.stop_button.isEnabled()


def test_reloads_when_the_file_changes_on_disk(qtbot: Any, window: Any, scripts: Path) -> None:
    path = scripts / "quick.yaml"
    open_script(qtbot, window, path)
    path.write_text(path.read_text(encoding="utf-8") + "  - id: added\n    log: new\n",
                    encoding="utf-8", newline="\n")
    wait_for(qtbot, lambda: "added" in window.editor.tree.step_ids())
    assert "id: added" in window.editor.yaml_view.toPlainText()


def test_a_script_that_does_not_load_shows_its_problems(qtbot: Any, window: Any,
                                                       scripts: Path) -> None:
    window.open_target(str(scripts / "bad.yaml"))
    wait_for(qtbot, lambda: window.editor.problems.isVisible())
    assert "frobnicate" in window.editor.problems.text()
    assert window.ctx.document.state is None
    assert not window.run_panel.run_button.isEnabled()


def test_dry_run_streams_events_and_lands_in_history(qtbot: Any, window: Any,
                                                    scripts: Path) -> None:
    open_script(qtbot, window, scripts / "quick.yaml")
    steps_seen: list[object] = []
    window.ctx.run.current_step_changed.connect(steps_seen.append)
    with qtbot.waitSignal(window.ctx.run.run_finished, timeout=WAIT) as finished:
        qtbot.mouseClick(window.run_panel.dry_run_button, Qt.MouseButton.LeftButton)
    assert finished.args[0]["status"] == "completed"
    lines = window.run_panel.log_lines()
    assert lines[0].startswith("run started: Quick (dry mode")
    assert "▶ hello (log)" in lines and "[info] hello from the GUI" in lines
    assert any(line.startswith("✓ press") and "would press ctrl+s" in line for line in lines)
    assert lines[-1].startswith("run completed")
    assert steps_seen[:2] == ["hello", "repeat"] and steps_seen[-1] is None
    assert window.editor.tree.current_step() is None
    assert window.run_panel.state_label.text() == "Finished: completed"
    wait_for(qtbot, lambda: len(window.history.runs) == 1)
    assert window.history.runs[0].mode == "dry"


def test_a_normal_run_with_the_fake_backend_and_stop(qtbot: Any, window: Any,
                                                    scripts: Path) -> None:
    open_script(qtbot, window, scripts / "slow.yaml")
    qtbot.mouseClick(window.run_panel.run_button, Qt.MouseButton.LeftButton)
    wait_for(qtbot, lambda: window.ctx.run.log.current_step_id == "wait")
    tree = window.editor.tree
    assert tree.current_step() == "wait"
    assert tree.item_for("wait").font(1).bold()
    assert window.run_panel.stop_button.isEnabled()
    assert not window.run_panel.run_button.isEnabled()
    with qtbot.waitSignal(window.ctx.run.run_finished, timeout=WAIT) as finished:
        qtbot.mouseClick(window.run_panel.stop_button, Qt.MouseButton.LeftButton)
    assert finished.args[0]["status"] == "stopped"
    assert window.run_panel.run_button.isEnabled()
    assert "not reached" not in "\n".join(window.run_panel.log_lines())


def test_breakpoint_gutter_toggles_markers(qtbot: Any, window: Any, scripts: Path) -> None:
    open_script(qtbot, window, scripts / "quick.yaml")
    tree = window.editor.tree
    item = tree.item_for("bump")
    tree.itemClicked.emit(item, GUTTER)
    assert window.ctx.run.breakpoints == frozenset({"bump"})
    assert item.text(GUTTER) == BREAKPOINT_MARK
    tree.itemClicked.emit(item, GUTTER)
    assert window.ctx.run.breakpoints == frozenset()
    assert item.text(GUTTER) == ""


def test_creates_a_script(qtbot: Any, window: Any, scripts: Path) -> None:
    window.open_target(str(scripts))
    wait_for(qtbot, lambda: len(window.script_list.entries) == 3)
    path = scripts / "new.yaml"
    window.script_list.create_script(str(path), "Brand new")
    wait_for(qtbot, lambda: window.ctx.document.state is not None
             and window.ctx.document.state.name == "Brand new")
    assert path.read_text(encoding="utf-8").startswith("version: 1\nname: Brand new\n")
    wait_for(qtbot, lambda: len(window.script_list.entries) == 4)


def test_engine_death_is_reported_and_restart_recovers(qtbot: Any, window: Any,
                                                      scripts: Path) -> None:
    open_script(qtbot, window, scripts / "quick.yaml")
    pid = window.ctx.bridge.pid
    assert pid is not None
    with qtbot.waitSignal(window.ctx.bridge.engine_died, timeout=WAIT):
        os.kill(pid, signal.SIGTERM)
    assert window.banner.isVisible()
    assert "The engine stopped unexpectedly" in window.banner.label.text()
    assert window.engine_label.text() == "Engine stopped"
    assert not window.run_panel.run_button.isEnabled()

    # Restarting loads the script again into the new engine.
    with qtbot.waitSignals([window.ctx.bridge.engine_started,
                            window.ctx.document.state_changed], timeout=WAIT):
        qtbot.mouseClick(window.banner.restart_button, Qt.MouseButton.LeftButton)
    assert not window.banner.isVisible()
    assert window.ctx.document.state is not None
    assert window.run_panel.run_button.isEnabled()
    with qtbot.waitSignal(window.ctx.run.run_finished, timeout=WAIT) as finished:
        window.run_panel.start("dry")
    assert finished.args[0]["status"] == "completed"
