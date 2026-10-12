"""``pause`` never cuts an input action in half (docs/contract.md, "Commands", ``pause``):
the real input session on the fake backend, with the pause requested from inside the
action, which completes before the run pauses ahead of the next step."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from runtime_input_helpers import InputRun

from j5_macro.backends.fake import FakeBackend
from j5_macro.input.session import create_session


@dataclass
class PausingBackend(FakeBackend):
    """Calls :attr:`hook` once, right after the ``at``-th ``char_down`` or pointer move."""

    at: int = 3
    hook: Callable[[], None] | None = None
    _count: int = field(default=0, repr=False)

    def _tick(self) -> None:
        self._count += 1
        if self._count == self.at and self.hook is not None:
            self.hook()

    def char_down(self, char: str) -> None:
        super().char_down(char)
        self._tick()

    def _moved(self) -> None:
        super()._moved()
        self._tick()


def pause_midway(script: str, backend: PausingBackend) -> InputRun:
    """Start ``script`` and request a pause from inside its first input action."""
    started = threading.Event()
    holder: list[InputRun] = []

    def request_pause() -> None:
        started.wait(1.0)
        holder[0].controller.pause()

    backend.hook = request_pause
    run = InputRun(script, factory=create_session,  # type: ignore[arg-type]
                   backend=backend, scale=1.0)
    holder.append(run)
    started.set()
    run.recorder.wait_paused()
    return run


def assert_paused_after(run: InputRun, step_id: str) -> None:
    summary = run.recorder.summary()
    assert summary[-1] == "state_changed:paused/user"
    assert f"step_finished:{step_id}" in summary
    assert summary.index("input_executed") < summary.index(f"step_finished:{step_id}")
    assert "step_started:after" not in summary
    assert all(not p["aborted"] for p in run.recorder.of("input_executed"))
    assert run.recorder.of("keys_released") == []
    assert run.controller.state()["step_id"] == "after"


def test_pause_during_type_lets_the_whole_text_be_typed():
    backend = PausingBackend(at=3)
    run = pause_midway("""
        input:
          keyboard: {strategy: linear, wpm: 300, variance: 0}
        steps:
          - id: t
            type: hello world
          - id: after
            log: done
    """, backend)
    assert backend.typed_text() == "hello world"
    assert backend.held_chars == [] and backend.held_keys == []
    assert_paused_after(run, "t")
    run.controller.resume()
    assert run.join().status == "completed"
    assert run.recorder.of("log")[-1]["message"] == "done"


def test_pause_during_drag_completes_the_drag():
    backend = PausingBackend(at=4, pointer=(100, 100))
    run = pause_midway("""
        input:
          mouse: {strategy: linear, speed: 2}
        steps:
          - id: pull
            drag: {from: {x: 100, y: 100}, to: {x: 900, y: 500}}
          - id: after
            log: done
    """, backend)
    assert backend._count > 4  # the movement went on after the request
    assert backend.pointer == (900, 500)
    assert backend.held_buttons == []
    names = [c.name for c in backend.calls]
    assert names.count("button_down") == 1 and names[-1] == "button_up"
    assert_paused_after(run, "pull")
    run.controller.resume()
    assert run.join().status == "completed"
