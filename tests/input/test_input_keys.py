import pytest

from j5_macro.input.keys import Combo, normalize_key, parse_combo


@pytest.mark.parametrize(("combo", "expected"), [
    ("ctrl+shift+s", Combo(("ctrl", "shift"), "s")),
    ("alt+f4", Combo(("alt",), "f4")),
    ("Ctrl + S", Combo(("ctrl",), "s")),
    ("CONTROL+Esc", Combo(("ctrl",), "escape")),
    ("ctl+return", Combo(("ctrl",), "enter")),
    ("cmd+q", Combo(("super",), "q")),
    ("win+e", Combo(("super",), "e")),
    ("meta+option+del", Combo(("super", "alt"), "delete")),
    ("shift+pgup", Combo(("shift",), "pageup")),
    ("pgdn", Combo((), "pagedown")),
    ("ins", Combo((), "insert")),
    ("arrowup", Combo((), "up")),
    ("shift+ArrowLeft", Combo(("shift",), "left")),
    ("ctrl+plus", Combo(("ctrl",), "+")),
    ("ctrl+minus", Combo(("ctrl",), "-")),
    ("ctrl+/", Combo(("ctrl",), "/")),
    ("shift", Combo((), "shift")),
    ("ctrl+shift", Combo(("ctrl",), "shift")),
    ("F12", Combo((), "f12")),
    ("space", Combo((), "space")),
])
def test_parse_combo(combo, expected):
    assert parse_combo(combo) == expected


def test_combo_keys_in_press_order():
    assert parse_combo("ctrl+alt+delete").keys == ("ctrl", "alt", "delete")


@pytest.mark.parametrize(("combo", "message"), [
    ("", "empty key combination"),
    ("ctrl++", "write the + key as 'plus'"),
    ("+", "write the + key as 'plus'"),
    ("ctrl+", "empty part"),
    ("a+b", "'a' is not a modifier"),
    ("ctrl+s+shift", "'s' is not a modifier"),
    ("ctrl+ctrl+s", "a key is repeated"),
    ("ctrl+hyper", "unknown key 'hyper'"),
    ("ctrl+ñ", "only ASCII characters name physical keys"),
    ("f25", "unknown key 'f25'"),
])
def test_parse_combo_errors(combo, message):
    with pytest.raises(ValueError, match=message.replace("+", r"\+")):
        parse_combo(combo)


def test_normalize_key():
    assert normalize_key("Esc") == "escape"
    assert normalize_key("A") == "a"
    assert normalize_key("printscreen") == "printscreen"
    with pytest.raises(ValueError):
        normalize_key(" ")
