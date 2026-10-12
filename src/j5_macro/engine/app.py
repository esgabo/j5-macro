"""Wiring: build a server with the core commands and run it over stdio."""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from j5_macro.engine import commands
from j5_macro.engine.persistence import RUNS_DIR_ENV, resolve_runs_dir
from j5_macro.engine.server import EngineServer
from j5_macro.engine.transport import LineTransport, stdio_transport
from j5_macro.runtime.api import RunFactory
from j5_macro.steps import StepRegistry, default_registry


def create_server(
    transport: LineTransport,
    *,
    registry: StepRegistry | None = None,
    run_factory: RunFactory | None = None,
    runs_dir: Path | None = None,
) -> EngineServer:
    """A server with every core command registered.

    ``registry`` defaults to the built-in steps, ``run_factory`` to the interpreter, and
    ``runs_dir`` None keeps run history in memory only (nothing is written to disk).
    """
    server = EngineServer(transport)
    commands.install(server, registry if registry is not None else default_registry(),
                     run_factory=run_factory, runs_dir=runs_dir)
    return server


def run(transport: LineTransport, *, runs_dir: Path | None = None) -> int:
    """Serve until end of input; returns the process exit code."""
    return create_server(transport, runs_dir=runs_dir).serve()


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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of ``python -m j5_macro.engine``."""
    args = parse_args(argv)
    # Ctrl+C in a terminal reaches every process of the console, including this child.
    # The client owns the user's intent: it sends ``stop`` and then closes our stdin,
    # so the engine ignores SIGINT and ends on end of input instead of dying mid-run.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return run(stdio_transport(), runs_dir=resolve_runs_dir(args.runs_dir))
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        # The client closed our stdout: nobody is listening any more.
        sys.stderr.write("j5 engine: client closed the output stream\n")
        return 0
