"""Releasing what a dead engine held, from the CLI process."""

from __future__ import annotations

from j5_macro.backends.base import Button
from j5_macro.backends.fake import FakeBackend
from j5_macro.backends.select import BackendChoice
from j5_macro.cli.release import release_held


def test_entries_map_to_primitives_newest_first():
    backend = FakeBackend()
    report = release_held(["shift", "a", "button:left", "ctrl"],
                          select=lambda: BackendChoice("fake", backend, "test"))
    assert [(c.name, c.args) for c in backend.calls] == [
        ("key_up", ("ctrl",)), ("button_up", (Button.LEFT,)), ("char_up", ("a",)),
        ("key_up", ("shift",))]
    assert backend.closed
    assert report.released == ["ctrl", "button:left", "a", "shift"]
    assert report.message() == ("released what the engine held: ctrl, button:left, a, shift "
                                "(backend fake)")


def test_failures_are_reported_and_the_rest_released():
    backend = FakeBackend()
    report = release_held(["button:side", "nonsense_key", "shift"],
                          select=lambda: BackendChoice("fake", backend, "test"))
    assert report.released == ["shift"]
    assert [entry for entry, _ in report.failed] == ["nonsense_key", "button:side"]
    assert "could not release nonsense_key" in report.message()


def test_without_a_backend_nothing_is_released():
    report = release_held(["shift"], select=lambda: BackendChoice(None, None, "no X11"))
    assert report.message() == ("the engine held shift and they could not be released: "
                                "no input backend (no X11)")


def test_nothing_held_needs_no_backend():
    def select() -> BackendChoice:
        raise AssertionError("no backend needed")

    assert release_held([], select=select).released == []
