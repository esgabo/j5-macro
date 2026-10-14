"""The screen tools and the history on a real X server (Xvfb in CI) with the X11
backend: the non-interactive forms of the authoring commands (``capture_region`` with a
``bbox``, ``find_image``, ``screenshot``, ``list_windows``) through the GUI's widgets,
and a run that fails an image search, seen in the history.

Qt itself stays on the ``offscreen`` platform, so the window never covers the target.
The interactive overlay needs tkinter; it is covered by
tests/backends/test_e2e_authoring.py.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from gui_support import WAIT, wait_for

pytestmark = pytest.mark.x11

TITLE = "j5 gui target"
WINDOW = (400, 300, 120, 80)
SCRIPT = """\
# The GUI's screen tools.
name: Shots
steps:
  - id: look
    wait_image_in: {image: missing.png, window: {title: j5 gui target}, timeout: 1s}
"""


def x11_unavailable() -> str | None:
    try:
        from Xlib.display import Display
    except ImportError:
        return "python-xlib is not installed"
    if not os.environ.get("DISPLAY"):
        return "DISPLAY is not set (run under xvfb-run)"
    try:
        Display().close()
    except Exception as exc:
        return f"no X server: {exc}"
    return None


@pytest.fixture
def observer() -> Iterator[Any]:
    reason = x11_unavailable()
    if reason is not None:
        if os.environ.get("J5_REQUIRE_X11"):
            pytest.fail(f"J5_REQUIRE_X11 is set but {reason}")
        pytest.skip(reason)
    pytest.importorskip("cv2")
    pytest.importorskip("mss")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backends"))
    from x11_harness import Observer

    obs = Observer()
    try:
        yield obs
    finally:
        obs.close()


@pytest.fixture
def target(observer: Any) -> Any:
    """A window with coloured stripes and a white bar: texture to match."""
    window = observer.create_window(*WINDOW, wm_name=TITLE, wm_state=True)
    colours = [0xD03030, 0x30A040, 0x3050D0, 0xE0C020, 0x202020, 0x9040B0]
    for index, colour in enumerate(colours):
        gc = window.create_gc(foreground=colour)
        window.fill_rectangle(gc, index * 20, 0, 20, 80)
        gc.free()
    gc = window.create_gc(foreground=0xFFFFFF)
    window.fill_rectangle(gc, 10, 30, 70, 12)
    gc.free()
    observer.display.sync()
    return window


@pytest.fixture
def engine_env(engine_env: dict[str, str], target: Any) -> dict[str, str]:
    return {**engine_env, "J5_BACKEND": "x11"}


def open_script(qtbot: Any, window: Any, path: Path) -> None:
    window.open_target(str(path))
    wait_for(qtbot, lambda: window.ctx.document.state is not None
             and window.ctx.document.state.path == str(path))


def test_capture_embeds_and_test_finds_it(qtbot: Any, window: Any, scripts: Path) -> None:
    script = scripts / "shots.yaml"
    script.write_text(SCRIPT, encoding="utf-8")
    open_script(qtbot, window, script)
    window.editor.tree.select_step("look")
    tools = window.step_tools.image_tools[None]

    dialog = tools.open_capture()
    assert dialog.embedding and dialog.name_edit.text() == "look"
    with qtbot.waitSignal(dialog.captured, timeout=WAIT) as captured:
        dialog.capture(bbox=[10, 10, 60, 40], window=TITLE)
    value, result = captured.args
    assert value == "#look"
    assert result["image_name"] == "look" and result["window_bbox"] == [10, 10, 60, 40]
    yaml = window.editor.yaml_view
    wait_for(qtbot, lambda: "images:\n  look:\n    png: |" in yaml.toPlainText())
    assert yaml.toPlainText().startswith("# The GUI's screen tools.\n")
    wait_for(qtbot, lambda: "image: '#look'" in yaml.toPlainText())
    state = window.ctx.document.state
    assert [image["name"] for image in state.images] == ["look"]
    preview = tools.preview
    wait_for(qtbot, lambda: preview.image is not None and preview.value == "#look")
    assert (preview.image.width(), preview.image.height()) == (60, 40)

    with qtbot.waitSignal(tools.tested, timeout=WAIT) as tested:
        tools.test()
    test = tested.args[0]
    assert test.outcome.found and test.outcome.score > 0.99
    assert test.outcome.bbox == (410, 310, 60, 40)
    assert test.outcome.window == TITLE
    assert test.shot is not None and (test.shot.width(), test.shot.height()) == (120, 80)
    assert test.mark == (10, 10, 60, 40)
    dialog_ = tools.result_dialog
    assert dialog_ is not None and dialog_.summary.text().startswith("Found: score")
    assert [mark.rect for mark in dialog_.view.marks] == [(10, 10, 60, 40)]

    # The same name again is refused unless replacing it.
    dialog = tools.open_capture()
    assert dialog.name_edit.text() == "look_2"
    dialog.name_edit.setText("look")
    with qtbot.waitSignal(dialog.problem, timeout=WAIT) as problem:
        dialog.capture(bbox=[10, 10, 60, 40], window=TITLE)
    assert problem.args[0].details["reason"] == "exists"
    assert "already has an image" in dialog.info.text()


def test_capture_to_a_file_and_the_window_list(qtbot: Any, window: Any,
                                               scripts: Path) -> None:
    script = scripts / "shots.yaml"
    script.write_text(SCRIPT, encoding="utf-8")
    open_script(qtbot, window, script)
    window.editor.tree.select_step("look")
    tools = window.step_tools.image_tools[None]
    dialog = tools.open_capture()
    dialog.file_option.setChecked(True)
    assert dialog.file_edit.text() == "look.png"
    dialog.padding.setValue(2)
    with qtbot.waitSignal(dialog.captured, timeout=WAIT) as captured:
        dialog.capture(bbox=[410, 310, 60, 40])
    assert captured.args[0] == "look.png"
    assert captured.args[1]["bbox"] == [408, 308, 64, 44]
    assert (scripts / "look.png").read_bytes().startswith(b"\x89PNG")
    wait_for(qtbot, lambda: "image: look.png" in window.editor.yaml_view.toPlainText())
    wait_for(qtbot, lambda: tools.preview.image is not None)
    assert tools.preview.caption.text() == "look.png · 64 x 44 px · file"

    chooser = window.step_tools.windows()
    with qtbot.waitSignal(chooser.listed, timeout=WAIT):
        pass
    assert TITLE in chooser.titles()
    chooser.filter.setText("gui target")
    chooser.close()


def test_a_failed_search_is_in_the_history(qtbot: Any, window: Any, scripts: Path) -> None:
    from PySide6.QtGui import QColor, QImage

    noise = QImage(40, 40, QImage.Format.Format_RGB32)
    for y in range(40):
        for x in range(40):
            noise.setPixelColor(x, y, QColor((x * 37 + y * 11) % 256, (x * y) % 256,
                                             (x * 5 + y * 91) % 256))
    assert noise.save(str(scripts / "noise.png"))
    script = scripts / "fail.yaml"
    script.write_text("name: Fail\nsteps:\n  - id: look\n    wait_image_in: "
                      f"{{image: noise.png, window: {{title: {TITLE}}}, timeout: 1s}}\n",
                      encoding="utf-8")
    open_script(qtbot, window, script)
    with qtbot.waitSignal(window.ctx.run.run_finished, timeout=WAIT) as finished:
        window.run_panel.start("normal")
    assert finished.args[0]["status"] == "failed"

    history = window.history
    wait_for(qtbot, lambda: len(history.runs) == 1 and history.runs[0].status == "failed")
    with qtbot.waitSignal(history.run_loaded, timeout=WAIT) as loaded:
        history.select_run(history.runs[0].run_id)
    detail = loaded.args[0]
    [failure] = detail.failures
    assert failure.step_id == "look" and failure.template == "noise.png"
    assert failure.region == (400, 300, 120, 80)
    view = history.failure_view
    assert view.template.image is not None and view.template.image.width() == 40
    assert view.capture.image is not None and view.capture.image.width() == 120
    [mark] = view.capture.marks
    assert mark.rect == failure.bbox_in_capture
    assert history.tabs.currentIndex() == 1
