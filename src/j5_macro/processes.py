"""Starting external programs for the ``exec`` step (docs/contract.md, "Running programs").

Everything that differs between Linux and Windows lives here: how a command line is
split, how a program is detached, and how a program is killed together with its
children. The step itself (``j5_macro.steps.programs``) only decides what to run and
when to give up.

- :func:`split_command` turns a ``command`` string into what :class:`subprocess.Popen`
  takes without a shell: a list (POSIX ``shlex`` rules) on Linux, the string unchanged on
  Windows, where the program splits its own command line.
- :func:`start_detached` starts a program that outlives the run: a new session on Linux,
  ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` on Windows. A daemon thread reaps it so
  it never lingers as a zombie of the engine.
- :class:`Waited` runs a program whose output is captured, in its own process group so
  :meth:`Waited.kill` reaches its children too. Its output is read by two threads that
  keep only the first :data:`MAX_OUTPUT` bytes of each stream and drain the rest, so a
  chatty program never blocks on a full pipe nor fills the engine's memory.

Standard input is always empty: the engine's own standard streams carry the protocol, so
a child must never read from or write to them.
"""

from __future__ import annotations

import contextlib
import locale
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Any

MAX_OUTPUT = 64 * 1024
"""Bytes kept of each of a waited-for program's standard output and error."""

DRAIN_GRACE = 1.0
"""Seconds a waited-for program's output is still read after it exits."""

_CHUNK = 8192

Command = str | list[str]
"""What :class:`subprocess.Popen` runs: a list, or a string (Windows, or a shell)."""


def split_command(command: str) -> Command:
    """``command`` as a command line run without a shell. Raises ValueError when it
    cannot be split (an unclosed quote) or is empty."""
    if sys.platform == "win32":
        if not command.strip():
            raise ValueError("the command is empty")
        return command
    parts = shlex.split(command)
    if not parts:
        raise ValueError("the command is empty")
    return parts


def environment(extra: Mapping[str, str] | None) -> dict[str, str] | None:
    """The engine's environment plus ``extra``; None (inherit as is) without extras."""
    if not extra:
        return None
    return {**os.environ, **extra}


def _popen(command: Command, *, shell: bool, cwd: str, env: Mapping[str, str] | None,
           detached: bool) -> subprocess.Popen[bytes]:
    options: dict[str, Any] = {
        "shell": shell, "cwd": cwd, "env": env, "stdin": subprocess.DEVNULL,
    }
    if detached:
        options.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        flags |= subprocess.DETACHED_PROCESS if detached else subprocess.CREATE_NO_WINDOW
        options["creationflags"] = flags
    else:
        # Its own session (and so its own process group): Ctrl+C in the terminal never
        # reaches it, and kill() can take its children along.
        options["start_new_session"] = True
    return subprocess.Popen(command, **options)


def start_detached(command: Command, *, shell: bool, cwd: str,
                   env: Mapping[str, str] | None) -> int:
    """Start a program that outlives the run; its pid. Raises OSError when it cannot be
    started."""
    process = _popen(command, shell=shell, cwd=cwd, env=env, detached=True)
    threading.Thread(target=process.wait, name=f"j5-reap-{process.pid}", daemon=True).start()
    return process.pid


class _Reader:
    """Drains one pipe on a thread, keeping its first :data:`MAX_OUTPUT` bytes."""

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream
        self._kept = bytearray()
        self.truncated = False
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        with self._stream:
            while chunk := self._stream.read(_CHUNK):
                room = MAX_OUTPUT - len(self._kept)
                if len(chunk) > room:
                    self.truncated = True
                self._kept += chunk[:max(room, 0)]

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def text(self) -> str:
        encoding = locale.getpreferredencoding(False)
        text = bytes(self._kept).decode(encoding, errors="replace")  # a snapshot
        return text.replace("\r\n", "\n")


@dataclass(frozen=True, slots=True)
class Finished:
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool


class Waited:
    """A program whose exit and output the step waits for. Poll :meth:`poll` until it is
    true, then take :meth:`result`; :meth:`kill` ends it and its children."""

    def __init__(self, command: Command, *, shell: bool, cwd: str,
                 env: Mapping[str, str] | None) -> None:
        self._process = _popen(command, shell=shell, cwd=cwd, env=env, detached=False)
        assert self._process.stdout is not None and self._process.stderr is not None
        self._out = _Reader(self._process.stdout)
        self._err = _Reader(self._process.stderr)
        self._exited_at: float | None = None

    @property
    def pid(self) -> int:
        return self._process.pid

    def poll(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds; True once the program has exited and its output
        is read to the end, or :data:`DRAIN_GRACE` seconds after it exited (a child left
        running in the background may keep the pipes open)."""
        if self._exited_at is None:
            try:
                self._process.wait(timeout)
            except subprocess.TimeoutExpired:
                return False
            self._exited_at = time.monotonic()
        left = self._exited_at + DRAIN_GRACE - time.monotonic()
        for reader in (self._out, self._err):
            reader.join(max(0.0, min(timeout, left)))
        return (self._out.done and self._err.done) or left <= 0

    def result(self) -> Finished:
        code = self._process.returncode
        assert code is not None
        return Finished(exit_code=code, stdout=self._out.text(), stderr=self._err.text(),
                        truncated=self._out.truncated or self._err.truncated)

    def kill(self) -> None:
        """Kill the program and its children, then reap it. Never raises."""
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            _kill_tree(self._process)
        with contextlib.suppress(OSError):
            self._process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._process.wait(5.0)
        # The pipes close once every process holding them is gone.
        self._out.join(1.0)
        self._err.join(1.0)


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        # The program leads its own process group (start_new_session); the group outlives
        # its leader, so this also reaches children of a program that already exited.
        os.killpg(process.pid, signal.SIGKILL)


def describe(command: Command | Sequence[str]) -> str:
    """A command for people: a list joined as a POSIX shell would read it."""
    if isinstance(command, str):
        return command
    return shlex.join(command)
