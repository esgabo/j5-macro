"""The run's vision: one :class:`~j5_macro.vision.api.VisionToolkit` per run, and the
:class:`~j5_macro.vision.api.StepVision` each step sees as ``ctx.vision``.

docs/contract.md, "Script format and step registry" (images, coordinates), "Events"
(``image_search``) and "Run states and debugging semantics" (dry runs).

- The toolkit is created lazily, the first time a step searches, with a
  :data:`~j5_macro.vision.api.ToolkitFactory` (by default
  ``j5_macro.vision.toolkit.create_toolkit``). If it cannot be created, every search of
  the run fails with ``capability_missing`` giving the reason.
- Image paths are relative to the script file's directory (the current directory for a
  script without a file). ``window: {title}`` is looked up with the engine's backend;
  ``region`` is relative to that window when given, else absolute.
- Every attempt emits ``image_search``. Captures go to ``<run_dir>/captures/`` following
  the run's ``captures`` policy, and events carry their path relative to ``run_dir``.
- :meth:`BoundVision.wait` sleeps between attempts through the step's interruptible
  wait: ``stop`` aborts it, ``pause`` suspends it and paused time does not count towards
  the timeout.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, cast

from j5_macro.backends.base import Backend, Rect
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.payloads.events import ImageSearchEvent
from j5_macro.runtime.api import RunEventSink
from j5_macro.runtime.clock import Clock
from j5_macro.steps.spec import LogLevel, StepError
from j5_macro.vision.api import (
    DEFAULT_INTERVAL,
    DEFAULT_THRESHOLD,
    Frame,
    Match,
    Template,
    ToolkitFactory,
    VisionToolkit,
)

TOOLKIT_MODULE = "j5_macro.vision.toolkit"
"""Provides ``create_toolkit``, the default :data:`ToolkitFactory`."""
CAPTURES_DIR = "captures"
CapturePolicy = Literal["on_failure", "all"]


def default_toolkit_factory() -> ToolkitFactory:
    """``j5_macro.vision.toolkit.create_toolkit``, imported when first needed (raises
    ImportError when the vision module or its extra is missing)."""
    module = importlib.import_module(TOOLKIT_MODULE)
    factory = getattr(module, "create_toolkit", None)
    if factory is None:
        raise ImportError(f"{TOOLKIT_MODULE} has no 'create_toolkit'")
    return cast(ToolkitFactory, factory)


def rect_list(rect: Rect) -> list[int]:
    return [rect.x, rect.y, rect.width, rect.height]


class RunVision:
    """Owns the run's toolkit and capture counter (see the module docstring). Used only
    from the run's worker thread."""

    def __init__(self, *, factory: ToolkitFactory | None, backend: Backend | None,
                 backend_reason: str | None, dry_run: bool, captures: CapturePolicy,
                 run_dir: Path | None, script_path: str | None, emit: RunEventSink,
                 clock: Clock) -> None:
        self._factory = factory
        self._backend = backend
        self._backend_reason = backend_reason
        self.dry_run = dry_run
        self.captures = captures
        self.run_dir = run_dir
        self._script_dir = Path(script_path).parent if script_path else None
        self._emit = emit
        self.clock = clock
        self._toolkit: VisionToolkit | None = None
        self._unavailable: str | None = None
        self._saved = 0
        self._save_warned = False

    # --- toolkit --------------------------------------------------------------------

    def toolkit(self) -> VisionToolkit:
        """The toolkit, created on first use; ``capability_missing`` when unavailable."""
        if self._toolkit is not None:
            return self._toolkit
        if self._unavailable is None:
            try:
                factory = self._factory if self._factory is not None \
                    else default_toolkit_factory()
                self._toolkit = factory()
                return self._toolkit
            except ImportError as exc:
                self._unavailable = (f"vision is not available ({exc}); install it with "
                                     "pip install j5-macro[vision]")
            except RuntimeError as exc:
                self._unavailable = f"the screen cannot be captured: {exc}"
        raise StepError(ErrorCode.CAPABILITY_MISSING, self._unavailable)

    def close(self) -> None:
        toolkit, self._toolkit = self._toolkit, None
        if toolkit is not None:
            toolkit.capturer.close()

    # --- resolution -----------------------------------------------------------------

    def template_path(self, image: str) -> Path:
        path = Path(image)
        if path.is_absolute():
            return path
        base = self._script_dir if self._script_dir is not None else Path.cwd()
        return base / path

    def load_template(self, image: Any) -> Template:
        if not isinstance(image, str) or not image:
            raise StepError(ErrorCode.VALIDATION_FAILED, "'image' must be a non-empty path",
                            {"param": "image"})
        toolkit = self.toolkit()
        path = self.template_path(image)
        details = {"template": image, "path": str(path)}
        if not path.is_file():
            raise StepError(ErrorCode.TEMPLATE_MISSING, f"image file not found: {path}",
                            details)
        try:
            return toolkit.load_template(path)
        except FileNotFoundError:
            raise StepError(ErrorCode.TEMPLATE_MISSING, f"image file not found: {path}",
                            details) from None
        except ValueError as exc:
            raise StepError(ErrorCode.TEMPLATE_MISSING,
                            f"{path} is not a readable image: {exc}", details) from exc

    def window_rect(self, window: Any) -> Rect:
        title = window.get("title") if isinstance(window, Mapping) else None
        if not isinstance(title, str) or not title:
            raise StepError(ErrorCode.VALIDATION_FAILED,
                            "'window' needs a non-empty 'title', e.g. {title: Notepad}",
                            {"param": "window"})
        backend = self._backend
        if backend is None:
            why = f": {self._backend_reason}" if self._backend_reason else ""
            raise StepError(ErrorCode.CAPABILITY_MISSING,
                            f"window-relative searches need a backend{why}")
        if not backend.capabilities.windows:
            raise StepError(ErrorCode.CAPABILITY_MISSING,
                            f"the {backend.name} backend cannot find windows "
                            "(window-relative searches)")
        rect = backend.find_window(title)
        if rect is None:
            raise StepError(ErrorCode.WINDOW_NOT_FOUND,
                            f"no visible window whose title contains {title!r}",
                            {"title": title})
        return rect

    def search_area(self, region: Any, window: Any) -> Rect | None:
        """The desktop rectangle to capture, or None for the whole desktop."""
        frame = self.window_rect(window) if window is not None else None
        if region is None:
            return frame
        if (not isinstance(region, list) or len(region) != 4
                or any(isinstance(v, bool) or not isinstance(v, int | float) for v in region)):
            raise StepError(ErrorCode.VALIDATION_FAILED,
                            "'region' must be [x, y, width, height]", {"param": "region"})
        x, y, width, height = (round(v) for v in region)
        if width <= 0 or height <= 0:
            raise StepError(ErrorCode.VALIDATION_FAILED,
                            "'region' needs a positive width and height", {"param": "region"})
        if frame is not None:
            x, y = frame.x + x, frame.y + y
        return Rect(x, y, width, height)

    # --- captures and events --------------------------------------------------------

    def save(self, frame: Frame, step_id: str, attempt: int, outline: Rect | None,
             log: Callable[[LogLevel, str], None]) -> str | None:
        """Save a capture; returns its path relative to ``run_dir`` (None on failure)."""
        if self.run_dir is None:
            return None
        self._saved += 1
        relative = f"{CAPTURES_DIR}/{self._saved:04d}-{step_id}-{attempt}.png"
        try:
            directory = self.run_dir / CAPTURES_DIR
            directory.mkdir(exist_ok=True)
            self.toolkit().save_image(frame, self.run_dir / relative, outline)
        except Exception as exc:
            if not self._save_warned:
                self._save_warned = True
                log("warning", f"cannot save captures in {self.run_dir}: {exc}")
            return None
        return relative

    def emit_search(self, event: ImageSearchEvent) -> None:
        self._emit("image_search", event.to_json())


class BoundVision:
    """A :class:`~j5_macro.vision.api.StepVision`: the run's vision bound to one step
    attempt. ``wait`` and ``check_stop`` are the step's interruptible wait and stop
    check; ``paused`` returns the run's total paused seconds so far."""

    def __init__(self, run: RunVision, *, step_id: str, step_timeout: float | None,
                 wait: Callable[[float], None], check_stop: Callable[[], None],
                 log: Callable[[LogLevel, str], None], paused: Callable[[], float]) -> None:
        self._run = run
        self._step_id = step_id
        self._step_timeout = step_timeout
        self._wait = wait
        self._check_stop = check_stop
        self._log = log
        self._paused = paused

    def find(self, image: str, *, threshold: float = DEFAULT_THRESHOLD, scale: float = 1.0,
             region: list[int] | None = None, window: dict[str, Any] | None = None) -> Match:
        """One attempt; not finding the image is not a failure (no capture under
        ``on_failure``)."""
        search = self._prepare(image, threshold, scale, region, window)
        return self._attempt(search, 1, final_miss_fails=False)

    def wait(self, image: str, *, timeout: float, threshold: float = DEFAULT_THRESHOLD,
             scale: float = 1.0, region: list[int] | None = None,
             window: dict[str, Any] | None = None,
             interval: float = DEFAULT_INTERVAL) -> Match:
        """Attempts every ``interval`` until found or ``timeout`` (the step's common
        ``timeout`` replaces it when set) seconds of unpaused time pass. Dry runs make
        exactly one attempt and return its match, found or not."""
        if self._step_timeout is not None:
            timeout = self._step_timeout
        search = self._prepare(image, threshold, scale, region, window)
        if self._run.dry_run:
            return self._attempt(search, 1, final_miss_fails=False)
        clock = self._run.clock
        started, paused_before = clock.now(), self._paused()

        def active() -> float:
            return clock.now() - started - (self._paused() - paused_before)

        attempt = 0
        while True:
            self._check_stop()
            attempt += 1
            match = self._attempt(search, attempt, final_miss_fails=True,
                                  out_of_time=lambda: active() >= timeout)
            if match.found:
                return match
            if search.failed:
                raise StepError(
                    ErrorCode.IMAGE_NOT_FOUND,
                    f"{image} not found after {attempt} attempt(s) in {timeout:g}s "
                    f"(best score {match.score:.3f}, threshold {threshold:g})",
                    {"best_score": round(match.score, 4), "threshold": threshold,
                     "template": image, "attempts": attempt},
                    screenshot=search.failed_capture)
            self._wait(max(0.0, min(interval, timeout - active())))

    # --- internals ------------------------------------------------------------------

    def _prepare(self, image: str, threshold: float, scale: float, region: Any,
                 window: Any) -> _Search:
        if isinstance(threshold, bool) or not isinstance(threshold, int | float) \
                or not 0 <= threshold <= 1:
            raise StepError(ErrorCode.VALIDATION_FAILED, "'threshold' must be between 0 and 1",
                            {"param": "threshold"})
        if isinstance(scale, bool) or not isinstance(scale, int | float) or scale <= 0:
            raise StepError(ErrorCode.VALIDATION_FAILED, "'scale' must be a positive number",
                            {"param": "scale"})
        template = self._run.load_template(image)
        self._run.search_area(region, window)  # fail early on a bad region or window
        return _Search(image=image, template=template, threshold=float(threshold),
                       scale=float(scale), region=region, window=window)

    def _attempt(self, search: _Search, attempt: int, *, final_miss_fails: bool,
                 out_of_time: Callable[[], bool] | None = None) -> Match:
        run = self._run
        toolkit = run.toolkit()
        clock = run.clock
        began = clock.now()
        # Looked up on every attempt, so a wait follows a window that moves.
        area = run.search_area(search.region, search.window)
        try:
            frame = toolkit.capturer.grab(area)
        except ValueError as exc:  # e.g. a region entirely outside the virtual desktop
            raise StepError(ErrorCode.VALIDATION_FAILED, f"cannot search there: {exc}",
                            {"param": "region"}) from exc
        match = toolkit.locate(search.template, frame, search.threshold, search.scale)
        elapsed_ms = max(0, round((clock.now() - began) * 1000))
        failing = (final_miss_fails and not match.found
                   and (out_of_time is None or out_of_time()))
        screenshot: str | None = None
        if run.captures == "all" or failing:
            screenshot = run.save(frame, self._step_id, attempt, match.bbox, self._log)
        if failing:
            search.failed_capture = screenshot
            search.failed = True
        if area is None:
            area = frame.rect
        run.emit_search(ImageSearchEvent(
            template=search.image, threshold=search.threshold, found=match.found,
            best_score=round(float(match.score), 4),
            bbox=None if match.bbox is None else rect_list(match.bbox),
            region=rect_list(area), attempt=attempt, elapsed_ms=elapsed_ms,
            screenshot=screenshot))
        return match


class _Search:
    """One find or wait: its resolved inputs and how its last attempt ended."""

    __slots__ = ("failed", "failed_capture", "image", "region", "scale", "template",
                 "threshold", "window")

    def __init__(self, *, image: str, template: Template, threshold: float, scale: float,
                 region: Any, window: Any) -> None:
        self.image = image
        self.template = template
        self.threshold = threshold
        self.scale = scale
        self.region = region
        self.window = window
        self.failed = False
        self.failed_capture: str | None = None
