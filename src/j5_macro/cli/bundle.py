"""``j5 pack`` and ``j5 unpack``: embed the image files a script references in its
``images`` section, and write them back to files (docs/cli.md).

Both work without an engine. The work is in :mod:`j5_macro.script.bundle` (shared with
the desktop application); this module loads the step registry (plugins included),
prints the problems and the summary, and turns failures into :class:`CliError`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TextIO, TypeVar

from j5_macro.cli.commands import CliError, ExitCode
from j5_macro.cli.render import PLAIN, Style, format_problem, plural
from j5_macro.script.bundle import (
    IMAGES_DIR,
    BundleError,
    PackPlan,
    image_name,
    pack_script,
    plan_pack,
    unpack_script,
    write_text,
)
from j5_macro.steps.registry import StepRegistry

__all__ = ["IMAGES_DIR", "PackPlan", "image_name", "pack", "plan_pack", "unpack"]

T = TypeVar("T")


def _registry(err: TextIO) -> StepRegistry:
    from j5_macro.steps.builtins import registry_with_plugins

    registry, report = registry_with_plugins()
    for problem in report.skipped:
        print(f"j5: warning: {problem}", file=err)
    return registry


def _run(script: str, work: Callable[[], T], err: TextIO, style: Style) -> T:
    """``work()``, its :class:`BundleError` printed (the script's problems first) and
    raised as :class:`CliError`."""
    try:
        return work()
    except BundleError as exc:
        for error in exc.errors:
            print(format_problem(script, error, style), file=err)
        raise CliError(exc.message) from exc


def pack(script: str, output: str | None, *, out: TextIO, err: TextIO,
         style: Style = PLAIN) -> int:
    """``j5 pack``: write ``script`` with its image files embedded to ``output``
    (standard output when None)."""
    registry = _registry(err)
    packed = _run(script, lambda: pack_script(script, registry, output=output), err, style)
    for note in packed.plan.skipped:
        print(f"j5: warning: {note}", file=err)
    if output is None:
        out.write(packed.text)
    else:
        _run(script, lambda: write_text(Path(output), packed.text), err, style)
    names = ", ".join(f"#{name}" for name in packed.plan.added)
    summary = (f"embedded {plural(len(packed.plan.added), 'image')} ({names})"
               if packed.plan.added else "no new image to embed")
    destination = f"; wrote {output}" if output is not None else ""
    print(f"j5: {summary}, {plural(packed.rewritten, 'image parameter')} rewritten"
          f"{destination}", file=err)
    return ExitCode.OK


def unpack(bundle: str, directory: str, *, force: bool, err: TextIO,
           style: Style = PLAIN) -> int:
    """``j5 unpack``: write ``bundle``'s embedded images to ``directory/img/<name>.png``
    and the script, referring to them by path, to ``directory/<bundle file name>``."""
    registry = _registry(err)
    result = _run(bundle, lambda: unpack_script(bundle, directory, registry, force=force),
                  err, style)
    for warning in result.warnings:
        print(f"j5: warning: {warning}", file=err)
    print(f"j5: wrote {plural(len(result.images), 'image')} to "
          f"{Path(directory) / IMAGES_DIR} and the script to {result.script_path}",
          file=err)
    return ExitCode.OK
