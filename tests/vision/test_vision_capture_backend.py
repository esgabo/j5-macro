"""Which mss backend the capturer opens: the default one, else (on Linux) the Xlib one."""

from __future__ import annotations

import types
from typing import Any

import pytest

pytest.importorskip("numpy")
pytest.importorskip("mss")

from mss.exception import ScreenShotError

from j5_macro.vision import capture


def fake_mss(monkeypatch: pytest.MonkeyPatch, *, failing: set[str]) -> list[str]:
    """Replace ``mss.MSS`` with a factory that fails for the backends in ``failing``;
    returns the list of backends asked for, in order."""
    asked: list[str] = []

    def factory(**kwargs: Any) -> str:
        backend = kwargs.get("backend", "default")
        asked.append(backend)
        if backend in failing:
            raise ScreenShotError(f"{backend} cannot start")
        return backend

    monkeypatch.setattr(capture, "mss", types.SimpleNamespace(MSS=factory))
    return asked


def test_the_default_backend_is_used_when_it_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = fake_mss(monkeypatch, failing=set())
    assert capture._new_mss() == "default"
    assert asked == ["default"]


def test_linux_falls_back_to_xlib(monkeypatch: pytest.MonkeyPatch) -> None:
    """KasmVNC's Xvnc lists the root depth twice and mss 10's XCB backend refuses it."""
    monkeypatch.setattr(capture.sys, "platform", "linux")
    asked = fake_mss(monkeypatch, failing={"default"})
    assert capture._new_mss() == "xlib"
    assert asked == ["default", "xlib"]


def test_the_first_error_is_raised_when_xlib_fails_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture.sys, "platform", "linux")
    fake_mss(monkeypatch, failing={"default", "xlib"})
    with pytest.raises(ScreenShotError, match="default cannot start"):
        capture._new_mss()


def test_no_fallback_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture.sys, "platform", "win32")
    asked = fake_mss(monkeypatch, failing={"default"})
    with pytest.raises(ScreenShotError):
        capture._new_mss()
    assert asked == ["default"]
