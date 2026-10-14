"""The X11 display scale from the resource database and the environment, without a
display (docs/contract.md, "Display scale")."""

from __future__ import annotations

import pytest

from j5_macro.backends.x11.scale import display_scale_from, xft_dpi

RESOURCES = "Xcursor.size:\t24\nXft.antialias:\t1\nXft.dpi:\t144\nXft.hinting:\t1\n"


@pytest.mark.parametrize(("resources", "dpi"), [
    (RESOURCES, 144.0),
    ("Xft.dpi: 120", 120.0),
    ("Xft.dpi:\t96\nXft.dpi:\t192\n", 192.0),  # the last entry wins, as in xrdb
    ("  Xft.dpi :  110.5  ", 110.5),
    ("Xft.dpi:\t0\n", None),
    ("Xft.dpi:\t-96\n", None),
    ("Xft.dpi:\tlarge\n", None),
    ("Xft.dpi:\tnan\n", None),
    ("XXft.dpi:\t144\n", None),
    ("Xcursor.size:\t24\n", None),
    ("", None),
    (None, None),
])
def test_xft_dpi(resources: str | None, dpi: float | None) -> None:
    assert xft_dpi(resources) == dpi


def test_xft_dpi_wins_over_gdk_scale() -> None:
    assert display_scale_from(RESOURCES, {"GDK_SCALE": "2"}) == 1.5
    assert display_scale_from("Xft.dpi: 120", {}) == 1.25
    assert display_scale_from("Xft.dpi: 100", {}) == 1.0417


@pytest.mark.parametrize(("env", "scale"), [
    ({"GDK_SCALE": "2"}, 2.0),
    ({"GDK_SCALE": " 1.5 "}, 1.5),
    ({"GDK_SCALE": "0"}, 1.0),
    ({"GDK_SCALE": "two"}, 1.0),
    ({"GDK_SCALE": ""}, 1.0),
    ({}, 1.0),
])
def test_without_xft_dpi(env: dict[str, str], scale: float) -> None:
    assert display_scale_from("Xcursor.size:\t24\n", env) == scale
    assert display_scale_from(None, env) == scale
