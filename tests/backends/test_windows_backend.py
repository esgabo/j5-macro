"""The Windows backend against a fake Win32 layer (any platform), its pure helpers, and
a few checks against the real system on Windows."""

from __future__ import annotations

import ctypes
import sys
import threading
import uuid

import pytest
from win32_fake import FakeWin32, FakeWindow

from j5_macro.backends.base import NAMED_KEYS, Button, Rect, UnknownKey
from j5_macro.backends.windows import HotkeyConflict, WindowsBackend
from j5_macro.backends.windows.dpi import declare_dpi_awareness
from j5_macro.backends.windows.keys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    char_units,
    named_vk,
    us_vk,
)
from j5_macro.backends.windows.win32 import (
    INPUT,
    INPUT_KEYBOARD,
    INPUT_MOUSE,
    KEYBDINPUT,
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_HWHEEL,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MIDDLEDOWN,
    MOUSEEVENTF_MOVE,
    MOUSEEVENTF_RIGHTUP,
    MOUSEEVENTF_VIRTUALDESK,
    MOUSEEVENTF_WHEEL,
    MOUSEINPUT,
    InputDenied,
    KeyEvent,
    MouseEvent,
    encode_inputs,
    normalize,
)

ABSOLUTE_MOVE = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
on_windows = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows")


def make(**kwargs: object) -> tuple[WindowsBackend, FakeWin32]:
    api = FakeWin32(**kwargs)  # type: ignore[arg-type]
    return WindowsBackend(api), api


def denormalize(n: int, origin: int, size: int) -> int:
    """How Windows maps an absolute coordinate back to a pixel."""
    return origin + n * size // 0x10000


# --- structures and pure helpers --------------------------------------------------------


def test_input_has_its_windows_layout() -> None:
    pointer = ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(MOUSEINPUT) == (32 if pointer == 8 else 24)
    assert ctypes.sizeof(KEYBDINPUT) == (24 if pointer == 8 else 16)
    assert ctypes.sizeof(INPUT) == (40 if pointer == 8 else 28)


def test_encode_inputs() -> None:
    array = encode_inputs([MouseEvent(MOUSEEVENTF_WHEEL, data=-120),
                           KeyEvent(0x41, 0x1E, KEYEVENTF_KEYUP)])
    assert array[0].type == INPUT_MOUSE
    assert array[0].u.mi.dwFlags == MOUSEEVENTF_WHEEL
    assert array[0].u.mi.mouseData == 0xFFFFFF88  # -120 as a DWORD
    assert array[1].type == INPUT_KEYBOARD
    assert (array[1].u.ki.wVk, array[1].u.ki.wScan, array[1].u.ki.dwFlags) == \
        (0x41, 0x1E, KEYEVENTF_KEYUP)


@pytest.mark.parametrize(("origin", "size"), [(0, 1920), (-1920, 3840), (0, 1366),
                                               (-1280, 4480), (0, 7680)])
def test_normalize_lands_on_every_pixel(origin: int, size: int) -> None:
    for value in range(origin, origin + size):
        n = normalize(value, origin, size)
        assert 0 <= n <= 0xFFFF
        assert denormalize(n, origin, size) == value


def test_normalize_clamps_to_the_desktop() -> None:
    assert normalize(-5000, -1920, 3840) == 0
    assert denormalize(normalize(9999, -1920, 3840), -1920, 3840) == 1919
    assert normalize(0, 0, 0) == 0


def test_named_keys_all_have_virtual_keys() -> None:
    for key in NAMED_KEYS:
        assert named_vk(key) is not None, key
    assert named_vk("f24") == 0x87
    assert named_vk("a") is None


@pytest.mark.parametrize(("char", "vk"), [("a", 0x41), ("Z", 0x5A), ("7", 0x37),
                                          ("!", 0x31), (")", 0x30), ("/", 0xBF),
                                          ("?", 0xBF), ("`", 0xC0), ("'", 0xDE),
                                          (" ", 0x20), ("+", 0xBB), ("\\", 0xDC)])
def test_us_layout_fallback(char: str, vk: int) -> None:
    assert us_vk(char) == vk


def test_every_printable_ascii_character_has_a_us_key() -> None:
    for code in range(0x20, 0x7F):
        assert us_vk(chr(code)) > 0


def test_char_units() -> None:
    assert char_units("a") == [0x61]
    assert char_units("ñ") == [0xF1]
    assert char_units("😀") == [0xD83D, 0xDE00]
    for bad in ("ab", "", "\x07", "\ud800"):
        with pytest.raises(UnknownKey):
            char_units(bad)


# --- pointer and buttons ----------------------------------------------------------------


def test_screen_and_pointer() -> None:
    backend, api = make(cursor=(-100, 50))
    assert backend.screen_bounds() == Rect(-1920, 0, 3840, 1080)
    assert backend.pointer_position() == (-100, 50)
    api.cursor = None
    assert backend.pointer_position() is None


def test_move_abs_is_an_absolute_virtual_desktop_move() -> None:
    backend, api = make()
    backend.move_abs(-1920, 1079)
    (event,) = api.sent
    assert isinstance(event, MouseEvent)
    assert event.flags == ABSOLUTE_MOVE
    assert denormalize(event.dx, -1920, 3840) == -1920
    assert denormalize(event.dy, 0, 1080) == 1079


def test_move_rel_moves_from_the_real_cursor() -> None:
    backend, api = make(cursor=(10, 20))
    backend.move_rel(-15, 5)
    (event,) = api.sent
    assert isinstance(event, MouseEvent)
    assert (denormalize(event.dx, -1920, 3840), denormalize(event.dy, 0, 1080)) == (-5, 25)
    api.cursor = None
    with pytest.raises(RuntimeError):
        backend.move_rel(1, 1)


def test_buttons_and_scroll() -> None:
    backend, api = make()
    backend.button_down(Button.LEFT)
    backend.button_up(Button.LEFT)
    backend.button_down(Button.MIDDLE)
    backend.scroll(-1, 2)
    flags = [e.flags for e in api.sent if isinstance(e, MouseEvent)]
    assert flags == [MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MIDDLEDOWN,
                     MOUSEEVENTF_WHEEL, MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL]
    wheel = [e.data for e in api.sent if isinstance(e, MouseEvent)][3:]
    assert wheel == [-120, -120, -120]  # down twice, left once
    with pytest.raises(ValueError):
        backend.button_down("thumb")  # type: ignore[arg-type]


# --- keys -------------------------------------------------------------------------------


def test_physical_keys_carry_scan_codes_and_extended_flags() -> None:
    backend, api = make()
    backend.key_down("ctrl")
    backend.key_down("left")
    backend.key_up("left")
    backend.key_up("ctrl")
    assert api.sent == [
        KeyEvent(0xA2, 0x1A2, 0),
        KeyEvent(0x25, 0x125, KEYEVENTF_EXTENDEDKEY),
        KeyEvent(0x25, 0x125, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP),
        KeyEvent(0xA2, 0x1A2, KEYEVENTF_KEYUP),
    ]


def test_ascii_keys_follow_the_current_layout() -> None:
    backend, api = make(layout={"/": 0xDF})  # e.g. a layout with '/' elsewhere
    backend.key_down("/")
    backend.key_down("a")  # not reported by the layout: the US key
    assert [e.vk for e in api.sent if isinstance(e, KeyEvent)] == [0xDF, 0x41]


def test_unknown_keys() -> None:
    backend, api = make()
    for key in ("hyper", "ñ", "ab"):
        with pytest.raises(UnknownKey):
            backend.key_down(key)
    assert api.sent == []


def test_characters_are_unicode_packets() -> None:
    backend, api = make()
    backend.char_down("😀")
    backend.char_up("😀")
    assert api.sent == [
        KeyEvent(0, 0xD83D, KEYEVENTF_UNICODE), KeyEvent(0, 0xDE00, KEYEVENTF_UNICODE),
        KeyEvent(0, 0xD83D, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
        KeyEvent(0, 0xDE00, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
    ]


def test_control_characters_press_their_keys() -> None:
    backend, api = make()
    for char in "\n\t\b":
        backend.char_down(char)
        backend.char_up(char)
    assert [(e.vk, e.flags) for e in api.sent if isinstance(e, KeyEvent)] == [
        (0x0D, 0), (0x0D, KEYEVENTF_KEYUP), (0x09, 0), (0x09, KEYEVENTF_KEYUP),
        (0x08, 0), (0x08, KEYEVENTF_KEYUP)]


def test_close_releases_everything_held() -> None:
    backend, api = make()
    backend.key_down("shift")
    backend.char_down("é")
    backend.char_down("\n")
    backend.button_down(Button.RIGHT)
    api.sent.clear()
    backend.close()
    assert api.sent == [
        KeyEvent(0, 0xE9, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
        KeyEvent(0x0D, 0x10D, KEYEVENTF_KEYUP),
        KeyEvent(0xA0, 0x1A0, KEYEVENTF_KEYUP),
        MouseEvent(MOUSEEVENTF_RIGHTUP),
    ]
    with pytest.raises(RuntimeError):
        backend.move_abs(0, 0)
    backend.close()  # idempotent


def test_refused_input_is_a_permission_error() -> None:
    backend, _ = make(deny=True)
    with pytest.raises(InputDenied) as exc:
        backend.key_down("a")
    assert isinstance(exc.value, PermissionError)


# --- windows ----------------------------------------------------------------------------


def test_find_window_returns_the_client_area_of_the_topmost_shown_match() -> None:
    backend, _ = make(windows_list=[
        FakeWindow("Notes - hidden", Rect(0, 0, 10, 10), shown=False),
        FakeWindow("Untitled - Notes", Rect(-1500, 40, 800, 600)),
        FakeWindow("Other Notes", Rect(5, 5, 50, 50)),
    ])
    assert backend.find_window("Notes") == Rect(-1500, 40, 800, 600)
    assert backend.find_window("notes") is None  # case-sensitive
    assert backend.find_window("Calculator") is None


# --- display scale ----------------------------------------------------------------------


def test_display_scale_is_per_monitor() -> None:
    left, main = Rect(-1920, 0, 1920, 1080), Rect(0, 0, 2560, 1440)
    backend, _ = make(monitors=[(main, 1.5), (left, 1.0)])
    assert backend.capabilities.display_scale
    assert backend.display_scale(100, 100) == 1.5
    assert backend.display_scale(-100, 100) == 1.0
    assert backend.display_scale(5000, 100) == 1.5  # off every monitor: the nearest
    assert backend.display_scales() == [(main, 1.5), (left, 1.0)]


def test_display_scale_unknown_without_per_monitor_dpi() -> None:
    backend, _ = make()
    assert backend.display_scale(0, 0) is None
    assert backend.display_scales() == []


# --- hotkeys ----------------------------------------------------------------------------


def test_hotkey_fires_until_unwatched() -> None:
    backend, api = make()
    pressed = threading.Event()
    unwatch = backend.watch_hotkey(["ctrl", "alt", "shift", "q"], pressed.set)
    ((modifiers, vk, _),) = api.registered.values()
    assert modifiers == MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT
    assert vk == 0x51
    api.press_hotkey(modifiers, vk)
    assert pressed.wait(5)
    unwatch()
    assert api.registered == {}
    unwatch()  # idempotent


def test_hotkey_taken_by_another_program() -> None:
    backend, _ = make(taken_hotkeys={(MOD_CONTROL | MOD_NOREPEAT, 0x7B)})
    with pytest.raises(HotkeyConflict):
        backend.watch_hotkey(["ctrl", "f12"], lambda: None)


def test_close_stops_hotkeys() -> None:
    backend, api = make()
    backend.watch_hotkey(["alt", "f9"], lambda: None)
    assert len(api.registered) == 1
    backend.close()
    assert api.registered == {}


def test_hotkey_rejects_two_plain_keys() -> None:
    backend, _ = make()
    with pytest.raises(UnknownKey):
        backend.watch_hotkey(["ctrl", "a", "b"], lambda: None)


# --- the real system --------------------------------------------------------------------


def test_dpi_awareness_is_a_no_op_elsewhere() -> None:
    if sys.platform != "win32":
        assert declare_dpi_awareness() == "not Windows"


@on_windows
def test_real_backend_reports_the_desktop() -> None:
    assert declare_dpi_awareness() in ("per-monitor v2", "per-monitor", "already set",
                                       "system")
    with WindowsBackend() as backend:
        bounds = backend.screen_bounds()
        assert bounds.width > 0 and bounds.height > 0
        assert backend.find_window(f"j5-no-such-window-{uuid.uuid4()}") is None
        monitors = backend.display_scales()
        assert monitors and all(scale > 0 for _, scale in monitors)
        origin = monitors[0][0]
        assert backend.display_scale(origin.x, origin.y) == monitors[0][1]


@on_windows
def test_real_hotkey_registers_and_unregisters() -> None:
    with WindowsBackend() as backend:
        unwatch = backend.watch_hotkey(["ctrl", "alt", "shift", "f23"], lambda: None)
        unwatch()
        # Free again: registering it a second time works.
        backend.watch_hotkey(["ctrl", "alt", "shift", "f23"], lambda: None)()
