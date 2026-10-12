"""EngineClient against the fake engine in tests/fakes (no real engine needed)."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from j5_macro.client import ClientClosed, EngineClient, EngineDied, EngineError
from j5_macro.protocol import Event

FAKE_ENGINE = Path(__file__).parent / "fakes" / "fake_engine.py"
TEST_TIMEOUT = 20.0


def fake(mode: str = "normal") -> list[str]:
    return [sys.executable, str(FAKE_ENGINE), mode]


def run(coro: Coroutine[Any, Any, Any], timeout: float = TEST_TIMEOUT) -> Any:
    """Run a coroutine with a hard timeout so a bug fails fast instead of hanging CI."""
    return asyncio.run(asyncio.wait_for(coro, timeout))


def keys(events: list[Event]) -> list[tuple[str | None, int, str]]:
    return [(e.run_id, e.seq, e.type) for e in events]


async def collect(client: EngineClient, count: int) -> list[Event]:
    events: list[Event] = []
    async for event in client.events():
        events.append(event)
        if len(events) == count:
            break
    return events


def test_start_waits_for_ready_and_negotiates_hello() -> None:
    async def scenario() -> None:
        client = await EngineClient.start(fake(), client_name="tests")
        assert client.ready_event is not None and client.ready_event.type == "ready"
        assert client.version == 1
        assert client.capabilities["backend"] == "fake"
        assert client.capabilities["input_strategies"] == ["direct"]
        assert await client.close() == 0

    run(scenario())


def test_concurrent_requests_are_correlated_with_events_interleaved() -> None:
    async def scenario() -> None:
        async with await EngineClient.start(fake()) as client:
            emit = client.call("emit", {"events": [{"type": "log"}] * 3})
            echoes = [client.call("echo", {"n": n, "text": "año"}) for n in range(5)]
            results = await asyncio.gather(emit, *echoes)
            assert results[0] == {}
            assert [r["echo"]["n"] for r in results[1:]] == list(range(5))
            assert results[1]["echo"]["text"] == "año"
            events = await collect(client, 4)
            assert keys(events) == [(None, 1, "ready"), (None, 2, "log"), (None, 3, "log"),
                                    (None, 4, "log")]

    run(scenario())


def test_out_of_order_responses_reach_the_right_caller() -> None:
    async def scenario() -> None:
        async with await EngineClient.start(fake("out_of_order")) as client:
            first = asyncio.create_task(client.call("echo", {"name": "first"}))
            await asyncio.sleep(0.05)
            second = asyncio.create_task(client.call("echo", {"name": "second"}))
            await asyncio.wait({first, second})
            assert first.result() == {"echo": {"name": "first"}}
            assert second.result() == {"echo": {"name": "second"}}
            events = await collect(client, 3)
            assert [e.payload.get("message") for e in events[1:]] == ["before second",
                                                                     "before first"]

    run(scenario())


def test_call_raises_engine_error_and_request_returns_the_response() -> None:
    async def scenario() -> None:
        async with await EngineClient.start(fake()) as client:
            with pytest.raises(EngineError) as info:
                await client.call("fail")
            assert info.value.code == "validation_failed"
            assert info.value.error.message == "fail on purpose"
            response = await client.request("fail")
            assert not response.ok and response.error is not None

    run(scenario())


def test_request_timeout_leaves_the_client_usable() -> None:
    async def scenario() -> None:
        async with await EngineClient.start(fake("out_of_order")) as client:
            with pytest.raises(TimeoutError):
                await client.call("echo", {"never": "answered"}, timeout=0.2)
            with pytest.raises(EngineError):
                await client.call("fail")

    run(scenario())


def test_gaps_are_filled_with_get_events_and_duplicates_dropped() -> None:
    async def scenario() -> None:
        async with await EngineClient.start(fake("gap")) as client:
            events = await collect(client, 9)
            assert keys(events) == [
                (None, 1, "ready"),
                ("r-1", 1, "run_started"),
                ("r-1", 2, "step_started"),
                ("r-1", 3, "step_finished"),  # recovered
                ("r-1", 4, "log"),  # recovered
                ("r-1", 5, "from_the_future"),  # unknown type passes through
                ("r-1", 6, "run_finished"),  # the resent seq 2 was dropped
                (None, 2, "log"),  # recovered on the engine stream
                (None, 3, "log"),
            ]
            assert events[5].payload == {"new": True}
            assert events[7].payload == {"message": "lost"}

    run(scenario())


def test_duplicate_events_are_suppressed() -> None:
    async def scenario() -> None:
        async with await EngineClient.start(fake()) as client:
            await client.call("emit", {"events": [
                {"type": "log", "run_id": "r-9"},
                {"resend": 1, "run_id": "r-9"},
                {"resend": 1},
                {"type": "log", "run_id": "r-9"},
            ]})
            events = await collect(client, 3)
            assert keys(events) == [(None, 1, "ready"), ("r-9", 1, "log"), ("r-9", 2, "log")]

    run(scenario())


def test_engine_death_fails_requests_and_reports_held_keys() -> None:
    async def scenario() -> None:
        reports: list[tuple[list[str], int | None]] = []

        async def on_died(held: list[str], exit_code: int | None) -> None:
            reports.append((held, exit_code))

        client = await EngineClient.start(fake("crash"), on_engine_died=on_died)
        with pytest.raises(EngineDied) as info:
            await client.call("echo")
        assert info.value.exit_code == 3
        assert "RuntimeError: boom" in info.value.stderr_tail
        assert "boom" in str(info.value)
        assert reports == [(["ctrl", "mouse_left"], 3)]
        assert client.held == ["ctrl", "mouse_left"]

        seen: list[Event] = []
        with pytest.raises(EngineDied):
            async for event in client.events():
                seen.append(event)
        assert [e.type for e in seen] == ["ready", "input_state", "input_state"]
        with pytest.raises(EngineDied):
            await client.call("echo")
        assert await client.close() == 3
        assert len(reports) == 1

    run(scenario())


def test_sync_death_callback_and_no_callback_on_normal_close() -> None:
    async def scenario() -> None:
        reports: list[tuple[list[str], int | None]] = []
        client = await EngineClient.start(fake(), on_engine_died=lambda *a: reports.append(a))
        await client.close()
        assert reports == []
        with pytest.raises(ClientClosed):
            await client.call("echo")
        assert [e.type async for e in client.events()] == ["ready"]

        client = await EngineClient.start(fake("crash"),
                                          on_engine_died=lambda *a: reports.append(a))
        with pytest.raises(EngineDied):
            await client.request("echo")
        await client.close()
        assert reports == [(["ctrl", "mouse_left"], 3)]

    run(scenario())


def test_start_fails_with_engine_died_when_the_engine_exits_early() -> None:
    script = "import sys; sys.stderr.write('cannot start\\n'); sys.exit(2)"

    async def scenario() -> None:
        with pytest.raises(EngineDied) as info:
            await EngineClient.start([sys.executable, "-c", script])
        assert info.value.exit_code == 2
        assert info.value.stderr_tail == ["cannot start"]

    run(scenario())


def test_start_times_out_without_ready() -> None:
    script = "import time; time.sleep(60)"

    async def scenario() -> None:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await EngineClient.start([sys.executable, "-c", script], ready_timeout=0.5)
        assert time.monotonic() - started < 10

    run(scenario())


def test_noisy_stderr_never_blocks_and_keeps_the_tail() -> None:
    async def scenario() -> None:
        client = await EngineClient.start(fake("noisy"))
        assert await client.call("echo", {"x": 1}) == {"echo": {"x": 1}}
        assert await client.close() == 0
        tail = client.stderr_tail
        assert len(tail) == 50
        assert tail[-1] == "bye"
        assert tail[-2].startswith("exit noise 999 ñandú")

    run(scenario())


@pytest.mark.parametrize("mode", ["hang", "stubborn"])
def test_close_terminates_or_kills_a_hung_engine(mode: str) -> None:
    async def scenario() -> None:
        died: list[Any] = []
        client = await EngineClient.start(fake(mode), on_engine_died=lambda *a: died.append(a))
        started = time.monotonic()
        code = await client.close(timeout=0.5)
        assert code is not None and code != 0
        assert time.monotonic() - started < 5
        assert died == []

    run(scenario())
