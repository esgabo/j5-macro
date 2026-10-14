"""EngineBridge: results, errors and events reach the GUI thread through signals."""

from __future__ import annotations

import threading
from typing import Any

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from gui_support import WAIT, wait_for

from j5_macro.client import ClientClosed, EngineError
from j5_macro.gui.bridge import EngineBridge


def test_callbacks_run_later_on_the_gui_thread(qtbot: Any, ctx: Any) -> None:
    bridge: EngineBridge = ctx.bridge
    assert bridge.running and bridge.info is not None and bridge.info.backend == "fake"
    calls: list[tuple[str, Any, bool]] = []
    gui = threading.current_thread()

    bridge.call("list_step_types",
                on_result=lambda result: calls.append(
                    ("ok", result, threading.current_thread() is gui)))
    bridge.call("load", {"path": "missing.yaml"},
                on_error=lambda exc: calls.append(
                    ("error", exc, threading.current_thread() is gui)))
    assert calls == []  # never inside call()
    wait_for(qtbot, lambda: len(calls) == 2)
    by_kind = {kind: (value, on_gui) for kind, value, on_gui in calls}
    assert by_kind["ok"][1] and by_kind["error"][1]
    assert any(t["name"] == "click" for t in by_kind["ok"][0]["step_types"])
    error = by_kind["error"][0]
    assert isinstance(error, EngineError) and error.code == "script_not_found"


def test_events_arrive_as_signals(qtbot: Any, ctx: Any, scripts: Any) -> None:
    received: list[str] = []
    ctx.bridge.event_received.connect(lambda event: received.append(event.type))
    ids: list[str] = []
    ctx.bridge.call("load", {"path": str(scripts / "quick.yaml")},
                    on_result=lambda result: ids.append(result["script_id"]))
    wait_for(qtbot, lambda: ids)
    with qtbot.waitSignal(ctx.bridge.event_received, timeout=WAIT,
                          check_params_cb=lambda event: event.type == "run_finished"):
        ctx.bridge.call("run", {"script_id": ids[0], "mode": "dry"})
    assert received[0] == "run_started" and "step_started" in received


def test_shutdown_then_calls_fail_and_start_again(qtbot: Any, ctx: Any) -> None:
    bridge: EngineBridge = ctx.bridge
    with qtbot.waitSignal(bridge.running_changed, timeout=WAIT):
        bridge.shutdown()
    assert not bridge.running and bridge.info is None
    errors: list[BaseException] = []
    bridge.call("list_scripts", on_error=errors.append)
    wait_for(qtbot, lambda: errors)
    assert isinstance(errors[0], ClientClosed)
    with qtbot.waitSignal(bridge.engine_started, timeout=WAIT):
        bridge.start()
    assert bridge.running


def test_an_engine_that_cannot_start_is_reported(qtbot: Any, engine_env: Any) -> None:
    import sys

    bridge = EngineBridge([sys.executable, "-c", "import sys; sys.exit(5)"], env=engine_env)
    try:
        with qtbot.waitSignal(bridge.engine_failed, timeout=WAIT) as failed:
            bridge.start()
        assert "exit code 5" in failed.args[0]
        assert not bridge.running
    finally:
        bridge.shutdown()
