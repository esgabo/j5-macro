"""The X11 backend against a real X server (Xvfb in CI): input arrives as X events."""

from __future__ import annotations

import threading
from typing import Any

import pytest

pytest.importorskip("Xlib")

from x11_harness import TIMEOUT_S, Observer, wait_until
from Xlib import X

from j5_macro.backends.base import (
    MODIFIER_KEYS,
    NAMED_KEYS,
    Button,
    Rect,
    UnknownKey,
    WindowInfo,
)
from j5_macro.backends.x11 import HotkeyConflict, X11Backend
from j5_macro.backends.x11.keys import char_keysym, key_keysym

pytestmark = pytest.mark.x11

Backend = X11Backend


def _input_window(observer: Observer) -> Any:
    window = observer.create_window(100, 100, 400, 300, wm_name="j5 input target", focus=True)
    observer.drain()
    return window


# --- pointer and screen ------------------------------------------------------------------


def test_screen_bounds_is_the_root_window(x11_backend: Backend, observer: Observer) -> None:
    geometry = observer.root.get_geometry()
    assert x11_backend.screen_bounds() == Rect(0, 0, geometry.width, geometry.height)


def test_display_scale_reads_xft_dpi(x11_backend: Backend, observer: Observer,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from Xlib import Xatom

    monkeypatch.delenv("GDK_SCALE", raising=False)
    root = observer.display.screen(0).root
    saved = root.get_full_property(Xatom.RESOURCE_MANAGER, Xatom.STRING)
    try:
        root.change_property(Xatom.RESOURCE_MANAGER, Xatom.STRING, 8,
                             b"Xcursor.size:\t24\nXft.dpi:\t144\n")
        observer.display.sync()
        assert x11_backend.display_scale(10, 10) == 1.5
        bounds = x11_backend.screen_bounds()
        assert x11_backend.display_scales() == [(bounds, 1.5)]
        root.delete_property(Xatom.RESOURCE_MANAGER)
        observer.display.sync()
        assert x11_backend.display_scale(10, 10) == 1.0
        monkeypatch.setenv("GDK_SCALE", "2")
        assert x11_backend.display_scale(10, 10) == 2.0
    finally:
        if saved is not None:
            root.change_property(Xatom.RESOURCE_MANAGER, Xatom.STRING, 8, saved.value)
        else:
            root.delete_property(Xatom.RESOURCE_MANAGER)
        observer.display.sync()


def test_move_abs_and_rel(x11_backend: Backend) -> None:
    x11_backend.move_abs(300, 200)
    assert x11_backend.pointer_position() == (300, 200)
    x11_backend.move_rel(15, -7)
    assert x11_backend.pointer_position() == (315, 193)
    x11_backend.move_rel(-115, 7)
    assert x11_backend.pointer_position() == (200, 200)


def test_buttons_arrive_on_the_window_under_the_pointer(x11_backend: Backend,
                                                        observer: Observer) -> None:
    window = _input_window(observer)
    x11_backend.move_abs(250, 250)
    for button, code in ((Button.LEFT, 1), (Button.MIDDLE, 2), (Button.RIGHT, 3)):
        x11_backend.button_down(button)
        press = observer.wait_for(lambda e: e.type == X.ButtonPress)
        x11_backend.button_up(button)
        release = observer.wait_for(lambda e: e.type == X.ButtonRelease)
        assert (press.detail, release.detail) == (code, code)
        assert press.window == window
        assert (press.root_x, press.root_y) == (250, 250)
        assert (press.event_x, press.event_y) == (150, 150)


def test_scroll_clicks_buttons_4_to_7(x11_backend: Backend, observer: Observer) -> None:
    _input_window(observer)
    x11_backend.move_abs(250, 250)
    x11_backend.scroll(0, 2)
    x11_backend.scroll(0, -1)
    x11_backend.scroll(3, 0)
    x11_backend.scroll(-1, 0)
    events = observer.events(0.3)
    presses = [e.detail for e in events if e.type == X.ButtonPress]
    releases = [e.detail for e in events if e.type == X.ButtonRelease]
    assert presses == [5, 5, 4, 7, 7, 7, 6]
    assert releases == presses


# --- keys --------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["ctrl", "shift", "alt", "super", "enter", "escape",
                                 "backspace", "tab", "space", "left", "pageup", "f5", "a",
                                 "z", "1", "/", ";"])
def test_key_down_up_sends_the_key(x11_backend: Backend, observer: Observer,
                                   key: str) -> None:
    _input_window(observer)
    x11_backend.key_down(key)
    press = observer.wait_for(lambda e: e.type == X.KeyPress)
    x11_backend.key_up(key)
    release = observer.wait_for(lambda e: e.type == X.KeyRelease)
    assert press.detail == release.detail
    row = observer.display.get_keyboard_mapping(press.detail, 1)[0]
    assert key_keysym(key) in list(row)


def test_every_contract_key_maps_or_raises_unknown_key(x11_backend: Backend,
                                                       observer: Observer) -> None:
    """F13-F24 are missing from some keymaps: the backend must say so clearly."""
    _input_window(observer)
    mapped = 0
    for key in (*MODIFIER_KEYS, *NAMED_KEYS):
        try:
            x11_backend.key_down(key)
        except UnknownKey:
            continue
        x11_backend.key_up(key)
        mapped += 1
    assert mapped >= len(MODIFIER_KEYS) + len(NAMED_KEYS) - 12


def test_shortcut_carries_the_modifier_state(x11_backend: Backend, observer: Observer) -> None:
    _input_window(observer)
    x11_backend.key_down("ctrl")
    x11_backend.key_down("s")
    x11_backend.key_up("s")
    x11_backend.key_up("ctrl")
    events = [e for e in observer.events(0.3) if e.type == X.KeyPress]
    assert len(events) == 2
    assert events[1].state & X.ControlMask
    assert observer.keysym_of(events[1]) == ord("s")


def test_unknown_key_names(x11_backend: Backend) -> None:
    for key in ("esc", "ñ", "", "ctrl+a"):
        with pytest.raises(UnknownKey):
            x11_backend.key_down(key)


# --- characters --------------------------------------------------------------------------


@pytest.mark.parametrize("char", ["a", "A", "!", "ñ", "é", "€", "日", "Ñ", " ", "😀"])
def test_char_input_types_any_character(x11_backend: Backend, observer: Observer,
                                        char: str) -> None:
    _input_window(observer)
    before = observer.keyboard_mapping()
    x11_backend.char_down(char)
    press = observer.wait_for(lambda e: e.type == X.KeyPress)
    assert not press.state & X.ShiftMask
    assert observer.keysym_of(press) == char_keysym(char)
    x11_backend.char_up(char)
    release = observer.wait_for(lambda e: e.type == X.KeyRelease)
    assert release.detail == press.detail
    assert observer.keyboard_mapping() == before


def test_overlapping_chars_use_separate_keycodes(x11_backend: Backend,
                                                 observer: Observer) -> None:
    _input_window(observer)
    before = observer.keyboard_mapping()
    x11_backend.char_down("ñ")
    first = observer.wait_for(lambda e: e.type == X.KeyPress)
    first_keysym = observer.keysym_of(first)
    x11_backend.char_down("€")
    second = observer.wait_for(lambda e: e.type == X.KeyPress)
    assert first.detail != second.detail
    assert first_keysym == 0xF1
    assert observer.keysym_of(second) == 0x10020AC
    x11_backend.char_up("ñ")
    assert observer.keysym_of(second) == 0x10020AC  # still mapped while held
    x11_backend.char_up("€")
    assert observer.keyboard_mapping() == before


def test_repeated_char_is_reference_counted(x11_backend: Backend, observer: Observer) -> None:
    _input_window(observer)
    before = observer.keyboard_mapping()
    x11_backend.char_down("é")
    x11_backend.char_down("é")  # the key is released and pressed again: two characters
    x11_backend.char_up("é")
    events = [e for e in observer.events(0.2) if e.type in (X.KeyPress, X.KeyRelease)]
    assert [e.type for e in events] == [X.KeyPress, X.KeyRelease, X.KeyPress]
    assert len({e.detail for e in events}) == 1
    assert observer.keysym_of(events[0]) == 0xE9  # one press still holds the remap
    x11_backend.char_up("é")
    final = observer.wait_for(lambda e: e.type == X.KeyRelease)
    assert final.detail == events[0].detail
    assert observer.keyboard_mapping() == before


def test_remaps_are_restored_after_the_grace_period(observer: Observer) -> None:
    backend = X11Backend(None, remap_grace_seconds=0.05)
    try:
        before = observer.keyboard_mapping()
        backend.char_down("ß")
        backend.char_up("ß")
        assert observer.keyboard_mapping() != before  # kept briefly for slow clients
        assert wait_until(lambda: observer.keyboard_mapping() == before)
    finally:
        backend.close()


def test_close_restores_the_mapping_and_releases_held_chars(observer: Observer) -> None:
    _input_window(observer)
    before = observer.keyboard_mapping()
    backend = X11Backend(None, remap_grace_seconds=60)
    backend.char_down("¿")
    backend.char_down("ü")
    backend.char_up("ü")  # restore pending for a minute
    assert observer.keyboard_mapping() != before
    backend.close()
    assert observer.keyboard_mapping() == before
    events = observer.events(0.2)
    pressed = [e.detail for e in events if e.type == X.KeyPress]
    released = [e.detail for e in events if e.type == X.KeyRelease]
    assert sorted(pressed) == sorted(released)
    backend.close()  # idempotent


def test_untypeable_char(x11_backend: Backend) -> None:
    with pytest.raises(UnknownKey):
        x11_backend.char_down("ab")
    with pytest.raises(UnknownKey):
        x11_backend.char_down("\x00")


def test_closed_backend_refuses_calls() -> None:
    backend = X11Backend(None)
    backend.close()
    with pytest.raises(RuntimeError):
        backend.move_abs(1, 1)


# --- windows -----------------------------------------------------------------------------


def test_find_window_without_a_window_manager(x11_backend: Backend,
                                              observer: Observer) -> None:
    observer.create_window(120, 90, 320, 240, wm_name="Untitled - j5 notes")
    observer.create_window(500, 400, 200, 100, net_wm_name="Café ☕ — j5 utf8")
    observer.create_window(10, 10, 50, 50, wm_name="j5 hidden window", mapped=False)
    assert x11_backend.find_window("j5 notes") == Rect(120, 90, 320, 240)
    assert x11_backend.find_window("☕ — j5") == Rect(500, 400, 200, 100)
    assert x11_backend.find_window("j5 hidden") is None
    assert x11_backend.find_window("no such window j5") is None


def test_list_windows_without_a_window_manager(x11_backend: Backend,
                                               observer: Observer) -> None:
    observer.create_window(120, 90, 320, 240, wm_name="Untitled - j5 list")
    observer.create_window(500, 400, 200, 100, net_wm_name="Café ☕ — j5 list")
    observer.create_window(10, 10, 50, 50, wm_name="j5 list hidden", mapped=False)
    listed = [w for w in x11_backend.list_windows() if "j5 list" in w.title]
    # Topmost first: the last window created is on top.
    assert listed == [WindowInfo("Café ☕ — j5 list", Rect(500, 400, 200, 100)),
                      WindowInfo("Untitled - j5 list", Rect(120, 90, 320, 240))]
    assert all(window.title for window in x11_backend.list_windows())


def test_find_window_prefers_net_wm_name(x11_backend: Backend, observer: Observer) -> None:
    observer.create_window(30, 40, 100, 80, wm_name="legacy j5 title",
                           net_wm_name="modern j5 title")
    assert x11_backend.find_window("modern j5") == Rect(30, 40, 100, 80)


def test_find_window_returns_the_client_area_inside_a_frame(x11_backend: Backend,
                                                            observer: Observer) -> None:
    """Without EWMH: a reparenting window manager's frame holding a client with WM_STATE."""
    frame = observer.create_window(200, 150, 410, 330)
    observer.create_window(5, 25, 400, 300, parent=frame, wm_name="j5 framed client",
                           wm_state=True)
    assert x11_backend.find_window("framed client") == Rect(205, 175, 400, 300)


def test_find_window_with_ewmh_client_list(x11_backend: Backend, observer: Observer) -> None:
    frame = observer.create_window(600, 300, 310, 230)
    client = observer.create_window(5, 25, 300, 200, parent=frame, wm_name="j5 ewmh client")
    other = observer.create_window(0, 0, 50, 50, wm_name="j5 ewmh not listed")
    display = observer.display
    atom = display.get_atom("_NET_CLIENT_LIST")
    observer.root.change_property(atom, display.get_atom("WINDOW"), 32, [client.id])
    display.sync()
    try:
        assert x11_backend.find_window("ewmh client") == Rect(605, 325, 300, 200)
        # A window manager's list is authoritative: unlisted windows are not clients.
        assert x11_backend.find_window("ewmh not listed") is None
        assert other is not None
    finally:
        observer.root.delete_property(atom)
        display.sync()


def test_find_window_ewmh_stacking_order_prefers_topmost(x11_backend: Backend,
                                                         observer: Observer) -> None:
    lower = observer.create_window(10, 10, 100, 100, wm_name="j5 twin")
    upper = observer.create_window(300, 300, 100, 100, wm_name="j5 twin")
    display = observer.display
    atom = display.get_atom("_NET_CLIENT_LIST_STACKING")
    observer.root.change_property(atom, display.get_atom("WINDOW"), 32, [lower.id, upper.id])
    display.sync()
    try:
        assert x11_backend.find_window("j5 twin") == Rect(300, 300, 100, 100)
    finally:
        observer.root.delete_property(atom)
        display.sync()


# --- hotkeys -----------------------------------------------------------------------------

STOP_KEY = ["ctrl", "alt", "shift", "q"]


def _press_combo(backend: Backend, keys: list[str]) -> None:
    for key in keys:
        backend.key_down(key)
    for key in reversed(keys):
        backend.key_up(key)


def test_watch_hotkey_fires_and_stops(x11_backend: Backend) -> None:
    fired = threading.Event()
    count = [0]

    def callback() -> None:
        count[0] += 1
        fired.set()

    unwatch = x11_backend.watch_hotkey(STOP_KEY, callback)
    try:
        _press_combo(x11_backend, ["ctrl", "q"])  # not the combination
        _press_combo(x11_backend, STOP_KEY)
        assert fired.wait(TIMEOUT_S)
        assert wait_until(lambda: count[0] == 1)
    finally:
        unwatch()
    unwatch()  # idempotent
    _press_combo(x11_backend, STOP_KEY)
    assert not wait_until(lambda: count[0] > 1, timeout=0.3)


def test_watch_hotkey_works_with_numlock_and_capslock_on(x11_backend: Backend,
                                                         observer: Observer) -> None:
    num_lock, caps_lock = 0xFF7F, 0xFFE5
    fired = threading.Event()
    unwatch = x11_backend.watch_hotkey(STOP_KEY, fired.set)
    observer.tap_keysym(num_lock)
    try:
        assert wait_until(lambda: observer.modifier_state() & X.Mod2Mask != 0)
        _press_combo(x11_backend, STOP_KEY)
        assert fired.wait(TIMEOUT_S)
        fired.clear()
        observer.tap_keysym(caps_lock)
        assert wait_until(lambda: observer.modifier_state() & X.LockMask != 0)
        _press_combo(x11_backend, STOP_KEY)
        assert fired.wait(TIMEOUT_S)
    finally:
        if observer.modifier_state() & X.LockMask:
            observer.tap_keysym(caps_lock)
        if observer.modifier_state() & X.Mod2Mask:
            observer.tap_keysym(num_lock)
        unwatch()
    assert wait_until(lambda: observer.modifier_state() & (X.Mod2Mask | X.LockMask) == 0)


def test_watch_hotkey_reports_a_combination_taken_by_another_client(
        x11_backend: Backend, observer: Observer) -> None:
    keycode = observer.display.keysym_to_keycode(ord("j"))
    mods = X.ControlMask | X.Mod1Mask
    observer.root.grab_key(keycode, mods, False, X.GrabModeAsync, X.GrabModeAsync)
    observer.display.sync()
    try:
        with pytest.raises(HotkeyConflict, match="ctrl\\+alt\\+j"):
            x11_backend.watch_hotkey(["ctrl", "alt", "j"], lambda: None)
    finally:
        observer.root.ungrab_key(keycode, mods)
        observer.display.sync()
    # Once released, the combination can be watched.
    x11_backend.watch_hotkey(["ctrl", "alt", "j"], lambda: None)()


def test_watch_hotkey_rejects_bad_combinations(x11_backend: Backend) -> None:
    with pytest.raises(UnknownKey):
        x11_backend.watch_hotkey(["ctrl", "a", "b"], lambda: None)
    with pytest.raises(UnknownKey):
        x11_backend.watch_hotkey(["ctrl", "nosuchkey"], lambda: None)
    with pytest.raises(ValueError):
        x11_backend.watch_hotkey([], lambda: None)


def test_close_stops_hotkey_watchers() -> None:
    backend = X11Backend(None)
    fired = threading.Event()
    backend.watch_hotkey(["ctrl", "alt", "shift", "w"], fired.set)
    backend.close()
    other = X11Backend(None)
    try:
        # The grab is gone: watching the same combination again succeeds.
        other.watch_hotkey(["ctrl", "alt", "shift", "w"], lambda: None)()
    finally:
        other.close()


# --- threads -----------------------------------------------------------------------------


def test_calls_from_several_threads(x11_backend: Backend) -> None:
    errors: list[BaseException] = []

    def worker(offset: int) -> None:
        try:
            for i in range(50):
                x11_backend.move_abs(100 + offset * 10 + i, 100 + i)
                position = x11_backend.pointer_position()
                assert position is not None
                x11_backend.move_rel(1, 1)
                x11_backend.screen_bounds()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(TIMEOUT_S * 3)
    assert not any(t.is_alive() for t in threads)
    assert errors == []
    x11_backend.move_abs(640, 400)
    assert x11_backend.pointer_position() == (640, 400)
