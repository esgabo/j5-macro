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

``--scale 1.5`` draws everything 1.5 times larger, as an application that follows a
150 % display scale would (positions and sizes above are then multiplied by 1.5; the
event log keeps reporting real pixels of the client area). The smoke scripts' image
steps and relative offsets then follow it when j5 runs at the same display scale.
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


def _scale(text: str) -> float:
    value = float(text)
    if not 0.5 <= value <= 4:
        raise argparse.ArgumentTypeError("the scale must be between 0.5 and 4")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-template", metavar="PATH",
                        help="write the pattern as a PNG and exit")
    parser.add_argument("--scale", type=_scale, default=1.0,
                        help="draw everything this many times larger, like an "
                             "application on a scaled display (e.g. 1.5 for 150%%)")
    args = parser.parse_args(argv)
    if args.write_template:
        Path(args.write_template).write_bytes(pattern_png())
        return 0
    if sys.platform == "win32":
        import contextlib
        import ctypes

        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_ssize_t(-4))
    _run_window(args.scale)
    return 0


def _run_window(scale: float = 1.0) -> None:
    import tkinter as tk

    def s(value: int) -> int:
        return round(value * scale)

    def place(widget: Any, area: tuple[int, int, int, int]) -> None:
        x, y, width, height = area
        widget.place(x=s(x), y=s(y), width=s(width), height=s(height))

    root = tk.Tk()
    root.title(TITLE)
    root.geometry(f"{s(WINDOW_SIZE[0])}x{s(WINDOW_SIZE[1])}")
    root.resizable(False, False)

    size = s(PATTERN_SIZE)
    canvas = tk.Canvas(root, width=size, height=size, bd=0, highlightthickness=0)
    canvas.place(x=s(PATTERN_AT[0]), y=s(PATTERN_AT[1]))
    for row in range(GRID):
        for column in range(GRID):
            canvas.create_rectangle(s(column * CELL), s(row * CELL),
                                    s((column + 1) * CELL), s((row + 1) * CELL),
                                    fill=pattern_cell(row, column), width=0)

    # A canvas, so its caption takes no events (a label would swallow clicks on it).
    pad = tk.Canvas(root, bg="#dddddd", bd=0, highlightthickness=0)
    place(pad, PAD)
    pad.create_text(s(6), s(6), text="click, drag, scroll", anchor="nw")

    text = tk.Text(root, bd=1, highlightthickness=0, wrap="char")
    place(text, TEXT)

    log = tk.Listbox(root, bd=1, highlightthickness=0)
    place(log, LOG)

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
    record(f"ready: {TITLE}" + (f" at scale {scale:g}" if scale != 1 else ""))
    root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
