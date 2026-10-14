"""The history panel and the screen tools against a real engine with the fake backend:
saved runs with their events and captures (written as an earlier engine session would
have left them), the failing search's capture next to its template, deleting runs, and
the step tools writing a picked point into the script."""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from gui_support import WAIT, wait_for

RUN_ID = "r-20261014-120000-abcd"
TEMPLATE_SIZE = (40, 30)
CAPTURE_SIZE = (400, 300)
SEARCH = {"threshold": 0.9, "found": False, "best_score": 0.43,
          "bbox": [150, 120, 60, 40], "region": [100, 100, 400, 300], "scale": 1.0,
          "display_scale": 1.0, "capture_scale": 1.0, "elapsed_ms": 5}


def png(width: int, height: int, colour: str) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage, QPainter

    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(colour))
    painter = QPainter(image)
    painter.fillRect(width // 4, height // 4, width // 2, height // 2, QColor("white"))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data.data())


def event(type_: str, seq: int, **payload: Any) -> str:
    return json.dumps({"v": 1, "kind": "event", "type": type_, "run_id": RUN_ID, "seq": seq,
                       "ts": "2026-10-14T12:00:00.000Z", "t_ms": seq, "payload": payload})


def write_failed_run(runs: Path, script: Path, template: str) -> Path:
    """A run of ``script`` whose ``wait`` step failed to find ``template``."""
    folder = runs / RUN_ID
    (folder / "captures").mkdir(parents=True)
    shot = "captures/0001-wait-2.png"
    (folder / shot).write_bytes(png(*CAPTURE_SIZE, "#3050d0"))
    (folder / "run.json").write_text(json.dumps({
        "run_id": RUN_ID, "script_id": "s1", "script_name": "Look",
        "script_path": str(script), "mode": "normal", "seed": 1,
        "started": "2026-10-14T12:00:00.000Z", "finished": "2026-10-14T12:00:02.000Z",
        "status": "failed", "duration_ms": 2100, "warnings": 0,
        "error": {"code": "image_not_found", "message": "not found", "step_id": "wait"},
    }), encoding="utf-8")
    error = {"code": "image_not_found", "message": f"{template} not found",
             "details": {"best_score": 0.43, "threshold": 0.9, "attempts": 2,
                         "template": template}}
    lines = [
        event("run_started", 1, script_name="Look", mode="normal", seed=1, backend="fake"),
        event("step_started", 2, step_id="wait", type="wait_image_in"),
        event("image_search", 3, template=template, attempt=1, **SEARCH),
        event("image_search", 4, template=template, attempt=2, screenshot=shot, **SEARCH),
        event("step_failed", 5, step_id="wait", error=error, screenshot=shot),
        event("run_finished", 6, status="failed", duration_ms=2100, warnings=0, error=error),
    ]
    (folder / "events.jsonl").write_text("".join(line + "\n" for line in lines),
                                         encoding="utf-8")
    return folder


def open_script(qtbot: Any, window: Any, path: Path) -> None:
    window.open_target(str(path))
    wait_for(qtbot, lambda: window.ctx.document.state is not None
             and window.ctx.document.state.path == str(path))


def select_failed_run(qtbot: Any, window: Any) -> Any:
    history = window.history
    window.bottom_tabs.setCurrentWidget(history)
    wait_for(qtbot, lambda: [r.run_id for r in history.runs] == [RUN_ID])
    with qtbot.waitSignal(history.run_loaded, timeout=WAIT) as loaded:
        history.select_run(RUN_ID)
    return loaded.args[0]


def test_a_failed_search_shows_its_capture_next_to_its_file_template(
        qtbot: Any, window: Any, scripts: Path, tmp_path: Path) -> None:
    (scripts / "img").mkdir()
    (scripts / "img" / "button.png").write_bytes(png(*TEMPLATE_SIZE, "#d03030"))
    script = scripts / "look.yaml"
    script.write_text("name: Look\nsteps:\n  - id: wait\n    wait_image_in: "
                      "{image: img/button.png, timeout: 1s}\n", encoding="utf-8")
    folder = write_failed_run(tmp_path / "runs", script, "img/button.png")
    open_script(qtbot, window, script)

    detail = select_failed_run(qtbot, window)
    history = window.history
    assert detail.status == "failed"
    assert history.headline.text().endswith("failed in 2.1 s (normal mode) at wait: not "
                                            "found")
    assert history.events.count() == 6
    assert history.events.item(4).text() == "✗ wait: img/button.png not found"
    assert history.tabs.currentIndex() == 1  # the failed search
    view = history.failure_view
    template = view.template.image
    assert template is not None and (template.width(), template.height()) == TEMPLATE_SIZE
    assert "img/button.png · 40 x 30 px · file" in view.template.caption.text()
    capture = view.capture.image
    assert capture is not None and (capture.width(), capture.height()) == CAPTURE_SIZE
    [mark] = view.capture.marks
    assert mark.rect == (50, 20, 60, 40) and mark.label == "best 0.430"
    assert view.capture_caption.text() == "best score 0.430 (threshold 0.9) · 2 attempts"
    assert history.run_folder() == str(folder.resolve())
    assert history.open_button.isEnabled()

    history.tabs.setCurrentIndex(2)
    history.captures.setCurrentRow(0)
    assert history.capture_view.image is not None
    assert [m.rect for m in history.capture_view.marks] == [(50, 20, 60, 40)]

    with qtbot.waitSignal(history.run_deleted, timeout=WAIT):
        history.delete_run(RUN_ID)
    wait_for(qtbot, lambda: history.runs == [] and history.detail is None)
    assert not folder.exists()


def test_an_embedded_template_comes_from_the_engine(qtbot: Any, window: Any, scripts: Path,
                                                    tmp_path: Path) -> None:
    data = base64.b64encode(png(*TEMPLATE_SIZE, "#30a040")).decode()
    script = scripts / "look.yaml"
    script.write_text("name: Look\nsteps:\n  - id: wait\n    wait_image_in: "
                      "{image: \"#button\", timeout: 1s}\nimages:\n  button:\n    png: "
                      f"{data}\n    scale: 1.5\n", encoding="utf-8")
    write_failed_run(tmp_path / "runs", script, "#button")
    open_script(qtbot, window, script)

    select_failed_run(qtbot, window)
    preview = window.history.failure_view.template
    wait_for(qtbot, lambda: preview.image is not None)
    assert (preview.image.width(), preview.image.height()) == TEMPLATE_SIZE
    assert preview.caption.text() == ("#button · 40 x 30 px · embedded · captured at "
                                      "scale 1.5")

    preview.show_template("#nope")
    wait_for(qtbot, lambda: "template_missing" in preview.caption.text())
    assert preview.image is None


def test_the_step_tools_write_a_picked_point(qtbot: Any, window: Any, scripts: Path) -> None:
    script = scripts / "point.yaml"
    script.write_text("name: Point\nsteps:\n  - id: press\n    click: {image: a.png}\n"
                      "  - id: note\n    log: hi\n", encoding="utf-8")
    open_script(qtbot, window, script)
    window.editor.tree.select_step("press")
    tools = window.step_tools
    assert tools.step_id == "press"
    assert list(tools.image_tools) == [None] and list(tools.point_tools) == [None]
    assert tools.point_labels[None].text() == "an image"

    tools.point_tools[None].apply_pick({
        "x": 620, "y": 340, "display_scale": 1.0, "window": "Notes",
        "window_area": [500, 300, 400, 300], "window_point": [120, 40]})
    wait_for(qtbot, lambda: "window: {title: Notes}" in window.editor.yaml_view
             .toPlainText())
    step = tools.step()
    assert step is not None
    assert (step.params["x"], step.params["y"], step.params["window"]) == \
        (120, 40, {"title": "Notes"})
    assert "image" not in step.params
    assert tools.point_labels[None].text() == "x=120, y=40 in “Notes”"
    assert tools.point_tools[None].message.text() == \
        "Picked (120, 40) in “Notes” ((620, 340) on the desktop)."

    window.editor.tree.select_step("note")
    assert tools.image_tools == {} and tools.point_tools == {}
    window.capture_action.trigger()
    assert window.bottom_tabs.currentWidget() is tools


needs_screen = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and os.environ.get("DISPLAY")),
    reason="needs an X display to capture (run under xvfb-run)")
"""The engine checks that it can capture the screen before anything else (Linux under
Xvfb only: CI's other runners may have no desktop to capture)."""


@needs_screen
def test_picking_during_a_run_says_busy(qtbot: Any, window: Any, scripts: Path) -> None:
    from j5_macro.gui.views.screen_dialogs import PointFieldTools

    pick = PointFieldTools(window.ctx, get_point=lambda: {}, set_point=lambda _: None)
    qtbot.addWidget(pick)
    open_script(qtbot, window, scripts / "slow.yaml")
    with qtbot.waitSignal(window.ctx.run.run_started, timeout=WAIT):
        window.run_panel.start("normal")
    pick.pick()
    wait_for(qtbot, lambda: "A script is running" in pick.message.text())
    assert pick.pick_button.isEnabled()
    with qtbot.waitSignal(window.ctx.run.run_finished, timeout=WAIT):
        window.ctx.run.stop()


@needs_screen
def test_without_tkinter_pick_says_how_to_install_it(qtbot: Any, ctx: Any) -> None:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("tkinter is installed: the overlay would open")
    from j5_macro.gui.views.screen_dialogs import PointFieldTools

    picked: list[Any] = []
    tools = PointFieldTools(ctx, get_point=lambda: {}, set_point=picked.append)
    qtbot.addWidget(tools)
    tools.pick()
    wait_for(qtbot, lambda: "python3-tk" in tools.message.text())
    assert picked == []
