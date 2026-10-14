"""The DAP translator (:class:`DebugSession`) against a fake engine."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from dap_support import SCRIPT, FakeEngine, tree_of
from j5_macro.client import EngineError
from j5_macro.dap.session import CAPABILITIES, DebugSession
from j5_macro.protocol import ErrorInfo

Message = dict[str, Any]


def run(coro: Coroutine[Any, Any, None]) -> None:
    asyncio.run(asyncio.wait_for(coro, 10))


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


class Client:
    """Plays VS Code: sends requests and collects what the session sends back."""

    def __init__(self, tmp_path: Path, *, params: list[Message] | None = None,
                 environ: dict[str, str] | None = None, text: str = SCRIPT) -> None:
        self.program = tmp_path / "unit.yaml"
        self.program.write_text(text, encoding="utf-8")
        self.engine = FakeEngine(tree_of(text), params)
        self.sent: list[Message] = []
        self.seq = 0
        self.started = 0

        async def factory(hook: Callable[[list[str], int | None], Awaitable[None]]
                          ) -> FakeEngine:
            self.started += 1
            return self.engine

        self.session = DebugSession(self.sent.append, factory, environ or {})

    async def request(self, command: str, arguments: Message | None = None,
                      ok: bool = True) -> Message:
        self.seq += 1
        await self.session.handle({"seq": self.seq, "type": "request", "command": command,
                                   "arguments": arguments or {}})
        await settle()
        response = next(m for m in self.sent
                        if m["type"] == "response" and m["request_seq"] == self.seq)
        assert response["success"] is ok, response
        return response

    async def body(self, command: str, arguments: Message | None = None) -> Message:
        response = await self.request(command, arguments)
        body: Message = response.get("body") or {}
        return body

    def events(self, name: str) -> list[Message]:
        return [m.get("body") or {} for m in self.sent
                if m["type"] == "event" and m["event"] == name]

    def take(self, name: str) -> list[Message]:
        """The events ``name`` sent so far, removing them."""
        found = self.events(name)
        self.sent[:] = [m for m in self.sent
                        if not (m["type"] == "event" and m["event"] == name)]
        return found

    async def emit(self, type: str, payload: Message | None = None) -> None:
        self.engine.emit(type, payload)
        await settle()

    async def start(self, **launch: Any) -> None:
        """initialize, launch, configurationDone, run_started."""
        await self.request("initialize", {"adapterID": "j5"})
        await self.request("launch", {"program": str(self.program), **launch})
        await self.request("configurationDone")
        await self.emit("run_started", {"script_id": "s1", "mode": "step", "seed": 1})

    async def pause(self, reason: str, state: Message | None = None) -> Message:
        if state is not None:
            self.engine.state = state
        await self.emit("state_changed", {"state": "paused", "reason": reason})
        (stopped,) = self.take("stopped")
        return stopped


# -- initialize and launch ----------------------------------------------------------


def test_initialize_answers_capabilities_then_initialized(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        body = await client.body("initialize", {"adapterID": "j5"})
        assert body == CAPABILITIES
        assert body["exceptionBreakpointFilters"] == []
        kinds = [(m["type"], m.get("command") or m.get("event")) for m in client.sent]
        assert kinds == [("response", "initialize"), ("event", "initialized")]
        assert [m["seq"] for m in client.sent] == [1, 2]

    run(scenario())


@pytest.mark.parametrize(("launch", "payload"), [
    ({}, {"mode": "normal"}),
    ({"stopOnEntry": True}, {"mode": "step"}),
    ({"dryRun": True}, {"mode": "dry"}),
    ({"noDebug": True, "stopOnEntry": True}, {"mode": "normal"}),
    ({"seed": 42, "fast": True, "params": {"who": "Ann"}, "args": ["ignored"]},
     {"mode": "normal", "seed": 42, "params": {"who": "Ann"},
      "input_profile": {"mouse": {"strategy": "direct"}, "keyboard": {"strategy": "direct"}}}),
    ({"allowExec": True}, {"mode": "normal", "allow_exec": True}),
    ({"allowExec": "yes"}, {"mode": "normal"}),
    ({"displayScale": 1.5}, {"mode": "normal", "display_scale": 1.5}),
])
def test_launch_builds_the_run(tmp_path: Path, launch: Message, payload: Message) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start(**launch)
        assert client.engine.commands("load") == [{"path": str(client.program)}]
        assert client.engine.commands("run") == [{"script_id": "s1", **payload}]

    run(scenario())


def test_params_from_env(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path, environ={"TOKEN_VAR": "s3cret"},
                        params=[{"name": "token", "required": True, "secret": True}])
        await client.start(paramsFromEnv={"token": "TOKEN_VAR"})
        assert client.engine.commands("run")[0]["params"] == {"token": "s3cret"}

    run(scenario())


@pytest.mark.parametrize(("launch", "message"), [
    ({"paramsFromEnv": {"token": "MISSING"}}, "the environment variable MISSING is not set"),
    ({"params": "x"}, "'params' must be an object"),
    ({"seed": "1"}, "'seed' must be an integer"),
    ({"displayScale": 0}, "'displayScale' must be a positive number"),
    ({"displayScale": "150%"}, "'displayScale' must be a positive number"),
    ({"displayScale": True}, "'displayScale' must be a positive number"),
    ({"program": ""}, "launch needs 'program'"),
])
def test_bad_launch_arguments(tmp_path: Path, launch: Message, message: str) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.request("initialize")
        response = await client.request("launch", {"program": str(client.program), **launch},
                                        ok=False)
        assert message in response["message"]
        assert client.started == 0

    run(scenario())


def test_missing_required_param_fails_the_launch(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path, params=[{"name": "who", "required": True},
                                          {"name": "opt", "required": False}])
        response = await client.request("launch", {"program": str(client.program)}, ok=False)
        assert "needs the param(s) who" in response["message"]
        assert client.engine.closed

    run(scenario())


def test_load_errors_become_output_and_a_failed_launch(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        errors = [{"code": "unknown_step_type", "message": "unknown step type 'nope'",
                   "line": 3},
                  {"code": "validation_failed", "message": "bad", "line": 9}]

        def fail(payload: Message) -> Message:
            raise EngineError("load", ErrorInfo(code="unknown_step_type", message="x",
                                                details={"errors": errors}))

        client.engine.handlers["load"] = fail
        response = await client.request("launch", {"program": str(client.program)}, ok=False)
        path = str(client.program)
        assert response["message"] == (
            f"cannot load the script: {path}:3: unknown step type 'nope' [unknown_step_type]"
            " (and 1 more problem(s), see the Debug Console)")
        outputs = client.events("output")
        assert [o["output"] for o in outputs] == [
            f"{path}:3: unknown step type 'nope' [unknown_step_type]\n",
            f"{path}:9: bad [validation_failed]\n"]
        assert {o["category"] for o in outputs} == {"stderr"}
        assert client.engine.closed

    run(scenario())


def test_engine_that_cannot_start_fails_the_launch(tmp_path: Path) -> None:
    async def scenario() -> None:
        async def factory(hook: Any) -> Any:
            raise OSError("no such file")

        sent: list[Message] = []
        session = DebugSession(sent.append, factory, {})
        await session.handle({"seq": 1, "type": "request", "command": "launch",
                              "arguments": {"program": str(tmp_path / "x.yaml")}})
        assert sent[0]["success"] is False
        assert "cannot start the j5 engine: no such file" in sent[0]["message"]

    run(scenario())


# -- breakpoints --------------------------------------------------------------------


def test_breakpoints_before_launch_are_resolved_by_the_launch(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.request("initialize")
        body = await client.body("setBreakpoints", {
            "source": {"path": str(client.program)},
            "breakpoints": [{"line": 9}, {"line": 19}, {"line": 26}]})
        pending = "pending until the script is loaded"
        assert [(b["verified"], b["line"], b["message"]) for b in body["breakpoints"]] == [
            (False, 9, pending), (False, 19, pending), (False, 26, pending)]
        await client.request("launch", {"program": str(client.program)})
        assert client.engine.commands("set_breakpoints") == [
            {"script_id": "s1", "step_ids": ["first", "inner"]}]
        changed = client.events("breakpoint")
        assert [(c["reason"], c["breakpoint"]["id"], c["breakpoint"]["verified"],
                 c["breakpoint"]["line"]) for c in changed] == [
            ("changed", 1, True, 8), ("changed", 2, True, 19), ("changed", 3, False, 26)]
        # the events follow the launch response
        launch_index = next(i for i, m in enumerate(client.sent)
                            if m.get("command") == "launch")
        assert all(i > launch_index for i, m in enumerate(client.sent)
                   if m.get("event") == "breakpoint")

    run(scenario())


def test_breakpoints_after_launch(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        body = await client.body("setBreakpoints", {
            "source": {"path": str(client.program)},
            "breakpoints": [{"line": 11}, {"line": 10}, {"line": 21}, {"line": 30}]})
        results = [(b["verified"], b["line"]) for b in body["breakpoints"]]
        assert results == [(True, 11), (True, 11), (True, 15), (False, 30)]
        assert body["breakpoints"][0]["source"]["path"] == str(client.program)
        assert body["breakpoints"][3]["message"] == "no step at or after this line"
        assert client.engine.commands("set_breakpoints")[-1] == {
            "script_id": "s1", "step_ids": ["outer", "check"]}
        # clearing them
        await client.request("setBreakpoints", {"source": {"path": str(client.program)},
                                                "breakpoints": []})
        assert client.engine.commands("set_breakpoints")[-1]["step_ids"] == []

    run(scenario())


def test_breakpoints_in_another_file_are_not_verified(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        calls = len(client.engine.commands("set_breakpoints"))
        body = await client.body("setBreakpoints", {"source": {"path": str(tmp_path / "o.yaml")},
                                                    "breakpoints": [{"line": 8}]})
        (result,) = body["breakpoints"]
        assert result["verified"] is False
        assert "not the script being debugged (unit.yaml)" in result["message"]
        assert len(client.engine.commands("set_breakpoints")) == calls

    run(scenario())


def test_zero_based_lines(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.request("initialize", {"linesStartAt1": False, "columnsStartAt1": False})
        await client.request("launch", {"program": str(client.program), "stopOnEntry": True})
        body = await client.body("setBreakpoints", {"source": {"path": str(client.program)},
                                                    "breakpoints": [{"line": 8}]})
        # client line 8 is engine line 9, inside step `first` (engine line 8 = client 7)
        assert (body["breakpoints"][0]["verified"], body["breakpoints"][0]["line"]) == (True, 7)
        await client.request("configurationDone")
        await client.emit("run_started")
        await client.pause("step", {"state": "paused", "step_id": "inner",
                                    "path": [1, 0, 0, 0, 0], "line": 19, "variables": {}})
        frames = (await client.body("stackTrace", {"threadId": 1}))["stackFrames"]
        assert [(f["line"], f["column"]) for f in frames] == [(18, 0), (14, 0), (10, 0)]

    run(scenario())


def test_dry_run_stop_on_entry_uses_a_temporary_breakpoint(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.request("initialize")
        await client.request("setBreakpoints", {"source": {"path": str(client.program)},
                                                "breakpoints": [{"line": 19}]})
        await client.request("launch", {"program": str(client.program), "dryRun": True,
                                        "stopOnEntry": True})
        assert client.engine.commands("set_breakpoints")[-1]["step_ids"] == ["inner", "first"]
        await client.request("configurationDone")
        assert client.engine.commands("run")[0]["mode"] == "dry"
        await client.emit("run_started")
        stopped = await client.pause("breakpoint", {"state": "paused", "step_id": "first",
                                                    "path": [0], "line": 8, "variables": {}})
        assert stopped["reason"] == "entry"
        assert client.engine.commands("set_breakpoints")[-1]["step_ids"] == ["inner"]

    run(scenario())


# -- run control and stops ----------------------------------------------------------


@pytest.mark.parametrize(("reason", "expected"), [
    ("breakpoint", {"reason": "breakpoint"}),
    ("step", {"reason": "step"}),
    ("user", {"reason": "pause"}),
    ("interference", {"reason": "pause", "description": "user moved the mouse"}),
])
def test_stopped_reasons(tmp_path: Path, reason: str, expected: Message) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        stopped = await client.pause(reason)
        assert stopped == {"threadId": 1, "allThreadsStopped": True, **expected}

    run(scenario())


def test_entry_then_step(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start(stopOnEntry=True)
        assert (await client.pause("step"))["reason"] == "entry"
        await client.emit("state_changed", {"state": "running", "reason": "step"})
        assert client.take("continued") == [{"threadId": 1, "allThreadsContinued": True}]
        assert (await client.pause("step"))["reason"] == "step"

    run(scenario())


def test_error_pause_is_an_exception_with_the_message(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        await client.emit("step_started", {"step_id": "first", "type": "log", "line": 8})
        await client.emit("step_failed", {"step_id": "first", "line": 8, "error": {
            "code": "image_not_found", "message": "img/ok.png not found",
            "details": {"attempt": 2}}})
        stopped = await client.pause("error")
        assert stopped["reason"] == "exception"
        assert stopped["text"] == "img/ok.png not found"
        assert stopped["description"] == "step 'first' failed"
        failed = [o for o in client.events("output") if o["category"] == "stderr"]
        assert failed[-1]["output"] == ("step 'first' failed (attempt 2): [image_not_found] "
                                        "img/ok.png not found\n")
        assert failed[-1]["line"] == 8
        assert failed[-1]["source"]["path"] == str(client.program)

    run(scenario())


@pytest.mark.parametrize(("request_name", "command", "body"), [
    ("continue", "resume", {"allThreadsContinued": True}),
    ("next", "step_over", None),
    ("stepIn", "step_into", None),
    ("stepOut", "step_out", None),
    ("pause", "pause", None),
])
def test_control_requests(tmp_path: Path, request_name: str, command: str,
                          body: Message | None) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        response = await client.request(request_name, {"threadId": 1})
        assert response.get("body") == body
        assert client.engine.commands(command) == [{"run_id": "r1"}]

    run(scenario())


def test_control_without_a_run_fails(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.request("initialize")
        response = await client.request("next", {"threadId": 1}, ok=False)
        assert response["message"] == "no run is active"

    run(scenario())


def test_engine_errors_fail_the_request(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()

        def busy(payload: Message) -> Message:
            raise EngineError("step_over", ErrorInfo(code="invalid_state",
                                                     message="'step_over' needs a paused run"))

        client.engine.handlers["step_over"] = busy
        response = await client.request("next", {"threadId": 1}, ok=False)
        assert response["message"] == "'step_over' needs a paused run"

    run(scenario())


@pytest.mark.parametrize(("status", "code"), [("completed", 0), ("failed", 1),
                                              ("stopped", 3)])
def test_run_finished_exits_and_terminates(tmp_path: Path, status: str, code: int) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        await client.emit("run_finished", {"status": status, "duration_ms": 5})
        assert client.events("exited") == [{"exitCode": code}]
        assert client.events("terminated") == [{}]
        names = [m.get("event") for m in client.sent if m["type"] == "event"]
        assert names.index("exited") < names.index("terminated")
        # later events of the run are ignored
        await client.emit("log", {"level": "info", "message": "late"})
        assert not any("late" in o["output"] for o in client.events("output"))

    run(scenario())


def test_outputs(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        client.take("output")
        await client.emit("log", {"level": "info", "message": "hello ***", "step_id": "first"})
        await client.emit("log", {"level": "warning", "message": "careful"})
        await client.emit("user_interference", {"step_id": "first", "expected": [1, 2],
                                                "actual": [50, 60], "action": "pause"})
        await client.emit("step_started", {"step_id": "last", "type": "log", "line": 24})
        client.engine.emit("log", {"level": "info", "message": "engine note"}, run_id=None)
        await settle()
        outputs = [(o["category"], o["output"]) for o in client.events("output")]
        assert outputs == [
            ("console", "hello ***\n"),
            ("stderr", "warning: careful\n"),
            ("stderr", "you moved the mouse during step 'first' (expected [1, 2], found "
                       "[50, 60]); the run will pause\n"),
            ("console", "> last (log)\n"),
            ("console", "engine note\n"),
        ]
        assert client.events("output")[0]["line"] == 8

    run(scenario())


def test_no_debug_never_stops(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.request("initialize")
        await client.request("setBreakpoints", {"source": {"path": str(client.program)},
                                                "breakpoints": [{"line": 8}]})
        await client.request("launch", {"program": str(client.program), "noDebug": True})
        await client.request("configurationDone")
        assert client.engine.commands("set_breakpoints") == [{"script_id": "s1",
                                                              "step_ids": []}]
        await client.emit("run_started")
        await client.emit("state_changed", {"state": "paused", "reason": "breakpoint"})
        await client.emit("state_changed", {"state": "running", "reason": "user"})
        await client.emit("state_changed", {"state": "paused", "reason": "error"})
        assert client.events("stopped") == []
        assert client.events("continued") == []
        assert client.engine.commands("resume") == [{"run_id": "r1"}]
        assert client.engine.commands("stop") == [{"run_id": "r1", "reason": "user"}]

    run(scenario())


# -- inspection ---------------------------------------------------------------------


def test_threads(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        assert await client.body("threads") == {"threads": [{"id": 1, "name": "j5 run"}]}

    run(scenario())


def test_stack_trace_with_iterations_from_get_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        await client.emit("loop_iteration", {"step_id": "outer", "index": 0, "total": 2})
        await client.pause("breakpoint", {"state": "paused", "step_id": "inner",
                                          "path": [1, 0, 0, 0, 0], "line": 19,
                                          "iterations": [1], "variables": {}})
        body = await client.body("stackTrace", {"threadId": 1})
        assert body["totalFrames"] == 3
        assert [(f["id"], f["name"], f["line"], f["column"]) for f in body["stackFrames"]] == [
            (1, "inner (set)", 19, 1),
            (2, "check (if)", 15, 1),
            (3, "outer (loop) iteration 2 of 2", 11, 1)]
        assert body["stackFrames"][0]["source"] == {"name": "unit.yaml",
                                                    "path": str(client.program)}
        paged = await client.body("stackTrace", {"threadId": 1, "startFrame": 1, "levels": 1})
        assert [f["name"] for f in paged["stackFrames"]] == ["check (if)"]

    run(scenario())


def test_stack_trace_iterations_from_loop_events(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        await client.emit("loop_iteration", {"step_id": "outer", "index": 0})
        await client.emit("loop_iteration", {"step_id": "outer", "index": 1})
        await client.pause("step", {"state": "paused", "step_id": "check",
                                    "path": [1, 0, 0], "line": 15, "variables": {}})
        frames = (await client.body("stackTrace", {"threadId": 1}))["stackFrames"]
        assert [f["name"] for f in frames] == ["check (if)", "outer (loop) iteration 2"]

    run(scenario())


def test_stack_trace_before_the_first_step(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start(stopOnEntry=True)
        await client.pause("step", {"state": "paused", "variables": {}})
        frames = (await client.body("stackTrace", {"threadId": 1}))["stackFrames"]
        assert [(f["name"], f["line"]) for f in frames] == [("first (log)", 8)]

    run(scenario())


def test_stack_trace_without_a_run_is_empty(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        assert await client.body("stackTrace", {"threadId": 1}) == {"stackFrames": [],
                                                                    "totalFrames": 0}
        assert await client.body("scopes", {"frameId": 1}) == {"scopes": []}

    run(scenario())


VARIABLES = {"n": 3, "name": "Ann", "password": "***", "flag": True, "nothing": None,
             "data": {"list": [1, 2], "name": "x"}, "empty": []}


def test_variables_and_nested_values(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        await client.pause("step", {"state": "paused", "step_id": "first", "path": [0],
                                    "variables": VARIABLES})
        (scope,) = (await client.body("scopes", {"frameId": 1}))["scopes"]
        assert scope["name"] == "Variables"
        variables = (await client.body("variables", {
            "variablesReference": scope["variablesReference"]}))["variables"]
        shown = {v["name"]: (v["value"], v["type"]) for v in variables}
        assert shown == {"n": ("3", "integer"), "name": ('"Ann"', "string"),
                         "password": ('"***"', "string"), "flag": ("true", "boolean"),
                         "nothing": ("null", "null"),
                         "data": ('{"list": [1, 2], "name": "x"}', "map"),
                         "empty": ("[]", "list")}
        refs = {v["name"]: v["variablesReference"] for v in variables}
        assert refs["n"] == 0 and refs["empty"] == 0 and refs["data"] > 1
        data = (await client.body("variables", {"variablesReference": refs["data"]}))
        assert [(v["name"], v["value"], v["evaluateName"]) for v in data["variables"]] == [
            ("list", "[1, 2]", 'data["list"]'), ("name", '"x"', 'data["name"]')]
        inner = data["variables"][0]["variablesReference"]
        items = (await client.body("variables", {"variablesReference": inner}))["variables"]
        assert [(v["name"], v["evaluateName"]) for v in items] == [
            ("0", 'data["list"][0]'), ("1", 'data["list"][1]')]
        # one get_state per stop
        assert len(client.engine.commands("get_state")) == 1
        assert (await client.body("variables", {"variablesReference": 999})) == {
            "variables": []}

    run(scenario())


def test_evaluate(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        await client.pause("step", {"state": "paused", "variables": {}})
        client.engine.handlers["evaluate"] = lambda p: {"value": [1, {"a": "***"}]}
        for context in ("repl", "watch", "hover"):
            body = await client.body("evaluate", {"expression": "xs", "context": context})
            assert body["result"] == '[1, {"a": "***"}]'
            assert body["type"] == "list"
            assert body["variablesReference"] > 1
        assert client.engine.commands("evaluate")[0] == {"run_id": "r1", "expression": "xs"}

        def bad(payload: Message) -> Message:
            raise EngineError("evaluate", ErrorInfo(code="expression_error",
                                                    message="unknown variable 'x'"))

        client.engine.handlers["evaluate"] = bad
        response = await client.request("evaluate", {"expression": "x", "context": "watch"},
                                        ok=False)
        assert response["message"] == "unknown variable 'x'"

    run(scenario())


def test_evaluate_without_a_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        response = await client.request("evaluate", {"expression": "1"}, ok=False)
        assert "only be evaluated while the run is paused" in response["message"]

    run(scenario())


@pytest.mark.parametrize(("text", "value"), [("5", 5), ('"5"', "5"), ("hello", "hello"),
                                             ("[1, 2]", [1, 2]), ("null", None)])
def test_set_variable(tmp_path: Path, text: str, value: Any) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        await client.pause("step", {"state": "paused", "variables": {"n": 1}})
        body = await client.body("setVariable", {"variablesReference": 1, "name": "n",
                                                 "value": text})
        assert client.engine.commands("set_variable") == [{"run_id": "r1", "name": "n",
                                                           "value": value}]
        assert body["type"] in ("integer", "string", "list", "null")

    run(scenario())


def test_set_nested_variable(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        await client.pause("step", {"state": "paused", "variables": VARIABLES})
        variables = (await client.body("variables", {"variablesReference": 1}))["variables"]
        data_ref = next(v for v in variables if v["name"] == "data")["variablesReference"]
        data = (await client.body("variables", {"variablesReference": data_ref}))["variables"]
        list_ref = data[0]["variablesReference"]
        await client.body("variables", {"variablesReference": list_ref})
        body = await client.body("setVariable", {"variablesReference": list_ref, "name": "1",
                                                 "value": "20"})
        assert body["value"] == "20"
        assert client.engine.commands("set_variable") == [{
            "run_id": "r1", "name": "data", "value": {"list": [1, 20], "name": "x"}}]

    run(scenario())


def test_set_nested_variable_holding_a_secret_is_refused(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        await client.pause("step", {"state": "paused",
                                    "variables": {"login": {"user": "a", "pass": "***"}}})
        variables = (await client.body("variables", {"variablesReference": 1}))["variables"]
        ref = variables[0]["variablesReference"]
        response = await client.request("setVariable", {"variablesReference": ref,
                                                        "name": "user", "value": "b"},
                                        ok=False)
        assert "masked secret" in response["message"]
        assert client.engine.commands("set_variable") == []

    run(scenario())


# -- ending -------------------------------------------------------------------------


def test_terminate_stops_the_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()
        await client.request("terminate")
        assert client.engine.commands("stop") == [{"run_id": "r1", "reason": "user"}]
        await client.emit("run_finished", {"status": "stopped", "reason": "user"})
        assert client.events("exited") == [{"exitCode": 3}]
        assert len(client.events("terminated")) == 1

    run(scenario())


def test_terminate_without_a_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.request("terminate")
        assert client.events("terminated") == [{}]
        assert client.events("exited") == []

    run(scenario())


def test_disconnect_stops_waits_and_closes(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        await client.start()

        def stop(payload: Message) -> Message:
            client.engine.emit("run_finished", {"status": "stopped", "reason": "user"})
            return {}

        client.engine.handlers["stop"] = stop
        await client.request("disconnect", {"terminateDebuggee": True})
        assert client.engine.commands("stop") == [{"run_id": "r1", "reason": "user"}]
        assert client.engine.closed
        assert client.session.closed.is_set()
        assert client.events("exited") == [{"exitCode": 3}]
        assert client.sent[-1]["command"] == "disconnect"

    run(scenario())


def test_unknown_request_fails(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = Client(tmp_path)
        response = await client.request("restart", ok=False)
        assert response["message"] == "unsupported request 'restart'"
        assert (await client.body("setExceptionBreakpoints", {"filters": []})) == {
            "breakpoints": []}

    run(scenario())
