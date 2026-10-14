"""``j5 pack`` and ``j5 unpack``: embed the image files a script references in its
``images`` section, and write them back to files (docs/cli.md).

Both work without an engine. They load the script with the step registry (plugins
included), refuse an invalid one, and change it with
:func:`~j5_macro.script.editor.rewrite_images`, the structured editing ``edit_script``
uses, so comments and formatting survive and no step id is added.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from j5_macro.cli.commands import CliError, ExitCode
from j5_macro.cli.render import PLAIN, Style, format_problem, plural
from j5_macro.script import images as embedded
from j5_macro.script.editor import EditError, rewrite_images
from j5_macro.script.loader import load_file, load_script
from j5_macro.script.model import Script, Step
from j5_macro.steps.registry import StepRegistry

IMAGES_DIR = "img"
"""Where ``j5 unpack`` writes the images, inside its output directory."""

_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def _registry(err: TextIO) -> StepRegistry:
    from j5_macro.steps.builtins import registry_with_plugins

    registry, report = registry_with_plugins()
    for problem in report.skipped:
        print(f"j5: warning: {problem}", file=err)
    return registry


def _load(path: str, registry: StepRegistry, out: TextIO, style: Style) -> Script:
    """The script at ``path``; its problems are printed and fail the command."""
    result = load_file(path, lookup=registry.spec, check=registry.check_params)
    if result.script is None:
        for error in result.errors:
            print(format_problem(path, error, style), file=out)
        raise CliError(f"{path} is not valid ({plural(len(result.errors), 'error')}); "
                       "fix it first (j5 validate shows the problems)")
    return result.script


def _write(path: Path, text: str) -> None:
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as exc:
        raise CliError(f"cannot write {path}: {exc.strerror or exc}") from exc


def _slots(script: Script, registry: StepRegistry) -> Iterator[tuple[Step, str, str]]:
    """``(step, param, value)`` for every image parameter of the script."""
    for step in script.walk():
        for mapping, key, param in embedded.image_slots(step.params,
                                                        registry.spec(step.type)):
            yield step, param, str(mapping[key])


def image_name(stem: str) -> str:
    """A valid entry name from a file stem: ``save button`` gives ``save_button``,
    ``1st`` gives ``_1st``."""
    name = _INVALID_NAME_CHARS.sub("_", stem).strip("_-") or "image"
    return name if embedded.NAME_PATTERN.match(name) else f"_{name}"


# --- pack -------------------------------------------------------------------------------


@dataclass(slots=True)
class PackPlan:
    """What ``j5 pack`` changes: each image value as written and the reference that
    replaces it, the new entries (as ``set_meta`` takes them) and what it leaves."""

    references: dict[str, str] = field(default_factory=dict)
    added: dict[str, bytes] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


def plan_pack(script: Script, base: Path, registry: StepRegistry) -> PackPlan:
    """Read and check every image file ``script`` references literally (paths relative
    to ``base``). Identical files share one entry, including one already embedded;
    names come from the file stems, with ``_2``, ``_3``... when taken. Raises
    :class:`CliError` for a file that cannot be embedded."""
    plan = PackPlan()
    taken = set(script.images)
    by_digest = {hashlib.sha256(image.data).hexdigest(): name
                 for name, image in script.images.items()}
    for step, param, value in _slots(script, registry):
        if value in plan.references or embedded.reference_name(value) is not None:
            continue
        if "${" in value:
            plan.skipped.append(f"step {step.id}: {param} {value!r} is built at run time; "
                                "left as it is")
            continue
        path = Path(value)
        if not path.is_absolute():
            path = base / path
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise CliError(f"step {step.id}: {param}: cannot read {value!r} ({path}): "
                           f"{exc.strerror or exc}") from exc
        try:
            embedded.check_png(data, value)
        except ValueError as exc:
            raise CliError(f"step {step.id}: {param}: {value!r} cannot be embedded: {exc}"
                           + ("; only PNG files can be embedded"
                              if not data.startswith(embedded.PNG_SIGNATURE) else "")
                           ) from exc
        digest = hashlib.sha256(data).hexdigest()
        name = by_digest.get(digest)
        if name is None:
            name = stem = image_name(Path(value).stem)
            n = 1
            while name in taken:
                n += 1
                name = f"{stem}_{n}"
            taken.add(name)
            by_digest[digest] = name
            plan.added[name] = data
        plan.references[value] = f"{embedded.REFERENCE_PREFIX}{name}"
    total = sum(len(image.data) for image in script.images.values())
    total += sum(len(data) for data in plan.added.values())
    if total > embedded.MAX_SCRIPT_BYTES:
        raise CliError(f"the embedded images would add up to {total:,} bytes, over the limit "
                       f"of {embedded.MAX_SCRIPT_BYTES:,} bytes (8 MiB) per script")
    return plan


def pack(script: str, output: str | None, *, out: TextIO, err: TextIO,
         style: Style = PLAIN) -> int:
    """``j5 pack``: write ``script`` with its image files embedded to ``output``
    (standard output when None)."""
    registry = _registry(err)
    loaded = _load(script, registry, err, style)
    plan = plan_pack(loaded, Path(script).absolute().parent, registry)
    for note in plan.skipped:
        print(f"j5: warning: {note}", file=err)
    try:
        text = rewrite_images(
            loaded.text, plan.references, lookup=registry.spec,
            add={name: {"png": embedded.encode_base64(data)}
                 for name, data in plan.added.items()})
    except EditError as exc:  # pragma: no cover - the script was just loaded
        raise CliError(f"cannot edit {script}: {exc.message}") from exc
    target = None if output is None else os.path.abspath(output)
    check = load_script(text, lookup=registry.spec, check=registry.check_params,
                        source_path=target)
    if check.script is None:  # pragma: no cover - a bug, never a user error
        raise CliError(f"packing produced an invalid script: {check.errors[0].message}")
    if output is None:
        out.write(text)
    else:
        _write(Path(output), text)
    names = ", ".join(f"#{name}" for name in plan.added)
    rewritten = sum(1 for _, _, value in _slots(loaded, registry)
                    if value in plan.references)
    summary = (f"embedded {plural(len(plan.added), 'image')} ({names})" if plan.added
               else "no new image to embed")
    destination = f"; wrote {output}" if output is not None else ""
    print(f"j5: {summary}, {plural(rewritten, 'image parameter')} rewritten"
          f"{destination}", file=err)
    return ExitCode.OK


# --- unpack -----------------------------------------------------------------------------


def unpack(bundle: str, directory: str, *, force: bool, err: TextIO,
           style: Style = PLAIN) -> int:
    """``j5 unpack``: write ``bundle``'s embedded images to ``directory/img/<name>.png``
    and the script, referring to them by path, to ``directory/<bundle file name>``."""
    registry = _registry(err)
    loaded = _load(bundle, registry, err, style)
    if not loaded.images:
        raise CliError(f"{bundle} has no embedded images")
    root = Path(directory)
    script_path = root / Path(bundle).name
    files = {name: root / IMAGES_DIR / f"{name}.png" for name in loaded.images}
    if not force:
        existing = [str(p) for p in (script_path, *files.values()) if p.exists()]
        if existing:
            raise CliError(f"would overwrite {', '.join(existing)}; use --force to "
                           "overwrite")
    built = [(step, param) for step, param, value in _slots(loaded, registry)
             if "${" in value]
    keep = bool(built)
    for step, param in built:
        print(f"j5: warning: step {step.id}: {param} is built at run time and may name "
              "an embedded image, so the images section is kept", file=err)
    if not keep:
        for image in loaded.images.values():
            if image.scale is not None:
                print(f"j5: warning: image #{image.name}: its capture scale "
                      f"{image.scale:g} cannot be kept in a file and is dropped; set "
                      "the script's capture_scale if the templates share it", file=err)
    references = {f"{embedded.REFERENCE_PREFIX}{name}": f"{IMAGES_DIR}/{path.name}"
                  for name, path in files.items()}
    try:
        text = rewrite_images(loaded.text, references, lookup=registry.spec,
                              remove_section=not keep)
    except EditError as exc:  # pragma: no cover - the script was just loaded
        raise CliError(f"cannot edit {bundle}: {exc.message}") from exc
    try:
        (root / IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CliError(f"cannot create {root / IMAGES_DIR}: {exc.strerror or exc}") from exc
    for name, path in files.items():
        try:
            path.write_bytes(loaded.images[name].data)
        except OSError as exc:
            raise CliError(f"cannot write {path}: {exc.strerror or exc}") from exc
    _write(script_path, text)
    print(f"j5: wrote {plural(len(files), 'image')} to {root / IMAGES_DIR} and the script "
          f"to {script_path}", file=err)
    return ExitCode.OK
