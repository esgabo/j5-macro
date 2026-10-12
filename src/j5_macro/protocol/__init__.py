"""Wire protocol shared by the engine and every client (see docs/contract.md)."""

from j5_macro.protocol.messages import (
    PROTOCOL_VERSION,
    Command,
    ErrorInfo,
    Event,
    Message,
    ProtocolError,
    Response,
    decode,
    encode,
)

__all__ = [
    "PROTOCOL_VERSION",
    "Command",
    "ErrorInfo",
    "Event",
    "Message",
    "ProtocolError",
    "Response",
    "decode",
    "encode",
]
