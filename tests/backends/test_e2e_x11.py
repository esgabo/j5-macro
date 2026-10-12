"""The whole stack on a real X server: ``j5 run`` clicks and types into an X11 window.

CLI → engine process → interpreter → input masters → X11 backend → Xvfb, with a test
window recording what it actually receives.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from j5_macro.cli.main import main
from j5_macro.cli.options import ENGINE_CMD_ENV

pytestmark = pytest.mark.x11

TITLE = "j5 e2e target"
SCRIPT = f"""\
version: 1
name: e2e
input:
  mouse: {{strategy: linear, speed: 4}}
  keyboard: {{strategy: linear, wpm: 400}}
steps:
  - id: focus
    click: {{window: {{title: "{TITLE}"}}, x: 30, y: 40}}
  - id: write
    type: "Hi ñé€!"
  - id: save
    key: ctrl+s
"""


def keysym_to_char(keysym: int) -> str:
    if keysym >= 0x1000000:
        return chr(keysym - 0x1000000)
    if keysym == 0x20AC:
        return "€"
    if keysym < 0x100:
        return chr(keysym)
    return f"<{keysym:#x}>"


class Recorder(threading.Thread):
    """Resolves key events while they arrive, before temporary key remaps are undone."""

    def __init__(self, observer: Any) -> None:
        super().__init__(daemon=True)
        self.observer = observer
        self.done = threading.Event()
        self.clicks: list[tuple[int, int, int]] = []
        self.keys: list[tuple[int, int]] = []

    def run(self) -> None:
        from Xlib import X

        while not self.done.is_set():
            for event in self.observer.events(0.02):
                if event.type == X.ButtonPress:
                    self.clicks.append((event.detail, event.event_x, event.event_y))
                elif event.type == X.KeyPress:
                    self.keys.append((self.observer.keysym_of(event), event.state))


def test_run_clicks_and_types_into_a_real_window(observer, tmp_path, monkeypatch, capsys):
    from Xlib import X

    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("J5_BACKEND", "x11")
    monkeypatch.delenv(ENGINE_CMD_ENV, raising=False)
    script = tmp_path / "e2e.yaml"
    script.write_text(SCRIPT, encoding="utf-8")
    observer.create_window(100, 100, 300, 200, wm_name=TITLE, net_wm_name=TITLE,
                           wm_state=True, focus=True)
    observer.drain()

    recorder = Recorder(observer)
    recorder.start()
    try:
        code = main(["run", "--seed", "1", str(script)])
    finally:
        recorder.done.set()
        recorder.join(5)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "backend x11" in out

    assert recorder.clicks == [(1, 30, 40)]
    control = 0xFFE3
    typed = "".join(keysym_to_char(keysym) for keysym, _ in recorder.keys
                    if keysym != control and not keysym_to_char(keysym).startswith("<"))
    assert typed == "Hi ñé€!s"
    save = recorder.keys[-1]
    assert keysym_to_char(save[0]) == "s"
    assert save[1] & X.ControlMask
    assert os.environ.get("DISPLAY")
    (run_dir,) = Path(tmp_path / "runs").iterdir()
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"input_planned"' in events and '"keys_released"' not in events


# --- vision ------------------------------------------------------------------------------

VISION_SCRIPT = """\
version: 1
name: vision e2e
input:
  mouse: {strategy: linear, speed: 4}
steps:
  - id: appear
    wait_image: {image: target.png, timeout: 3s}
  - id: check
    if:
      when: {image_visible: target.png}
      then:
        - id: hit
          click: {image: target.png}
"""

MISSING_SCRIPT = """\
version: 1
name: vision e2e missing
steps:
  - id: never
    wait_image: {image: missing.png, timeout: 600ms}
"""


def paint_stripes(observer: Any, window: Any) -> None:
    """Fill the window with coloured stripes: a pattern with texture to match."""
    colours = [0xD03030, 0x30A040, 0x3050D0, 0xE0C020, 0x202020, 0x9040B0]
    for index, colour in enumerate(colours):
        gc = window.create_gc(foreground=colour)
        window.fill_rectangle(gc, index * 20, 0, 20, 80)
        gc.free()
    gc = window.create_gc(foreground=0xFFFFFF)
    window.fill_rectangle(gc, 10, 30, 70, 12)
    gc.free()
    observer.display.sync()


def vision_env(monkeypatch: Any, tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    pytest.importorskip("mss")
    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("J5_BACKEND", "x11")
    monkeypatch.delenv(ENGINE_CMD_ENV, raising=False)


def test_run_waits_for_an_image_and_clicks_it(observer, tmp_path, monkeypatch, capsys):
    from j5_macro.backends import Rect
    from j5_macro.vision.toolkit import create_toolkit

    vision_env(monkeypatch, tmp_path)
    window = observer.create_window(400, 300, 120, 80, wm_name="j5 vision target",
                                    wm_state=True)
    paint_stripes(observer, window)
    toolkit = create_toolkit()
    try:
        toolkit.save_image(toolkit.capturer.grab(Rect(410, 310, 60, 40)),
                           tmp_path / "target.png", None)
    finally:
        toolkit.capturer.close()
    script = tmp_path / "vision.yaml"
    script.write_text(VISION_SCRIPT, encoding="utf-8")
    observer.drain()

    recorder = Recorder(observer)
    recorder.start()
    try:
        code = main(["run", "--seed", "3", str(script)])
    finally:
        recorder.done.set()
        recorder.join(5)
    out = capsys.readouterr().out
    assert code == 0, out
    # The template is the window's (10, 10)-(70, 50) area: its center is (40, 30).
    assert recorder.clicks == [(1, 40, 30)]
    (run_dir,) = (tmp_path / "runs").iterdir()
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert events.count('"image_search"') >= 3
    assert not (run_dir / "captures").exists() or not any((run_dir / "captures").iterdir())


def test_run_fails_with_a_capture_when_the_image_never_appears(tmp_path, monkeypatch,
                                                               capsys):
    from j5_macro.vision.toolkit import create_toolkit

    vision_env(monkeypatch, tmp_path)
    import numpy as np

    toolkit = create_toolkit()
    try:
        noise = np.random.default_rng(7).integers(0, 255, (40, 60, 3), dtype=np.uint8)
        from j5_macro.vision import Frame

        toolkit.save_image(Frame(noise), tmp_path / "missing.png", None)
    finally:
        toolkit.capturer.close()
    script = tmp_path / "missing.yaml"
    script.write_text(MISSING_SCRIPT, encoding="utf-8")

    assert main(["run", str(script)]) == 1
    out = capsys.readouterr().out
    assert "image_not_found" in out
    (run_dir,) = (tmp_path / "runs").iterdir()
    captures = list((run_dir / "captures").iterdir())
    assert len(captures) == 1 and captures[0].suffix == ".png"


# --- user interference -------------------------------------------------------------------

INTERFERENCE_SCRIPT = """\
version: 1
name: interference e2e
input:
  mouse: {strategy: linear, speed: 0.1, interference: stop}
steps:
  - id: slow
    move: {x: 1200, y: 700}
  - id: never
    log: not reached
"""
START = (100, 100)
USER_AT = (150, 600)


class User(threading.Thread):
    """Plays the user on a connection of its own: once the run has started moving the
    pointer away from ``START``, it puts the pointer at ``USER_AT`` (again, should the
    run's next point land before the run had a chance to notice)."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.done = threading.Event()
        self.warps = 0

    def run(self) -> None:
        from Xlib.display import Display

        display = Display(os.environ.get("DISPLAY"))
        try:
            root = display.screen().root
            while not self.done.wait(0.02):
                pointer = root.query_pointer()
                position = (pointer.root_x, pointer.root_y)
                if position != USER_AT and position[0] > START[0] + 5:
                    root.warp_pointer(*USER_AT)
                    display.sync()
                    self.warps += 1
        finally:
            display.close()


def test_moving_the_mouse_mid_movement_stops_the_run(observer, tmp_path, monkeypatch,
                                                     capsys):
    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("J5_BACKEND", "x11")
    monkeypatch.delenv(ENGINE_CMD_ENV, raising=False)
    script = tmp_path / "interference.yaml"
    script.write_text(INTERFERENCE_SCRIPT, encoding="utf-8")
    observer.root.warp_pointer(*START)
    observer.display.sync()

    user = User()
    user.start()
    try:
        code = main(["run", "--seed", "5", str(script)])
    finally:
        user.done.set()
        user.join(5)
    out = capsys.readouterr().out
    assert user.warps >= 1, out
    assert code == 3, out
    assert "you moved the mouse" in out
    assert "stopped (interference)" in out
    (run_dir,) = (tmp_path / "runs").iterdir()
    events = [json.loads(line) for line in
              (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    types = [event["type"] for event in events]
    (interference,) = [e["payload"] for e in events if e["type"] == "user_interference"]
    assert interference["action"] == "stop"
    assert interference["actual"] == list(USER_AT)
    assert events[-1]["type"] == "run_finished"
    assert events[-1]["payload"]["status"] == "stopped"
    assert events[-1]["payload"]["reason"] == "interference"
    assert "step_failed" not in types and "step_finished" not in types
    assert not any(e["type"] == "step_started" and e["payload"]["step_id"] == "never"
                   for e in events)
    # The movement was cut short: the pointer stays where the user put it.
    pointer = observer.root.query_pointer()
    assert (pointer.root_x, pointer.root_y) == USER_AT


# --- human strategy ----------------------------------------------------------------------

HUMAN_TEXT = "Hello, human typist! Quick brown fox."

HUMAN_SCRIPT = f"""\
version: 1
name: human e2e
input:
  mouse: {{strategy: human, path: PATH, speed: 3, overshoot: 0.5}}
  keyboard: {{strategy: human, wpm: 400, typos: true}}
steps:
  - id: focus
    click: {{window: {{title: "{TITLE}"}}, x: 150, y: 100}}
  - id: write
    type: "{HUMAN_TEXT}"
"""

BACKSPACE = 0xFF08


def replay(keys: list[tuple[int, int]]) -> str:
    """The text a key-press sequence leaves behind (backspace deletes)."""
    text: list[str] = []
    for keysym, _ in keys:
        if keysym == BACKSPACE:
            if text:
                text.pop()
            continue
        char = keysym_to_char(keysym)
        if not char.startswith("<"):
            text.append(char)
    return "".join(text)


@pytest.mark.parametrize("path", ["windmouse", "bezier"])
def test_human_input_clicks_and_types_exactly(observer, tmp_path, monkeypatch, capsys,
                                              path):
    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("J5_BACKEND", "x11")
    monkeypatch.delenv(ENGINE_CMD_ENV, raising=False)
    script = tmp_path / "human.yaml"
    script.write_text(HUMAN_SCRIPT.replace("PATH", path), encoding="utf-8")
    observer.create_window(100, 100, 300, 200, wm_name=TITLE, net_wm_name=TITLE,
                           wm_state=True, focus=True)
    observer.drain()

    recorder = Recorder(observer)
    recorder.start()
    try:
        # Seed 2 makes at least one typo in this text (checked below), so the
        # corrections are exercised against a real X server.
        code = main(["run", "--seed", "2", str(script)])
    finally:
        recorder.done.set()
        recorder.join(5)
    out = capsys.readouterr().out
    assert code == 0, out

    assert recorder.clicks == [(1, 150, 100)]
    assert any(keysym == BACKSPACE for keysym, _ in recorder.keys), "no typo was made"
    assert replay(recorder.keys) == HUMAN_TEXT
    (run_dir,) = (tmp_path / "runs").iterdir()
    events = [json.loads(line) for line in
              (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    planned = [e["payload"] for e in events if e["type"] == "input_planned"]
    assert {p["strategy"] for p in planned} == {"human"}
    assert not [e for e in events if e["type"] in ("user_interference", "keys_released")]
