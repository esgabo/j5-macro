"""The authoring tools on a real X server (Xvfb in CI): the CLI captures a painted
window into a script and finds it again, and the overlay (when tkinter is there) is
driven with real pointer and key events."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from j5_macro.cli.main import main
from j5_macro.cli.options import ENGINE_CMD_ENV

pytestmark = pytest.mark.x11

TITLE = "j5 authoring target"
WINDOW = (400, 300, 120, 80)
SCRIPT = """\
# Templates for the authoring target.
name: Authoring
steps:
  - log: ready
"""


def paint_stripes(observer: Any, window: Any) -> None:
    """Coloured stripes with a white bar: a pattern with texture to match."""
    colours = [0xD03030, 0x30A040, 0x3050D0, 0xE0C020, 0x202020, 0x9040B0]
    for index, colour in enumerate(colours):
        gc = window.create_gc(foreground=colour)
        window.fill_rectangle(gc, index * 20, 0, 20, 80)
        gc.free()
    gc = window.create_gc(foreground=0xFFFFFF)
    window.fill_rectangle(gc, 10, 30, 70, 12)
    gc.free()
    observer.display.sync()


@pytest.fixture
def target(observer: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    pytest.importorskip("cv2")
    pytest.importorskip("mss")
    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("J5_BACKEND", "x11")
    monkeypatch.delenv(ENGINE_CMD_ENV, raising=False)
    window = observer.create_window(*WINDOW, wm_name=TITLE, wm_state=True)
    paint_stripes(observer, window)
    return window


def j5(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_agent_workflow_from_the_cli(target: Any, tmp_path: Path,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = j5(capsys, "windows")
    assert code == 0, err
    assert f"[400, 300, 120, 80]      {TITLE}" in out.splitlines()

    shot = tmp_path / "window.png"
    code, out, err = j5(capsys, "screenshot", "--window", "authoring target", "-o", str(shot))
    assert code == 0, err
    assert out.strip() == str(shot)
    assert shot.read_bytes().startswith(b"\x89PNG")

    script = tmp_path / "script.yaml"
    script.write_text(SCRIPT, encoding="utf-8")
    code, out, err = j5(capsys, "capture", "--bbox", "10,10,60,40", "--window",
                        "authoring target", "--embed", str(script), "--name", "stripes")
    assert code == 0, err
    assert out.strip() == f'click: {{image: "#stripes", window: {{title: {TITLE}}}}}'
    assert script.read_text(encoding="utf-8").startswith(SCRIPT)

    code, out, err = j5(capsys, "find", "#stripes", "--script", str(script), "--window",
                        "authoring target", "--json")
    assert code == 0, err
    result = json.loads(out)
    assert result["found"] is True and result["score"] > 0.99
    assert result["bbox"] == [410, 310, 60, 40]
    assert result["window_bbox"] == [10, 10, 60, 40]

    code, out, err = j5(capsys, "validate", str(script))
    assert code == 0, out

    # A region where it is not: not found, exit code 1, the best score on stderr.
    code, out, err = j5(capsys, "find", "#stripes", "--script", str(script),
                        "--region", "0,0,200,200")
    assert code == 1 and out == ""
    assert "not found: best score" in err


def test_capture_to_a_file_prints_a_click_step(target: Any, tmp_path: Path,
                                               capsys: pytest.CaptureFixture[str],
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "img").mkdir()
    code, out, err = j5(capsys, "capture", "--bbox", "410,310,60,40", "-o", "img/bar.png")
    assert code == 0, err
    assert out.strip() == f"click: {{image: img/bar.png, window: {{title: {TITLE}}}}}"
    code, out, err = j5(capsys, "find", "img/bar.png")
    assert code == 0, err
    assert out.strip() == "click: {image: img/bar.png}"
    code, _, err = j5(capsys, "capture", "--bbox", "410,310,60,40", "-o", "img/bar.png")
    assert code == 1 and "already exists" in err


# --- the overlay ------------------------------------------------------------------------


class Cli(threading.Thread):
    """``j5 ARGS`` on a thread, so the test can drive the overlay meanwhile."""

    def __init__(self, capsys: pytest.CaptureFixture[str], *args: str) -> None:
        super().__init__(daemon=True)
        self.capsys = capsys
        self.args = args
        self.code: int | None = None

    def run(self) -> None:
        self.code = main(list(self.args))

    def result(self, timeout: float = 30.0) -> tuple[int | None, str, str]:
        self.join(timeout)
        assert not self.is_alive(), "j5 did not finish"
        captured = self.capsys.readouterr()
        return self.code, captured.out, captured.err


@pytest.fixture
def overlay_target(target: Any) -> Any:
    pytest.importorskip("tkinter")
    return target


def wait_for_overlay(observer: Any, timeout: float = 20.0) -> None:
    """Until a viewable window as large as the screen is mapped (the overlay), plus a
    moment for Tk to draw it and take the grab."""
    from Xlib import X

    width, height = observer.screen.width_in_pixels, observer.screen.height_in_pixels
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for child in observer.root.query_tree().children:
            try:
                geometry = child.get_geometry()
                viewable = child.get_attributes().map_state == X.IsViewable
            except Exception:
                continue
            if viewable and geometry.width == width and geometry.height == height:
                time.sleep(0.5)
                return
        time.sleep(0.05)
    raise AssertionError("the overlay did not appear")


def drag(backend: Any, start: tuple[int, int], end: tuple[int, int]) -> None:
    from j5_macro.backends.base import Button

    backend.move_abs(*start)
    time.sleep(0.05)
    backend.button_down(Button.LEFT)
    steps = 10
    for i in range(1, steps + 1):
        backend.move_abs(start[0] + (end[0] - start[0]) * i // steps,
                         start[1] + (end[1] - start[1]) * i // steps)
        time.sleep(0.02)
    backend.button_up(Button.LEFT)


def test_overlay_drag_captures_the_rectangle(overlay_target: Any, observer: Any,
                                             x11_backend: Any, tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "dragged.png"
    cli = Cli(capsys, "capture", "-o", str(out), "--json")
    cli.start()
    wait_for_overlay(observer)
    drag(x11_backend, (410, 310), (469, 349))
    code, stdout, err = cli.result()
    assert code == 0, err
    result = json.loads(stdout)
    assert result["bbox"] == [410, 310, 60, 40]
    assert result["window"] == TITLE and result["window_bbox"] == [10, 10, 60, 40]
    # The overlay covered the window, which has no backing store: paint it again.
    paint_stripes(observer, overlay_target)
    code, stdout, err = j5(capsys, "find", str(out), "--json")
    assert code == 0, err
    assert json.loads(stdout)["bbox"] == [410, 310, 60, 40]


def test_overlay_click_picks_a_point(overlay_target: Any, observer: Any,
                                     x11_backend: Any,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    from j5_macro.backends.base import Button

    cli = Cli(capsys, "pick")
    cli.start()
    wait_for_overlay(observer)
    x11_backend.move_abs(450, 340)
    time.sleep(0.05)
    x11_backend.button_down(Button.LEFT)
    x11_backend.button_up(Button.LEFT)
    code, stdout, err = cli.result()
    assert code == 0, err
    assert stdout.strip() == f"click: {{window: {{title: {TITLE}}}, x: 50, y: 40}}"


def test_escape_closes_the_overlay(overlay_target: Any, observer: Any, x11_backend: Any,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    cli = Cli(capsys, "pick")
    cli.start()
    wait_for_overlay(observer)
    x11_backend.key_down("escape")
    x11_backend.key_up("escape")
    code, stdout, err = cli.result()
    assert code == 3 and stdout == ""
    assert "cancelled" in err


def test_find_show_closes_by_itself(overlay_target: Any, tmp_path: Path,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    template = tmp_path / "t.png"
    code, _, err = j5(capsys, "capture", "--bbox", "410,310,60,40", "-o", str(template))
    assert code == 0, err
    started = time.monotonic()
    code, stdout, err = j5(capsys, "find", str(template), "--show", "--show-ms", "300")
    assert code == 0, err
    assert stdout.strip() == f"click: {{image: {template}}}"
    assert time.monotonic() - started < 15
