"""The engine's backend: capabilities, run_started.backend, the emergency stop key, the
no-backend warning and closing it (J5_BACKEND=fake, real interpreter)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from engine_harness import Engine

from j5_macro.backends import select_backend
from j5_macro.backends.base import Capabilities, Rect
from j5_macro.backends.fake import FakeBackend
from j5_macro.backends.select import BackendChoice
from j5_macro.engine.backend import (
    DEFAULT_STOP_KEY,
    STOP_KEY_ENV,
    EngineBackend,
    parse_combo,
    stop_key_from,
)
from j5_macro.protocol.payloads import parse_event_payload
from j5_macro.protocol.payloads.commands import HelloResult
from j5_macro.protocol.payloads.events import (
    LogEvent,
    ReadyEvent,
    RunFinishedEvent,
    RunStartedEvent,
)

SLOW = "steps:\n  - id: nap\n    sleep: 100s\n  - id: after\n    log: done\n"
STOP_KEYS = ["ctrl", "alt", "shift", "q"]


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("J5_BACKEND", "fake")
    monkeypatch.delenv(STOP_KEY_ENV, raising=False)


@pytest.fixture
def engine(fake_env: None) -> Iterator[Engine]:
    engine = Engine(backend=EngineBackend(select_backend(), stop_key_from(None)))
    yield engine
    engine.close()


def fake_of(engine: Engine) -> FakeBackend:
    backend = engine.runs.backend
    assert backend is not None and isinstance(backend.backend, FakeBackend)
    return backend.backend


def typed(message: dict[str, Any]) -> Any:
    parsed = parse_event_payload(message["type"], message["payload"])
    assert parsed is not None
    return parsed


def test_ready_and_hello_report_the_backend(engine: Engine) -> None:
    ready = typed(engine.engine_events("ready")[0])
    assert isinstance(ready, ReadyEvent)
    assert ready.backend == "fake"
    assert ready.capabilities.backend == "fake"
    assert ready.capabilities.input_strategies == ["direct", "linear", "human"]
    hello = HelloResult.from_json(engine.ok("hello", {"v_min": 1, "v_max": 1,
                                                      "client": "tests"}))
    assert hello.capabilities.backend == "fake"
    assert hello.capabilities.input_strategies == ["direct", "linear", "human"]
    log = typed(engine.engine_events("log")[0])
    assert log == LogEvent(level="info", message=f"emergency stop key: {DEFAULT_STOP_KEY}")
    assert fake_of(engine).hotkeys[0][0] == STOP_KEYS


def test_ready_and_hello_report_the_display_scales(fake_env: None) -> None:
    fake = FakeBackend(monitors=[(Rect(0, 0, 1920, 1080), 1.0),
                                 (Rect(1920, 0, 2560, 1440), 1.5)])
    engine = Engine(backend=EngineBackend(BackendChoice("fake", fake, "test")))
    try:
        expected = [{"bbox": [0, 0, 1920, 1080], "scale": 1.0},
                    {"bbox": [1920, 0, 2560, 1440], "scale": 1.5}]
        ready = typed(engine.engine_events("ready")[0])
        assert ready.capabilities.display_scales == expected
        hello = HelloResult.from_json(engine.ok("hello", {"v_min": 1, "v_max": 1,
                                                          "client": "tests"}))
        assert hello.capabilities.display_scales == expected
    finally:
        engine.close()


def test_run_started_reports_the_display_scale(fake_env: None) -> None:
    fake = FakeBackend(monitors=[(Rect(0, 0, 1920, 1080), 1.25)])
    engine = Engine(backend=EngineBackend(BackendChoice("fake", fake, "test")))
    try:
        script_id = engine.load_source("capture_scale: 1.5\nsteps:\n  - log: hi\n")
        detected = engine.start(script_id)
        started = typed(engine.event("run_started", detected))
        assert (started.display_scale, started.display_scale_source,
                started.capture_scale) == (1.25, "detected", 1.5)
        engine.wait_finished(detected)
        forced = engine.start(script_id, display_scale=2)
        started = typed(engine.event("run_started", forced))
        assert (started.display_scale, started.display_scale_source) == (2.0, "run")
        engine.wait_finished(forced)
        assert engine.runs.get(forced).options.display_scale == 2.0
        error = engine.error("run", {"script_id": script_id, "mode": "normal",
                                     "display_scale": 0})
        assert error["code"] == "validation_failed"
        assert "display_scale" in error["message"]
    finally:
        engine.close()


def test_run_started_without_a_backend_defaults_to_1() -> None:
    engine = Engine()
    try:
        run_id = engine.start(engine.load_source())
        payload = engine.event("run_started", run_id)["payload"]
        assert (payload["display_scale"], payload["display_scale_source"],
                payload["capture_scale"]) == (1.0, "default", 1.0)
        engine.wait_finished(run_id)
    finally:
        engine.close()


@pytest.mark.parametrize("mode", ["normal", "dry"])
def test_run_started_names_the_backend(engine: Engine, mode: str) -> None:
    run_id = engine.start(engine.load_source(), mode=mode)
    started = typed(engine.event("run_started", run_id))
    assert isinstance(started, RunStartedEvent)
    assert started.backend == "fake"
    engine.wait_finished(run_id)


def test_runs_get_the_backend(engine: Engine) -> None:
    run_id = engine.start(engine.load_source())
    engine.wait_finished(run_id)
    run = engine.runs.get(run_id)
    assert run.options.backend is fake_of(engine)
    assert run.options.backend_reason == "selected by J5_BACKEND=fake"


def test_the_stop_key_stops_the_active_run(engine: Engine) -> None:
    run_id = engine.start(engine.load_source(SLOW))
    engine.event("step_started", run_id)
    fake_of(engine).press_hotkey(STOP_KEYS)
    finished = typed(engine.wait_finished(run_id))
    assert isinstance(finished, RunFinishedEvent)
    assert (finished.status, finished.reason) == ("stopped", "user")
    logs = [typed(m) for m in engine.run_events(run_id) if m["type"] == "log"]
    assert logs == [LogEvent(level="warning", message=f"emergency stop key "
                             f"({DEFAULT_STOP_KEY}) pressed: stopping the run")]
    types = [m["type"] for m in engine.run_events(run_id)]
    assert types.index("log") < types.index("run_finished")
    assert not [m for m in engine.run_events(run_id)
                if m["type"] == "step_started" and m["payload"]["step_id"] == "after"]


def test_the_stop_key_without_a_run_only_logs(engine: Engine) -> None:
    fake_of(engine).press_hotkey(STOP_KEYS)
    engine.writer.wait_for(lambda m: m.get("type") == "log"
                           and "no run is active" in m["payload"]["message"])


def test_the_backend_is_closed_and_unwatched_at_shutdown(fake_env: None) -> None:
    engine = Engine(backend=EngineBackend(select_backend(), "ctrl+f12"))
    fake = fake_of(engine)
    assert fake.hotkeys[0][0] == ["ctrl", "f12"]
    engine.close()
    assert fake.closed
    assert fake.hotkeys == []


def test_no_backend_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("J5_BACKEND", "none")
    engine = Engine(backend=EngineBackend(select_backend()))
    try:
        logs = [typed(m) for m in engine.engine_events("log")]
        assert len(logs) == 1 and logs[0].level == "warning"
        assert logs[0].message.startswith("no input backend: disabled by J5_BACKEND=none")
        ready = typed(engine.engine_events("ready")[0])
        assert ready.backend is None and ready.capabilities.input_strategies == []
        run_id = engine.start(engine.load_source("steps:\n  - id: c\n    click: {x: 1, y: 1}\n"))
        assert typed(engine.event("run_started", run_id)).backend == "none"
        finished = typed(engine.wait_finished(run_id))
        assert finished.error.code == "capability_missing"
        assert "disabled by J5_BACKEND=none" in finished.error.message
    finally:
        engine.close()


def test_a_backend_without_hotkeys_warns() -> None:
    fake = FakeBackend(capabilities=Capabilities(
        pointer_position=True, move_abs=True, move_rel=True, char_input=True,
        screen_capture=False, windows=True, hotkey=False))
    engine = Engine(backend=EngineBackend(BackendChoice("fake", fake, "test")))
    try:
        logs = [typed(m) for m in engine.engine_events("log")]
        assert logs[0].level == "warning"
        assert "cannot watch global hotkeys" in logs[0].message
        assert fake.hotkeys == []
    finally:
        engine.close()


def test_a_bad_stop_key_from_the_environment_warns(fake_env: None) -> None:
    engine = Engine(backend=EngineBackend(select_backend(),
                                          stop_key_from(None, {STOP_KEY_ENV: "ctrl+nope"})))
    try:
        logs = [typed(m) for m in engine.engine_events("log")]
        assert logs[0].level == "warning"
        assert "unknown key 'nope'" in logs[0].message
    finally:
        engine.close()


# --- stop key parsing -------------------------------------------------------------------


@pytest.mark.parametrize(("text", "keys"), [
    ("ctrl+alt+shift+q", STOP_KEYS),
    ("Control+Esc", ["ctrl", "escape"]),
    (" super + F12 ", ["super", "f12"]),
    ("ctrl+/", ["ctrl", "/"]),
])
def test_parse_combo(text: str, keys: list[str]) -> None:
    assert parse_combo(text) == keys


@pytest.mark.parametrize("text", ["", "ctrl+", "ctrl+alt", "ctrl+ctrl+q", "ctrl+banana"])
def test_parse_combo_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_combo(text)


def test_stop_key_precedence() -> None:
    assert stop_key_from("ctrl+f1", {STOP_KEY_ENV: "ctrl+f2"}) == "ctrl+f1"
    assert stop_key_from(None, {STOP_KEY_ENV: "ctrl+f2"}) == "ctrl+f2"
    assert stop_key_from(None, {}) == DEFAULT_STOP_KEY


def test_capabilities_report_whether_vision_is_installed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from j5_macro.engine import commands

    installed = {"j5_macro.vision.toolkit", "cv2", "numpy"}
    monkeypatch.setattr(commands.importlib.util, "find_spec",
                        lambda name: object() if name in installed else None)
    assert commands.capabilities()["vision"] is True
    installed.discard("cv2")
    assert commands.capabilities()["vision"] is False
    engine = Engine()
    try:
        hello = HelloResult.from_json(engine.ok("hello", {"v_min": 1, "v_max": 1,
                                                          "client": "tests"}))
        assert hello.capabilities.vision is False
    finally:
        engine.close()
