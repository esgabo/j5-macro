"""The authoring overlay: a full-screen still of the virtual desktop on which the user
selects a rectangle, picks a point, or sees where an image matched (docs/contract.md,
"Authoring tools": the overlay).

The engine draws it itself with tkinter (standard library), on the thread that calls
:class:`TkOverlay`'s methods: Tk is created, used and destroyed on that one thread. The
screen is captured before the overlay opens and shown as it was, so what the user
selects is exactly what gets saved, and the overlay works the same with or without a
compositor. Coordinates are physical pixels of the virtual desktop: on Windows the
engine is per-monitor DPI aware before any window exists, so Tk never scales them.

Importing this module never imports tkinter; :func:`tk_overlay` does, and raises
``ImportError`` when Python was built without it.
"""

from __future__ import annotations

import base64
import contextlib
import importlib
import threading
from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol

from j5_macro.backends.base import Rect
from j5_macro.vision.api import Frame

ZOOM = 8
"""Magnification of the magnifier."""
MAGNIFIER_RADIUS = 7
"""Pixels shown on each side of the pointer: the magnifier shows 15 x 15 pixels."""
POLL_MS = 50
"""How often the overlay checks whether it was cancelled from another thread."""
SELECT_COLOR = "#ff00ff"
FOUND_COLOR = "#00d000"
NOT_FOUND_COLOR = "#ff2020"
OTHER_MATCH_COLOR = "#ffd000"
"""The outline of the other matches of ``find_image`` with ``all``."""

Mode = Literal["region", "point", "show"]


class Overlay(Protocol):
    """What the engine's authoring commands use; every method blocks until the user
    is done, and returns None when the overlay was cancelled."""

    def select_region(self, frame: Frame, hint: str = "") -> Rect | None:
        """The rectangle the user drags (desktop coordinates, inside the frame)."""
        ...

    def pick_point(self, frame: Frame, hint: str = "") -> tuple[int, int] | None:
        """The point the user clicks (desktop coordinates)."""
        ...

    def show_match(self, frame: Frame, bbox: Rect | None, found: bool, label: str,
                   duration_ms: int, *, others: Sequence[Rect] = ()) -> None:
        """Highlight ``bbox`` (green when found, red otherwise) with ``label``, and
        ``others`` (the other matches) in yellow, until ``duration_ms`` pass or the
        user presses a key or clicks."""
        ...

    def cancel(self) -> None:
        """From any thread: close the open overlay, if any (its method returns None)."""
        ...


OverlayFactory = Callable[[], Overlay]
"""Builds an overlay; raises ``ImportError`` when it cannot exist (no tkinter)."""


def tk_overlay() -> Overlay:
    """The tkinter overlay (the default :data:`OverlayFactory`)."""
    try:
        importlib.import_module("tkinter")
    except ImportError as exc:
        raise ImportError(f"the overlay needs tkinter, which this Python lacks ({exc}); "
                          "install it (e.g. the python3-tk package on Debian and "
                          "Ubuntu)") from exc
    return TkOverlay()


def magnified(frame: Frame, x: int, y: int, *, radius: int = MAGNIFIER_RADIUS,
              zoom: int = ZOOM) -> Any:
    """The pixels around desktop point (x, y), enlarged ``zoom`` times with the centre
    pixel outlined (BGR; outside the frame is black)."""
    import cv2
    import numpy as np

    size = 2 * radius + 1
    patch = np.zeros((size, size, 3), dtype=np.uint8)
    left, top = x - radius - frame.origin[0], y - radius - frame.origin[1]
    height, width = frame.pixels.shape[:2]
    src_top, src_left = max(top, 0), max(left, 0)
    src_bottom, src_right = min(top + size, height), min(left + size, width)
    if src_bottom > src_top and src_right > src_left:
        patch[src_top - top:src_bottom - top, src_left - left:src_right - left] = \
            frame.pixels[src_top:src_bottom, src_left:src_right, :3]
    big = cv2.resize(patch, (size * zoom, size * zoom), interpolation=cv2.INTER_NEAREST)
    corner = radius * zoom
    cv2.rectangle(big, (corner - 1, corner - 1), (corner + zoom, corner + zoom),
                  (255, 0, 255), 1)
    cv2.rectangle(big, (0, 0), (size * zoom - 1, size * zoom - 1), (40, 40, 40), 1)
    return big


def png_base64(pixels: Any) -> str:
    """BGR pixels as base64 PNG data, which Tk's ``PhotoImage(data=...)`` reads."""
    from j5_macro.vision.snapshots import encode_png

    return base64.b64encode(encode_png(pixels)).decode("ascii")


class TkOverlay:
    """:class:`Overlay` drawn with tkinter. One overlay at a time per instance."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def select_region(self, frame: Frame, hint: str = "") -> Rect | None:
        result = self._session(frame, "region", hint or
                               "Drag a rectangle with the left button. Escape cancels.")
        return result if isinstance(result, Rect) else None

    def pick_point(self, frame: Frame, hint: str = "") -> tuple[int, int] | None:
        result = self._session(frame, "point", hint or
                               "Click a point with the left button. Escape cancels.")
        return result if isinstance(result, tuple) else None

    def show_match(self, frame: Frame, bbox: Rect | None, found: bool, label: str,
                   duration_ms: int, *, others: Sequence[Rect] = ()) -> None:
        self._session(frame, "show", "", bbox=bbox, found=found, label=label,
                      duration_ms=duration_ms, others=others)

    def cancel(self) -> None:
        self._cancelled.set()

    # --- internals -------------------------------------------------------------------

    def _session(self, frame: Frame, mode: Mode, hint: str, *, bbox: Rect | None = None,
                 found: bool = False, label: str = "", duration_ms: int = 0,
                 others: Sequence[Rect] = ()) -> Rect | tuple[int, int] | None:
        import tkinter as tk

        self._cancelled.clear()
        session = _Session(tk, frame, mode, hint, self._cancelled)
        try:
            if mode == "show":
                session.highlight(bbox, found, label, duration_ms, others)
            return session.run()
        finally:
            session.close()


class _Session:
    """One overlay window, from opening to closing."""

    def __init__(self, tk: Any, frame: Frame, mode: Mode, hint: str,
                 cancelled: threading.Event) -> None:
        self.tk = tk
        self.frame = frame
        self.mode = mode
        self.cancelled = cancelled
        self.result: Rect | tuple[int, int] | None = None
        self.anchor: tuple[int, int] | None = None
        self.selection: Any = None
        self.size_label: Any = None
        rect = frame.rect
        self.root = root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.geometry(f"{rect.width}x{rect.height}+{rect.x}+{rect.y}")
        root.configure(background="black")
        root.attributes("-topmost", True)
        self.canvas = canvas = tk.Canvas(root, width=rect.width, height=rect.height,
                                         highlightthickness=0, borderwidth=0,
                                         cursor="crosshair", background="black")
        canvas.pack()
        self.screen = tk.PhotoImage(master=root, data=png_base64(frame.pixels[:, :, :3]))
        canvas.create_image(0, 0, image=self.screen, anchor="nw")
        self.magnifier_image: Any = None
        self.magnifier = canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.magnifier_text = canvas.create_text(0, 0, anchor="nw", fill="white",
                                                 state="hidden", font=("TkFixedFont", 10))
        if hint:
            # On the primary monitor, whose top-left corner is the desktop's (0, 0).
            hx, hy = self.canvas_point(16, 16)
            self.hint_box = canvas.create_rectangle(hx - 6, hy - 4, hx, hy, fill="#202020",
                                                    outline="#808080")
            self.hint = canvas.create_text(hx, hy, anchor="nw", text=hint, fill="white",
                                           font=("TkDefaultFont", 12))
            x1, y1, x2, y2 = canvas.bbox(self.hint)
            canvas.coords(self.hint_box, x1 - 8, y1 - 6, x2 + 8, y2 + 6)
        root.bind("<Escape>", lambda _e: self.finish(None))
        root.bind("<ButtonPress-3>", lambda _e: self.finish(None))
        if mode == "show":
            root.bind("<KeyPress>", lambda _e: self.finish(None))
            root.bind("<ButtonPress-1>", lambda _e: self.finish(None))
        else:
            root.bind("<Motion>", self.on_motion)
            root.bind("<ButtonPress-1>", self.on_press)
            root.bind("<B1-Motion>", self.on_drag)
            root.bind("<ButtonRelease-1>", self.on_release)
        root.deiconify()
        root.lift()
        root.after(POLL_MS, self.poll)
        root.after(1, self.take_focus)

    # --- coordinates -----------------------------------------------------------------

    def canvas_point(self, x: int, y: int) -> tuple[int, int]:
        return x - self.frame.origin[0], y - self.frame.origin[1]

    def desktop_point(self, event: Any) -> tuple[int, int]:
        rect = self.frame.rect
        x = min(max(int(event.x), 0), rect.width - 1)
        y = min(max(int(event.y), 0), rect.height - 1)
        return x + rect.x, y + rect.y

    # --- lifecycle -------------------------------------------------------------------

    def run(self) -> Rect | tuple[int, int] | None:
        self.root.mainloop()
        return self.result

    def finish(self, result: Rect | tuple[int, int] | None) -> None:
        self.result = result
        self.root.quit()

    def close(self) -> None:
        with contextlib.suppress(self.tk.TclError):
            self.root.destroy()

    def poll(self) -> None:
        if self.cancelled.is_set():
            self.finish(None)
            return
        self.root.after(POLL_MS, self.poll)

    def take_focus(self, attempts: int = 20) -> None:
        """Keyboard focus and a global grab, so Escape and clicks reach the overlay
        whatever had the focus; retried while another program holds a grab."""
        try:
            self.root.focus_force()
            self.root.grab_set_global()
        except self.tk.TclError:
            if attempts > 0:
                self.root.after(100, lambda: self.take_focus(attempts - 1))

    # --- events ----------------------------------------------------------------------

    def on_motion(self, event: Any) -> None:
        self.show_magnifier(*self.desktop_point(event))

    def on_press(self, event: Any) -> None:
        point = self.desktop_point(event)
        if self.mode == "point":
            self.finish(point)
            return
        self.anchor = point
        self.draw_selection(point)

    def on_drag(self, event: Any) -> None:
        point = self.desktop_point(event)
        self.show_magnifier(*point)
        if self.anchor is not None:
            self.draw_selection(point)

    def on_release(self, event: Any) -> None:
        if self.mode != "region" or self.anchor is None:
            return
        rect = self.selected(self.desktop_point(event))
        self.anchor = None
        if rect.width <= 1 and rect.height <= 1:
            # A click without dragging: forget it and let the user try again.
            for item in (self.selection, self.size_label):
                if item is not None:
                    self.canvas.delete(item)
            self.selection = self.size_label = None
            return
        self.finish(rect)

    # --- drawing ---------------------------------------------------------------------

    def selected(self, point: tuple[int, int]) -> Rect:
        """The rectangle from the anchor to ``point``, both pixels included."""
        assert self.anchor is not None
        (ax, ay), (bx, by) = self.anchor, point
        left, top = min(ax, bx), min(ay, by)
        return Rect(left, top, abs(bx - ax) + 1, abs(by - ay) + 1)

    def draw_selection(self, point: tuple[int, int]) -> None:
        rect = self.selected(point)
        x, y = self.canvas_point(rect.x, rect.y)
        # The outline goes around the selected pixels, not over them.
        coords = (x - 1, y - 1, x + rect.width, y + rect.height)
        if self.selection is None:
            self.selection = self.canvas.create_rectangle(*coords, outline=SELECT_COLOR)
            self.size_label = self.canvas.create_text(0, 0, anchor="sw", fill=SELECT_COLOR,
                                                      font=("TkFixedFont", 10))
        else:
            self.canvas.coords(self.selection, *coords)
        self.canvas.coords(self.size_label, x, y - 4)
        self.canvas.itemconfigure(self.size_label,
                                  text=f"{rect.x}, {rect.y}  {rect.width} x {rect.height}")

    def show_magnifier(self, x: int, y: int) -> None:
        pixels = magnified(self.frame, x, y)
        self.magnifier_image = self.tk.PhotoImage(master=self.root, data=png_base64(pixels))
        size = pixels.shape[0]
        cx, cy = self.canvas_point(x, y)
        rect = self.frame.rect
        # Below and right of the pointer, flipped near the right and bottom edges.
        mx = cx + 24 if cx + 24 + size < rect.width else cx - 24 - size
        my = cy + 24 if cy + 24 + size + 18 < rect.height else cy - 24 - size - 18
        self.canvas.itemconfigure(self.magnifier, image=self.magnifier_image,
                                  state="normal")
        self.canvas.coords(self.magnifier, mx, my)
        self.canvas.itemconfigure(self.magnifier_text, text=f"{x}, {y}", state="normal")
        self.canvas.coords(self.magnifier_text, mx, my + size + 2)
        self.canvas.tag_raise(self.magnifier)
        self.canvas.tag_raise(self.magnifier_text)

    def highlight(self, bbox: Rect | None, found: bool, label: str,
                  duration_ms: int, others: Sequence[Rect] = ()) -> None:
        colour = FOUND_COLOR if found else NOT_FOUND_COLOR
        for other in others:
            x, y = self.canvas_point(other.x, other.y)
            for inset in range(2):
                self.canvas.create_rectangle(x - 1 - inset, y - 1 - inset,
                                             x + other.width + inset,
                                             y + other.height + inset,
                                             outline=OTHER_MATCH_COLOR)
        if bbox is not None:
            x, y = self.canvas_point(bbox.x, bbox.y)
            for inset in range(3):
                self.canvas.create_rectangle(x - 1 - inset, y - 1 - inset,
                                             x + bbox.width + inset,
                                             y + bbox.height + inset, outline=colour)
            tx, ty = x, y - 6
        else:
            tx, ty = self.canvas_point(16, 64)
        text = self.canvas.create_text(tx, ty, anchor="sw", text=label, fill=colour,
                                       font=("TkDefaultFont", 12, "bold"))
        x1, y1, x2, y2 = self.canvas.bbox(text)
        box = self.canvas.create_rectangle(x1 - 4, y1 - 2, x2 + 4, y2 + 2, fill="#202020",
                                           outline=colour)
        self.canvas.tag_raise(text, box)
        self.root.after(max(1, duration_ms), lambda: self.finish(None))
