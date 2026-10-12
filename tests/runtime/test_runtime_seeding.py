"""Per-action sub-seeds and reproducibility (docs/contract.md, "Mouse and keyboard
masters": per-action seeds). Human plans are treated as black boxes: only seeds, the
points sent and the calls made are compared, never their shape or timing."""

from __future__ import annotations

import random
from typing import Any

import pytest
from runtime_helpers import Recorder, ScaledClock, load, run

from j5_macro.backends.fake import FakeBackend
from j5_macro.input.session import create_session
from j5_macro.protocol.errors import ErrorCode
from j5_macro.runtime import RunOptions, create_run
from j5_macro.runtime.seeding import derive_seed
from j5_macro.script.loader import load_script
from j5_macro.steps import StepContext, StepError, StepSpec, StepType, default_registry

# --- derive_seed ------------------------------------------------------------------------


def test_derive_seed_is_stable_and_pinned():
    # SHA-256 over a canonical JSON encoding: the same in every process and version.
    # Pinned values: changing them makes every recorded run replay differently.
    assert derive_seed(42, "go", [2, 0], 3) == 11526578168112484822
    assert derive_seed(1, "click_1", None, 1) == 1454659565768006918
    # Outside loops ``iterations`` is omitted: the same as an empty list.
    assert derive_seed(1, "click_1", None, 1) == derive_seed(1, "click_1", [], 1)


@pytest.mark.parametrize("other", [
    (43, "go", [2, 0], 3),
    (42, "go_2", [2, 0], 3),
    (42, "go", [2, 1], 3),
    (42, "go", [0, 2], 3),
    (42, "go", [2], 3),
    (42, "go", [2, 0], 2),
])
def test_derive_seed_depends_on_every_input(other):
    assert derive_seed(*other) != derive_seed(42, "go", [2, 0], 3)


def test_derive_seed_is_64_bits():
    seeds = {derive_seed(seed, "s", None, 1) for seed in range(200)}
    assert len(seeds) == 200
    assert all(0 <= s < 2 ** 64 for s in seeds)


# --- step generators --------------------------------------------------------------------


def test_each_loop_iteration_gets_its_own_sub_seed():
    script = """
        steps:
          - id: repeat
            loop:
              times: 3
              do:
                - id: r
                  rand:
    """
    first, recorder = run(script, seed=5)
    assert first.status == "completed"
    values = [p["result"]["value"] for p in recorder.of("step_finished", step_id="r")]
    assert len(set(values)) == 3
    for index, value in enumerate(values):
        assert value == random.Random(derive_seed(5, "r", [index], 1)).random()
    _, again = run(script, seed=5)
    assert [p["result"] for p in again.of("step_finished", step_id="r")] == \
        [{"value": v} for v in values]
    _, other = run(script, seed=6)
    assert [p["result"]["value"] for p in other.of("step_finished", step_id="r")] != values


def test_each_retry_attempt_gets_its_own_sub_seed():
    draws: list[float] = []

    def shaky(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
        draws.append(ctx.rng.random())
        if len(draws) < 3:
            raise StepError(ErrorCode.IMAGE_NOT_FOUND, "not yet")
        return {}

    registry = default_registry()
    registry.register(StepType(StepSpec(name="shaky", doc="Fails twice."), shaky))
    script = load_script("steps: [{id: s, shaky: , on_error: retry}]", lookup=registry.spec,
                         check=registry.check_params).script
    assert script is not None
    outcome = create_run(script, registry, Recorder(), RunOptions(seed=11),
                         clock=ScaledClock()).run()
    assert outcome.status == "completed"
    assert draws == [random.Random(derive_seed(11, "s", None, attempt)).random()
                     for attempt in (1, 2, 3)]
    assert len(set(draws)) == 3


# --- whole runs -------------------------------------------------------------------------

HUMAN_SCRIPT = """
    input:
      mouse: {strategy: human, speed: 4}
      keyboard: {strategy: human, wpm: 600}
    steps:
      - id: repeat
        loop:
          times: 2
          do:
            - id: go
              move: {x: 700, y: 400}
            - id: press
              click: {x: 300, y: 500}
            - id: pull
              drag: {from: {x: 400, y: 400}, to: {x: 600, y: 300}}
            - id: write
              type: "hi!"
            - id: save
              key: ctrl+s
"""


def human_run(seed: int) -> tuple[FakeBackend, Recorder]:
    backend = FakeBackend(pointer=(640, 360))
    recorder = Recorder()
    controller = create_run(load(HUMAN_SCRIPT), default_registry(), recorder,
                            RunOptions(seed=seed, backend=backend),
                            session_factory=create_session)
    outcome = controller.run()
    assert outcome.status == "completed", recorder.summary()
    return backend, recorder


def test_the_same_seed_replays_the_same_input():
    backend_a, events_a = human_run(1234)
    backend_b, events_b = human_run(1234)
    assert backend_a.calls
    assert backend_a.calls == backend_b.calls
    planned_a = events_a.of("input_planned")
    assert planned_a == events_b.of("input_planned")
    assert {p["strategy"] for p in planned_a} == {"human"}
    # every plan of the run has its own seed, iterations included
    assert len({p["seed"] for p in planned_a}) == len(planned_a)


def test_a_different_seed_plans_differently():
    _, events_a = human_run(1234)
    _, events_b = human_run(4321)
    seeds_a = [p["seed"] for p in events_a.of("input_planned")]
    seeds_b = [p["seed"] for p in events_b.of("input_planned")]
    assert len(seeds_a) == len(seeds_b)
    assert set(seeds_a).isdisjoint(seeds_b)
