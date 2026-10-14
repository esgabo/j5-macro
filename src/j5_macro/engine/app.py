"""Wiring: build a server with the core commands and run it over stdio."""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from j5_macro.backends.select import select_backend
from j5_macro.backends.windows.dpi import declare_dpi_awareness
from j5_macro.engine import commands
from j5_macro.engine.backend import (
    DEFAULT_STOP_KEY,
    STOP_KEY_ENV,
    EngineBackend,
    parse_combo,
    stop_key_from,
)
from j5_macro.engine.persistence import RUNS_DIR_ENV, resolve_runs_dir
from j5_macro.engine.server import EngineServer
from j5_macro.engine.transport import LineTransport, stdio_transport
from j5_macro.runtime.api import RunFactory
from j5_macro.steps import StepRegistry, registry_with_plugins
from j5_macro.vision import toolkit


def create_server(
    transport: LineTransport,
    *,
    registry: StepRegistry | None = None,
    run_factory: RunFactory | None = None,
    runs_dir: Path | None = None,
    backend: EngineBackend | None = None,
) -> EngineServer:
    """A server with every core command registered.

    ``registry`` defaults to the built-in steps plus the installed step plugins (a plugin
    that cannot be loaded becomes a ``warning`` log right after ``ready``),
    ``run_factory`` to the interpreter, and ``runs_dir`` None keeps run history in memory
    only (nothing is written to disk). ``backend`` None leaves input unconfigured (see
    :func:`commands.install`).
    """
    server = EngineServer(transport)
    skipped: list[str] = []
    if registry is None:
        registry, report = registry_with_plugins()
        skipped = [str(problem) for problem in report.skipped]
    commands.install(server, registry, run_factory=run_factory, runs_dir=runs_dir,
                     backend=backend)
    if skipped:
        def warn_skipped_plugins() -> None:
            for message in skipped:
                server.log("warning", message)

        server.on_ready(warn_skipped_plugins)
    return server


def run(transport: LineTransport, *, runs_dir: Path | None = None,
        backend: EngineBackend | None = None) -> int:
    """Serve until end of input; returns the process exit code."""
    return create_server(transport, runs_dir=runs_dir, backend=backend).serve()


def _combo(text: str) -> str:
    try:
        parse_combo(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return text


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m j5_macro.engine",
        description="j5-macro engine: speaks the JSON-lines protocol over stdin/stdout.",
    )
    parser.add_argument(
        "--runs-dir", metavar="DIR",
        help=f"where run history is saved (default: ${RUNS_DIR_ENV}, else the platform "
             "state directory, e.g. ~/.local/state/j5-macro/runs on Linux)",
    )
    parser.add_argument(
        "--stop-key", metavar="COMBO", type=_combo,
        help=f"global emergency stop key that stops the active run (default: "
             f"${STOP_KEY_ENV}, else {DEFAULT_STOP_KEY})",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of ``python -m j5_macro.engine``."""
    args = parse_args(argv)
    # Ctrl+C in a terminal reaches every process of the console, including this child.
    # The client owns the user's intent: it sends ``stop`` and then closes our stdin,
    # so the engine ignores SIGINT and ends on end of input instead of dying mid-run.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    # Before any window, pointer or capture call, so Windows never scales coordinates.
    declare_dpi_awareness()
    if sys.platform == "win32":
        # Before reading stdin: native libraries loaded later by a run's thread would
        # hang on the pipe read (see toolkit.preload).
        toolkit.preload()
    backend = EngineBackend(select_backend(), stop_key_from(args.stop_key))
    try:
        return run(stdio_transport(), runs_dir=resolve_runs_dir(args.runs_dir),
                   backend=backend)
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        # The client closed our stdout: nobody is listening any more.
        sys.stderr.write("j5 engine: client closed the output stream\n")
        return 0
