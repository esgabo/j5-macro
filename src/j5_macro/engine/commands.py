"""Core command handlers: ``hello``, ``get_events`` and not-yet-implemented stubs."""

from __future__ import annotations

from j5_macro.engine.dispatch import CommandError, require_int, require_str
from j5_macro.engine.server import EngineServer
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import PROTOCOL_VERSION, Command, JsonObject

SUPPORTED_VERSIONS = frozenset({PROTOCOL_VERSION})

CONTRACT_COMMANDS = (
    # docs/contract.md, "Comandos"
    "hello", "list_step_types", "load", "validate", "run", "pause", "resume", "step_over",
    "step_into", "step_out", "stop", "set_breakpoints", "get_state", "set_variable",
    "evaluate", "get_events",
    # docs/contract.md, "Gestión de scripts"
    "list_scripts", "create_script", "get_script", "edit_script", "list_runs", "get_run",
    "delete_run",
)


def capabilities() -> JsonObject:
    """What this engine offers; no backend, strategies or step types exist yet."""
    return {"backend": None, "input_strategies": [], "step_types": []}


def ready_payload() -> JsonObject:
    return {"v": PROTOCOL_VERSION, "backend": None, "capabilities": capabilities()}


def install(server: EngineServer) -> None:
    """Register the core handlers; every other contract command answers not_implemented."""

    def hello(command: Command) -> JsonObject:
        v_min = require_int(command.payload, "v_min")
        v_max = require_int(command.payload, "v_max")
        require_str(command.payload, "client")
        if v_min > v_max:
            raise CommandError(ErrorCode.VALIDATION_FAILED,
                               f"v_min ({v_min}) is greater than v_max ({v_max})")
        overlap = [v for v in SUPPORTED_VERSIONS if v_min <= v <= v_max]
        if not overlap:
            supported = sorted(SUPPORTED_VERSIONS)
            raise CommandError(
                ErrorCode.VALIDATION_FAILED,
                f"no common protocol version: client supports {v_min}..{v_max}, "
                f"engine supports {supported}",
                {"engine_versions": supported},
            )
        server.negotiated_version = max(overlap)
        return {"v": server.negotiated_version, "capabilities": capabilities()}

    def get_events(command: Command) -> JsonObject:
        run_id = require_str(command.payload, "run_id", nullable=True)
        from_seq = require_int(command.payload, "from_seq")
        if run_id is not None:
            raise CommandError(ErrorCode.NOT_RUNNING, f"unknown run_id: {run_id!r}")
        return {"events": [event.to_json() for event in server.buffered_events(from_seq)]}

    def not_implemented(command: Command) -> JsonObject:
        raise CommandError(ErrorCode.INVALID_STATE,
                           f"'{command.type}' is not implemented yet",
                           {"reason": "not_implemented"})

    for name in CONTRACT_COMMANDS:
        server.register(name, not_implemented)
    server.register("hello", hello, requires_hello=False)
    server.register("get_events", get_events)
