"""Authoring tools: ``list_windows``, ``screenshot``, ``capture_region``, ``pick_point``
and ``find_image`` (docs/contract.md, "Authoring tools").

They need no run and send no input, but read the screen with the vision toolkit (one,
created on first use and kept until shutdown). Image searches reuse the run's vision
(:class:`~j5_macro.runtime.vision.RunVision`), so ``find_image`` searches exactly as a
step does. The interactive forms open the overlay (:mod:`j5_macro.engine.overlay`) on a
thread of their own and answer when the user is done, while the protocol thread goes on
serving (:meth:`EngineServer.answer_later`); one overlay at a time, never during a run.
"""

from __future__ import annotations

import base64
import contextlib
import os
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from j5_macro.backends.base import Backend, Rect, WindowInfo
from j5_macro.engine.backend import EngineBackend
from j5_macro.engine.dispatch import CommandError
from j5_macro.engine.editing import ScriptWriter, edit_error
from j5_macro.engine.overlay import Overlay, OverlayFactory, tk_overlay
from j5_macro.engine.runs import RunManager
from j5_macro.engine.scripts import LoadedScript, ScriptStore
from j5_macro.engine.server import EngineServer
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import Command, ErrorInfo, JsonObject
from j5_macro.protocol.payloads import (
    CaptureRegionPayload,
    CaptureRegionResult,
    CaptureWarning,
    EmptyPayload,
    FindImagePayload,
    FindImageResult,
    ListWindowsResult,
    PickPointPayload,
    PickPointResult,
    ScreenshotPayload,
    ScreenshotResult,
    WindowRef,
)
from j5_macro.protocol.payloads import WindowInfo as WindowPayload
from j5_macro.runtime.clock import MonotonicClock
from j5_macro.runtime.display import DisplayScale
from j5_macro.runtime.vision import BoundVision, RunVision, default_toolkit_factory
from j5_macro.script import images as embedded
from j5_macro.script.editor import EditError, rewrite_images
from j5_macro.steps.spec import StepError
from j5_macro.vision.api import Frame, Match, ToolkitFactory, VisionToolkit

SCREENSHOT_DIR = "j5-macro"
"""Default screenshots go in this directory of the system's temporary directory."""
SHUTDOWN_WAIT_S = 5.0
"""How long end of input waits for an open overlay to close and answer."""
OVERLAY_BUSY = "an authoring overlay is already open; finish it or press Escape"


def rect_list(rect: Rect) -> list[int]:
    return [rect.x, rect.y, rect.width, rect.height]


def relative(rect: Rect, to: Rect) -> list[int]:
    """``rect`` relative to the top-left corner of ``to``."""
    return [rect.x - to.x, rect.y - to.y, rect.width, rect.height]


def contains(outer: Rect, inner: Rect) -> bool:
    return (outer.x <= inner.x and outer.y <= inner.y
            and inner.x + inner.width <= outer.x + outer.width
            and inner.y + inner.height <= outer.y + outer.height)


def write_bytes_atomically(path: Path, data: bytes) -> None:
    """Write ``data`` through a temporary file in the same directory and a rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def rect_field(value: list[int] | None, field: str) -> Rect | None:
    """A payload's ``[x, y, w, h]``; ``validation_failed`` (``details.field``) when it is
    not four integers with a positive size."""
    if value is None:
        return None
    if len(value) != 4 or any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        raise CommandError(ErrorCode.VALIDATION_FAILED,
                           f"'{field}' must be [x, y, width, height] in whole pixels",
                           {"field": field})
    x, y, width, height = value
    if width <= 0 or height <= 0:
        raise CommandError(ErrorCode.VALIDATION_FAILED,
                           f"'{field}' needs a positive width and height", {"field": field})
    return Rect(x, y, width, height)


@contextlib.contextmanager
def step_errors() -> Iterator[None]:
    """Step errors (from the run's vision) as command errors; a ``details.param``
    becomes ``details.field``."""
    try:
        yield
    except StepError as exc:
        info = exc.info
        details = dict(info.details or {})
        if "param" in details:
            details["field"] = details.pop("param")
        raise CommandError.from_info(ErrorInfo(code=info.code, message=info.message,
                                               details=details or None)) from exc


@dataclass(frozen=True, slots=True)
class _Destination:
    """Where ``capture_region`` writes: a file, or an entry of a loaded script."""

    path: Path | None = None
    entry: LoadedScript | None = None
    name: str = ""


class Authoring:
    """The authoring commands of one engine (see the module docstring)."""

    def __init__(self, server: EngineServer, store: ScriptStore, manager: RunManager,
                 writer: ScriptWriter, backend: EngineBackend | None, *,
                 toolkit_factory: ToolkitFactory | None = None,
                 overlay_factory: OverlayFactory | None = None) -> None:
        self.server = server
        self.store = store
        self.manager = manager
        self.writer = writer
        self.engine_backend = backend
        self._toolkit_factory = toolkit_factory
        self._overlay_factory = overlay_factory if overlay_factory is not None \
            else tk_overlay
        self._toolkit: VisionToolkit | None = None
        self._toolkit_lock = threading.Lock()
        self._overlay: Overlay | None = None
        """The open overlay; set and cleared under :attr:`_overlay_lock`."""
        self._overlay_lock = threading.Lock()
        self._overlay_closed = threading.Event()
        self._overlay_closed.set()
        manager.overlay_open = self.overlay_open

    # --- shared pieces ---------------------------------------------------------------

    @property
    def backend(self) -> Backend | None:
        return None if self.engine_backend is None else self.engine_backend.backend

    def overlay_open(self) -> bool:
        with self._overlay_lock:
            return self._overlay is not None

    def shared_toolkit(self) -> VisionToolkit:
        """The engine's toolkit, created on first use. Raises ``ImportError`` or
        ``RuntimeError`` like a :data:`ToolkitFactory`; a failure is not remembered."""
        with self._toolkit_lock:
            if self._toolkit is None:
                factory = self._toolkit_factory if self._toolkit_factory is not None \
                    else default_toolkit_factory()
                self._toolkit = factory()
            return self._toolkit

    def toolkit(self) -> VisionToolkit:
        """:meth:`shared_toolkit`, or ``capability_missing`` saying why there is none."""
        try:
            return self.shared_toolkit()
        except ImportError as exc:
            raise CommandError(ErrorCode.CAPABILITY_MISSING,
                               f"vision is not available ({exc}); install it with "
                               "pip install j5-macro[vision]",
                               {"capability": "vision"}) from exc
        except RuntimeError as exc:
            raise CommandError(ErrorCode.CAPABILITY_MISSING,
                               f"the screen cannot be captured: {exc}",
                               {"capability": "vision"}) from exc

    def vision(self, entry: LoadedScript | None = None,
               display_scale: float | None = None) -> tuple[RunVision, list[JsonObject]]:
        """A run's vision for one command, and the ``image_search`` payloads it emits:
        image paths, embedded images and capture scale from ``entry``'s script, windows
        from the engine's backend. Never closed: its toolkit is the engine's."""
        script = None if entry is None else entry.script
        events: list[JsonObject] = []

        def emit(type: str, payload: JsonObject) -> None:
            events.append(payload)

        vision = RunVision(
            factory=self.shared_toolkit, backend=self.backend,
            backend_reason=None if self.engine_backend is None else self.engine_backend.reason,
            dry_run=False, captures="on_failure", run_dir=None,
            script_path=None if entry is None or entry.path is None else str(entry.path),
            emit=emit, clock=MonotonicClock(),
            images=None if script is None else script.images,
            display=DisplayScale(override=display_scale, backend=self.backend,
                                 capture_scale=None if script is None
                                 else script.capture_scale))
        return vision, events

    def window_rect(self, window: WindowRef | None) -> Rect | None:
        """The client area of ``window`` (errors as in steps), None without one."""
        if window is None:
            return None
        with step_errors():
            return self.vision()[0].window_rect({"title": window.title})

    def windows(self) -> list[WindowInfo]:
        """The backend's visible windows, topmost first; empty when it cannot tell."""
        backend = self.backend
        if backend is None or not backend.capabilities.windows:
            return []
        try:
            return list(backend.list_windows())
        except Exception:
            return []

    def display_scale_at(self, point: tuple[int, int]) -> float:
        return round(DisplayScale(backend=self.backend).at(point), 4)

    @staticmethod
    def window_for(windows: list[WindowInfo], given: WindowRef | None,
                   given_rect: Rect | None, fits: Callable[[Rect], bool]) -> WindowInfo | None:
        """The window something lies in: the given one when it fits there, else the
        topmost window it fits in. A given window is named by its full title when the
        backend lists it."""
        if given is not None and given_rect is not None and fits(given_rect):
            listed = next((w for w in windows if given.title in w.title), None)
            title = listed.title if listed is not None else given.title
            return WindowInfo(title, given_rect)
        return next((w for w in windows if fits(w.rect)), None)

    # --- overlays --------------------------------------------------------------------

    def open_overlay(self) -> Overlay:
        """Claim the overlay for an interactive command, on the protocol thread:
        ``busy`` during a run or while another overlay is open, ``capability_missing``
        without tkinter. Released by :meth:`release_overlay`."""
        run = self.manager.active_run()
        if run is not None:
            raise CommandError(ErrorCode.BUSY,
                               f"run {run.run_id} is active; overlays wait until it ends",
                               {"run_id": run.run_id})
        with self._overlay_lock:
            if self._overlay is not None:
                raise CommandError(ErrorCode.BUSY, OVERLAY_BUSY, {"reason": "overlay"})
            try:
                overlay = self._overlay_factory()
            except ImportError as exc:
                raise CommandError(ErrorCode.CAPABILITY_MISSING, str(exc),
                                   {"capability": "overlay"}) from exc
            self._overlay = overlay
            self._overlay_closed.clear()
        return overlay

    def release_overlay(self) -> None:
        with self._overlay_lock:
            self._overlay = None
            self._overlay_closed.set()

    def interactive(self, command: Command, work: Callable[[], JsonObject]) -> NoReturn:
        """Answer ``command`` with ``work`` on a thread of its own (the overlay claimed
        with :meth:`open_overlay` is released when it ends, whatever happens)."""

        def guarded() -> JsonObject:
            try:
                return work()
            finally:
                self.release_overlay()

        self.server.answer_later(command, guarded, name=f"j5-{command.type}")

    @staticmethod
    def cancelled() -> CommandError:
        return CommandError(ErrorCode.INVALID_STATE, "cancelled by the user",
                            {"reason": "cancelled"})

    def grab_desktop(self) -> Frame:
        """The whole virtual desktop, for the overlay."""
        try:
            return self.toolkit().capturer.grab(None)
        except RuntimeError as exc:
            raise CommandError(ErrorCode.CAPABILITY_MISSING,
                               f"the screen cannot be captured: {exc}",
                               {"capability": "vision"}) from exc

    def shutdown(self) -> None:
        """End of input: close an open overlay (it answers ``cancelled``), wait for it,
        then release the toolkit."""
        with self._overlay_lock:
            overlay = self._overlay
        if overlay is not None:
            overlay.cancel()
            self._overlay_closed.wait(SHUTDOWN_WAIT_S)
        with self._toolkit_lock:
            toolkit, self._toolkit = self._toolkit, None
        if toolkit is not None:
            with contextlib.suppress(Exception):
                toolkit.capturer.close()

    # --- list_windows ----------------------------------------------------------------

    def list_windows(self, command: Command) -> JsonObject:
        EmptyPayload.from_json(command.payload)
        backend = self.backend
        if backend is None:
            reason = "" if self.engine_backend is None else f": {self.engine_backend.reason}"
            raise CommandError(ErrorCode.CAPABILITY_MISSING,
                               f"listing windows needs a backend{reason}",
                               {"capability": "windows"})
        if not backend.capabilities.windows:
            raise CommandError(ErrorCode.CAPABILITY_MISSING,
                               f"the {backend.name} backend cannot list windows",
                               {"capability": "windows"})
        windows = [WindowPayload(title=w.title, bbox=rect_list(w.rect))
                   for w in backend.list_windows()]
        return ListWindowsResult(windows=windows).to_json()

    # --- screenshot ------------------------------------------------------------------

    def screenshot(self, command: Command) -> JsonObject:
        payload = ScreenshotPayload.from_json(command.payload)
        region = rect_field(payload.region, "region")
        window = self.window_rect(payload.window)
        area = window
        if region is not None:
            area = region if window is None else Rect(window.x + region.x,
                                                      window.y + region.y,
                                                      region.width, region.height)
        toolkit = self.toolkit()
        try:
            frame = toolkit.capturer.grab(area)
        except ValueError as exc:
            raise CommandError(ErrorCode.VALIDATION_FAILED, f"cannot capture there: {exc}",
                               {"field": "region"}) from exc
        except RuntimeError as exc:
            raise CommandError(ErrorCode.CAPABILITY_MISSING,
                               f"the screen cannot be captured: {exc}",
                               {"capability": "vision"}) from exc
        path = self.screenshot_path(payload.path)
        from j5_macro.vision.snapshots import encode_png

        self.write_file(path, encode_png(frame.pixels))
        bbox = frame.rect
        result = ScreenshotResult(path=str(path), bbox=rect_list(bbox),
                                  display_scale=self.display_scale_at(bbox.center))
        if payload.window is not None and window is not None:
            named = self.window_for(self.windows(), payload.window, window, lambda _r: True)
            result = ScreenshotResult(
                path=result.path, bbox=result.bbox, display_scale=result.display_scale,
                window=payload.window.title if named is None else named.title,
                window_bbox=relative(bbox, window))
        return result.to_json()

    @staticmethod
    def screenshot_path(path: str | None) -> Path:
        if path is not None:
            resolved = Path(path).absolute()
            if not resolved.parent.is_dir():
                raise CommandError(ErrorCode.VALIDATION_FAILED,
                                   f"no directory {str(resolved.parent)!r}",
                                   {"field": "path", "reason": "no_directory"})
            return resolved
        directory = Path(tempfile.gettempdir()) / SCREENSHOT_DIR
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CommandError(ErrorCode.VALIDATION_FAILED,
                               f"cannot create {str(directory)!r}: {exc.strerror or exc}",
                               {"field": "path"}) from exc
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        return directory / f"screenshot-{stamp}.png"

    @staticmethod
    def write_file(path: Path, data: bytes) -> None:
        try:
            write_bytes_atomically(path, data)
        except OSError as exc:
            raise CommandError(ErrorCode.VALIDATION_FAILED,
                               f"cannot write {str(path)!r}: {exc.strerror or exc}",
                               {"field": "path"}) from exc

    # --- capture_region --------------------------------------------------------------

    def capture_region(self, command: Command) -> JsonObject:
        payload = CaptureRegionPayload.from_json(command.payload)
        bbox = rect_field(payload.bbox, "bbox")
        destination = self.destination(payload)
        window = self.window_rect(payload.window)
        self.toolkit()
        if bbox is not None:
            if window is not None:
                bbox = Rect(window.x + bbox.x, window.y + bbox.y, bbox.width, bbox.height)
            return self.capture(payload, destination, window, self.windows(), bbox, None)
        overlay = self.open_overlay()

        def work() -> JsonObject:
            windows = self.windows()  # before the overlay hides them
            frame = self.grab_desktop()
            selected = overlay.select_region(frame)
            if selected is None:
                raise self.cancelled()
            return self.capture(payload, destination, window, windows, selected, frame)

        return self.interactive(command, work)

    def destination(self, payload: CaptureRegionPayload) -> _Destination:
        """Checks the destination before capturing anything."""
        if payload.path is not None:
            path = Path(payload.path).absolute()
            if not path.parent.is_dir():
                raise CommandError(ErrorCode.VALIDATION_FAILED,
                                   f"no directory {str(path.parent)!r}",
                                   {"field": "path", "reason": "no_directory"})
            if path.exists() and not payload.overwrite:
                raise CommandError(ErrorCode.VALIDATION_FAILED,
                                   f"{str(path)!r} already exists (overwrite replaces it)",
                                   {"field": "path", "reason": "exists"})
            return _Destination(path=path)
        assert payload.script_id is not None and payload.name is not None
        entry = self.store.get(payload.script_id)
        self.check_name(entry, payload.name, payload.overwrite)
        return _Destination(entry=entry, name=payload.name)

    @staticmethod
    def check_name(entry: LoadedScript, name: str, overwrite: bool,
                   images: Any = None) -> None:
        if not embedded.NAME_PATTERN.match(name):
            raise CommandError(ErrorCode.VALIDATION_FAILED,
                               f"{name!r} is not a valid image name (letters, digits, _ "
                               "and -, not starting with a digit or -)", {"field": "name"})
        existing = entry.script.images if images is None else images
        if name in existing and not overwrite:
            raise CommandError(ErrorCode.VALIDATION_FAILED,
                               f"the script already has an image named {name!r} (overwrite "
                               "replaces it)", {"field": "name", "reason": "exists"})

    def capture(self, payload: CaptureRegionPayload, destination: _Destination,
                window: Rect | None, windows: list[WindowInfo], rect: Rect,
                frame: Frame | None) -> JsonObject:
        """Save ``rect`` (desktop coordinates) of ``frame``, or of a new capture."""
        from j5_macro.vision import snapshots

        padding = payload.padding
        if padding:
            rect = Rect(rect.x - padding, rect.y - padding, rect.width + 2 * padding,
                        rect.height + 2 * padding)
        if frame is None:
            try:
                frame = self.toolkit().capturer.grab(rect)
            except ValueError as exc:
                raise CommandError(ErrorCode.VALIDATION_FAILED,
                                   f"cannot capture there: {exc}", {"field": "bbox"}) from exc
            except RuntimeError as exc:
                raise CommandError(ErrorCode.CAPABILITY_MISSING,
                                   f"the screen cannot be captured: {exc}",
                                   {"capability": "vision"}) from exc
            rect = frame.rect
            pixels = frame.pixels
        else:
            clipped = snapshots.intersect(rect, frame.rect)
            if clipped is None:
                raise CommandError(ErrorCode.VALIDATION_FAILED,
                                   f"{rect} is outside the virtual desktop {frame.rect}",
                                   {"field": "bbox"})
            rect = clipped
            pixels = snapshots.crop(frame, rect)
        warnings: list[CaptureWarning] = []
        if payload.transparent:
            try:
                pixels = snapshots.transparent_background(pixels)
            except ValueError as exc:
                raise CommandError(ErrorCode.VALIDATION_FAILED,
                                   f"transparent background: {exc}",
                                   {"field": "transparent", "reason": "transparent"}) from exc
        if snapshots.is_flat(pixels):
            warnings.append(CaptureWarning(
                code="flat", message="the capture is a single flat colour, so it would "
                "never match; capture something with an edge or some texture"))
        data = snapshots.encode_png(pixels)
        display_scale = self.display_scale_at(rect.center)
        inside = self.window_for(windows, payload.window, window,
                                 lambda area: contains(area, rect))
        fields: dict[str, Any] = {
            "bbox": rect_list(rect), "display_scale": display_scale, "warnings": warnings,
        }
        if inside is not None:
            fields.update(window=inside.title, window_bbox=relative(rect, inside.rect))
        if destination.path is not None:
            self.write_file(destination.path, data)
            fields["path"] = str(destination.path)
        else:
            fields.update(self.embed(destination, payload.overwrite, data, display_scale))
        return CaptureRegionResult(**fields).to_json()

    def embed(self, destination: _Destination, overwrite: bool, data: bytes,
              display_scale: float) -> dict[str, Any]:
        """Add (or replace) the image in the script's ``images``; the result's fields."""
        entry, name = destination.entry, destination.name
        assert entry is not None
        if len(data) > embedded.MAX_IMAGE_BYTES:
            raise CommandError(ErrorCode.VALIDATION_FAILED,
                               f"the capture is {len(data):,} bytes as PNG, over the "
                               f"{embedded.MAX_IMAGE_BYTES:,} bytes an embedded image may "
                               "have; save it as a file instead",
                               {"field": "name", "reason": "too_large"})
        source_path = None if entry.path is None else str(entry.path)
        with self.writer.lock:
            text = self.writer.current_text(entry)
            before = self.store.load_text(text, source_path)
            script = before.script if before.script is not None else entry.script
            self.check_name(entry, name, overwrite, script.images)
            capture_scale = script.capture_scale or 1.0
            value: dict[str, Any] = {"png": base64.b64encode(data).decode("ascii")}
            scale = None
            if abs(display_scale - capture_scale) > 1e-6:
                scale = display_scale
                value["scale"] = scale
            try:
                new_text = rewrite_images(text, {}, lookup=self.store.registry.spec,
                                          add={name: value})
            except EditError as exc:
                raise edit_error(exc) from exc
            result = self.store.load_text(new_text, source_path)
            if before.ok and not result.ok:
                first = result.errors[0]
                too_large = "MiB" in first.message
                raise CommandError(
                    ErrorCode.VALIDATION_FAILED,
                    f"embedding the capture would break the script: {first.message}",
                    {"field": "name", "errors": [e.to_json() for e in result.errors],
                     **({"reason": "too_large"} if too_large else {})})
            self.writer.commit(entry, text, new_text, result)
            fields: dict[str, Any] = {"image_name": name, "version": entry.version}
            if scale is not None:
                fields["scale"] = scale
            return fields

    # --- pick_point ------------------------------------------------------------------

    def pick_point(self, command: Command) -> JsonObject:
        payload = PickPointPayload.from_json(command.payload)
        window = self.window_rect(payload.window)
        self.toolkit()
        overlay = self.open_overlay()

        def work() -> JsonObject:
            windows = self.windows()
            frame = self.grab_desktop()
            point = overlay.pick_point(frame)
            if point is None:
                raise self.cancelled()
            x, y = point
            fields: dict[str, Any] = {"x": x, "y": y,
                                      "display_scale": self.display_scale_at(point)}
            if payload.window is not None and window is not None:
                inside = self.window_for(windows, payload.window, window, lambda _r: True)
            else:
                inside = next((w for w in windows if w.rect.contains(x, y)), None)
            if inside is not None:
                area = inside.rect
                fields.update(window=inside.title, window_area=rect_list(area),
                              window_point=[x - area.x, y - area.y])
            return PickPointResult(**fields).to_json()

        return self.interactive(command, work)

    # --- find_image ------------------------------------------------------------------

    def find_image(self, command: Command) -> JsonObject:
        payload = FindImagePayload.from_json(command.payload)
        entry = None if payload.script_id is None else self.store.get(payload.script_id)
        if entry is None and embedded.reference_name(payload.image) is not None:
            raise CommandError(ErrorCode.VALIDATION_FAILED,
                               f"{payload.image} is an embedded image: give the script it "
                               "is in (script_id)", {"field": "image"})
        rect_field(payload.region, "region")
        if not payload.show:
            return self.search(payload, entry)[0]
        overlay = self.open_overlay()

        def work() -> JsonObject:
            result, match = self.search(payload, entry)
            frame = self.grab_desktop()
            score = float(result["score"])
            if match.found:
                label = f"found: score {score:.3f}"
            else:
                label = f"not found: best score {score:.3f} (threshold {payload.threshold:g})"
            overlay.show_match(frame, match.bbox, match.found, label, payload.show_ms)
            return result

        return self.interactive(command, work)

    def search(self, payload: FindImagePayload,
               entry: LoadedScript | None) -> tuple[JsonObject, Match]:
        """One search as a step makes it; the result and the match."""
        vision, events = self.vision(entry, payload.display_scale)
        bound = BoundVision(vision, step_id="find_image", step_timeout=None,
                            wait=lambda _s: None, check_stop=lambda: None,
                            log=lambda _l, _m: None, paused=lambda: 0.0)
        window = None if payload.window is None else {"title": payload.window.title}
        with step_errors():
            match = bound.find(payload.image, threshold=payload.threshold,
                               scale=payload.scale, region=payload.region, window=window)
        event = events[-1]
        fields: dict[str, Any] = {
            "found": match.found, "score": event["best_score"], "region": event["region"],
            "scale": event["scale"], "display_scale": event["display_scale"],
            "capture_scale": event["capture_scale"], "elapsed_ms": event["elapsed_ms"],
        }
        if match.bbox is not None:
            fields.update(bbox=rect_list(match.bbox), center=list(match.bbox.center))
        if payload.window is not None and match.frame is not None:
            named = self.window_for(self.windows(), payload.window, match.frame,
                                    lambda _r: True)
            fields["window"] = payload.window.title if named is None else named.title
            if match.bbox is not None:
                fields["window_bbox"] = relative(match.bbox, match.frame)
        return FindImageResult(**fields).to_json(), match


def install(server: EngineServer, store: ScriptStore, manager: RunManager,
            writer: ScriptWriter, backend: EngineBackend | None, *,
            toolkit_factory: ToolkitFactory | None = None,
            overlay_factory: OverlayFactory | None = None) -> Authoring:
    authoring = Authoring(server, store, manager, writer, backend,
                          toolkit_factory=toolkit_factory, overlay_factory=overlay_factory)
    server.register("list_windows", authoring.list_windows)
    server.register("screenshot", authoring.screenshot)
    server.register("capture_region", authoring.capture_region)
    server.register("pick_point", authoring.pick_point)
    server.register("find_image", authoring.find_image)
    server.on_shutdown(authoring.shutdown)
    return authoring
