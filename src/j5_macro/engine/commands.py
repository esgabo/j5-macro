"""Core command handlers and wiring: ``hello``, the script and run commands, and
not-yet-implemented stubs for the rest of the contract."""

from __future__ import annotations

from pathlib import Path

from j5_macro.engine import runs as run_commands
from j5_macro.engine import scripts as script_commands
from j5_macro.engine.dispatch import CommandError
from j5_macro.engine.runs import RunManager
from j5_macro.engine.scripts import ScriptStore
from j5_macro.engine.server import EngineServer
from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import PROTOCOL_VERSION, Command, JsonObject
from j5_macro.protocol.payloads import HelloPayload
from j5_macro.runtime.api import RunFactory
from j5_macro.steps.registry import StepRegistry

SUPPORTED_VERSIONS = frozenset({PROTOCOL_VERSION})

CONTRACT_COMMANDS = (
    # docs/contract.md, "Commands"
    "hello", "list_step_types", "load", "validate", "run", "pause", "resume", "step_over",
    "step_into", "step_out", "stop", "set_breakpoints", "get_state", "set_variable",
    "evaluate", "get_events",
    # docs/contract.md, "Script management"
    "list_scripts", "create_script", "get_script", "edit_script", "list_runs", "get_run",
    "delete_run",
)


def capabilities(registry: StepRegistry | None = None) -> JsonObject:
    """What this engine offers; there is no backend or input strategy yet."""
    step_types = registry.names() if registry is not None else []
    return {"backend": None, "input_strategies": [], "step_types": step_types}


def ready_payload(registry: StepRegistry | None = None) -> JsonObject:
    return {"v": PROTOCOL_VERSION, "backend": None, "capabilities": capabilities(registry)}


def install(
    server: EngineServer,
    registry: StepRegistry,
    *,
    run_factory: RunFactory | None = None,
    runs_dir: Path | None = None,
) -> RunManager:
    """Register every contract command; the ones not built yet answer not_implemented.

    ``run_factory`` defaults to the interpreter's ``create_run`` (imported when a run
    starts); ``runs_dir`` None keeps runs in memory only.
    """

    def hello(command: Command) -> JsonObject:
        payload = HelloPayload.from_json(command.payload)
        v_min, v_max = payload.v_min, payload.v_max
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
        return {"v": server.negotiated_version, "capabilities": capabilities(registry)}

    def not_implemented(command: Command) -> JsonObject:
        raise CommandError(ErrorCode.INVALID_STATE,
                           f"'{command.type}' is not implemented yet",
                           {"reason": "not_implemented"})

    for name in CONTRACT_COMMANDS:
        server.register(name, not_implemented)
    server.register("hello", hello, requires_hello=False)
    server.ready_payload = ready_payload(registry)

    scripts = ScriptStore(registry)
    script_commands.install(server, scripts)
    manager = RunManager(server, scripts, run_factory, runs_dir)
    run_commands.install(server, manager)
    return manager
