"""Integration test: run ``python -m j5_macro.engine`` as a subprocess over stdio."""

from __future__ import annotations

import io
import json
import os
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


def read_until(lines: queue.Queue[str | None], kind: str,
               seen: list[dict[str, Any]]) -> dict[str, Any]:
    """The next message of ``kind``; messages skipped on the way go to ``seen``."""
    while True:
        message = read_json_line(lines)
        if message["kind"] == kind:
            return message
        seen.append(message)


def handshake(backend: str, *args: str) -> tuple[dict[str, Any], dict[str, Any],
                                                  list[dict[str, Any]]]:
    """Start the engine with ``J5_BACKEND=backend``, say hello and close stdin; returns
    ``ready``, the hello response and the other events, after a clean exit."""
    env = {**os.environ, "J5_BACKEND": backend}
    env.pop("J5_STOP_KEY", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "j5_macro.engine", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
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
    seen: list[dict[str, Any]] = []
    try:
        ready = read_json_line(lines)
        assert ready["kind"] == "event"
        assert ready["type"] == "ready"
        assert ready["seq"] == 1

        hello = {"v": 1, "kind": "command", "type": "hello", "id": "1",
                 "payload": {"v_min": 1, "v_max": 1, "client": "pytest ✓"}}
        stdin.write(json.dumps(hello, ensure_ascii=False) + "\n")
        stdin.flush()
        resp = read_until(lines, "response", seen)
        assert resp["id"] == "1"
        assert resp["ok"] is True
        assert resp["result"]["v"] == 1

        stdin.close()
        assert proc.wait(timeout=TIMEOUT_S) == 0
        while (line := lines.get(timeout=TIMEOUT_S)) is not None:
            seen.append(json.loads(line))
        return ready, resp, seen
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if not stdin.closed:
            stdin.close()


def test_engine_process_handshake_and_clean_exit() -> None:
    ready, hello, events = handshake("none")
    assert ready["payload"]["backend"] is None
    assert hello["result"]["capabilities"]["input_strategies"] == []
    warnings = [e["payload"]["message"] for e in events
                if e["type"] == "log" and e["payload"]["level"] == "warning"]
    assert len(warnings) == 1
    assert "no input backend: disabled by J5_BACKEND=none" in warnings[0]


def test_engine_process_reports_its_backend_and_stop_key() -> None:
    ready, hello, events = handshake("fake", "--stop-key", "ctrl+F12")
    assert ready["payload"]["backend"] == "fake"
    capabilities = hello["result"]["capabilities"]
    assert capabilities["backend"] == "fake"
    assert capabilities["input_strategies"] == ["direct", "linear", "human"]
    logs = [e["payload"] for e in events if e["type"] == "log"]
    assert logs == [{"level": "info", "message": "emergency stop key: ctrl+f12"}]


def test_engine_process_rejects_a_bad_stop_key() -> None:
    result = subprocess.run([sys.executable, "-m", "j5_macro.engine", "--stop-key", "ctrl+"],
                            capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    assert result.returncode == 2
    assert "bad key combination" in result.stderr
