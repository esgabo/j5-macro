"""Debug Adapter Protocol server: debug j5 scripts from VS Code (docs/contract.md,
"Debug Adapter Protocol")."""

from j5_macro.dap.framing import FrameDecoder, FramingError, encode_message
from j5_macro.dap.session import DapError, DebugSession, Engine, EngineFactory

__all__ = [
    "DapError",
    "DebugSession",
    "Engine",
    "EngineFactory",
    "FrameDecoder",
    "FramingError",
    "encode_message",
]
