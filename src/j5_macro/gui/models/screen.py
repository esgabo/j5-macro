"""The screen tools' logic, without Qt: where a step holds an image or a point, the
``edit_script`` edit that writes a new one, names for new templates, the arguments of a
``find_image`` that searches as the step would, and messages for the tools' errors.

The tools themselves (``capture_region``, ``pick_point``, ``find_image``,
``screenshot``, ``list_windows``, ``get_image``) are engine commands (docs/contract.md,
"Authoring tools"); the widgets in :mod:`j5_macro.gui.views.screen_dialogs` send them
and use these functions to read their results.

A *target* is one place in a step's parameters that the tools act on:

- the step's own parameters (``param`` None): ``click``, ``move``, ``wait_image_in``
  and ``wait_image_out`` (``image``, ``window``, ``region``, ``threshold``, ``scale``;
  ``x`` and ``y`` for ``click`` and ``move``);
- an endpoint of ``drag`` (``param`` ``from`` or ``to``), a mapping with the same keys;
- a condition's ``image_visible`` (``param`` ``when`` of ``if``, ``while`` of ``loop``),
  either ``"img.png"`` or a mapping with ``image``, ``threshold``, ``region``...
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from j5_macro.gui.models.steps import StepItem

REFERENCE_PREFIX = "#"
"""``image: "#name"`` names an entry of the script's ``images``."""
NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
"""A valid entry name of ``images`` (docs/contract.md, embedded images)."""
DEFAULT_THRESHOLD = 0.9

IMAGE_STEP_TYPES = frozenset({"click", "move", "wait_image_in", "wait_image_out"})
"""Step types whose own parameters hold an image target."""
POINT_STEP_TYPES = frozenset({"click", "move"})
"""Step types whose own parameters hold a point (``x``, ``y``, ``window``)."""
ENDPOINT_PARAMS: Mapping[str, tuple[str, ...]] = {"drag": ("from", "to")}
"""Step types with endpoint mappings, each an image or a point target."""
CONDITION_PARAMS: Mapping[str, str] = {"if": "when", "loop": "while"}
"""Step types with a condition that may test ``image_visible``."""
CONDITION_KEY = "image_visible"
LOCATION_KEYS = ("image", "x", "y", "at")
"""The keys that say where a target is; picking a point replaces all of them."""


@dataclass(frozen=True, slots=True)
class ImageTarget:
    """An image a step searches for, with the options of the search."""

    param: str | None
    """None for the step's own parameters, else the parameter holding the target
    (``from``, ``to``, ``when``, ``while``)."""
    image: str | None
    window: str | None = None
    """The ``window.title`` the search is relative to."""
    region: tuple[int, int, int, int] | None = None
    threshold: float | None = None
    scale: float | None = None
    region_text: str | None = None
    """A ``region`` that is not four numbers (an expression...): the search cannot be
    made outside a run."""
    condition_shorthand: bool = False
    """``image_visible: "img.png"``: the condition holds only the image."""
    has_point: bool = False
    """The target is a point now (``x``, ``y`` or ``at``); a new image replaces it."""

    @property
    def label(self) -> str:
        """``image``, ``from.image``, ``when.image_visible``."""
        if self.param is None:
            return "image"
        if self.param in CONDITION_PARAMS.values():
            return f"{self.param}.{CONDITION_KEY}"
        return f"{self.param}.image"

    @property
    def interpolated(self) -> bool:
        """The image is computed at run time (``${...}``)."""
        return self.image is not None and "${" in self.image


@dataclass(frozen=True, slots=True)
class PointTarget:
    """A point a step clicks or moves to: ``x``, ``y`` and the window they are
    relative to."""

    param: str | None
    x: int | None = None
    y: int | None = None
    window: str | None = None
    has_image: bool = False
    """The target is an image now; picking a point replaces it."""

    @property
    def label(self) -> str:
        return "point" if self.param is None else self.param


@dataclass(frozen=True, slots=True)
class FindArgs:
    """The arguments of a ``find_image`` that searches as the step would."""

    image: str
    window: str | None = None
    region: tuple[int, int, int, int] | None = None
    threshold: float | None = None
    scale: float | None = None

    def kwargs(self) -> dict[str, Any]:
        """For :meth:`j5_macro.gui.screen_tools.ScreenTools.find_image` (with
        ``image`` as the first argument)."""
        return {"window": self.window, "region": self.region, "threshold": self.threshold,
                "scale": self.scale}


# --- reading targets --------------------------------------------------------------------


def image_targets(step: StepItem) -> list[ImageTarget]:
    """The image targets of a step, in parameter order."""
    params = step.params
    targets: list[ImageTarget] = []
    if step.type in IMAGE_STEP_TYPES or isinstance(params.get("image"), str):
        targets.append(_image_target(None, params))
    for name in ENDPOINT_PARAMS.get(step.type, ()):
        value = params.get(name)
        if isinstance(value, Mapping) and not _is_point(value):
            targets.append(_image_target(name, value))
    condition = CONDITION_PARAMS.get(step.type)
    if condition is not None:
        value = params.get(condition)
        if isinstance(value, Mapping) and CONDITION_KEY in value:
            inner = value[CONDITION_KEY]
            if isinstance(inner, str):
                targets.append(ImageTarget(param=condition, image=inner,
                                           condition_shorthand=True))
            elif isinstance(inner, Mapping):
                targets.append(_image_target(condition, inner))
    return targets


def point_targets(step: StepItem) -> list[PointTarget]:
    """The point targets of a step: ``click`` and ``move`` (even while they target an
    image: a pick replaces it) and ``drag``'s endpoints."""
    targets: list[PointTarget] = []
    if step.type in POINT_STEP_TYPES:
        targets.append(_point_target(None, step.params))
    for name in ENDPOINT_PARAMS.get(step.type, ()):
        value = step.params.get(name)
        targets.append(_point_target(name, value if isinstance(value, Mapping) else {}))
    return targets


def image_target(values: Mapping[str, Any], param: str | None = None) -> ImageTarget:
    """The image target of a mapping shaped like a step's parameters (``image``,
    ``window: {title}``, ``region``, ``threshold``, ``scale``)."""
    return _image_target(param, values)


def point_target(values: Mapping[str, Any], param: str | None = None) -> PointTarget:
    """The point target of a mapping shaped like a step's parameters (``x``, ``y``,
    ``window: {title}``, ``image``)."""
    return _point_target(param, values)


def find_target(targets: Sequence[ImageTarget], param: str | None) -> ImageTarget | None:
    return next((target for target in targets if target.param == param), None)


def _image_target(param: str | None, values: Mapping[str, Any]) -> ImageTarget:
    image = values.get("image")
    region = values.get("region")
    rect = _rect(region)
    return ImageTarget(
        param=param,
        image=image if isinstance(image, str) else None,
        window=_window_title(values.get("window")),
        region=rect,
        threshold=_number(values.get("threshold")),
        scale=_number(values.get("scale")),
        region_text=None if region is None or rect is not None else str(region),
        has_point=any(key in values for key in ("x", "y", "at")),
    )


def _point_target(param: str | None, values: Mapping[str, Any]) -> PointTarget:
    x, y = values.get("x"), values.get("y")
    return PointTarget(param=param,
                       x=x if isinstance(x, int) and not isinstance(x, bool) else None,
                       y=y if isinstance(y, int) and not isinstance(y, bool) else None,
                       window=_window_title(values.get("window")),
                       has_image=isinstance(values.get("image"), str))


def _is_point(values: Mapping[str, Any]) -> bool:
    return "image" not in values and ("x" in values or "y" in values)


def _window_title(value: Any) -> str | None:
    if isinstance(value, Mapping) and isinstance(value.get("title"), str):
        return str(value["title"])
    if isinstance(value, str):
        return value
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _rect(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != 4:
        return None
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        return None
    return (int(value[0]), int(value[1]), int(value[2]), int(value[3]))


# --- searching like the step ------------------------------------------------------------


def find_args(target: ImageTarget) -> FindArgs:
    """The ``find_image`` arguments for ``target``; ValueError (with a message for
    people) when the search cannot be made outside a run."""
    if not target.image:
        raise ValueError("the step has no image to search for")
    if target.interpolated:
        raise ValueError(f"the image {target.image!r} is computed at run time; run the "
                         "script (or a dry run) to test it")
    if target.window is not None and "${" in target.window:
        raise ValueError(f"the window title {target.window!r} is computed at run time; "
                         "run the script (or a dry run) to test it")
    if target.region_text is not None:
        raise ValueError(f"the region {target.region_text!r} is computed at run time; "
                         "run the script (or a dry run) to test it")
    return FindArgs(image=target.image, window=target.window, region=target.region,
                    threshold=target.threshold, scale=target.scale)


@dataclass(frozen=True, slots=True)
class FindOutcome:
    """A ``find_image`` result, read."""

    found: bool
    score: float | None
    threshold: float
    bbox: tuple[int, int, int, int] | None
    """The best location on the desktop; None when the template does not fit."""
    region: tuple[int, int, int, int] | None
    """The searched rectangle on the desktop."""
    window: str | None = None
    window_bbox: tuple[int, int, int, int] | None = None
    scale: float | None = None
    display_scale: float | None = None
    capture_scale: float | None = None
    elapsed_ms: int | None = None

    @classmethod
    def from_result(cls, result: Mapping[str, Any],
                    threshold: float | None = None) -> FindOutcome:
        window = result.get("window")
        elapsed = result.get("elapsed_ms")
        return cls(found=result.get("found") is True,
                   score=_number(result.get("score")),
                   threshold=DEFAULT_THRESHOLD if threshold is None else threshold,
                   bbox=_rect(result.get("bbox")),
                   region=_rect(result.get("region")),
                   window=window if isinstance(window, str) else None,
                   window_bbox=_rect(result.get("window_bbox")),
                   scale=_number(result.get("scale")),
                   display_scale=_number(result.get("display_scale")),
                   capture_scale=_number(result.get("capture_scale")),
                   elapsed_ms=elapsed if isinstance(elapsed, int) else None)

    @property
    def summary(self) -> str:
        """``Found: score 0.987 (threshold 0.9)``."""
        score = "-" if self.score is None else f"{self.score:.3f}"
        verdict = "Found" if self.found else "Not found"
        best = "score" if self.found else "best score"
        return f"{verdict}: {best} {score} (threshold {self.threshold:g})"

    def details(self) -> list[str]:
        """Lines for people: where, at which scale, how long."""
        lines: list[str] = []
        if self.bbox is not None:
            where = f"at {_rect_text(self.bbox)} on the desktop"
            if self.window is not None and self.window_bbox is not None:
                where += f", {_rect_text(self.window_bbox)} in “{self.window}”"
            lines.append(("Match " if self.found else "Best location ") + where)
        else:
            lines.append("The template is larger than the searched area")
        if self.region is not None:
            lines.append(f"Searched {_rect_text(self.region)}")
        scales = []
        if self.scale is not None:
            scales.append(f"template scale {self.scale:g}")
        if self.display_scale is not None:
            scales.append(f"display scale {self.display_scale:g}")
        if self.capture_scale is not None:
            scales.append(f"capture scale {self.capture_scale:g}")
        if scales:
            lines.append(", ".join(scales).capitalize())
        if self.elapsed_ms is not None:
            lines.append(f"{self.elapsed_ms} ms")
        return lines


def rect_within(rect: Sequence[int], outer: Sequence[int]) -> tuple[int, int, int, int]:
    """``rect`` (on the desktop) relative to the top left corner of ``outer``: where a
    match lies in a screenshot or capture of ``outer``."""
    return (rect[0] - outer[0], rect[1] - outer[1], rect[2], rect[3])


def _rect_text(rect: Sequence[int]) -> str:
    return "[" + ", ".join(str(v) for v in rect) + "]"


# --- writing targets --------------------------------------------------------------------


def target_edit(step: StepItem, param: str | None, values: Mapping[str, Any],
                *, shorthand_key: str | None = None) -> dict[str, Any]:
    """The ``edit_script`` ``update`` that sets ``values`` in a target (a value None
    removes that key). For a target inside a parameter the whole parameter is
    rewritten, merged with what it holds. ``shorthand_key`` is the key of a condition's
    ``image_visible`` written as a bare string (``image``)."""
    if param is None:
        return {"op": "update", "step_id": step.id, "params": dict(values)}
    current = step.params.get(param)
    merged: dict[str, Any] = dict(current) if isinstance(current, Mapping) else {}
    if param in CONDITION_PARAMS.values():
        inner_raw = merged.get(CONDITION_KEY)
        if isinstance(inner_raw, str) and set(values) <= {shorthand_key or "image"}:
            merged[CONDITION_KEY] = values.get(shorthand_key or "image")
        else:
            inner: dict[str, Any] = dict(inner_raw) if isinstance(inner_raw, Mapping) else (
                {"image": inner_raw} if isinstance(inner_raw, str) else {})
            _merge(inner, values)
            merged[CONDITION_KEY] = inner
    else:
        _merge(merged, values)
    return {"op": "update", "step_id": step.id, "params": {param: merged}}


def _merge(into: dict[str, Any], values: Mapping[str, Any]) -> None:
    for key, value in values.items():
        if value is None:
            into.pop(key, None)
        else:
            into[key] = value


def image_values(target: ImageTarget | None, image: str) -> dict[str, Any]:
    """The parameters that make a target search for ``image``: the keys that located
    it another way (``x``, ``y``, ``at``) are removed."""
    values: dict[str, Any] = {"image": image}
    if target is not None and target.has_point:
        values.update(x=None, y=None, at=None)
    return values


def pick_values(result: Mapping[str, Any], target: PointTarget | None = None, *,
                use_window: bool = True) -> dict[str, Any]:
    """The parameters a ``pick_point`` result gives a point target: ``x`` and ``y``
    relative to the window the point is in (with ``window: {title}``), or on the
    desktop (``window`` removed) without one or with ``use_window`` False. The keys that
    located the target another way (``image``, ``at``) are removed."""
    window = result.get("window")
    point = result.get("window_point")
    values: dict[str, Any] = {}
    if use_window and isinstance(window, str) and isinstance(point, Sequence) \
            and len(point) == 2:
        values.update(x=int(point[0]), y=int(point[1]), window={"title": window})
    else:
        values.update(x=int(result["x"]), y=int(result["y"]), window=None)
    if target is None or target.has_image:
        values.update(image=None)
    values.setdefault("at", None)
    return values


def describe_pick(result: Mapping[str, Any], *, use_window: bool = True) -> str:
    """``(120, 40) in “Notes” ((620, 340) on the desktop)``."""
    desktop = f"({result.get('x')}, {result.get('y')})"
    window = result.get("window")
    point = result.get("window_point")
    if use_window and isinstance(window, str) and isinstance(point, Sequence) \
            and len(point) == 2:
        return f"({point[0]}, {point[1]}) in “{window}” ({desktop} on the desktop)"
    return f"{desktop} on the desktop"


# --- naming new templates ---------------------------------------------------------------


def capture_name(step_id: str | None, existing: Sequence[str] = ()) -> str:
    """A valid, unused ``images`` entry name for a template captured for a step: the
    step id, made valid (``click_1``, ``save-button``), with ``_2``, ``_3``... when
    it is taken."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", step_id or "").strip("_-") or "image"
    if not NAME_PATTERN.match(base):
        base = f"image_{base}"
    return _unique(base, set(existing), lambda stem, n: f"{stem}_{n}")


def capture_file(step_id: str | None, existing: Sequence[str] = ()) -> str:
    """A PNG file name for a template saved next to the script: ``<step id>.png``,
    then ``<step id>_2.png``...; ``existing`` are the names taken (compared without
    case, as on Windows)."""
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", step_id or "").strip("_.-") or "template"
    taken = {name.lower() for name in existing}
    name = f"{stem}.png"
    n = 2
    while name.lower() in taken:
        name = f"{stem}_{n}.png"
        n += 1
    return name


def _unique(base: str, taken: set[str], numbered: Any) -> str:
    if base not in taken:
        return base
    n = 2
    while numbered(base, n) in taken:
        n += 1
    return str(numbered(base, n))


def valid_name(name: str) -> bool:
    return NAME_PATTERN.match(name) is not None


def image_reference(name: str) -> str:
    return f"{REFERENCE_PREFIX}{name}"


def embedded_name(image: str | None) -> str | None:
    """``name`` for ``"#name"``; None for a path or an interpolated value."""
    if not image or not image.startswith(REFERENCE_PREFIX) or "${" in image:
        return None
    return image[len(REFERENCE_PREFIX):]


def file_value(path: str, script_path: str | None) -> str:
    """The ``image`` value for a template file: relative to the script's directory with
    ``/`` when it is inside it, else the absolute path."""
    if script_path is None:
        return path
    folder = os.path.dirname(os.path.abspath(script_path))
    try:
        relative = os.path.relpath(os.path.abspath(path), folder)
    except ValueError:  # another drive on Windows
        return path
    if relative.startswith(".."):
        return os.path.abspath(path)
    return relative.replace(os.sep, "/")


def resolve_file(image: str, script_path: str | None) -> str:
    """The file an ``image`` path names: relative paths are relative to the script's
    directory, as a step resolves them."""
    if os.path.isabs(image) or script_path is None:
        return image
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(script_path)),
                                         image))


# --- what went wrong --------------------------------------------------------------------


CANCELLED = "cancelled"
BUSY = "busy"
MISSING = "missing"
FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ToolProblem:
    """A failed screen tool, for people."""

    kind: str
    """``cancelled`` (the user pressed Escape: say nothing), ``busy``, ``missing`` (a
    capability, with an install hint) or ``failed``."""
    message: str
    hint: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def quiet(self) -> bool:
        return self.kind == CANCELLED

    @property
    def text(self) -> str:
        return f"{self.message}\n\n{self.hint}" if self.hint else self.message


VISION_HINT = ('Image search needs the vision extra: pip install "j5-macro[vision]" '
               "(with x11 or windows), and a graphical session.")
OVERLAY_HINT = ("The capture and pick overlay needs Python's tkinter. Python for Windows "
                "includes it; on Debian and Ubuntu install python3-tk, on Fedora "
                "python3-tkinter.")
WINDOWS_HINT = ("Window titles need an input backend that lists windows (X11 or "
                "Windows); install the x11 or windows extra.")


def tool_problem(code: str | None, message: str,
                 details: Mapping[str, Any] | None = None) -> ToolProblem:
    """Read an engine error of a screen tool (docs/contract.md, "Authoring tools")."""
    details = dict(details or {})
    if code == "invalid_state" and details.get("reason") == CANCELLED:
        return ToolProblem(CANCELLED, "Cancelled", details=details)
    if code == "busy":
        if details.get("reason") == "overlay":
            return ToolProblem(BUSY, "A capture or pick overlay is already open; finish it "
                               "or press Escape first.", details=details)
        return ToolProblem(BUSY, "A script is running. Stop it (or wait for it to end) "
                           "before capturing or picking on the screen.", details=details)
    if code == "capability_missing":
        capability = details.get("capability")
        if capability == "overlay":
            hint = OVERLAY_HINT
        elif "window" in message.lower() and "vision" not in message.lower():
            hint = WINDOWS_HINT
        else:
            hint = VISION_HINT
        return ToolProblem(MISSING, message, hint, details)
    return ToolProblem(FAILED, f"{message} ({code})" if code else message, details=details)
