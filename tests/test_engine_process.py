"""Integration test: run ``python -m j5_macro.engine`` as a subprocess over stdio."""

from __future__ import annotations

import io
import json
import queue
import subprocess
import sys
import threading
from typing import Any

TIMEOUT_S = 20


def read_json_line(lines: queue.Queue[str | None]) -> dict[str, Any]:
    line = lines.get(timeout=TIMEOUT_S)
    assert line is not None, "engine closed stdout unexpectedly"
    data: dict[str, Any] = json.loads(line)
    return data


def test_engine_process_handshake_and_clean_exit() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "j5_macro.engine"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdin is not None and proc.stdout is not None
    # Explicit UTF-8 and "\n" on both ends, independent of the platform locale.
    stdin = io.TextIOWrapper(proc.stdin, encoding="utf-8", newline="\n", write_through=True)
    stdout = io.TextIOWrapper(proc.stdout, encoding="utf-8", newline="\n")
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        for line in stdout:
            lines.put(line)
        lines.put(None)

    # A reader thread lets every read time out, so a hang fails instead of blocking.
    threading.Thread(target=pump, daemon=True).start()
    try:
        ready = read_json_line(lines)
        assert ready["kind"] == "event"
        assert ready["type"] == "ready"
        assert ready["seq"] == 1

        hello = {"v": 1, "kind": "command", "type": "hello", "id": "1",
                 "payload": {"v_min": 1, "v_max": 1, "client": "pytest ✓"}}
        stdin.write(json.dumps(hello, ensure_ascii=False) + "\n")
        stdin.flush()
        resp = read_json_line(lines)
        assert resp["kind"] == "response"
        assert resp["id"] == "1"
        assert resp["ok"] is True
        assert resp["result"]["v"] == 1

        stdin.close()
        assert proc.wait(timeout=TIMEOUT_S) == 0
        assert lines.get(timeout=TIMEOUT_S) is None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if not stdin.closed:
            stdin.close()
