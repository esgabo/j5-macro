"""Backend selection from J5_BACKEND, the platform and the session type."""

from __future__ import annotations

import os
from typing import Any

import pytest

from j5_macro.backends import select as select_module
from j5_macro.backends.fake import FakeBackend
from j5_macro.backends.select import (
    WAYLAND_REASON,
    WINDOWS_REASON,
    XLIB_MISSING_REASON,
    select_backend,
)


class _Opened:
    """Stands in for an X11Backend: records the display it was opened on."""

    name = "x11"

    def __init__(self, display_name: str) -> None:
        self.display_name = display_name


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_open(display_name: str) -> Any:
        calls.append(display_name)
        return _Opened(display_name)

    monkeypatch.setattr(select_module, "_open_x11", fake_open)
    return calls


def _fail_with(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    def fail(display_name: str) -> Any:
        raise exc

    monkeypatch.setattr(select_module, "_open_x11", fail)


# --- explicit choices ----------------------------------------------------------------------


def test_none_disables_input() -> None:
    choice = select_backend({"J5_BACKEND": "none", "DISPLAY": ":0"}, "linux")
    assert (choice.name, choice.backend) == (None, None)
    assert "J5_BACKEND=none" in choice.reason


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_fake_works_everywhere(platform: str) -> None:
    choice = select_backend({"J5_BACKEND": " Fake "}, platform)
    assert choice.name == "fake"
    assert isinstance(choice.backend, FakeBackend)


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_windows_is_not_available_yet(platform: str, opened: list[str]) -> None:
    choice = select_backend({"J5_BACKEND": "windows", "DISPLAY": ":0"}, platform)
    assert (choice.name, choice.backend, choice.reason) == (None, None, WINDOWS_REASON)
    assert opened == []


def test_unknown_value() -> None:
    choice = select_backend({"J5_BACKEND": "wayland"}, "linux")
    assert choice.backend is None
    assert "'wayland'" in choice.reason


def test_explicit_x11_skips_the_wayland_check(opened: list[str]) -> None:
    env = {"J5_BACKEND": "x11", "XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0",
           "DISPLAY": ":1"}
    choice = select_backend(env, "linux")
    assert choice.name == "x11"
    assert opened == [":1"]
    assert "J5_BACKEND=x11" in choice.reason


def test_explicit_x11_needs_display(opened: list[str]) -> None:
    choice = select_backend({"J5_BACKEND": "x11"}, "linux")
    assert choice.backend is None
    assert "DISPLAY" in choice.reason
    assert opened == []


@pytest.mark.parametrize("value", [None, "", "auto", "AUTO"])
def test_auto_is_the_default(value: str | None, opened: list[str]) -> None:
    env = {"DISPLAY": ":0"}
    if value is not None:
        env["J5_BACKEND"] = value
    assert select_backend(env, "linux").name == "x11"


# --- auto ----------------------------------------------------------------------------------


@pytest.mark.parametrize("env", [
    {"XDG_SESSION_TYPE": "wayland"},
    {"XDG_SESSION_TYPE": "Wayland", "DISPLAY": ":0"},
    {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
    {"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"},
])
def test_auto_refuses_wayland_even_with_xwayland(env: dict[str, str],
                                                 opened: list[str]) -> None:
    choice = select_backend(env, "linux")
    assert (choice.name, choice.backend, choice.reason) == (None, None, WAYLAND_REASON)
    assert opened == []


@pytest.mark.parametrize("env", [
    {"DISPLAY": ":0"},
    {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"},
    {"DISPLAY": ":0", "XDG_SESSION_TYPE": "tty", "WAYLAND_DISPLAY": ""},
])
def test_auto_picks_x11_with_display(env: dict[str, str], opened: list[str]) -> None:
    choice = select_backend(env, "linux")
    assert choice.name == "x11"
    assert isinstance(choice.backend, _Opened)
    assert opened == [":0"]
    assert "DISPLAY=:0" in choice.reason


def test_auto_x11_on_bsd(opened: list[str]) -> None:
    assert select_backend({"DISPLAY": ":0"}, "freebsd14").name == "x11"


@pytest.mark.parametrize("env", [{}, {"XDG_SESSION_TYPE": "x11"}, {"DISPLAY": " "}])
def test_auto_without_display(env: dict[str, str], opened: list[str]) -> None:
    choice = select_backend(env, "linux")
    assert choice.backend is None
    assert "DISPLAY is not set" in choice.reason
    assert opened == []


def test_auto_on_windows(opened: list[str]) -> None:
    choice = select_backend({"DISPLAY": ":0"}, "win32")
    assert (choice.backend, choice.reason) == (None, WINDOWS_REASON)
    assert opened == []


def test_auto_on_macos() -> None:
    choice = select_backend({}, "darwin")
    assert choice.backend is None
    assert "macOS" in choice.reason


def test_auto_on_an_unknown_platform() -> None:
    choice = select_backend({"DISPLAY": ":0"}, "aix")
    assert choice.backend is None
    assert "'aix'" in choice.reason


# --- X11 failures never raise --------------------------------------------------------------


def test_missing_python_xlib(monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_with(monkeypatch, ModuleNotFoundError("No module named 'Xlib'", name="Xlib"))
    choice = select_backend({"DISPLAY": ":0"}, "linux")
    assert (choice.backend, choice.reason) == (None, XLIB_MISSING_REASON)
    assert "pip install 'j5-macro[x11]'" in choice.reason


def test_other_import_errors_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_with(monkeypatch, ImportError("broken thing", name="something_else"))
    choice = select_backend({"DISPLAY": ":0"}, "linux")
    assert choice.backend is None
    assert "broken thing" in choice.reason


def test_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_with(monkeypatch, ConnectionRefusedError("Can't connect to display \":7\""))
    choice = select_backend({"DISPLAY": ":7"}, "linux")
    assert choice.backend is None
    assert "':7'" in choice.reason
    assert "Can't connect" in choice.reason


def test_failure_without_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_with(monkeypatch, RuntimeError())
    assert "RuntimeError" in select_backend({"DISPLAY": ":0"}, "linux").reason


def test_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("J5_BACKEND", "none")
    assert select_backend().backend is None


# --- against a real X server ---------------------------------------------------------------


@pytest.mark.x11
def test_auto_opens_the_real_x11_backend() -> None:
    display = os.environ["DISPLAY"]
    choice = select_backend({"DISPLAY": display}, "linux")
    assert choice.name == "x11", choice.reason
    assert choice.backend is not None
    try:
        assert choice.backend.capabilities.char_input
        assert choice.backend.pointer_position() is not None
    finally:
        choice.backend.close()


@pytest.mark.x11
def test_unreachable_display_gives_a_reason() -> None:
    choice = select_backend({"DISPLAY": ":987"}, "linux")
    assert choice.backend is None
    assert "cannot connect to the X display ':987'" in choice.reason
