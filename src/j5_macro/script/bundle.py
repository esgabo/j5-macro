"""Packing and unpacking a script's images, without an engine (docs/contract.md,
"Packing").

``pack`` embeds every image file a script references literally in its ``images``
section and rewrites those parameters to ``#name``; ``unpack`` writes the entries back
to PNG files and the references back to paths. Both change the text with
:func:`~j5_macro.script.editor.rewrite_images`, the structured editing ``edit_script``
uses, so comments and formatting survive and no step id is added.

The command line (``j5 pack``, ``j5 unpack``, in :mod:`j5_macro.cli.bundle`) and the
desktop application ("Save as self-contained file", "Unpack images to files") are thin
layers over :func:`pack_script` and :func:`unpack_script`. Problems are raised as
:class:`BundleError`, with the script's own problems in ``errors`` when it is invalid.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from j5_macro.protocol.messages import ErrorInfo
from j5_macro.script import images as embedded
from j5_macro.script.editor import EditError, rewrite_images
from j5_macro.script.loader import load_file, load_script
from j5_macro.script.model import Script, Step
from j5_macro.steps.registry import StepRegistry

IMAGES_DIR = "img"
"""Where :func:`unpack_script` writes the images, inside its output directory."""

_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


class BundleError(Exception):
    """Packing or unpacking cannot go on; ``errors`` holds the script's problems when it
    is not valid."""

    def __init__(self, message: str, errors: list[ErrorInfo] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or []


def load_valid(path: str | os.PathLike[str], registry: StepRegistry) -> Script:
    """The script at ``path``; :class:`BundleError` with its problems when invalid."""
    result = load_file(str(path), lookup=registry.spec, check=registry.check_params)
    if result.script is None:
        count = len(result.errors)
        raise BundleError(f"{path} is not valid ({count} error{'s' if count != 1 else ''})"
                          "; fix it first (j5 validate shows the problems)", result.errors)
    return result.script


def image_slots(script: Script, registry: StepRegistry) -> Iterator[tuple[Step, str, str]]:
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


def unique_name(stem: str, taken: set[str] | frozenset[str]) -> str:
    """:func:`image_name` of ``stem``, with ``_2``, ``_3``... when it is taken."""
    name = base = image_name(stem)
    n = 1
    while name in taken:
        n += 1
        name = f"{base}_{n}"
    return name


def read_png(path: str | os.PathLike[str], label: str) -> bytes:
    """The bytes of a PNG file that can be embedded; :class:`BundleError` otherwise."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise BundleError(f"{label}: cannot read {str(path)!r}: "
                          f"{exc.strerror or exc}") from exc
    try:
        embedded.check_png(data, label)
    except ValueError as exc:
        hint = ("; only PNG files can be embedded"
                if not data.startswith(embedded.PNG_SIGNATURE) else "")
        raise BundleError(f"{label}: cannot be embedded: {exc}{hint}") from exc
    return data


# --- pack -------------------------------------------------------------------------------


@dataclass(slots=True)
class PackPlan:
    """What packing changes: each image value as written and the reference that
    replaces it, the new entries (as ``set_meta`` takes them) and what it leaves."""

    references: dict[str, str] = field(default_factory=dict)
    added: dict[str, bytes] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


def plan_pack(script: Script, base: Path, registry: StepRegistry) -> PackPlan:
    """Read and check every image file ``script`` references literally (paths relative
    to ``base``). Identical files share one entry, including one already embedded;
    names come from the file stems, with ``_2``, ``_3``... when taken. Raises
    :class:`BundleError` for a file that cannot be embedded."""
    plan = PackPlan()
    taken = set(script.images)
    by_digest = {hashlib.sha256(image.data).hexdigest(): name
                 for name, image in script.images.items()}
    for step, param, value in image_slots(script, registry):
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
            raise BundleError(f"step {step.id}: {param}: cannot read {value!r} ({path}): "
                              f"{exc.strerror or exc}") from exc
        try:
            embedded.check_png(data, value)
        except ValueError as exc:
            raise BundleError(f"step {step.id}: {param}: {value!r} cannot be embedded: {exc}"
                              + ("; only PNG files can be embedded"
                                 if not data.startswith(embedded.PNG_SIGNATURE) else "")
                              ) from exc
        digest = hashlib.sha256(data).hexdigest()
        name = by_digest.get(digest)
        if name is None:
            name = unique_name(Path(value).stem, taken)
            taken.add(name)
            by_digest[digest] = name
            plan.added[name] = data
        plan.references[value] = f"{embedded.REFERENCE_PREFIX}{name}"
    total = sum(len(image.data) for image in script.images.values())
    total += sum(len(data) for data in plan.added.values())
    if total > embedded.MAX_SCRIPT_BYTES:
        raise BundleError(f"the embedded images would add up to {total:,} bytes, over the "
                          f"limit of {embedded.MAX_SCRIPT_BYTES:,} bytes (8 MiB) per script")
    return plan


@dataclass(slots=True)
class Packed:
    """The packed script's ``text``, its :class:`PackPlan` and how many image parameters
    were rewritten."""

    text: str
    plan: PackPlan
    rewritten: int


def pack_script(script: str | os.PathLike[str], registry: StepRegistry, *,
                output: str | os.PathLike[str] | None = None) -> Packed:
    """The text of ``script`` with every image file it references embedded. ``output``
    (where the text will be saved, if known) only matters to the check that the result
    loads. Writes nothing."""
    loaded = load_valid(script, registry)
    plan = plan_pack(loaded, Path(script).absolute().parent, registry)
    try:
        text = rewrite_images(
            loaded.text, plan.references, lookup=registry.spec,
            add={name: {"png": embedded.encode_base64(data)}
                 for name, data in plan.added.items()})
    except EditError as exc:  # pragma: no cover - the script was just loaded
        raise BundleError(f"cannot edit {script}: {exc.message}") from exc
    target = None if output is None else os.path.abspath(output)
    check = load_script(text, lookup=registry.spec, check=registry.check_params,
                        source_path=target)
    if check.script is None:  # pragma: no cover - a bug, never a user error
        raise BundleError(f"packing produced an invalid script: {check.errors[0].message}")
    rewritten = sum(1 for _, _, value in image_slots(loaded, registry)
                    if value in plan.references)
    return Packed(text=text, plan=plan, rewritten=rewritten)


# --- unpack -----------------------------------------------------------------------------


@dataclass(slots=True)
class Unpacked:
    """What :func:`unpack_script` wrote, and its warnings (for people)."""

    script_path: Path
    images: dict[str, Path]
    warnings: list[str]


def unpack_script(bundle: str | os.PathLike[str], directory: str | os.PathLike[str],
                  registry: StepRegistry, *, force: bool = False) -> Unpacked:
    """Write ``bundle``'s embedded images to ``directory/img/<name>.png`` and the script,
    referring to them by path, to ``directory/<bundle file name>``. Existing files are
    only replaced with ``force``."""
    loaded = load_valid(bundle, registry)
    if not loaded.images:
        raise BundleError(f"{bundle} has no embedded images")
    root = Path(directory)
    script_path = root / Path(bundle).name
    files = {name: root / IMAGES_DIR / f"{name}.png" for name in loaded.images}
    if not force:
        existing = [str(p) for p in (script_path, *files.values()) if p.exists()]
        if existing:
            raise BundleError(f"would overwrite {', '.join(existing)}; use --force to "
                              "overwrite")
    warnings: list[str] = []
    built = [(step, param) for step, param, value in image_slots(loaded, registry)
             if "${" in value]
    keep = bool(built)
    for step, param in built:
        warnings.append(f"step {step.id}: {param} is built at run time and may name an "
                        "embedded image, so the images section is kept")
    if not keep:
        for image in loaded.images.values():
            if image.scale is not None:
                warnings.append(f"image #{image.name}: its capture scale {image.scale:g} "
                                "cannot be kept in a file and is dropped; set the script's "
                                "capture_scale if the templates share it")
    references = {f"{embedded.REFERENCE_PREFIX}{name}": f"{IMAGES_DIR}/{path.name}"
                  for name, path in files.items()}
    try:
        text = rewrite_images(loaded.text, references, lookup=registry.spec,
                              remove_section=not keep)
    except EditError as exc:  # pragma: no cover - the script was just loaded
        raise BundleError(f"cannot edit {bundle}: {exc.message}") from exc
    try:
        (root / IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BundleError(f"cannot create {root / IMAGES_DIR}: {exc.strerror or exc}") from exc
    for name, path in files.items():
        try:
            path.write_bytes(loaded.images[name].data)
        except OSError as exc:
            raise BundleError(f"cannot write {path}: {exc.strerror or exc}") from exc
    write_text(script_path, text)
    return Unpacked(script_path=script_path, images=files, warnings=warnings)


def write_text(path: Path, text: str) -> None:
    """Write a script as UTF-8 with ``\\n`` line ends; :class:`BundleError` on failure."""
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as exc:
        raise BundleError(f"cannot write {path}: {exc.strerror or exc}") from exc
