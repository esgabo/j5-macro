"""End to end: ``python -m j5_macro.dap`` as a subprocess driving the real engine
(``J5_BACKEND=fake``), spoken to over framed stdio like VS Code does."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from j5_macro.cli.options import ENGINE_CMD_ENV
from j5_macro.dap.framing import FrameDecoder, encode_message

TIMEOUT_S = 30

SCRIPT = """\
version: 1
name: DAP end to end
params:
  who: {description: Name to greet}
  token: {secret: true}
vars:
  count: 0
  items: [1, {deep: true}]
steps:
  - id: hello
    log: "hi ${who} ${token}"
  - id: menu
    click: {x: 10, y: 20}
  - id: repeat
    loop:
      times: 2
      do:
        - id: bump
          set: {name: count, expr: "count + 1"}
  - id: done
    log: "count is ${count}"
"""

Message = dict[str, Any]


def line_of(text: str, needle: str) -> int:
    return next(n for n, line in enumerate(text.splitlines(), 1) if needle in line)


class DapProcess:
    """A DAP client over the adapter's stdio."""

    def __init__(self, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "j5_macro.dap"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.messages: queue.Queue[Message | None] = queue.Queue()
        self.seen: list[Message] = []
        self.stderr: list[bytes] = []
        self.seq = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        decoder = FrameDecoder()
        while chunk := self.proc.stdout.read1(65536):
            for message in decoder.feed(chunk):
                self.messages.put(message)
        self.messages.put(None)

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr.append(line)

    def send(self, command: str, arguments: Message | None = None) -> int:
        assert self.proc.stdin is not None
        self.seq += 1
        message: Message = {"seq": self.seq, "type": "request", "command": command}
        if arguments is not None:
            message["arguments"] = arguments
        self.proc.stdin.write(encode_message(message))
        self.proc.stdin.flush()
        return self.seq

    def wait_for(self, predicate: Callable[[Message], bool], what: str) -> Message:
        """The first message, already seen or new, that matches; it is consumed."""
        for index, message in enumerate(self.seen):
            if predicate(message):
                return self.seen.pop(index)
        deadline = time.monotonic() + TIMEOUT_S
        while True:
            remaining = deadline - time.monotonic()
            try:
                message = self.messages.get(timeout=max(0.01, remaining))
            except queue.Empty:
                message = None
                if remaining > 0:
                    continue
            if message is None:
                stderr = b"".join(self.stderr).decode("utf-8", "replace")
                pytest.fail(f"no {what}; seen {self.seen}\nstderr:\n{stderr}")
            if predicate(message):
                return message
            self.seen.append(message)

    def request(self, command: str, arguments: Message | None = None) -> Message:
        seq = self.send(command, arguments)
        response = self.wait_for(lambda m: m.get("type") == "response"
                                 and m.get("request_seq") == seq, f"{command} response")
        assert response["success"], response
        return response.get("body") or {}

    def event(self, name: str) -> Message:
        message = self.wait_for(lambda m: m.get("type") == "event" and m.get("event") == name,
                                f"{name} event")
        return message.get("body") or {}

    def stopped(self) -> Message:
        return self.event("stopped")

    def close(self) -> int:
        try:
            return self.proc.wait(timeout=TIMEOUT_S)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()


@pytest.fixture
def adapter_env(tmp_path: Path) -> dict[str, str]:
    env = {**os.environ, "J5_BACKEND": "fake", "J5_RUNS_DIR": str(tmp_path / "runs"),
           "J5_TEST_TOKEN": "s3cr3t-value"}
    env.pop(ENGINE_CMD_ENV, None)
    env.pop("J5_STOP_KEY", None)
    return env


def test_debug_session_end_to_end(tmp_path: Path, adapter_env: dict[str, str]) -> None:
    script = tmp_path / "flow.yaml"
    script.write_text(SCRIPT, encoding="utf-8")
    menu_line = line_of(SCRIPT, "- id: menu")
    bump_line = line_of(SCRIPT, "- id: bump")
    dap = DapProcess(adapter_env)
    try:
        caps = dap.request("initialize", {"adapterID": "j5", "linesStartAt1": True,
                                          "columnsStartAt1": True, "pathFormat": "path"})
        assert caps["supportsConfigurationDoneRequest"] is True
        assert caps["supportsSetVariable"] is True
        dap.event("initialized")

        dap.request("launch", {"program": str(script), "stopOnEntry": True, "seed": 3,
                               "fast": True, "params": {"who": "Ann"},
                               "paramsFromEnv": {"token": "J5_TEST_TOKEN"}})
        # A breakpoint on the `set:` line, inside step `bump`, moves to the step's line.
        body = dap.request("setBreakpoints", {"source": {"path": str(script)},
                                              "breakpoints": [{"line": bump_line + 1}]})
        (breakpoint,) = body["breakpoints"]
        assert breakpoint["verified"] is True
        assert breakpoint["line"] == bump_line

        dap.request("configurationDone")
        assert dap.stopped()["reason"] == "entry"
        threads = dap.request("threads")["threads"]
        assert threads == [{"id": 1, "name": "j5 run"}]

        dap.request("next")
        assert dap.stopped()["reason"] == "step"
        frames = dap.request("stackTrace", {"threadId": 1})["stackFrames"]
        assert frames[0]["name"] == "menu (click)"
        assert frames[0]["line"] == menu_line
        assert Path(frames[0]["source"]["path"]) == script

        (scope,) = dap.request("scopes", {"frameId": frames[0]["id"]})["scopes"]
        variables = {v["name"]: v for v in dap.request(
            "variables", {"variablesReference": scope["variablesReference"]})["variables"]}
        assert variables["who"]["value"] == '"Ann"'
        assert variables["token"]["value"] == '"***"'  # masked by the engine
        assert variables["count"]["value"] == "0"
        assert variables["items"]["variablesReference"] > 0
        items = dap.request("variables", {
            "variablesReference": variables["items"]["variablesReference"]})["variables"]
        assert [(v["name"], v["value"]) for v in items] == [("0", "1"),
                                                            ("1", '{"deep": true}')]

        result = dap.request("evaluate", {"expression": "count + 41", "context": "repl",
                                          "frameId": frames[0]["id"]})
        assert result["result"] == "41"

        dap.request("continue", {"threadId": 1})
        assert dap.stopped()["reason"] == "breakpoint"
        frames = dap.request("stackTrace", {"threadId": 1})["stackFrames"]
        assert [f["name"] for f in frames] == ["bump (set)",
                                               "repeat (loop) iteration 1 of 2"]
        assert frames[0]["line"] == bump_line

        dap.request("continue", {"threadId": 1})
        assert dap.stopped()["reason"] == "breakpoint"
        frames = dap.request("stackTrace", {"threadId": 1})["stackFrames"]
        assert frames[1]["name"] == "repeat (loop) iteration 2 of 2"

        dap.request("continue", {"threadId": 1})
        assert dap.event("exited")["exitCode"] == 0
        dap.event("terminated")
        outputs = [m["body"]["output"] for m in dap.seen
                   if m.get("type") == "event" and m.get("event") == "output"]
        assert any("hi Ann ***" in text for text in outputs)
        assert not any("s3cr3t-value" in text for text in outputs)

        dap.request("disconnect", {"terminateDebuggee": True})
        assert dap.close() == 0
    finally:
        if dap.proc.poll() is None:
            dap.proc.kill()
            dap.proc.wait()


def test_j5_dap_subcommand() -> None:
    from j5_macro.cli.main import build_parser

    assert build_parser().parse_args(["dap"]).command == "dap"


def test_launch_of_an_invalid_script_fails_clearly(tmp_path: Path,
                                                   adapter_env: dict[str, str]) -> None:
    script = tmp_path / "broken.yaml"
    script.write_text("version: 1\nsteps:\n  - id: a\n    nope: {}\n", encoding="utf-8")
    dap = DapProcess(adapter_env)
    try:
        dap.request("initialize", {"adapterID": "j5"})
        seq = dap.send("launch", {"program": str(script)})
        response = dap.wait_for(lambda m: m.get("type") == "response"
                                and m.get("request_seq") == seq, "launch response")
        assert response["success"] is False
        assert f"{script}:3" in response["message"]
        output = dap.event("output")
        assert output["category"] == "stderr"
        assert output["output"].startswith(f"{script}:3: ")
        dap.request("disconnect")
        assert dap.close() == 0
    finally:
        if dap.proc.poll() is None:
            dap.proc.kill()
            dap.proc.wait()
