"""Finding top-level windows by title on X11.

With a window manager that follows EWMH, ``_NET_CLIENT_LIST_STACKING`` (or
``_NET_CLIENT_LIST``) lists the application windows. Without one (bare Xvfb) or with an
older window manager, the root's children are walked: a child with ``WM_STATE`` is a
client window, otherwise it is a frame whose client is searched among its descendants.

The rectangle returned is the client area (inside the decorations) in root coordinates:
what scripts click into is the application's content, which keeps window-relative
coordinates stable when the theme or the window manager changes.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from Xlib import X, Xatom
from Xlib import error as xerror
from Xlib.display import Display
from Xlib.xobject.drawable import Window

from j5_macro.backends.base import Rect

_MAX_DEPTH = 4


def find_window(display: Display, title: str) -> Rect | None:
    """The client area of the first visible top-level window whose title contains
    ``title`` (topmost first when the window manager reports stacking order)."""
    root = display.screen().root
    for window in _client_windows(display, root):
        try:
            if window.get_attributes().map_state != X.IsViewable:
                continue
            name = window_title(display, window)
            if name is None or title not in name:
                continue
            geometry = window.get_geometry()
            origin = root.translate_coords(window, 0, 0)
            return Rect(int(origin.x), int(origin.y), int(geometry.width),
                        int(geometry.height))
        except (xerror.BadWindow, xerror.BadDrawable, xerror.BadMatch):
            continue  # the window went away while we looked at it
    return None


def window_title(display: Display, window: Window) -> str | None:
    """``_NET_WM_NAME`` (UTF-8) or else ``WM_NAME``."""
    net_wm_name = display.get_atom("_NET_WM_NAME")
    text = _text_property(window, net_wm_name)
    if text:
        return text
    return _text_property(window, Xatom.WM_NAME)


def _text_property(window: Window, atom: int) -> str | None:
    prop = window.get_full_property(atom, X.AnyPropertyType)
    if prop is None or prop.format != 8:
        return None
    value: Any = prop.value
    raw = value.encode("latin-1") if isinstance(value, str) else bytes(value)
    raw = raw.rstrip(b"\0")
    display = window.display
    utf8 = display.get_atom("UTF8_STRING")
    if prop.property_type == utf8:
        return raw.decode("utf-8", errors="replace")
    if prop.property_type == Xatom.STRING:
        return raw.decode("latin-1")
    # COMPOUND_TEXT and others: ASCII-compatible; UTF-8 is the best plain decoding.
    return raw.decode("utf-8", errors="replace")


def _client_windows(display: Display, root: Window) -> Iterator[Window]:
    for list_name in ("_NET_CLIENT_LIST_STACKING", "_NET_CLIENT_LIST"):
        ids = _window_list(display, root, list_name)
        if ids:
            if list_name == "_NET_CLIENT_LIST_STACKING":
                ids = list(reversed(ids))  # topmost first
            for wid in ids:
                yield display.create_resource_object("window", wid)
            return
    wm_state = display.get_atom("WM_STATE")
    try:
        children = root.query_tree().children
    except xerror.XError:
        return
    for child in reversed(children):  # topmost first
        client = _find_client(child, wm_state, 0)
        yield client if client is not None else child


def _window_list(display: Display, root: Window, name: str) -> list[int]:
    atom = display.get_atom(name, only_if_exists=True)
    if atom == X.NONE:
        return []
    prop = root.get_full_property(atom, X.AnyPropertyType)
    if prop is None or prop.format != 32:
        return []
    return [int(wid) for wid in prop.value]


def _find_client(window: Window, wm_state: int, depth: int) -> Window | None:
    """The window carrying ``WM_STATE`` at or under ``window`` (a frame), if any."""
    try:
        if window.get_full_property(wm_state, X.AnyPropertyType) is not None:
            return window
        if depth >= _MAX_DEPTH:
            return None
        for child in window.query_tree().children:
            found = _find_client(child, wm_state, depth + 1)
            if found is not None:
                return found
    except (xerror.BadWindow, xerror.BadDrawable):
        return None
    return None
