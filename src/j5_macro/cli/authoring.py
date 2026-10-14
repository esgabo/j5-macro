"""``j5 windows``, ``j5 screenshot``, ``j5 capture``, ``j5 pick`` and ``j5 find``: the
engine's authoring tools on the command line (docs/cli.md, docs/contract.md "Authoring
tools").

By default each command prints something ready to paste: the YAML of a step for
``capture``, ``pick`` and ``find``, the file's path for ``screenshot``, one window per
line for ``windows``. ``--json`` prints the engine's result instead. Notes and hints go
to standard error.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from j5_macro.cli.commands import CliError, ExitCode, Output, engine, load
from j5_macro.cli.render import format_problem, plural
from j5_macro.client import EngineClient, EngineError

DEFAULT_THRESHOLD = 0.9
_PLAIN = re.compile(r"^[A-Za-z0-9_./\\][A-Za-z0-9_./\\ ()-]*$")
_RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "y", "n", "~"}
_NUMBER = re.compile(r"^[-+]?(\d[\d_]*)?\.?\d*([eE][-+]?\d+)?$")


# --- YAML of a step ---------------------------------------------------------------------


def yaml_scalar(value: Any) -> str:
    """A value in YAML flow style: plain when it reads back the same, else quoted."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{k}: {yaml_scalar(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list | tuple):
        return "[" + ", ".join(yaml_scalar(v) for v in value) + "]"
    text = str(value)
    plain = (_PLAIN.match(text) is not None and text == text.strip()
             and text.lower() not in _RESERVED and not _NUMBER.match(text))
    return text if plain else json.dumps(text, ensure_ascii=False)


def step_yaml(step_type: str, params: Mapping[str, Any]) -> str:
    """``click: {image: img/save.png}``."""
    return f"{step_type}: {yaml_scalar(dict(params))}"


def parse_rect(text: str) -> list[int]:
    """``x,y,w,h`` (commas or spaces) as four integers; ValueError otherwise."""
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if len(parts) != 4:
        raise ValueError(f"expected x,y,width,height, got {text!r}")
    try:
        values = [int(p) for p in parts]
    except ValueError:
        raise ValueError(f"expected four whole numbers x,y,width,height, got {text!r}") \
            from None
    if values[2] <= 0 or values[3] <= 0:
        raise ValueError(f"the width and height must be positive, got {text!r}")
    return values


def rect_text(rect: Any) -> str:
    return "[" + ", ".join(str(v) for v in rect) + "]" if isinstance(rect, list) else "?"


# --- engine calls -----------------------------------------------------------------------


async def call(client: EngineClient, command: str, payload: Mapping[str, Any], *,
               interactive: bool = False) -> dict[str, Any]:
    """The engine's result; a cancelled overlay exits with code 3, other errors with 1."""
    try:
        return await client.call(command, payload, timeout=None if interactive else 60.0)
    except EngineError as exc:
        details = exc.error.details or {}
        if details.get("reason") == "cancelled":
            raise CliError("cancelled", ExitCode.STOPPED) from exc
        raise CliError(f"{command} failed: [{exc.code}] {exc.error.message}") from exc


async def load_script(client: EngineClient, script: str, io: Output) -> str:
    """Load ``script``; its problems are printed and fail the command."""
    outcome = await load(client, script)
    if outcome.script_id is None:
        for error in outcome.errors:
            io.note(format_problem(script, error, io.err_style))
        raise CliError(f"{script} is not valid ({plural(len(outcome.errors), 'error')}); "
                       "fix it first (j5 validate shows the problems)")
    return outcome.script_id


def window_payload(title: str | None) -> dict[str, Any]:
    return {} if title is None else {"window": {"title": title}}


def print_json(io: Output, result: Mapping[str, Any]) -> None:
    io.line(json.dumps(result, indent=2, ensure_ascii=False))


# --- j5 windows -------------------------------------------------------------------------


async def windows(cmd: Sequence[str], *, as_json: bool, io: Output) -> int:
    async with engine(cmd, io) as client:
        result = await call(client, "list_windows", {})
    if as_json:
        print_json(io, result)
        return ExitCode.OK
    found = [w for w in result.get("windows") or [] if isinstance(w, dict)]
    if not found:
        io.note("j5: no visible window with a title")
    for window in found:
        io.line(f"{rect_text(window.get('bbox')):<24} {window.get('title')}")
    return ExitCode.OK


# --- j5 screenshot ----------------------------------------------------------------------


async def screenshot(cmd: Sequence[str], *, output: str | None, window: str | None,
                     region: list[int] | None, as_json: bool, io: Output) -> int:
    payload: dict[str, Any] = window_payload(window)
    if output is not None:
        payload["path"] = os.path.abspath(output)
    if region is not None:
        payload["region"] = region
    async with engine(cmd, io) as client:
        result = await call(client, "screenshot", payload)
    if as_json:
        print_json(io, result)
        return ExitCode.OK
    io.line(str(result.get("path")))
    where = f"window {result['window']!r} at {rect_text(result.get('window_bbox'))}" \
        if result.get("window") else "the desktop"
    io.note(f"j5: {rect_text(result.get('bbox'))} of {where}, display scale "
            f"{result.get('display_scale', 1):g}")
    return ExitCode.OK


# --- j5 capture -------------------------------------------------------------------------


@dataclass(slots=True)
class CaptureOptions:
    output: str | None = None
    embed: str | None = None
    name: str | None = None
    window: str | None = None
    bbox: list[int] | None = None
    padding: int = 0
    transparent: bool = False
    force: bool = False
    as_json: bool = False


async def capture(cmd: Sequence[str], options: CaptureOptions, *, io: Output) -> int:
    if (options.embed is None) == (options.output is None):
        raise CliError("give either -o FILE or --embed SCRIPT --name NAME", ExitCode.USAGE)
    if (options.embed is None) != (options.name is None):
        raise CliError("--embed and --name go together", ExitCode.USAGE)
    payload: dict[str, Any] = window_payload(options.window)
    payload.update(overwrite=options.force, padding=options.padding,
                   transparent=options.transparent)
    if options.bbox is not None:
        payload["bbox"] = options.bbox
    async with engine(cmd, io) as client:
        if options.embed is not None:
            payload["script_id"] = await load_script(client, options.embed, io)
            payload["name"] = options.name
        else:
            assert options.output is not None
            payload["path"] = os.path.abspath(options.output)
        if options.bbox is None:
            io.note("j5: drag a rectangle on the screen (Escape cancels)")
        result = await call(client, "capture_region", payload,
                            interactive=options.bbox is None)
    if options.as_json:
        print_json(io, result)
        return ExitCode.OK
    for warning in result.get("warnings") or []:
        if isinstance(warning, dict):
            io.note(f"j5: warning: {warning.get('message')}")
    scale = result.get("display_scale", 1)
    if options.embed is not None:
        image = f"#{result.get('image_name')}"
        note = f"j5: embedded {rect_text(result.get('bbox'))} as {image} in {options.embed}"
        if result.get("scale") is not None:
            note += f", recorded at display scale {result['scale']:g}"
        io.note(note)
    else:
        image = options.output or ""
        io.note(f"j5: saved {rect_text(result.get('bbox'))} to {result.get('path')}")
        if isinstance(scale, int | float) and scale != 1:
            io.note(f"j5: hint: captured at display scale {scale:g}; a script using this "
                    f"file needs capture_scale: {scale:g} (embedding with --embed records "
                    "the scale per image)")
    params: dict[str, Any] = {"image": image}
    if result.get("window"):
        params["window"] = {"title": result["window"]}
    io.line(step_yaml("click", params))
    return ExitCode.OK


# --- j5 pick ----------------------------------------------------------------------------


async def pick(cmd: Sequence[str], *, window: str | None, as_json: bool, io: Output) -> int:
    async with engine(cmd, io) as client:
        io.note("j5: click a point on the screen (Escape cancels)")
        result = await call(client, "pick_point", window_payload(window), interactive=True)
    if as_json:
        print_json(io, result)
        return ExitCode.OK
    point = result.get("window_point")
    if result.get("window") and isinstance(point, list) and len(point) == 2:
        io.note(f"j5: ({result.get('x')}, {result.get('y')}) on the desktop, "
                f"({point[0]}, {point[1]}) in {result['window']!r}")
        io.line(step_yaml("click", {"window": {"title": result["window"]}, "x": point[0],
                                    "y": point[1]}))
    else:
        io.line(step_yaml("click", {"x": result.get("x"), "y": result.get("y")}))
    return ExitCode.OK


# --- j5 find ----------------------------------------------------------------------------


@dataclass(slots=True)
class FindOptions:
    script: str | None = None
    window: str | None = None
    region: list[int] | None = None
    threshold: float = DEFAULT_THRESHOLD
    scale: float = 1.0
    display_scale: float | None = None
    show: bool = False
    show_ms: int = 3000
    as_json: bool = False


async def find(cmd: Sequence[str], image: str, options: FindOptions, *, io: Output) -> int:
    if image.startswith("#") and options.script is None:
        raise CliError(f"{image} is an embedded image: give its script with --script",
                       ExitCode.USAGE)
    payload: dict[str, Any] = window_payload(options.window)
    payload.update(threshold=options.threshold, scale=options.scale, show=options.show,
                   show_ms=options.show_ms)
    if options.region is not None:
        payload["region"] = options.region
    if options.display_scale is not None:
        payload["display_scale"] = options.display_scale
    async with engine(cmd, io) as client:
        if options.script is not None:
            payload["script_id"] = await load_script(client, options.script, io)
            # Relative to the script's directory, as the steps of the script see it.
            payload["image"] = image
        else:
            payload["image"] = image if image.startswith("#") else os.path.abspath(image)
        result = await call(client, "find_image", payload, interactive=options.show)
    found = bool(result.get("found"))
    if options.as_json:
        print_json(io, result)
        return ExitCode.OK if found else ExitCode.FAILED
    score = result.get("score", 0)
    where = rect_text(result.get("bbox")) if result.get("bbox") else "nowhere (too large)"
    if result.get("window") and result.get("window_bbox"):
        where += f" ({rect_text(result['window_bbox'])} in {result['window']!r})"
    scales = (f"scale {result.get('scale', 1):g} = display {result.get('display_scale', 1):g}"
              f" / capture {result.get('capture_scale', 1):g}")
    if not found:
        io.note(f"j5: {image} not found: best score {score:.3f} at {where}, threshold "
                f"{options.threshold:g}; {scales}")
        return ExitCode.FAILED
    io.note(f"j5: {image} found at {where}, score {score:.3f}; {scales}")
    params: dict[str, Any] = {"image": image}
    if options.window is not None:
        params["window"] = {"title": options.window}
    if options.region is not None:
        params["region"] = options.region
    if options.threshold != DEFAULT_THRESHOLD:
        params["threshold"] = options.threshold
    if options.scale != 1.0:
        params["scale"] = options.scale
    io.line(step_yaml("click", params))
    return ExitCode.OK
