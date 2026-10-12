"""The DAP server on stdio: ``j5 dap`` or ``python -m j5_macro.dap``.

Standard input and output carry framed DAP messages in binary; logs go to stderr. A
thread reads standard input (portable: asyncio cannot read a console or an anonymous pipe
on every platform) and hands chunks to the event loop, where :class:`DebugSession`
handles requests one at a time and starts the engine with :class:`EngineClient`
(``$J5_ENGINE_CMD`` overrides the command, as for the CLI).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import sys
import threading
from collections.abc import Callable, Sequence
from typing import Any, BinaryIO

from j5_macro.cli.options import engine_command
from j5_macro.client import EngineClient
from j5_macro.dap.framing import FrameDecoder, FramingError, encode_message
from j5_macro.dap.session import DeathHook, DebugSession, Engine, EngineFactory

CHUNK = 64 * 1024
log = logging.getLogger("j5_macro.dap")


def default_engine_factory(cmd: Sequence[str] | None = None) -> EngineFactory:
    async def start(on_death: DeathHook) -> Engine:
        command = list(cmd) if cmd is not None else engine_command()
        return await EngineClient.start(command, client_name="j5-dap", on_engine_died=on_death)

    return start


def _chunk_reader(stream: BinaryIO) -> Callable[[], bytes]:
    """Reads whatever is available, up to :data:`CHUNK` bytes; b"" at the end.

    A real file descriptor is read with ``os.read``: a daemon thread blocked inside a
    buffered stream holds its lock, and the interpreter aborts on exit when it cannot
    take that lock."""
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, io.UnsupportedOperation):
        read1 = getattr(stream, "read1", None)
        if read1 is not None:
            return lambda: bytes(read1(CHUNK))
        return lambda: stream.read(CHUNK)
    return lambda: os.read(fd, CHUNK)


async def serve(reader: BinaryIO, writer: BinaryIO,
                engine_factory: EngineFactory | None = None) -> int:
    """Serve one debug session until ``disconnect`` or the end of input."""
    loop = asyncio.get_running_loop()
    chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
    read_chunk = _chunk_reader(reader)

    def pump_stdin() -> None:
        try:
            while True:
                chunk = read_chunk()
                if not chunk:
                    break
                loop.call_soon_threadsafe(chunks.put_nowait, chunk)
        except (OSError, ValueError) as exc:
            log.debug("reading the client failed: %s", exc)
        finally:
            with contextlib.suppress(RuntimeError):  # the loop may be closed already
                loop.call_soon_threadsafe(chunks.put_nowait, None)

    write_lock = threading.Lock()

    def send(message: dict[str, Any]) -> None:
        data = encode_message(message)
        with write_lock:
            try:
                writer.write(data)
                writer.flush()
            except (OSError, ValueError) as exc:
                log.debug("writing to the client failed: %s", exc)

    session = DebugSession(send, engine_factory or default_engine_factory())
    threading.Thread(target=pump_stdin, name="j5-dap-stdin", daemon=True).start()
    decoder = FrameDecoder()
    closed = asyncio.ensure_future(session.closed.wait())
    exit_code = 0
    try:
        while not session.closed.is_set():
            next_chunk = asyncio.ensure_future(chunks.get())
            done, _ = await asyncio.wait({next_chunk, closed},
                                         return_when=asyncio.FIRST_COMPLETED)
            if next_chunk not in done:
                next_chunk.cancel()
                break
            chunk = next_chunk.result()
            if chunk is None:
                await session.shutdown()
                break
            try:
                messages = decoder.feed(chunk)
            except FramingError as exc:
                print(f"j5 dap: invalid input from the client: {exc}", file=sys.stderr)
                await session.shutdown()
                exit_code = 1
                break
            for error in decoder.errors:
                print(f"j5 dap: {error}", file=sys.stderr)
            decoder.errors.clear()
            for message in messages:
                await session.handle(message)
                if session.closed.is_set():
                    break
    finally:
        closed.cancel()
    return exit_code


def main() -> int:
    """Run the adapter on this process's stdio."""
    reader, writer = sys.stdin.buffer, sys.stdout.buffer
    if sys.platform == "win32":  # no CRLF translation at the descriptor level either
        import msvcrt

        for stream in (reader, writer):
            with contextlib.suppress(OSError, ValueError, io.UnsupportedOperation):
                msvcrt.setmode(stream.fileno(), os.O_BINARY)
    # Nothing but DAP may reach standard output: send stray prints to stderr.
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="j5 dap: %(levelname)s: %(message)s")
    try:
        return asyncio.run(serve(reader, writer))
    except KeyboardInterrupt:
        return 130
