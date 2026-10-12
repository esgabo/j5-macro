"""``edit_script``: structured edits of a loaded script (docs/contract.md, "Script
management"). The YAML work is :func:`j5_macro.script.editor.apply_edits`; this module
checks the version, saves the file and updates the loaded script."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Literal

from j5_macro.engine.dispatch import CommandError
from j5_macro.engine.scripts import LoadedScript, ScriptStore, script_version
from j5_macro.engine.server import EngineServer
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import Command, ErrorInfo, JsonObject
from j5_macro.protocol.payloads import EditScriptPayload, EditScriptResult, ScriptChangedEvent
from j5_macro.script.editor import EditError, apply_edits
from j5_macro.script.loader import LoadResult


def read_script_text(path: Path) -> str:
    """The text of a script file as the loader reads it (UTF-8, universal newlines)."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise CommandError(ErrorCode.PARSE_ERROR,
                           f"script is not valid UTF-8: {exc.reason}",
                           {"path": str(path)}) from exc
    except OSError as exc:
        raise CommandError(ErrorCode.SCRIPT_NOT_FOUND,
                           f"cannot open script {str(path)!r}: {exc.strerror or exc}",
                           {"path": str(path)}) from exc


def write_atomically(path: Path, text: str) -> None:
    """Write ``text`` as UTF-8 with ``\\n`` line ends through a temporary file in the same
    directory and an atomic rename, keeping the file's permission bits."""
    try:
        mode: int | None = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        mode = None
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def install(server: EngineServer, store: ScriptStore) -> None:
    lock = threading.Lock()
    """Edits are applied one at a time, whatever thread handles them."""

    def current_text(entry: LoadedScript) -> str:
        # The file as it is now: it may have changed before the watcher noticed.
        return entry.text if entry.path is None else read_script_text(entry.path)

    def changed(entry: LoadedScript, source: Literal["disk", "edit"]) -> None:
        server.emit("script_changed", ScriptChangedEvent(
            script_id=entry.script_id, version=entry.version, source=source,
            valid=entry.valid, errors=list(entry.errors) or None,
        ).to_json())

    def save(entry: LoadedScript, text: str, result: LoadResult) -> None:
        """Write the file and record the new text in the store under the store's file
        lock, which the watcher also takes: it never sees the written file before the
        store knows its version, so the edit is not reported as a disk change too."""
        with store.file_lock:
            if entry.path is not None:
                try:
                    write_atomically(entry.path, text)
                except OSError as exc:
                    raise CommandError(ErrorCode.SCRIPT_NOT_FOUND,
                                       f"cannot write script {str(entry.path)!r}: "
                                       f"{exc.strerror or exc}",
                                       {"path": str(entry.path)}) from exc
            store.update(entry, text, result)

    def edit_script(command: Command) -> JsonObject:
        payload = EditScriptPayload.from_json(command.payload)
        entry = store.get(payload.script_id)
        with lock:
            text = current_text(entry)
            version = script_version(text)
            if version != payload.base_version:
                raise CommandError(
                    ErrorCode.CONFLICT,
                    f"script {entry.script_id} changed: it is at version {version}, not "
                    f"{payload.base_version}", {"version": version})
            try:
                new_text = apply_edits(text, payload.edits, lookup=store.registry.spec)
            except EditError as exc:
                details: JsonObject = dict(exc.details or {})
                if exc.index is not None:
                    details["edit"] = exc.index
                error = CommandError(exc.code, exc.message, details or None)
                error.info = ErrorInfo(code=str(exc.code), message=exc.message,
                                       line=exc.line, details=details or None)
                raise error from exc
            source_path = None if entry.path is None else str(entry.path)
            result = store.load_text(new_text, source_path)
            if new_text != text:
                save(entry, new_text, result)
                changed(entry, "edit")
            elif entry.version != script_version(new_text):
                # Nothing to change, but the file changed before the watcher saw it.
                with store.file_lock:
                    store.update(entry, new_text, result)
                changed(entry, "disk")
            return EditScriptResult(version=entry.version, valid=entry.valid,
                                    errors=list(entry.errors)).to_json()

    server.register("edit_script", edit_script)
