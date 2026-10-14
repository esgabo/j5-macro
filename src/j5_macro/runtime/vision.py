"""The run's vision: one :class:`~j5_macro.vision.api.VisionToolkit` per run, and the
:class:`~j5_macro.vision.api.StepVision` each step sees as ``ctx.vision``.

docs/contract.md, "Script format and step registry" (images, coordinates), "Events"
(``image_search``) and "Run states and debugging semantics" (dry runs).

- The toolkit is created lazily, the first time a step searches, with a
  :data:`~j5_macro.vision.api.ToolkitFactory` (by default
  ``j5_macro.vision.toolkit.create_toolkit``). If it cannot be created, every search of
  the run fails with ``capability_missing`` giving the reason.
- Image paths are relative to the script file's directory (the current directory for a
  script without a file); ``#name`` is an image embedded in the script, decoded with the
  toolkit's ``decode_template`` the first time the run uses it and kept for the rest of
  the run. ``window: {title}`` is looked up with the engine's backend; ``region`` is
  relative to that window when given, else absolute. A region placed
  ``{at: <stored result>, ...}`` is on the desktop, following the stored result's own
  window (:func:`~j5_macro.steps.positions.anchored_region`).
- Matches carry the client area of the window searched in (``Match.frame``), so image
  results can report where they are relative to it (``window_bbox``).
- Templates are resized by the step's ``scale`` times the display scale ratio: the
  display scale at the centre of the searched area (the primary monitor's for the whole
  desktop) over the template's capture scale (its embedded ``scale``, else the
  script's ``capture_scale``); see :mod:`j5_macro.runtime.display`. Matches carry that
  ratio (``Match.display_ratio``) for the offsets measured from them.
- Every attempt emits ``image_search``. Captures go to ``<run_dir>/captures/`` following
  the run's ``captures`` policy, and events carry their path relative to ``run_dir``.
- The waits of :class:`BoundVision` (``wait``, ``wait_gone``, ``wait_window``,
  ``wait_window_gone``) poll every 0.25 s and sleep between polls through the step's
  interruptible wait: ``stop`` aborts it, ``pause`` suspends it and paused time does not
  count towards the timeout. While an image wait polls, a missing ``window`` is "not
  found yet"; it fails with ``window_not_found`` only when the window never appeared.
  Window waits use the backend's ``list_windows`` (no vision needed) and emit no events.
"""

from __future__ import annotations

import dataclasses
import importlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

from j5_macro.backends.base import Backend, Rect, WindowInfo
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.payloads.events import ImageSearchEvent
from j5_macro.runtime.api import RunEventSink
from j5_macro.runtime.clock import Clock
from j5_macro.runtime.display import DisplayScale
from j5_macro.script.images import reference_name
from j5_macro.script.model import EmbeddedImage
from j5_macro.steps.images import not_found_note, relaxed_warning, strict_not_found
from j5_macro.steps.positions import anchored_region, locate_stored, read_stored
from j5_macro.steps.spec import LogLevel, StepError
from j5_macro.vision.api import (
    DEFAULT_INTERVAL,
    DEFAULT_SCALE,
    DEFAULT_THRESHOLD,
    Frame,
    Match,
    Template,
    ToolkitFactory,
    VisionToolkit,
)
from j5_macro.vision.selection import (
    ANCHORS,
    Selection,
    choose,
    eligible,
    none_chosen,
    reading_order,
    selection_problem,
)

TOOLKIT_MODULE = "j5_macro.vision.toolkit"
"""Provides ``create_toolkit``, the default :data:`ToolkitFactory`."""
CAPTURES_DIR = "captures"
CapturePolicy = Literal["on_failure", "all"]
_T = TypeVar("_T")


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
                 clock: Clock, images: Mapping[str, EmbeddedImage] | None = None,
                 display: DisplayScale | None = None) -> None:
        self._factory = factory
        self._backend = backend
        self._backend_reason = backend_reason
        self.dry_run = dry_run
        self.captures = captures
        self.run_dir = run_dir
        self._script_dir = Path(script_path).parent if script_path else None
        self._images: Mapping[str, EmbeddedImage] = images or {}
        self.display = display if display is not None else DisplayScale(backend=backend)
        """The run's display and capture scales."""
        self._embedded: dict[str, Template] = {}
        """Embedded templates decoded so far in this run, by name."""
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
        name = reference_name(image)
        if name is not None:
            return self.embedded_template(image, name)
        path = self.template_path(image)
        details = {"template": image, "path": str(path)}
        # Checked before the toolkit, so a script error is reported wherever it runs.
        if not path.is_file():
            raise StepError(ErrorCode.TEMPLATE_MISSING, f"image file not found: {path}",
                            details)
        toolkit = self.toolkit()
        try:
            return toolkit.load_template(path)
        except FileNotFoundError:
            raise StepError(ErrorCode.TEMPLATE_MISSING, f"image file not found: {path}",
                            details) from None
        except ValueError as exc:
            raise StepError(ErrorCode.TEMPLATE_MISSING,
                            f"{path} is not a readable image: {exc}", details) from exc

    def capture_scale(self, image: str) -> float:
        """The capture scale of a template: an embedded image's ``scale``, else the
        script's ``capture_scale``."""
        name = reference_name(image)
        entry = None if name is None else self._images.get(name)
        if entry is not None and entry.scale is not None:
            return entry.scale
        return self.display.capture_scale

    def embedded_template(self, image: str, name: str) -> Template:
        """The template of ``images[name]``, decoded once per run. No path is reported:
        the image lives in the script."""
        cached = self._embedded.get(name)
        if cached is not None:
            return cached
        details = {"template": image}
        entry = self._images.get(name)
        # Checked before the toolkit, like a missing file.
        if entry is None:
            raise StepError(ErrorCode.TEMPLATE_MISSING,
                            f"the script has no embedded image named {name!r}", details)
        toolkit = self.toolkit()
        if toolkit.decode_template is None:
            raise StepError(ErrorCode.CAPABILITY_MISSING,
                            "this vision toolkit cannot load images embedded in a script")
        try:
            template = toolkit.decode_template(entry.data, image)
        except ValueError as exc:
            raise StepError(ErrorCode.TEMPLATE_MISSING,
                            f"{image} is not a readable image: {exc}", details) from exc
        self._embedded[name] = template
        return template

    def windows_backend(self, what: str = "window-relative searches") -> Backend:
        """The backend, when it can find windows; ``capability_missing`` otherwise."""
        backend = self._backend
        if backend is None:
            why = f": {self._backend_reason}" if self._backend_reason else ""
            raise StepError(ErrorCode.CAPABILITY_MISSING, f"{what} need a backend{why}")
        if not backend.capabilities.windows:
            raise StepError(ErrorCode.CAPABILITY_MISSING,
                            f"the {backend.name} backend cannot find windows ({what})")
        return backend

    def find_window(self, title: Any) -> WindowInfo | None:
        """The topmost visible window whose title contains ``title`` (its full title and
        client area), or None."""
        if not isinstance(title, str) or not title:
            raise StepError(ErrorCode.VALIDATION_FAILED, "'title' must be a non-empty text",
                            {"param": "title"})
        backend = self.windows_backend("window lookups")
        return next((w for w in backend.list_windows() if title in w.title), None)

    def window_rect(self, window: Any) -> Rect:
        title = window.get("title") if isinstance(window, Mapping) else None
        if not isinstance(title, str) or not title:
            raise StepError(ErrorCode.VALIDATION_FAILED,
                            "'window' needs a non-empty 'title', e.g. {title: Notepad}",
                            {"param": "window"})
        rect = self.windows_backend().find_window(title)
        if rect is None:
            raise StepError(ErrorCode.WINDOW_NOT_FOUND,
                            f"no visible window whose title contains {title!r}",
                            {"title": title})
        return rect

    def window_frame(self, window: Any) -> Rect | None:
        """The client area of the step's ``window``, None without one."""
        return None if window is None else self.window_rect(window)

    def search_area(self, region: Any, frame: Rect | None) -> Rect | None:
        """The desktop rectangle to capture (``region``, relative to ``frame`` when given;
        else ``frame``), or None for the whole desktop."""
        if region is None:
            return frame
        if isinstance(region, Mapping):
            return anchored_region(region, lambda title: self.window_rect({"title": title}),
                                   ratio_at=self.display.ratio_at)
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
                 log: Callable[[LogLevel, str], None], paused: Callable[[], float],
                 strict: bool = False, relaxed_by: str | None = None) -> None:
        self._run = run
        self._strict = strict
        """A strict dry run: the single search of a wait fails when it misses, unless
        ``relaxed_by`` (see :attr:`~j5_macro.steps.spec.StepContext.relaxed_by`) says
        that it may depend on an earlier step; then it is a warning."""
        self._relaxed_by = relaxed_by
        self._step_id = step_id
        self._step_timeout = step_timeout
        self._wait = wait
        self._check_stop = check_stop
        self._log = log
        self._paused = paused

    def find(self, image: str, *, threshold: float = DEFAULT_THRESHOLD,
             scale: float = DEFAULT_SCALE, region: list[int] | dict[str, Any] | None = None,
             window: dict[str, Any] | None = None,
             failing: Callable[[Match], bool] | None = None,
             select: Selection | None = None) -> Match:
        """One attempt; not finding the image is not a failure (no capture under
        ``on_failure``) unless ``failing(match)`` says the step fails with it."""
        search = self._prepare(image, threshold, scale, region, window, select=select)
        return self._attempt(search, 1, failing=failing)

    def wait(self, image: str, *, timeout: float, threshold: float = DEFAULT_THRESHOLD,
             scale: float = DEFAULT_SCALE, region: list[int] | dict[str, Any] | None = None,
             window: dict[str, Any] | None = None,
             interval: float = DEFAULT_INTERVAL, select: Selection | None = None,
             own_timeout: bool = False) -> Match:
        """Attempts every ``interval`` until found or ``timeout`` (the step's common
        ``timeout`` replaces it when set, unless ``own_timeout``) seconds of unpaused
        time pass. Dry runs make
        exactly one attempt and return its match, found or not; strict dry runs fail
        with ``image_not_found`` when it misses (a warning once relaxed, see
        :attr:`~j5_macro.steps.spec.StepContext.relaxed_by`). While waiting (a timeout
        above 0) a missing window is "not found yet"."""
        timeout = timeout if own_timeout else self._timeout(timeout)
        polls = _WindowPolls(tolerate=timeout > 0 and not self._run.dry_run)
        search = self._prepare(image, threshold, scale, region, window, polls.tolerate,
                               select)
        if self._run.dry_run:
            strict = self._strict and self._relaxed_by is None
            match = self._attempt(search, 1, failing=(lambda m: not m.found) if strict
                                  else None)
            if strict and not match.found:
                raise strict_not_found(image, match, threshold)
            if self._strict and self._relaxed_by is not None and not match.found:
                self._log("warning", relaxed_warning(
                    strict_not_found(image, match, threshold, when=""), self._relaxed_by))
            return match

        def attempt(number: int, final: _Final) -> Match | None:
            match = polls.run(lambda: self._attempt(
                search, number, failing=lambda m: not m.found and final()))
            return match if match is not None and match.found else None

        def timed_out(attempts: int) -> StepError:
            missing = polls.never_seen(timeout)
            if missing is not None:
                return missing
            match = polls.last
            assert match is not None  # never_seen covers a wait without any search
            return StepError(
                ErrorCode.IMAGE_NOT_FOUND,
                f"{image} not found after {attempts} attempt(s) in {timeout:g}s "
                f"(best score {match.score:.3f}, threshold {threshold:g}"
                f"{not_found_note(match)})",
                {"best_score": round(match.score, 4), "threshold": threshold,
                 "template": image, "attempts": attempts},
                screenshot=search.failed_capture)

        return self._poll(timeout, interval, attempt, timed_out)[0]

    def wait_gone(self, image: str, *, timeout: float,
                  threshold: float = DEFAULT_THRESHOLD, scale: float = DEFAULT_SCALE,
                  region: list[int] | dict[str, Any] | None = None,
                  window: dict[str, Any] | None = None,
                  interval: float = DEFAULT_INTERVAL,
                  select: Selection | None = None) -> float:
        """Attempts every ``interval`` until the image is not found; returns the unpaused
        seconds waited. A missing window is "not gone yet"."""
        timeout = self._timeout(timeout)
        polls = _WindowPolls(tolerate=timeout > 0)
        search = self._prepare(image, threshold, scale, region, window, polls.tolerate,
                               select)

        def attempt(number: int, final: _Final) -> Match | None:
            match = polls.run(lambda: self._attempt(
                search, number, failing=lambda m: m.found and final()))
            return match if match is not None and not match.found else None

        def timed_out(attempts: int) -> StepError:
            missing = polls.never_seen(timeout)
            if missing is not None:
                return missing
            match = polls.last
            assert match is not None  # never_seen covers a wait without any search
            return StepError(
                ErrorCode.STILL_VISIBLE,
                f"{image} still visible after {timeout:g}s (score {match.score:.3f})",
                {"image": image, "score": round(match.score, 4), "timeout": timeout},
                screenshot=search.failed_capture)

        return self._poll(timeout, interval, attempt, timed_out)[1]

    def find_window(self, title: str) -> WindowInfo | None:
        return self._run.find_window(title)

    def wait_window(self, title: str, *, timeout: float,
                    interval: float = DEFAULT_INTERVAL) -> WindowInfo:
        """Looks for the window every ``interval`` until it is there."""
        timeout = self._timeout(timeout)

        def attempt(number: int, final: _Final) -> WindowInfo | None:
            return self._run.find_window(title)

        def timed_out(attempts: int) -> StepError:
            return StepError(ErrorCode.WINDOW_NOT_FOUND,
                             f"no visible window whose title contains {title!r} "
                             f"appeared in {timeout:g}s", {"title": title, "timeout": timeout})

        return self._poll(timeout, interval, attempt, timed_out)[0]

    def wait_window_gone(self, title: str, *, timeout: float,
                         interval: float = DEFAULT_INTERVAL) -> float:
        """Looks for the window every ``interval`` until none matches; returns the
        unpaused seconds waited."""
        timeout = self._timeout(timeout)
        seen: list[WindowInfo] = []

        def attempt(number: int, final: _Final) -> bool | None:
            found = self._run.find_window(title)
            if found is None:
                return True
            seen[:] = [found]
            return None

        def timed_out(attempts: int) -> StepError:
            return StepError(ErrorCode.STILL_VISIBLE,
                             f"window {seen[0].title!r} still visible after {timeout:g}s",
                             {"title": title, "window": seen[0].title, "timeout": timeout})

        return self._poll(timeout, interval, attempt, timed_out)[1]

    # --- internals ------------------------------------------------------------------

    def _timeout(self, timeout: float) -> float:
        """The step's common ``timeout`` when set, else the wait's own."""
        return timeout if self._step_timeout is None else self._step_timeout

    def _poll(self, timeout: float, interval: float,
              attempt: Callable[[int, _Final], _T | None],
              timed_out: Callable[[int], StepError]) -> tuple[_T, float]:
        """Calls ``attempt(number, final)`` at once and then every ``interval`` until it
        returns a value, and returns it with the unpaused seconds waited. ``final()``
        tells an attempt whether its time is up (decided once per attempt, so the
        attempt that fails knows it and saves its capture); after such an attempt the
        wait raises ``timed_out(attempts)``."""
        clock = self._run.clock
        started, paused_before = clock.now(), self._paused()

        def active() -> float:
            return clock.now() - started - (self._paused() - paused_before)

        number = 0
        while True:
            self._check_stop()
            number += 1
            final = _Final(lambda: active() >= timeout)
            value = attempt(number, final)
            if value is not None:
                return value, active()
            if final():
                raise timed_out(number)
            self._wait(max(0.0, min(interval, timeout - active())))

    def _prepare(self, image: str, threshold: float, scale: float, region: Any,
                 window: Any, missing_window_ok: bool = False,
                 select: Selection | None = None) -> _Search:
        if isinstance(threshold, bool) or not isinstance(threshold, int | float) \
                or not 0 <= threshold <= 1:
            raise StepError(ErrorCode.VALIDATION_FAILED, "'threshold' must be between 0 and 1",
                            {"param": "threshold"})
        if isinstance(scale, bool) or not isinstance(scale, int | float) or scale <= 0:
            raise StepError(ErrorCode.VALIDATION_FAILED, "'scale' must be a positive number",
                            {"param": "scale"})
        template = self._run.load_template(image)
        anchors: dict[str, _Anchor] = {}
        if select is not None:
            problem = selection_problem(select)
            if problem is not None:
                param, message = problem
                raise StepError(ErrorCode.VALIDATION_FAILED, f"'{param}' {message}",
                                {"param": param})
            if self._run.toolkit().locate_all is None:
                raise StepError(ErrorCode.CAPABILITY_MISSING,
                                "this vision toolkit cannot find every match of an image "
                                "(pick, nth, after, before)")
            anchors = self._anchors(select)
        # Fail early on a bad region or window (unless a wait polls for the window).
        try:
            self._run.search_area(region, self._run.window_frame(window))
        except StepError as exc:
            if not (missing_window_ok and exc.info.code == ErrorCode.WINDOW_NOT_FOUND):
                raise
        return _Search(image=image, template=template, threshold=float(threshold),
                       scale=float(scale), region=region, window=window,
                       capture_scale=self._run.capture_scale(image), select=select,
                       anchors=anchors)

    def _anchors(self, select: Selection) -> dict[str, _Anchor]:
        """The anchors of ``select``: images (with their template, loaded now) and
        stored results (checked now, placed on every attempt)."""
        anchors: dict[str, _Anchor] = {}
        for name in ANCHORS:
            value = getattr(select, name)
            if value is None:
                continue
            if isinstance(value, str):
                template = self._run.load_template(value)
                anchors[name] = _Anchor(image=value, template=template,
                                        capture_scale=self._run.capture_scale(value))
            elif isinstance(value, Mapping):
                read_stored(value, name)
                anchors[name] = _Anchor(stored=value)
            else:
                raise StepError(ErrorCode.VALIDATION_FAILED,
                                f"'{name}' must be an image or an image result kept with "
                                "'as'", {"param": name})
        return anchors

    def _attempt(self, search: _Search, attempt: int, *,
                 failing: Callable[[Match], bool] | None = None) -> Match:
        """One search. ``failing(match)`` tells whether this attempt fails its wait, so
        its capture is saved under the ``on_failure`` policy."""
        run = self._run
        toolkit = run.toolkit()
        clock = run.clock
        began = clock.now()
        # Looked up on every attempt, so a wait follows a window that moves.
        frame = run.window_frame(search.window)
        area = run.search_area(search.region, frame)
        try:
            capture = toolkit.capturer.grab(area)
        except ValueError as exc:  # e.g. a region entirely outside the virtual desktop
            raise StepError(ErrorCode.VALIDATION_FAILED, f"cannot search there: {exc}",
                            {"param": "region"}) from exc
        # The display scale where the search looks: the area's centre, else the primary
        # monitor's (a search of the whole desktop).
        display_scale = run.display.at(None if area is None else area.center)
        ratio = display_scale / search.capture_scale
        scale = search.scale * ratio
        if search.select is None:
            match = toolkit.locate(search.template, capture, search.threshold, scale)
        else:
            match = self._choose(search, capture, scale, display_scale, attempt)
        match = dataclasses.replace(match, display_ratio=ratio)
        if frame is not None:
            match = dataclasses.replace(match, frame=frame)
        elapsed_ms = max(0, round((clock.now() - began) * 1000))
        fails = failing is not None and failing(match)
        screenshot: str | None = None
        if run.captures == "all" or fails:
            screenshot = run.save(capture, self._step_id, attempt, match.bbox, self._log)
        if fails:
            search.failed_capture = screenshot
        if screenshot is not None:
            match = dataclasses.replace(match, capture=screenshot)
        if area is None:
            area = capture.rect
        run.emit_search(ImageSearchEvent(
            template=search.image, threshold=search.threshold, found=match.found,
            best_score=round(float(match.score), 4),
            bbox=None if match.bbox is None else rect_list(match.bbox),
            region=rect_list(area), scale=round(scale, 4),
            display_scale=round(display_scale, 4),
            capture_scale=round(search.capture_scale, 4),
            attempt=attempt, elapsed_ms=elapsed_ms,
            screenshot=screenshot))
        return match


    def _choose(self, search: _Search, capture: Frame, scale: float,
                display_scale: float, attempt: int) -> Match:
        """The match ``search.select`` chooses among every match in ``capture``, with
        ``matches`` (all of them, in reading order) and, when none is chosen, a ``note``
        saying why. Image anchors are searched in the same capture."""
        run = self._run
        toolkit = run.toolkit()
        assert search.select is not None and toolkit.locate_all is not None
        ordered = reading_order(
            [m for m in toolkit.locate_all(search.template, capture, search.threshold, scale)
             if m.bbox is not None])
        places: dict[str, Rect | None] = {}
        missing: str | None = None
        for name, anchor in search.anchors.items():
            if anchor.stored is not None:
                stored = read_stored(anchor.stored, name)
                places[name], _ = locate_stored(
                    stored, lambda title: run.window_rect({"title": title}), name)
                continue
            assert anchor.image is not None and anchor.template is not None
            anchor_scale = search.scale * display_scale / anchor.capture_scale
            found = self._locate_anchor(anchor, capture, search.threshold, anchor_scale,
                                        display_scale, attempt)
            places[name] = found.bbox if found.found else None
            if not found.found and missing is None:
                missing = (f"{name} {anchor.image} not found (best score "
                           f"{found.score:.3f})")
        after, before = places.get("after"), places.get("before")
        chosen = None if missing is not None else choose(
            ordered, search.select, after=after, before=before)
        if chosen is not None:
            return dataclasses.replace(chosen, matches=tuple(ordered))
        # The best location, for the event and messages (below the threshold too).
        best = max(ordered, key=lambda m: m.score) if ordered else \
            toolkit.locate(search.template, capture, search.threshold, scale)
        note = missing
        if note is None and ordered:
            note = none_chosen(len(ordered), len(eligible(ordered, after, before)),
                               search.select)
        return Match(found=False, score=best.score, bbox=best.bbox,
                     elapsed_ms=best.elapsed_ms, matches=tuple(ordered), note=note)

    def _locate_anchor(self, anchor: _Anchor, capture: Frame, threshold: float,
                       scale: float, display_scale: float, attempt: int) -> Match:
        """One search of an image anchor in the target's capture, with its
        ``image_search`` event."""
        run = self._run
        assert anchor.image is not None and anchor.template is not None
        began = run.clock.now()
        match = run.toolkit().locate(anchor.template, capture, threshold, scale)
        run.emit_search(ImageSearchEvent(
            template=anchor.image, threshold=threshold, found=match.found,
            best_score=round(float(match.score), 4),
            bbox=None if match.bbox is None else rect_list(match.bbox),
            region=rect_list(capture.rect), scale=round(scale, 4),
            display_scale=round(display_scale, 4),
            capture_scale=round(anchor.capture_scale, 4),
            attempt=attempt, elapsed_ms=max(0, round((run.clock.now() - began) * 1000))))
        return match


class _Anchor:
    """An ``after`` or ``before`` anchor: an image (with its template) or a stored
    result."""

    __slots__ = ("capture_scale", "image", "stored", "template")

    def __init__(self, *, image: str | None = None, template: Template | None = None,
                 capture_scale: float = 1.0, stored: Mapping[str, Any] | None = None) -> None:
        self.image = image
        self.template = template
        self.capture_scale = capture_scale
        self.stored = stored


class _Final:
    """Whether a poll is its wait's last one (its time is up), decided once per poll."""

    __slots__ = ("_check", "_value")

    def __init__(self, check: Callable[[], bool]) -> None:
        self._check = check
        self._value: bool | None = None

    def __call__(self) -> bool:
        if self._value is None:
            self._value = self._check()
        return self._value


class _WindowPolls:
    """The searches of an image wait whose window may be missing on some polls."""

    __slots__ = ("last", "missing", "tolerate")

    def __init__(self, *, tolerate: bool) -> None:
        self.tolerate = tolerate
        """Whether a missing window is "not found yet" (a wait with a timeout)."""
        self.last: Match | None = None
        """The match of the last poll that searched."""
        self.missing: StepError | None = None
        """The last ``window_not_found`` of a poll."""

    def run(self, search: Callable[[], Match]) -> Match | None:
        """The match of one poll, or None when its window is missing."""
        try:
            match = search()
        except StepError as exc:
            if not self.tolerate or exc.info.code != ErrorCode.WINDOW_NOT_FOUND:
                raise
            self.missing = exc
            return None
        self.last = match
        return match

    def never_seen(self, timeout: float) -> StepError | None:
        """``window_not_found`` with ``details.timeout`` when no poll found the window,
        else None."""
        if self.last is not None or self.missing is None:
            return None
        info = self.missing.info
        return StepError(ErrorCode.WINDOW_NOT_FOUND,
                         f"{info.message} (waited {timeout:g}s)",
                         {**(info.details or {}), "timeout": timeout})


class _Search:
    """One find or wait: its resolved inputs and the capture of its failing attempt."""

    __slots__ = ("anchors", "capture_scale", "failed_capture", "image", "region", "scale",
                 "select", "template", "threshold", "window")

    def __init__(self, *, image: str, template: Template, threshold: float, scale: float,
                 region: Any, window: Any, capture_scale: float = 1.0,
                 select: Selection | None = None,
                 anchors: dict[str, _Anchor] | None = None) -> None:
        self.select = select
        self.anchors = anchors or {}
        self.image = image
        self.capture_scale = capture_scale
        self.template = template
        self.threshold = threshold
        self.scale = scale
        self.region = region
        self.window = window
        self.failed_capture: str | None = None
