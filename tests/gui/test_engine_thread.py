"""EngineThread without Qt: the engine client on its own thread, futures and callbacks."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from j5_macro.cli.release import ReleaseReport
from j5_macro.client import ClientClosed, EngineDied, EngineError
from j5_macro.gui.engine_thread import THREAD_NAME, EngineThread, describe_error, died_message
from j5_macro.protocol import Event

FAKE_ENGINE = Path(__file__).resolve().parent.parent / "fakes" / "fake_engine.py"
TIMEOUT = 20.0


class Recorder:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.threads: set[str] = set()
        self.died = threading.Event()
        self.death: tuple[EngineDied, str | None] | None = None
        self.got = threading.Condition()

    def on_event(self, event: Event) -> None:
        with self.got:
            self.threads.add(threading.current_thread().name)
            self.events.append(event)
            self.got.notify_all()

    def on_died(self, exc: EngineDied, released: str | None) -> None:
        self.death = (exc, released)
        self.died.set()

    def wait_event(self, type_: str) -> Event:
        with self.got:
            assert self.got.wait_for(lambda: any(e.type == type_ for e in self.events),
                                     TIMEOUT), f"no {type_} event"
            return next(e for e in self.events if e.type == type_)


def test_real_engine_on_a_thread(engine_env: dict[str, str], scripts: Path) -> None:
    recorder = Recorder()
    engine = EngineThread(on_event=recorder.on_event, on_died=recorder.on_died,
                          env=engine_env, cwd=str(scripts))
    assert isinstance(engine.call("list_scripts").exception(TIMEOUT), ClientClosed)
    started = engine.start()
    early = engine.call("list_scripts")  # sent before the handshake: waits for it
    info = started.result(TIMEOUT)
    try:
        assert info.backend == "fake" and info.stop_key == "ctrl+alt+shift+q"
        assert engine.alive and engine.pid is not None
        names = sorted(item["name"] for item in early.result(TIMEOUT)["scripts"])
        assert names == ["Bad", "Quick", "Slow"]
        with pytest.raises(EngineError) as failure:
            engine.call("load", {"path": "missing.yaml"}).result(TIMEOUT)
        assert failure.value.code == "script_not_found"
        assert recorder.wait_event("ready").run_id is None
        assert recorder.threads == {THREAD_NAME}
    finally:
        engine.close()
    assert not engine.alive and engine.closed
    with pytest.raises(ClientClosed):
        engine.call("list_scripts").result(TIMEOUT)
    engine.close()  # twice is fine
    assert recorder.death is None


def test_engine_death_releases_what_it_held_then_reports() -> None:
    recorder = Recorder()
    released: list[list[str]] = []

    def release(held: list[str]) -> ReleaseReport:
        released.append(held)
        return ReleaseReport(released=list(reversed(held)), backend="fake")

    engine = EngineThread([sys.executable, str(FAKE_ENGINE), "crash"],
                          on_event=recorder.on_event, on_died=recorder.on_died,
                          release=release)
    engine.start().result(TIMEOUT)
    try:
        call = engine.call("echo")  # the crash engine dies on the next command
        with pytest.raises(EngineDied):
            call.result(TIMEOUT)
        assert recorder.died.wait(TIMEOUT)
        assert recorder.death is not None
        exc, message = recorder.death
        assert released == [["ctrl", "mouse_left"]]
        assert message == "released what the engine held: mouse_left, ctrl (backend fake)"
        text = died_message(exc, message)
        assert text.startswith("The engine stopped unexpectedly (exit code 3). Released ")
        assert "RuntimeError: boom" in text
        assert describe_error(exc) == died_message(exc)
        assert not engine.alive
    finally:
        engine.close()


def test_a_command_that_cannot_launch_fails_the_start() -> None:
    engine = EngineThread([sys.executable, "-c", "import sys; sys.exit(4)"],
                          on_event=lambda event: None, on_died=lambda exc, released: None)
    start = engine.start()
    try:
        with pytest.raises(EngineDied) as failure:
            start.result(TIMEOUT)
        assert failure.value.exit_code == 4
        with pytest.raises(EngineDied):
            engine.call("list_scripts").result(TIMEOUT)
    finally:
        engine.close()
