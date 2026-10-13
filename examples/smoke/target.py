"""The window the smoke scripts drive: ``python examples/smoke/target.py``.

It shows, at fixed positions of its client area (the coordinates the smoke scripts use
with ``window: {title: j5 smoke target}``):

- a 48x48 colour pattern at (20, 20), the same pixels as ``img/pattern.png``, for the
  image steps;
- a pad at (100, 20), 200x100, that records clicks, drags and the wheel;
- a text box at (20, 140), 280x120, to type into; F5 logs its whole content;
- an event log at (320, 20): every button and wheel notch the pad and the pattern
  receive and every key the text box receives, also printed to standard output.

Only the standard library is used (tkinter: the ``python3-tk`` package on Debian and
Ubuntu). ``--write-template PATH`` writes the pattern as a PNG instead of opening the
window. On Windows the window declares per-monitor DPI awareness, like the engine, so
the pattern keeps its size on a scaled display.
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

TITLE = "j5 smoke target"
CELL = 8
GRID = 6
PATTERN_SIZE = CELL * GRID
PATTERN_AT = (20, 20)
PAD = (100, 20, 200, 100)
TEXT = (20, 140, 280, 120)
LOG = (320, 20, 300, 240)
WINDOW_SIZE = (640, 280)

_PALETTE = ["#d62728", "#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd", "#17becf",
            "#ffffff", "#000000"]


def pattern_cell(row: int, column: int) -> str:
    """The colour of a cell: no symmetry, so the pattern matches in one place only."""
    return _PALETTE[(row * 5 + column * 3 + row * column) % len(_PALETTE)]


def _rgb(colour: str) -> bytes:
    return bytes.fromhex(colour[1:])


def pattern_png() -> bytes:
    """The pattern as an RGB PNG, pixel for pixel what the window draws."""
    rows = []
    for y in range(PATTERN_SIZE):
        line = b"".join(_rgb(pattern_cell(y // CELL, x // CELL))
                        for x in range(PATTERN_SIZE))
        rows.append(b"\x00" + line)  # filter type 0 per scanline

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", PATTERN_SIZE, PATTERN_SIZE, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-template", metavar="PATH",
                        help="write the pattern as a PNG and exit")
    args = parser.parse_args(argv)
    if args.write_template:
        Path(args.write_template).write_bytes(pattern_png())
        return 0
    if sys.platform == "win32":
        import contextlib
        import ctypes

        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_ssize_t(-4))
    _run_window()
    return 0


def _run_window() -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title(TITLE)
    root.geometry(f"{WINDOW_SIZE[0]}x{WINDOW_SIZE[1]}")
    root.resizable(False, False)

    canvas = tk.Canvas(root, width=PATTERN_SIZE, height=PATTERN_SIZE, bd=0,
                       highlightthickness=0)
    canvas.place(x=PATTERN_AT[0], y=PATTERN_AT[1])
    for row in range(GRID):
        for column in range(GRID):
            canvas.create_rectangle(column * CELL, row * CELL, (column + 1) * CELL,
                                    (row + 1) * CELL, fill=pattern_cell(row, column),
                                    width=0)

    # A canvas, so its caption takes no events (a label would swallow clicks on it).
    pad = tk.Canvas(root, bg="#dddddd", bd=0, highlightthickness=0)
    pad.place(x=PAD[0], y=PAD[1], width=PAD[2], height=PAD[3])
    pad.create_text(6, 6, text="click, drag, scroll", anchor="nw")

    text = tk.Text(root, bd=1, highlightthickness=0, wrap="char")
    text.place(x=TEXT[0], y=TEXT[1], width=TEXT[2], height=TEXT[3])

    log = tk.Listbox(root, bd=1, highlightthickness=0)
    log.place(x=LOG[0], y=LOG[1], width=LOG[2], height=LOG[3])

    def record(line: str) -> None:
        print(line, flush=True)
        log.insert("end", line)
        log.see("end")

    def where(event: Any) -> str:
        """The event's position in the window's client area."""
        x = event.x_root - root.winfo_rootx()
        y = event.y_root - root.winfo_rooty()
        return f"({x}, {y})"

    def on_button(kind: str) -> Any:
        def handler(event: Any) -> None:
            number = int(event.num)
            if number in (4, 5, 6, 7):  # X11 wheel
                if kind == "down":
                    # Tk 8.6 reports the horizontal buttons 6 and 7 as Shift+4 and 5.
                    horizontal = number in (6, 7) or bool(int(event.state) & 0x1)
                    if horizontal:
                        name = "left" if number in (4, 6) else "right"
                    else:
                        name = "up" if number == 4 else "down"
                    record(f"wheel {name} at {where(event)}")
                return
            button = {1: "left", 2: "middle", 3: "right"}.get(number, str(number))
            record(f"{button} {kind} at {where(event)}")
        return handler

    def on_wheel(horizontal: bool) -> Any:
        def handler(event: Any) -> None:
            notches = int(event.delta) / 120
            if horizontal:
                name = "right" if notches > 0 else "left"
            else:
                name = "up" if notches > 0 else "down"
            record(f"wheel {name} x{abs(notches):g} at {where(event)}")
        return handler

    pad.bind("<ButtonPress>", on_button("down"))
    pad.bind("<ButtonRelease>", on_button("up"))
    pad.bind("<MouseWheel>", on_wheel(False))
    pad.bind("<Shift-MouseWheel>", on_wheel(True))
    canvas.bind("<ButtonPress>", lambda e: record(f"pattern clicked at {where(e)}"))

    def on_key(event: Any) -> None:
        char = f" {event.char!r}" if event.char and event.char.isprintable() else ""
        record(f"key {event.keysym}{char}")
        if event.keysym == "F5":
            record(f"text {text.get('1.0', 'end-1c')!r}")

    # On the widget, so the keys its class handles (Tab inserts a tab) are logged too.
    text.bind("<KeyPress>", on_key)
    text.focus_set()
    record(f"ready: {TITLE}")
    root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
