"""Loaded scripts and the script commands: ``list_step_types``, ``load``, ``validate``,
``list_scripts``, ``create_script`` and ``get_script``, and the file watcher that emits
``script_changed`` (docs/contract.md, "Commands", "Script format and step registry" and
"Script management")."""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from j5_macro.engine.dispatch import CommandError
from j5_macro.engine.events import format_ts
from j5_macro.engine.server import EngineServer
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import Command, ErrorInfo, JsonObject
from j5_macro.protocol.payloads import (
    CreateScriptPayload,
    CreateScriptResult,
    Diagnostic,
    GetScriptResult,
    ListScriptsPayload,
    ListScriptsResult,
    LoadPayload,
    LoadResult,
    ScriptChangedEvent,
    ScriptInfo,
    ScriptRef,
    StepNode,
    ValidateResult,
)
from j5_macro.script.loader import LoadResult as LoaderResult
from j5_macro.script.loader import load_file, load_script
from j5_macro.script.model import Script, Step
from j5_macro.steps.registry import StepRegistry


def script_version(text: str) -> str:
    """A script's ``version``: the first 16 hex digits of the SHA-256 of its UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class LoadedScript:
    script_id: str
    script: Script
    """The last valid version: what ``run`` executes."""
    path: Path | None
    """The file it was loaded from; None for ``load`` with ``source``."""
    breakpoints: frozenset[str] = field(default_factory=frozenset)
    """Client breakpoints set with ``set_breakpoints``, by step id."""
    text: str = ""
    """The current text (of the file, or the edited source), possibly invalid."""
    version: str = ""
    """``script_version(text)``."""
    errors: list[ErrorInfo] = field(default_factory=list)
    """Problems of ``text``; empty when ``script`` is ``text`` parsed. While not empty,
    ``run`` answers ``validation_failed``."""

    @property
    def valid(self) -> bool:
        return not self.errors


class ScriptStore:
    """Scripts loaded in this engine session, by ``script_id`` (``s1``, ``s2``, ...)."""

    def __init__(self, registry: StepRegistry) -> None:
        self.registry = registry
        self._scripts: dict[str, LoadedScript] = {}
        self._counter = 0
        self._lock = threading.Lock()
        self.file_lock = threading.RLock()
        """Held while a loaded script's file is written or re-read and its entry updated
        (the watcher holds it for each check). ``edit_script`` should hold it around
        writing the file and calling :meth:`update`, so the watcher never reads the new
        file before the entry has the new version and never reports an edit as a
        ``disk`` change."""

    def load_text(self, text: str, source_path: str | None = None) -> LoaderResult:
        return load_script(text, lookup=self.registry.spec, check=self.registry.check_params,
                           source_path=source_path)

    def load_path(self, path: Path) -> LoaderResult:
        return load_file(path, lookup=self.registry.spec, check=self.registry.check_params)

    def add(self, script: Script, path: Path | None) -> LoadedScript:
        with self._lock:
            self._counter += 1
            entry = LoadedScript(f"s{self._counter}", script, path, text=script.text,
                                 version=script_version(script.text))
            self._scripts[entry.script_id] = entry
            return entry

    def get(self, script_id: str) -> LoadedScript:
        with self._lock:
            entry = self._scripts.get(script_id)
        if entry is None:
            raise CommandError(ErrorCode.SCRIPT_NOT_FOUND, f"unknown script_id: {script_id!r}",
                               {"script_id": script_id})
        return entry

    def update(self, entry: LoadedScript, text: str, result: LoaderResult) -> None:
        """Record new ``text`` for ``entry``: its version and problems, and the parsed
        script when it is valid (an invalid text keeps the last valid script)."""
        with self._lock:
            entry.text = text
            entry.version = script_version(text)
            if result.script is not None:
                entry.script = result.script
                entry.errors = []
            else:
                entry.errors = list(result.errors)

    def entries(self) -> list[LoadedScript]:
        with self._lock:
            return list(self._scripts.values())

    def view(self, entry: LoadedScript) -> tuple[Script, str, str, list[ErrorInfo]]:
        """A consistent snapshot of ``entry``: ``(script, text, version, errors)``."""
        with self._lock:
            return entry.script, entry.text, entry.version, list(entry.errors)

    def set_breakpoints(self, script_id: str, step_ids: list[str]) -> LoadedScript:
        entry = self.get(script_id)
        with self._lock:
            entry.breakpoints = frozenset(step_ids)
        return entry


# --- wire shapes ------------------------------------------------------------------------


def format_seconds(seconds: float) -> str:
    """A duration in the script syntax: ``2s``, ``1.5s`` or ``500ms``."""
    ms = round(seconds * 1000)
    if ms % 1000 == 0:
        return f"{ms // 1000}s"
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:g}s"


def step_node(step: Step) -> StepNode:
    return StepNode(
        id=step.id,
        type=step.type,
        path=list(step.path),
        line=step.line,
        params=step.params,
        breakpoint=step.breakpoint,
        on_error=step.on_error,
        timeout=None if step.timeout is None else format_seconds(step.timeout),
        input=step.input,
        blocks={slot: [step_node(child) for child in children]
                for slot, children in step.blocks.items()} or None,
    )


def diagnostic(info: ErrorInfo) -> Diagnostic:
    return Diagnostic(message=info.message, step_id=info.step_id, line=info.line)


def _warnings(result: LoaderResult) -> list[Diagnostic]:
    return [] if result.script is None else [diagnostic(w) for w in result.script.warnings]


# --- files ------------------------------------------------------------------------------


MAX_LISTED_FILES = 1000
"""``list_scripts`` stops after looking at this many ``*.yaml``/``*.yml`` files."""
SCRIPT_SUFFIXES = (".yaml", ".yml")
_STEPS_KEY = re.compile(r"^steps\s*:", re.MULTILINE)


def resolve(path: str) -> Path:
    """``path`` made absolute against the engine's working directory."""
    return Path(path).absolute()


def read_script_file(store: ScriptStore, path: Path) -> tuple[str, LoaderResult]:
    """Read and load ``path`` as ``load`` does: the text (newlines normalised, as
    :func:`~j5_macro.script.loader.load_file` reads it) and the load result. A file that
    is not UTF-8 is decoded with replacement characters and reported as ``parse_error``.
    Raises :class:`OSError` when the file cannot be read."""
    try:
        with open(path, encoding="utf-8") as file:
            text = file.read()
    except UnicodeDecodeError:
        with open(path, encoding="utf-8", errors="replace") as file:
            text = file.read()
        return text, store.load_path(path)
    return text, store.load_text(text, source_path=str(path))


def _script_info(store: ScriptStore, path: Path) -> ScriptInfo | None:
    """What ``list_scripts`` reports for one file, or None when it is not a script (no
    ``steps`` key) or cannot be read."""
    try:
        modified = format_ts(datetime.fromtimestamp(path.stat().st_mtime, UTC))
        text, result = read_script_file(store, path)
    except OSError:
        return None
    if result.script is not None:
        return ScriptInfo(path=str(path), name=result.script.name, modified=modified,
                          valid=True)
    name = path.stem
    try:
        data = YAML(typ="safe", pure=True).load(text)
    except YAMLError:
        # Unparseable: still a script (being edited) when it has a top-level ``steps``.
        if not _STEPS_KEY.search(text):
            return None
    else:
        if not isinstance(data, dict) or "steps" not in data:
            return None
        if isinstance(data.get("name"), str):
            name = data["name"]
    return ScriptInfo(path=str(path), name=name, modified=modified, valid=False)


def list_scripts(store: ScriptStore, root: Path) -> list[ScriptInfo]:
    """Scripts under ``root``, recursively, skipping hidden directories, in path order;
    at most :data:`MAX_LISTED_FILES` candidate files are looked at."""
    found: list[ScriptInfo] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename in sorted(filenames):
            if not filename.lower().endswith(SCRIPT_SUFFIXES):
                continue
            if seen >= MAX_LISTED_FILES:
                return found
            seen += 1
            info = _script_info(store, Path(dirpath) / filename)
            if info is not None:
                found.append(info)
    return found


def new_script_text(name: str) -> str:
    """The text of a new, empty script called ``name``."""
    yaml = YAML(typ="rt")
    yaml.width = 1 << 16  # never fold a long name
    out = io.StringIO()
    yaml.dump({"version": 1, "name": name, "steps": []}, out)
    return out.getvalue()


# --- file watching ----------------------------------------------------------------------


WATCH_INTERVAL_S = 0.5
"""How often the watcher checks every loaded script that has a path."""
RACY_WINDOW_S = 2.0
"""A file modified this recently is re-read even when its mtime and size look the same,
since a coarse mtime may not tell two quick writes apart."""

ChangeSource = Literal["disk", "edit", "deleted"]
EmitFn = Callable[[str, JsonObject], object]


@dataclass(slots=True)
class _FileState:
    stat: tuple[int, int, int] | None = None
    """``(mtime_ns, size, inode)`` when the file was last read; None before."""
    deleted_version: str | None = None
    """While the file is missing: the entry's version when it went missing."""


class ScriptWatcher:
    """Polls every loaded script that has a path and emits ``script_changed``.

    A cheap ``stat`` comparison comes first; the file is only read when that changed (or
    when it was modified within :data:`RACY_WINDOW_S`), and an event is only emitted when
    the content's version differs from the entry's. Touching a file therefore emits
    nothing, and neither does a write by ``edit_script``, which updates the entry itself.
    """

    def __init__(self, store: ScriptStore, emit: EmitFn, *,
                 interval: float = WATCH_INTERVAL_S,
                 on_error: Callable[[str], object] | None = None) -> None:
        self.store = store
        self.emit = emit
        self.interval = interval
        self.on_error = on_error
        self._states: dict[str, _FileState] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        # A daemon thread: it never keeps the engine process alive.
        self._thread = threading.Thread(target=self._loop, name="j5-script-watcher",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.poll()

    def poll(self) -> None:
        """Check every watched script once."""
        for entry in self.store.entries():
            if self._stop.is_set():
                return
            if entry.path is None:
                continue
            try:
                self.check(entry)
            except Exception as exc:  # one bad file never stops the watcher
                if self.on_error is not None:
                    with contextlib.suppress(Exception):
                        self.on_error(f"cannot check script {entry.script_id} "
                                      f"({entry.path}) for changes: {exc}")

    def check(self, entry: LoadedScript) -> None:
        """Check one script's file and emit ``script_changed`` if it changed."""
        assert entry.path is not None
        state = self._states.setdefault(entry.script_id, _FileState())
        try:
            st = entry.path.stat()
        except FileNotFoundError:
            self._deleted(entry, state)
            return
        key = (st.st_mtime_ns, st.st_size, st.st_ino)
        if (state.stat == key and state.deleted_version is None
                and time.time() - st.st_mtime > RACY_WINDOW_S):
            return
        with self.store.file_lock:
            try:
                text, result = read_script_file(self.store, entry.path)
            except FileNotFoundError:
                self._deleted(entry, state)
                return
            state.stat = key
            current = self.store.view(entry)[2]
            # Back after being deleted with the entry unchanged meanwhile: clients were
            # told version null, so announce it again even with the same content.
            announce = state.deleted_version is not None and state.deleted_version == current
            state.deleted_version = None
            if script_version(text) != current:
                self.store.update(entry, text, result)
            elif not announce:
                return
            self._emit(entry, "disk")

    def _deleted(self, entry: LoadedScript, state: _FileState) -> None:
        with self.store.file_lock:
            if state.deleted_version is not None:
                return
            state.deleted_version = self.store.view(entry)[2]
            state.stat = None
            self._emit(entry, "deleted")

    def _emit(self, entry: LoadedScript, source: ChangeSource) -> None:
        _, _, version, errors = self.store.view(entry)
        event = ScriptChangedEvent(
            script_id=entry.script_id,
            version=None if source == "deleted" else version,
            source=source,
            valid=not errors,
            errors=errors or None,
        )
        self.emit("script_changed", event.to_json())


# --- handlers ---------------------------------------------------------------------------


def install(server: EngineServer, store: ScriptStore, *,
            watch_interval: float | None = None) -> ScriptWatcher:
    """Register the script commands, and watch loaded files from ``ready`` until
    shutdown. ``watch_interval`` defaults to :data:`WATCH_INTERVAL_S` (read when this
    is called)."""
    registry = store.registry
    watcher = ScriptWatcher(
        store, server.emit,
        interval=WATCH_INTERVAL_S if watch_interval is None else watch_interval,
        on_error=lambda message: server.log("warning", message))

    def list_step_types(command: Command) -> JsonObject:
        return {"step_types": registry.describe()}

    def load(command: Command) -> JsonObject:
        payload = LoadPayload.from_json(command.payload)
        path: Path | None = None
        if payload.path is not None:
            # Absolute, so relative image paths in the script do not depend on the
            # engine's working directory changing later.
            path = Path(payload.path).absolute()
            result = store.load_path(path)
        else:
            assert payload.source is not None
            result = store.load_text(payload.source)
        if result.script is None:
            first = result.errors[0]
            raise CommandError(
                first.code,
                first.message if len(result.errors) == 1
                else f"{first.message} (and {len(result.errors) - 1} more errors)",
                {"errors": [e.to_json() for e in result.errors], "warnings": []},
            )
        entry = store.add(result.script, path)
        return LoadResult(
            script_id=entry.script_id,
            steps=[step_node(step) for step in result.script.steps],
            params=[param.to_json() for param in result.script.params.values()],
            images=[image.to_json() for image in result.script.images.values()],
            warnings=_warnings(result),
        ).to_json()

    def validate(command: Command) -> JsonObject:
        entry = store.get(ScriptRef.from_json(command.payload).script_id)
        if entry.path is not None:
            result = store.load_path(entry.path)
        else:
            result = store.load_text(store.view(entry)[1])
        data = ValidateResult(ok=result.ok, errors=result.errors).to_json()
        # Not in ValidateResult yet; clients ignore unknown fields.
        data["warnings"] = [w.to_json() for w in _warnings(result)]
        return data

    def list_scripts_command(command: Command) -> JsonObject:
        payload = ListScriptsPayload.from_json(command.payload)
        root = resolve(payload.root if payload.root is not None else ".")
        if not root.is_dir():
            raise CommandError(ErrorCode.VALIDATION_FAILED,
                               f"not a directory: {str(root)!r}",
                               {"field": "root", "reason": "no_directory",
                                "path": str(root)})
        return ListScriptsResult(scripts=list_scripts(store, root)).to_json()

    def create_script(command: Command) -> JsonObject:
        payload = CreateScriptPayload.from_json(command.payload)
        path = resolve(payload.path)
        if not path.parent.is_dir():
            raise CommandError(ErrorCode.VALIDATION_FAILED,
                               f"the directory does not exist: {str(path.parent)!r}",
                               {"field": "path", "reason": "no_directory",
                                "path": str(path)})
        text = new_script_text(payload.name)
        with store.file_lock:
            try:
                # "x": never overwrite, even a file created since the check above.
                with open(path, "x", encoding="utf-8", newline="\n") as file:
                    file.write(text)
            except FileExistsError:
                raise CommandError(ErrorCode.VALIDATION_FAILED,
                                   f"the file already exists: {str(path)!r}",
                                   {"field": "path", "reason": "exists",
                                    "path": str(path)}) from None
            except OSError as exc:
                raise CommandError(ErrorCode.VALIDATION_FAILED,
                                   f"cannot create {str(path)!r}: {exc.strerror or exc}",
                                   {"field": "path", "path": str(path)}) from None
            result = store.load_path(path)
            if result.script is None:  # e.g. the name is not valid in a script
                first = result.errors[0]
                raise CommandError(first.code, first.message,
                                   {"errors": [e.to_json() for e in result.errors]})
            entry = store.add(result.script, path)
        return CreateScriptResult(script_id=entry.script_id,
                                  version=entry.version).to_json()

    def get_script(command: Command) -> JsonObject:
        entry = store.get(ScriptRef.from_json(command.payload).script_id)
        script, text, version, errors = store.view(entry)
        # While the current text is invalid, name, steps, params and images are those of the
        # last valid version, kept until the text is fixed.
        return GetScriptResult(
            script_id=entry.script_id,
            version=version,
            source=text,
            name=script.name,
            path=None if entry.path is None else str(entry.path),
            steps=[step_node(step) for step in script.steps],
            params=[param.to_json() for param in script.params.values()],
            images=[image.to_json() for image in script.images.values()],
            valid=not errors,
            errors=errors,
        ).to_json()

    server.register("list_step_types", list_step_types)
    server.register("load", load)
    server.register("validate", validate)
    server.register("list_scripts", list_scripts_command)
    server.register("create_script", create_script)
    server.register("get_script", get_script)
    server.on_ready(watcher.start)
    server.on_shutdown(watcher.stop)
    return watcher

