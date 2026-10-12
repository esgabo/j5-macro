"""Wiring: build a server with the core commands and run it over stdio."""

from __future__ import annotations

import sys

from j5_macro.engine import commands
from j5_macro.engine.server import EngineServer
from j5_macro.engine.transport import LineTransport, stdio_transport


def create_server(transport: LineTransport) -> EngineServer:
    """A server with every core command registered."""
    server = EngineServer(transport)
    commands.install(server)
    return server


def run(transport: LineTransport) -> int:
    """Serve until end of input; returns the process exit code."""
    return create_server(transport).serve(commands.ready_payload())


def main() -> int:
    """Entry point of ``python -m j5_macro.engine``."""
    try:
        return run(stdio_transport())
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        # The client closed our stdout: nobody is listening any more.
        sys.stderr.write("j5 engine: client closed the output stream\n")
        return 0
