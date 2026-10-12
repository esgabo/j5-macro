"""The whole stack on a real X server: ``j5 run`` clicks and types into an X11 window.

CLI → engine process → interpreter → input masters → X11 backend → Xvfb, with a test
window recording what it actually receives.
"""

from __future__ import annotations

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
