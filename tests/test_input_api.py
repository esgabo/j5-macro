import pytest

from j5_macro.backends import Button, Rect, UnknownKey
from j5_macro.backends.fake import FakeBackend
from j5_macro.input import InputProfile, KeyboardProfile, MouseProfile


def test_profile_defaults_and_merge_order():
    script = InputProfile().merged({"mouse": {"strategy": "linear", "speed": 2.0}})
    run = script.merged({"keyboard": {"strategy": "direct"}})
    step = run.merged({"mouse": {"speed": 0.5}})
    assert step.mouse == MouseProfile(strategy="linear", speed=0.5)
    assert step.keyboard == KeyboardProfile(strategy="direct")
    assert InputProfile().merged(None) == InputProfile()


@pytest.mark.parametrize("override", [
    {"pointer": {}},
    {"mouse": {"warp": True}},
    {"mouse": {"strategy": "teleport"}},
    {"mouse": {"speed": 0}},
    {"mouse": {"path": "spiral"}},
    {"mouse": {"overshoot": 2}},
    {"keyboard": {"wpm": -5}},
    {"keyboard": "fast"},
])
def test_profile_rejects_bad_overrides(override):
    with pytest.raises(ValueError):
        InputProfile().merged(override)


def test_rect_helpers():
    rect = Rect(-100, 20, 50, 10)
    assert rect.contains(-100, 20)
    assert not rect.contains(-50, 20)
    assert rect.center == (-75, 25)


def test_fake_backend_records_and_tracks_state():
    fake = FakeBackend(windows={"Untitled - Notepad": Rect(10, 10, 300, 200)})
    fake.move_abs(5, 6)
    fake.move_rel(1, -1)
    fake.button_down(Button.LEFT)
    fake.key_down("ctrl")
    fake.char_down("ñ")
    assert fake.pointer == (6, 5)
    assert fake.held_buttons == [Button.LEFT]
    assert fake.held_keys == ["ctrl"]
    fake.char_up("ñ")
    fake.key_up("ctrl")
    fake.button_up(Button.LEFT)
    assert fake.held_keys == fake.held_chars == fake.held_buttons == []
    assert fake.typed_text() == "ñ"
    assert fake.find_window("Notepad") == Rect(10, 10, 300, 200)
    assert fake.find_window("Calculator") is None
    with pytest.raises(UnknownKey):
        fake.key_down("hyper")


def test_fake_backend_hotkeys():
    fake = FakeBackend()
    presses = []
    unwatch = fake.watch_hotkey(["ctrl", "alt", "shift", "q"], lambda: presses.append(1))
    fake.press_hotkey(["ctrl", "alt", "shift", "q"])
    unwatch()
    fake.press_hotkey(["ctrl", "alt", "shift", "q"])
    assert presses == [1]


def test_select_backend_overrides():
    from j5_macro.backends import select_backend

    assert select_backend({"J5_BACKEND": "none"}).backend is None
    fake = select_backend({"J5_BACKEND": "fake"})
    assert fake.name == "fake" and isinstance(fake.backend, FakeBackend)
    assert "unknown" in select_backend({"J5_BACKEND": "amiga"}).reason
