"""Run history commands: ``list_runs``, ``get_run`` and ``delete_run``
(docs/contract.md, "Script management": run history).

They read the runs directory through :class:`~j5_macro.engine.persistence.RunStore`.
Without a runs directory nothing is saved, so the history is empty: ``list_runs``
answers no runs and the other two ``not_running``. A run directory that cannot be read
never fails a command: ``list_runs`` leaves it out and logs a warning.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from j5_macro.engine.dispatch import CommandError
from j5_macro.engine.persistence import RunStore, is_run_id
from j5_macro.engine.runs import RunManager
from j5_macro.engine.server import EngineServer
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import Command, JsonObject
from j5_macro.protocol.payloads import (
    GetRunPayload,
    GetRunResult,
    ListRunsPayload,
    ListRunsResult,
    RunInfo,
    RunRef,
)

DEFAULT_LIMIT = 20
"""Runs answered by ``list_runs`` when the client gives no ``limit``."""


def check_run_id(run_id: str) -> str:
    """``validation_failed`` unless ``run_id`` is shaped like a run id, so that it can
    only name a directory directly inside the runs directory."""
    if not is_run_id(run_id):
        raise CommandError(ErrorCode.VALIDATION_FAILED,
                           f"payload.run_id: not a run id (r-YYYYMMDD-HHMMSS-xxxx): "
                           f"{run_id!r}", {"field": "run_id"})
    return run_id


def unknown_run(run_id: str) -> CommandError:
    return CommandError(ErrorCode.NOT_RUNNING, f"no saved run {run_id!r}", {"run_id": run_id})


def same_path(a: str, b: str) -> bool:
    """Whether two absolute script paths name the same file (case-insensitively on
    Windows)."""
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def run_info(run_id: str, meta: dict[str, Any]) -> RunInfo:
    """The ``list_runs`` entry of a run; PayloadError when ``run.json`` lacks ``started``
    or has fields of the wrong type."""
    return RunInfo.from_json({
        "run_id": run_id,
        "status": meta.get("status"),
        "started": meta.get("started"),
        "duration_ms": meta.get("duration_ms"),
        "script_name": meta.get("script_name"),
        "script_path": meta.get("script_path"),
        "mode": meta.get("mode"),
    }, f"{run_id}/run.json")


class RunHistory:
    """The saved runs, seen through the engine's run manager (for the active run)."""

    def __init__(self, server: EngineServer, runs: RunManager) -> None:
        self.server = server
        self.runs = runs

    @property
    def store(self) -> RunStore | None:
        return self.runs.store

    def list(self, payload: ListRunsPayload) -> list[RunInfo]:
        limit = DEFAULT_LIMIT if payload.limit is None else payload.limit
        if limit < 0:
            raise CommandError(ErrorCode.VALIDATION_FAILED,
                               "payload.limit: must not be negative", {"field": "limit"})
        keep = self._script_filter(payload.script_id)
        store = self.store
        if store is None:
            return []
        try:
            run_ids = store.run_ids()
        except OSError as exc:
            self.server.log("warning", f"cannot list the runs in {store.root}: {exc}")
            return []
        active = self.runs.active_run()
        infos: list[RunInfo] = []
        for run_id in run_ids:
            try:
                if active is not None and active.run_id == run_id:
                    # Its run.json may be being created; the run knows its metadata.
                    meta = dict(active.meta)
                    meta.pop("status", None)
                else:
                    meta = store.read_meta(run_id)
                info = run_info(run_id, meta)
            except (OSError, ValueError) as exc:
                # PayloadError is a ValueError too.
                self.server.log("warning", f"run history: skipping {run_id}, its run.json "
                                f"cannot be read: {exc}")
                continue
            if keep(info):
                infos.append(info)
        infos.sort(key=lambda info: (info.started, info.run_id), reverse=True)
        return infos[:limit]

    def _script_filter(self, script_id: str | None) -> Callable[[RunInfo], bool]:
        """A predicate for ``list_runs.script_id``: runs of the script's file (by path),
        or, for a script loaded from ``source``, its runs in this engine session."""
        if script_id is None:
            return lambda info: True
        entry = self.runs.scripts.get(script_id)
        if entry.path is None:
            session = self.runs.session_run_ids(script_id)
            return lambda info: info.run_id in session and info.script_path is None
        path = str(entry.path)
        return lambda info: info.script_path is not None and same_path(info.script_path, path)

    def get(self, payload: GetRunPayload) -> GetRunResult:
        run_id = check_run_id(payload.run_id)
        store = self.store
        if store is None or not store.exists(run_id):
            raise unknown_run(run_id)
        active = self.runs.active_run()
        try:
            if active is not None and active.run_id == run_id:
                meta = dict(active.meta)
            else:
                try:
                    meta = store.read_meta(run_id)
                except FileNotFoundError:
                    meta = {"run_id": run_id}
                except ValueError as exc:
                    self.server.log("warning", f"run {run_id}: its run.json cannot be "
                                    f"read: {exc}")
                    meta = {"run_id": run_id}
            from_seq = 1 if payload.from_seq is None else payload.from_seq
            events, bad = store.read_events(run_id, from_seq)
            captures = store.captures(run_id)
        except FileNotFoundError as exc:
            # Deleted (or pruned) while being read.
            raise unknown_run(run_id) from exc
        except OSError as exc:
            raise CommandError(ErrorCode.INTERNAL_ERROR,
                               f"cannot read run {run_id}: {exc}", {"run_id": run_id}) from exc
        if bad:
            self.server.log("warning", f"run {run_id}: skipped {bad} unreadable line(s) of "
                            "its events.jsonl")
        return GetRunResult(run_id=run_id, run=meta, events=events, captures=captures)

    def delete(self, payload: RunRef) -> None:
        run_id = check_run_id(payload.run_id)
        active = self.runs.active_run()
        if active is not None and active.run_id == run_id:
            raise CommandError(ErrorCode.INVALID_STATE,
                               f"run {run_id} is active; stop it before deleting it",
                               {"reason": "active", "run_id": run_id})
        store = self.store
        if store is None or not store.exists(run_id):
            raise unknown_run(run_id)
        try:
            store.delete(run_id)
        except FileNotFoundError as exc:
            raise unknown_run(run_id) from exc
        except OSError as exc:
            raise CommandError(ErrorCode.INTERNAL_ERROR,
                               f"cannot delete run {run_id}: {exc}", {"run_id": run_id}) from exc


def install(server: EngineServer, runs: RunManager) -> RunHistory:
    history = RunHistory(server, runs)

    def list_runs(command: Command) -> JsonObject:
        payload = ListRunsPayload.from_json(command.payload)
        return ListRunsResult(runs=history.list(payload)).to_json()

    def get_run(command: Command) -> JsonObject:
        return history.get(GetRunPayload.from_json(command.payload)).to_json()

    def delete_run(command: Command) -> JsonObject:
        history.delete(RunRef.from_json(command.payload))
        return {}

    server.register("list_runs", list_runs)
    server.register("get_run", get_run)
    server.register("delete_run", delete_run)
    return history
