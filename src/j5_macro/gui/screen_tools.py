"""Screen tools for the editor: capture a template, pick a point, test a search.

Thin wrappers over the engine's authoring commands (docs/contract.md, "Authoring
tools"), so image and coordinate fields can offer "Capture", "Pick" and "Test" buttons.
The interactive ones open the engine's full-screen overlay and answer when the user
finishes (Escape answers ``invalid_state`` with ``details.reason: cancelled``), so they
are sent without a timeout. Results and errors arrive on the GUI thread, like every
:meth:`EngineBridge.call`.

The widgets that use this service are in :mod:`j5_macro.gui.views.screen_dialogs`
(``ImageFieldTools``, ``PointFieldTools``, the capture and test dialogs) and
:mod:`j5_macro.gui.views.template_preview`.

**Hiding the windows.** The overlay is a still image of the desktop taken when the
command arrives, and a search looks at the screen as it is: the application's own
windows must be out of the way first. A :class:`WindowHider` (the main window installs
one, :class:`~j5_macro.gui.views.screen_dialogs.TopLevelHider`) hides them; every
interactive command, and any work wrapped in :meth:`ScreenTools.while_hidden`, is sent
once they are hidden and shows them again when it is answered.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from PySide6.QtCore import QObject, Signal

from j5_macro.gui.bridge import EngineBridge, ErrorCallback, ResultCallback


class WindowHider(Protocol):
    def hide(self, then: Callable[[], None]) -> None:
        """Hide the application's windows, then call ``then`` (later, once they are
        gone from the screen)."""

    def restore(self) -> None:
        """Show again what :meth:`hide` hid."""


class ScreenTools(QObject):
    overlay_changed = Signal(bool)
    """An interactive command opened (True) or closed (False) the overlay."""

    def __init__(self, bridge: EngineBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._overlays = 0
        self.hider: WindowHider | None = None
        """Hides the application's windows during interactive commands; None leaves
        them as they are (tests, embedding)."""

    @property
    def overlay_open(self) -> bool:
        return self._overlays > 0

    def while_hidden(self, work: Callable[[Callable[[], None]], None]) -> None:
        """Run ``work(done)`` with the application's windows hidden (see
        :attr:`hider`); ``work`` calls ``done()`` once when it has finished, which shows
        them again. Without a hider, ``work`` runs right away."""
        hider = self.hider
        if hider is None:
            work(lambda: None)
            return
        finished = False

        def done() -> None:
            nonlocal finished
            if not finished:
                finished = True
                hider.restore()

        hider.hide(lambda: work(done))

    def get_image(self, script_id: str, name: str, *, on_result: ResultCallback,
                  on_error: ErrorCallback | None = None) -> None:
        """``get_image``: an embedded image of a loaded script, with its data
        (base64 PNG) and size."""
        self._bridge.call("get_image", {"script_id": script_id, "name": name},
                          on_result=on_result, on_error=on_error)

    def list_windows(self, *, on_result: ResultCallback,
                     on_error: ErrorCallback | None = None) -> None:
        """``windows``: ``[{title, bbox}]``, topmost first."""
        self._bridge.call("list_windows", on_result=on_result, on_error=on_error)

    def screenshot(self, *, on_result: ResultCallback, on_error: ErrorCallback | None = None,
                   path: str | None = None, window: str | None = None,
                   region: Sequence[int] | None = None) -> None:
        """A PNG of the desktop, a window's client area or a region; ``path`` in the
        result."""
        self._bridge.call("screenshot", _payload(path=path, window=_window(window),
                                                 region=_list(region)),
                          on_result=on_result, on_error=on_error)

    def capture_region(self, *, on_result: ResultCallback,
                       on_error: ErrorCallback | None = None, path: str | None = None,
                       script_id: str | None = None, name: str | None = None,
                       bbox: Sequence[int] | None = None, window: str | None = None,
                       padding: int | None = None, transparent: bool | None = None,
                       overwrite: bool | None = None) -> None:
        """Save a template to ``path`` or embed it in ``script_id`` as ``name``;
        interactive (the user drags a rectangle) without ``bbox``."""
        payload = _payload(path=path, script_id=script_id, name=name, bbox=_list(bbox),
                           window=_window(window), padding=padding, transparent=transparent,
                           overwrite=overwrite)
        self._interactive("capture_region", payload, bbox is None, on_result, on_error)

    def pick_point(self, *, on_result: ResultCallback, on_error: ErrorCallback | None = None,
                   window: str | None = None) -> None:
        """The user clicks a point: ``x``, ``y`` and, inside a window, ``window`` and
        ``window_point``."""
        self._interactive("pick_point", _payload(window=_window(window)), True,
                          on_result, on_error)

    def find_image(self, image: str, *, on_result: ResultCallback,
                   on_error: ErrorCallback | None = None, script_id: str | None = None,
                   threshold: float | None = None, scale: float | None = None,
                   region: Sequence[int] | None = None, window: str | None = None,
                   show: bool = False, show_ms: int | None = None) -> None:
        """One search as a step makes it: ``found``, ``score``, ``bbox``... With
        ``show`` the overlay highlights the match until ``show_ms`` passes."""
        payload = _payload(image=image, script_id=script_id, threshold=threshold,
                           scale=scale, region=_list(region), window=_window(window),
                           show=show or None, show_ms=show_ms)
        self._interactive("find_image", payload, show, on_result, on_error)

    def _interactive(self, command: str, payload: dict[str, Any], opens_overlay: bool,
                     on_result: ResultCallback, on_error: ErrorCallback | None) -> None:
        if not opens_overlay:
            self._bridge.call(command, payload, on_result=on_result, on_error=on_error)
            return
        self._overlay(+1)

        def send(restore: Callable[[], None]) -> None:
            def done(result: dict[str, Any]) -> None:
                self._overlay(-1)
                restore()
                on_result(result)

            def failed(exc: BaseException) -> None:
                self._overlay(-1)
                restore()
                if on_error is not None:
                    on_error(exc)

            self._bridge.call(command, payload, on_result=done, on_error=failed,
                              timeout=None)

        self.while_hidden(send)

    def _overlay(self, delta: int) -> None:
        before = self.overlay_open
        self._overlays += delta
        if self.overlay_open != before:
            self.overlay_changed.emit(self.overlay_open)


def _payload(**fields: Any) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}


def _window(title: str | None) -> dict[str, str] | None:
    return {"title": title} if title else None


def _list(values: Sequence[int] | None) -> list[int] | None:
    return list(values) if values is not None else None
