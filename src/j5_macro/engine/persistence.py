"""Run history on disk (docs/contract.md, "Script management": run history).

Every run gets ``<runs_dir>/<run_id>/`` with:

- ``events.jsonl``: every event of the run, one JSON line each, appended as emitted;
- ``run.json``: metadata (script, mode, seed, start time) and, once the run ends, its
  ``status``, ``reason``, ``duration_ms`` and ``error``.

Only the newest :data:`RUNS_KEPT` run directories are kept. Every method raises
:class:`OSError` on failure; the caller decides how to report it (the engine logs it
and keeps the run going). The read side (:meth:`RunStore.read_meta`,
:meth:`RunStore.read_events`, ...) serves ``list_runs``, ``get_run`` and ``delete_run``;
it only ever touches ``<runs_dir>/<run_id>`` for a well-formed run id (see
:func:`is_run_id`), so a request cannot reach outside the runs directory.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import IO, Any

RUNS_KEPT = 20
"""Run directories kept on disk; older ones are deleted when a new run starts."""

RUNS_DIR_ENV = "J5_RUNS_DIR"
APP_DIR_NAME = "j5-macro"

RUN_ID_PATTERN = re.compile(r"^r-\d{8}-\d{6}-[0-9a-f]{4}$")
"""Only directories named like a run id are ever pruned or read."""

EVENTS_FILE = "events.jsonl"
META_FILE = "run.json"
CAPTURES_DIR = "captures"


def is_run_id(text: str) -> bool:
    """True for ``r-YYYYMMDD-HHMMSS-xxxx`` exactly (ASCII digits, lowercase hex). Such a
    name has no separator or ``..``, so it always names a direct child of the runs dir."""
    return RUN_ID_PATTERN.fullmatch(text) is not None and text.isascii()


def platform_state_dir() -> Path:
    """Where the platform keeps per-user application state."""
    home = Path.home()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        return Path(local) if local else home / "AppData" / "Local"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support"
    state = os.environ.get("XDG_STATE_HOME")
    # The XDG spec says relative values are invalid and must be ignored.
    if state and Path(state).is_absolute():
        return Path(state)
    return home / ".local" / "state"


def resolve_runs_dir(option: str | os.PathLike[str] | None = None) -> Path:
    """The runs directory: the ``--runs-dir`` option, else ``$J5_RUNS_DIR``, else
    ``<platform state dir>/j5-macro/runs``."""
    if option is not None and str(option):
        return Path(option)
    env = os.environ.get(RUNS_DIR_ENV)
    if env:
        return Path(env)
    return platform_state_dir() / APP_DIR_NAME / "runs"


class RunFiles:
    """The files of one run. Not thread-safe: the run serializes its calls."""

    def __init__(self, directory: Path, meta: dict[str, Any]) -> None:
        self.directory = directory
        self.meta = dict(meta)
        self._events: IO[str] | None = None

    @property
    def events_path(self) -> Path:
        return self.directory / EVENTS_FILE

    @property
    def meta_path(self) -> Path:
        return self.directory / META_FILE

    def create(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.write_meta()
        self._events = self.events_path.open("a", encoding="utf-8", newline="\n")

    def append(self, line: str) -> None:
        if self._events is None:
            raise OSError(f"events file of {self.directory.name} is not open")
        self._events.write(line + "\n")
        self._events.flush()

    def finish(self, fields: dict[str, Any]) -> None:
        """Record how the run ended and close the events file."""
        try:
            self.close()
        finally:
            self.meta.update(fields)
            self.write_meta()

    def close(self) -> None:
        events, self._events = self._events, None
        if events is not None:
            events.close()

    def write_meta(self) -> None:
        """Rewrite ``run.json`` atomically, so a reader never sees half a file."""
        tmp = self.meta_path.with_name(META_FILE + ".tmp")
        tmp.write_text(json.dumps(self.meta, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8", newline="\n")
        os.replace(tmp, self.meta_path)


class RunStore:
    """The runs directory: creates run folders and prunes old ones."""

    def __init__(self, root: Path, keep: int = RUNS_KEPT) -> None:
        self.root = root
        self.keep = keep

    def create(self, run_id: str, meta: dict[str, Any]) -> RunFiles:
        files = RunFiles(self.root / run_id, meta)
        files.create()
        return files

    def prune(self, *, protect: frozenset[str] = frozenset(), room_for: int = 1) -> list[str]:
        """Delete the oldest run directories so that, with ``room_for`` new runs, at most
        :attr:`keep` remain. Returns the deleted run ids; a run that cannot be deleted is
        skipped, and the first failure is raised after trying the others."""
        if not self.root.is_dir():
            return []
        runs = [p for p in self.root.iterdir()
                if p.is_dir() and RUN_ID_PATTERN.match(p.name) and p.name not in protect]
        # Run ids start with their UTC start time (to the second); the folder's mtime
        # breaks ties between runs started within the same second.
        runs.sort(key=lambda p: (p.name[:17], _mtime_ns(p)))
        excess = len(runs) + len(protect) + room_for - self.keep
        deleted: list[str] = []
        failure: OSError | None = None
        for path in runs[:max(0, excess)]:
            try:
                shutil.rmtree(path)
            except OSError as exc:
                failure = failure or exc
            else:
                deleted.append(path.name)
        if failure is not None:
            raise failure
        return deleted


    # -- reading the history ----------------------------------------------

    def path_of(self, run_id: str) -> Path:
        """The directory of ``run_id``; ValueError unless it is a well-formed run id."""
        if not is_run_id(run_id):
            raise ValueError(f"not a run id: {run_id!r}")
        return self.root / run_id

    def run_ids(self) -> list[str]:
        """The run directories present, in no particular order."""
        if not self.root.is_dir():
            return []
        return [p.name for p in self.root.iterdir() if is_run_id(p.name) and p.is_dir()]

    def exists(self, run_id: str) -> bool:
        return is_run_id(run_id) and self.path_of(run_id).is_dir()

    def read_meta(self, run_id: str) -> dict[str, Any]:
        """``run.json`` as an object. Raises OSError, or ValueError when it is not JSON
        or not an object."""
        text = (self.path_of(run_id) / META_FILE).read_text(encoding="utf-8")
        meta = json.loads(text)
        if not isinstance(meta, dict):
            raise ValueError(f"{META_FILE} is not a JSON object")
        return meta

    def read_events(self, run_id: str, from_seq: int = 1) -> tuple[list[dict[str, Any]], int]:
        """The events with ``seq >= from_seq``, in file order, and how many lines were
        unreadable. A last line that does not parse is a write in progress (or cut by a
        crash) and is skipped without counting; a missing file means no events."""
        try:
            data = (self.path_of(run_id) / EVENTS_FILE).read_bytes()
        except FileNotFoundError:
            return [], 0
        lines = data.split(b"\n")
        events: list[dict[str, Any]] = []
        bad = 0
        for number, raw in enumerate(lines, start=1):
            if not raw.strip():
                continue
            try:
                event = json.loads(raw.decode("utf-8"))
            except ValueError:
                if number < len(lines):
                    bad += 1
                continue
            seq = event.get("seq") if isinstance(event, dict) else None
            if not isinstance(seq, int) or isinstance(seq, bool):
                bad += 1
                continue
            if seq >= from_seq:
                events.append(event)
        return events, bad

    def captures(self, run_id: str) -> list[str]:
        """Files under the run's ``captures/``, relative to the run's directory with
        ``/`` separators, sorted."""
        directory = self.path_of(run_id)
        captures = directory / CAPTURES_DIR
        if not captures.is_dir():
            return []
        return sorted(p.relative_to(directory).as_posix()
                      for p in captures.rglob("*") if p.is_file())

    def delete(self, run_id: str) -> None:
        """Delete the run's directory. Raises FileNotFoundError when there is none."""
        path = self.path_of(run_id)
        if path.is_symlink():
            path.unlink()
            return
        if not path.is_dir():
            raise FileNotFoundError(f"no run directory {path}")
        shutil.rmtree(path)


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0
