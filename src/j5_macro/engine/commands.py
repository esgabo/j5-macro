"""Core command handlers and wiring: ``hello``, the script and run commands, and
not-yet-implemented stubs for the rest of the contract."""

from __future__ import annotations

import contextlib
import importlib.util
from pathlib import Path

from j5_macro.engine import editing as edit_commands
from j5_macro.engine import runs as run_commands
from j5_macro.engine import scripts as script_commands
from j5_macro.engine.backend import EngineBackend
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


VISION_MODULES = ("j5_macro.vision.toolkit", "cv2", "numpy")
"""What image steps need installed (the toolkit and the ``vision`` extra)."""


def vision_installed() -> bool:
    """True when the vision toolkit and its extra can be imported. Cheap: it looks the
    modules up without importing them."""
    try:
        return all(importlib.util.find_spec(name) is not None for name in VISION_MODULES)
    except (ImportError, ValueError):
        return False


def capabilities(registry: StepRegistry | None = None,
                 backend: EngineBackend | None = None) -> JsonObject:
    """What this engine offers: the active backend (None without one), its input
    strategies, the registered step types and whether vision is installed."""
    step_types = registry.names() if registry is not None else []
    return {"backend": None if backend is None else backend.name,
            "input_strategies": [] if backend is None else backend.input_strategies,
            "step_types": step_types,
            "vision": vision_installed()}


def ready_payload(registry: StepRegistry | None = None,
                  backend: EngineBackend | None = None) -> JsonObject:
    return {"v": PROTOCOL_VERSION, "backend": None if backend is None else backend.name,
            "capabilities": capabilities(registry, backend)}


def install(
    server: EngineServer,
    registry: StepRegistry,
    *,
    run_factory: RunFactory | None = None,
    runs_dir: Path | None = None,
    backend: EngineBackend | None = None,
) -> RunManager:
    """Register every contract command; the ones not built yet answer not_implemented.

    ``run_factory`` defaults to the interpreter's ``create_run`` (imported when a run
    starts); ``runs_dir`` None keeps runs in memory only. ``backend`` is the engine's
    input backend (see :mod:`j5_macro.engine.backend`); None leaves input unconfigured
    and logs nothing about it (embedding and tests).
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
        return {"v": server.negotiated_version,
                "capabilities": capabilities(registry, backend)}

    def not_implemented(command: Command) -> JsonObject:
        raise CommandError(ErrorCode.INVALID_STATE,
                           f"'{command.type}' is not implemented yet",
                           {"reason": "not_implemented"})

    for name in CONTRACT_COMMANDS:
        server.register(name, not_implemented)
    server.register("hello", hello, requires_hello=False)
    server.ready_payload = ready_payload(registry, backend)

    scripts = ScriptStore(registry)
    script_commands.install(server, scripts)
    edit_commands.install(server, scripts)
    manager = RunManager(server, scripts, run_factory, runs_dir, backend=backend)
    run_commands.install(server, manager)
    if backend is not None:
        install_backend(server, manager, backend)
    return manager


def install_backend(server: EngineServer, manager: RunManager, backend: EngineBackend) -> None:
    """After ``ready``: warn when there is no backend, or watch the emergency stop key.
    At shutdown (after the active run is stopped): stop watching and close the backend."""

    def stop_key_pressed() -> None:
        try:
            run_id = manager.stop_active(
                f"emergency stop key ({backend.stop_key}) pressed: stopping the run")
            if run_id is None:
                server.log("info", f"emergency stop key ({backend.stop_key}) pressed; "
                           "no run is active")
        except Exception as exc:
            server.log("error", f"emergency stop failed: {exc}")

    def ready() -> None:
        if backend.backend is None:
            server.log("warning", f"no input backend: {backend.reason}; input steps fail "
                       "with capability_missing (dry runs still work)")
            return
        message = backend.watch_stop_key(stop_key_pressed)
        level = "info" if message.startswith("emergency stop key:") else "warning"
        server.log(level, message)

    def shutdown() -> None:
        for problem in backend.close():
            with contextlib.suppress(OSError, ValueError):
                server.log("warning", problem)

    server.on_ready(ready)
    server.on_shutdown(shutdown)
