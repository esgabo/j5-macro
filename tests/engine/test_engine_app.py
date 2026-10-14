"""The engine's entry point: what it does before it starts reading commands."""

from __future__ import annotations

from typing import Any

import pytest

from j5_macro.engine import app
from j5_macro.vision import toolkit


@pytest.mark.parametrize(("platform", "preloaded"), [("win32", True), ("linux", False)])
def test_windows_loads_the_vision_libraries_before_reading_commands(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Any, platform: str,
        preloaded: bool) -> None:
    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(app.sys, "platform", platform)
    monkeypatch.setattr(app.signal, "signal", lambda *args: None)
    monkeypatch.setattr(app, "declare_dpi_awareness", lambda: None)
    monkeypatch.setattr(app, "select_backend", lambda: None)
    monkeypatch.setattr(toolkit, "preload", lambda: calls.append("preload") or True)

    def run(*args: Any, **kwargs: Any) -> int:
        calls.append("run")
        return 0

    monkeypatch.setattr(app, "run", run)
    assert app.main([]) == 0
    assert calls == (["preload", "run"] if preloaded else ["run"])


def test_preload_reports_whether_vision_is_installed() -> None:
    pytest.importorskip("cv2")
    assert toolkit.preload() is True
