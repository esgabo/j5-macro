"""Client library: launches the engine and speaks the protocol (docs/contract.md)."""

from j5_macro.client.engine_client import DEFAULT_COMMAND, DeathCallback, EngineClient
from j5_macro.client.errors import ClientClosed, ClientError, EngineDied, EngineError
from j5_macro.client.sequencer import EventSequencer

__all__ = [
    "DEFAULT_COMMAND",
    "ClientClosed",
    "ClientError",
    "DeathCallback",
    "EngineClient",
    "EngineDied",
    "EngineError",
    "EventSequencer",
]
