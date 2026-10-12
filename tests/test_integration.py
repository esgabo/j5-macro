"""End to end: the real EngineClient against the real engine process.

Every payload that crosses the wire is also checked against the typed payloads,
so the engine, the client and ``j5_macro.protocol.payloads`` cannot drift apart.
"""

import asyncio

import pytest

from j5_macro.client import EngineClient, EngineError
from j5_macro.protocol import (
    PROTOCOL_VERSION,
    ErrorCode,
    GetEventsPayload,
    GetEventsResult,
    HelloResult,
    parse_event_payload,
)

TIMEOUT = 20


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, TIMEOUT))


def test_handshake_and_ready_match_typed_payloads():
    async def scenario():
        async with await EngineClient.start(client_name="integration-test") as client:
            assert client.version == PROTOCOL_VERSION
            HelloResult.from_json({"v": client.version, "capabilities": client.capabilities})
            assert client.ready_event is not None
            assert client.ready_event.seq == 1
            assert client.ready_event.run_id is None
            parse_event_payload("ready", client.ready_event.payload)

    run(scenario())


def test_get_events_with_typed_payload_returns_engine_stream():
    async def scenario():
        async with await EngineClient.start() as client:
            payload = GetEventsPayload(from_seq=1).to_json()
            assert "run_id" not in payload  # null fields are omitted on the wire
            result = GetEventsResult.from_json(await client.call("get_events", payload))
            assert [event["type"] for event in result.events][:1] == ["ready"]

    run(scenario())


def test_invalid_payload_reports_field_path():
    async def scenario():
        async with await EngineClient.start() as client:
            with pytest.raises(EngineError) as info:
                await client.call("get_events", {"from_seq": "one"})
            assert info.value.error.code == ErrorCode.VALIDATION_FAILED
            assert "from_seq" in info.value.error.message

    run(scenario())


def test_events_stream_starts_with_ready_and_close_exits_cleanly():
    async def scenario():
        client = await EngineClient.start()
        events = client.events()
        first = await anext(events)
        assert (first.type, first.seq) == ("ready", 1)
        assert await client.close() == 0

    run(scenario())
