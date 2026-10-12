import random
import threading

import pytest
from input_helpers import Stopped, make

from j5_macro.backends import Button, Capabilities, Rect, UnknownKey
from j5_macro.backends.fake import FakeBackend
from j5_macro.input import (
    EmergencyStop,
    InputSession,
    KeyboardProfile,
    MouseProfile,
    UserInterference,
)
from j5_macro.input.planning import plan_move
from j5_macro.input.session import create_session, in_corner
from j5_macro.protocol.payloads.events import (
    InputExecutedEvent,
    InputPlannedEvent,
    InputStateEvent,
    KeysReleasedEvent,
    UserInterferenceEvent,
)

LINEAR = MouseProfile(strategy="linear")
DIRECT = MouseProfile(strategy="direct")
KB_LINEAR = KeyboardProfile(strategy="linear", wpm=60)
KB_DIRECT = KeyboardProfile(strategy="direct")


def rng(seed: int = 1) -> random.Random:
    return random.Random(seed)


def caps(**overrides: bool) -> Capabilities:
    base = dict(pointer_position=True, move_abs=True, move_rel=True, char_input=True,
                screen_capture=False, windows=True, hotkey=True)
    base.update(overrides)
    return Capabilities(**base)


def test_create_session_is_an_input_session():
    session = create_session(FakeBackend(), lambda name, payload: None, object())
    assert isinstance(session.held, list)
    _: InputSession = session


# --- mouse ------------------------------------------------------------------------------


def test_linear_move_executes_the_plan_on_the_clock():
    h = make()
    h.session.move(120, 0, profile=LINEAR, rng=rng())
    plan = plan_move((0, 0), (120, 0), LINEAR, rng())
    assert h.calls() == [("move_abs", p.x, p.y) for p in plan.points]
    assert h.calls()[-1] == ("move_abs", 120, 0)
    assert h.clock.now == pytest.approx(0.1)  # 120 px at 1200 px/s
    assert h.events.names() == ["input_planned", "input_executed"]
    planned = InputPlannedEvent.from_json(h.events.of("input_planned")[0])
    assert planned == InputPlannedEvent(device="mouse", strategy="linear", seed=plan.seed,
                                        start=[0, 0], to=[120, 0], duration_ms=100)
    assert h.events.of("input_planned")[0]["from"] == [0, 0]
    executed = InputExecutedEvent.from_json(h.events.of("input_executed")[0])
    assert executed == InputExecutedEvent(device="mouse", actual_duration_ms=100,
                                          aborted=False)


def test_execution_follows_elapsed_time_not_fixed_sleeps():
    late = {"done": False}

    def lag(now: float) -> None:
        if not late["done"]:  # the first wake-up arrives 30 ms late
            late["done"] = True
            h.clock.now += 0.03

    h = make(on_wake=lag)
    h.session.move(1200, 0, profile=LINEAR, rng=rng())
    assert h.clock.now == pytest.approx(1.0)
    assert h.events.of("input_executed")[0]["actual_duration_ms"] == 1000


def test_direct_move_jumps_without_waiting():
    h = make()
    h.session.move(300, 200, profile=DIRECT, rng=rng())
    assert h.calls() == [("move_abs", 300, 200)]
    assert h.abort.sleeps == []
    assert h.abort.checks >= 1


def test_move_starts_from_the_real_pointer():
    h = make(FakeBackend(pointer=(50, 60)))
    h.session.move(60, 60, profile=DIRECT, rng=rng())
    assert h.events.of("input_planned")[0]["from"] == [50, 60]


def test_click_when_already_there_only_presses():
    h = make(FakeBackend(pointer=(10, 10)))
    h.session.click(10, 10, button=Button.LEFT, clicks=2, profile=DIRECT, rng=rng())
    assert h.calls() == [("button_down", Button.LEFT), ("button_up", Button.LEFT)] * 2
    assert h.events.of("input_state") == [
        {"held": ["button:left"]}, {"held": []}, {"held": ["button:left"]}, {"held": []}]
    assert "input_planned" not in h.events.names()


def test_linear_click_moves_then_clicks_with_gaps():
    h = make()
    times: list[tuple[str, float]] = []
    backend = h.backend
    down, up = backend.button_down, backend.button_up
    backend.button_down = lambda b: (times.append(("down", h.clock.now)), down(b))[1]
    backend.button_up = lambda b: (times.append(("up", h.clock.now)), up(b))[1]
    h.session.click(120, 0, button=Button.RIGHT, clicks=2, profile=LINEAR, rng=rng())
    assert h.calls("move_abs")[-1] == ("move_abs", 120, 0)
    assert h.events.names() == ["input_planned", "input_executed"] + ["input_state"] * 4
    assert h.events.of("input_state")[0] == {"held": ["button:right"]}
    (_, d1), (_, u1), (_, d2), (_, u2) = times
    assert d1 == pytest.approx(0.1)
    assert u1 - d1 == pytest.approx(0.04)
    assert 0.05 <= d2 - u1 <= 0.08
    assert u2 - d2 == pytest.approx(0.04)


def test_drag_moves_presses_moves_releases():
    h = make()
    h.session.drag((10, 10), (20, 10), button=Button.LEFT, profile=DIRECT, rng=rng())
    assert h.calls() == [("move_abs", 10, 10), ("button_down", Button.LEFT),
                         ("move_abs", 20, 10), ("button_up", Button.LEFT)]
    assert h.events.names() == ["input_planned", "input_executed", "input_state",
                                "input_planned", "input_executed", "input_state"]
    assert h.session.held == []


def test_scroll_notches():
    h = make()
    h.session.scroll(0, -3, profile=LINEAR, rng=rng())
    assert h.calls() == [("scroll", 0, -1)] * 3
    assert h.clock.now == pytest.approx(0.1)
    assert h.events.of("input_planned")[0]["duration_ms"] == 100
    d = make()
    d.session.scroll(2, 5, profile=DIRECT, rng=rng())
    assert d.calls() == [("scroll", 2, 5)]


def test_move_rel_only_backend_sends_deltas():
    backend = FakeBackend(capabilities=caps(move_abs=False), pointer=(100, 100))
    h = make(backend)
    h.session.move(400, 500, profile=LINEAR, rng=rng())
    rel = h.calls("move_rel")
    assert h.calls("move_abs") == []
    assert sum(c[1] for c in rel) == 300
    assert sum(c[2] for c in rel) == 400
    assert backend.pointer == (400, 500)


def test_blind_move_rel_only_backend_homes_the_pointer_first():
    backend = FakeBackend(capabilities=caps(move_abs=False, pointer_position=False),
                          pointer=(700, 300))
    h = make(backend)
    h.session.move(50, 40, profile=DIRECT, rng=rng())
    assert h.calls() == [("move_rel", -1920, -1080), ("move_rel", 50, 40)]
    h.session.move(60, 40, profile=DIRECT, rng=rng())
    assert h.calls()[-1] == ("move_rel", 10, 0)


# --- keyboard ---------------------------------------------------------------------------


def test_type_text_uses_chars_and_named_keys():
    h = make()
    h.session.type_text("añ日😀\n\t", profile=KB_DIRECT, rng=rng())
    assert h.backend.typed_text() == "añ日😀"
    assert h.calls("key_down", "key_up") == [
        ("key_down", "enter"), ("key_up", "enter"), ("key_down", "tab"), ("key_up", "tab")]
    assert h.calls()[:2] == [("char_down", "a"), ("char_up", "a")]
    assert h.events.of("input_state")[2] == {"held": ["char:ñ"]}
    planned = h.events.of("input_planned")[0]
    assert planned["device"] == "keyboard"
    assert planned["chars"] == 6
    assert "wpm" not in planned
    assert h.abort.sleeps == []


def test_linear_typing_takes_the_planned_time():
    h = make()
    h.session.type_text("abc", profile=KB_LINEAR, rng=rng())
    assert h.backend.typed_text() == "abc"
    assert h.clock.now == pytest.approx(0.46)
    assert h.events.of("input_planned")[0]["wpm"] == 60
    assert h.events.of("input_executed")[0] == {
        "device": "keyboard", "actual_duration_ms": 460, "aborted": False}


def test_press_combo_order_and_held_state():
    snapshots: list[list[str]] = []
    h = make(on_wake=lambda now: snapshots.append(h.session.held))
    h.session.press("ctrl+shift+s", hold=0.5, profile=KB_LINEAR, rng=rng())
    assert h.calls() == [
        ("key_down", "ctrl"), ("key_down", "shift"), ("key_down", "s"),
        ("key_up", "s"), ("key_up", "shift"), ("key_up", "ctrl")]
    assert ["ctrl", "shift", "s"] in snapshots
    assert [p["held"] for p in h.events.of("input_state")] == [
        ["ctrl"], ["ctrl", "shift"], ["ctrl", "shift", "s"], ["ctrl", "shift"], ["ctrl"], []]
    assert h.clock.now == pytest.approx(0.62)  # downs 0/30/60 ms, ups 560/590/620 ms
    assert h.session.held == []
    assert "keys_released" not in h.events.names()


def test_press_rejects_bad_combo_before_doing_anything():
    h = make()
    with pytest.raises(ValueError, match="not a modifier"):
        h.session.press("s+ctrl", hold=None, profile=KB_LINEAR, rng=rng())
    assert h.calls() == []
    assert h.events.events == []


def test_backend_errors_propagate_and_release():
    class PickyBackend(FakeBackend):
        def char_down(self, char: str) -> None:
            if char == "☃":
                raise UnknownKey("cannot type ☃")
            super().char_down(char)

    h = make(PickyBackend())
    h.session.press("shift", hold=None, profile=KB_DIRECT, rng=rng())
    with pytest.raises(UnknownKey):
        h.session.type_text("a☃", profile=KB_DIRECT, rng=rng())
    assert h.events.of("input_executed")[-1]["aborted"] is True
    assert h.session.held == []


# --- held state, stop and release --------------------------------------------------------


def test_stop_mid_movement_reraises_and_reports_aborted():
    h = make(stop_at=0.05)
    with pytest.raises(Stopped):
        h.session.move(1200, 0, profile=LINEAR, rng=rng())
    assert 0 < len(h.calls("move_abs")) < 10
    executed = h.events.of("input_executed")[0]
    assert executed == {"device": "mouse", "actual_duration_ms": 50, "aborted": True}


def test_stop_mid_drag_releases_the_button():
    h = make(stop_at=0.2)
    with pytest.raises(Stopped):
        h.session.drag((0, 0), (1200, 0), button=Button.LEFT, profile=LINEAR, rng=rng())
    assert h.calls()[-1] == ("button_up", Button.LEFT)
    assert h.backend.held_buttons == []
    assert h.events.of("keys_released") == [{"keys": ["button:left"], "reason": "aborted"}]
    assert h.events.of("input_state")[-1] == {"held": []}
    assert h.session.held == []


def test_stop_mid_typing_releases_the_held_character():
    h = make(stop_at=0.23)  # "b" goes down at 200 ms and up at 260 ms
    with pytest.raises(Stopped):
        h.session.type_text("abc", profile=KB_LINEAR, rng=rng())
    assert h.backend.held_chars == []
    assert h.calls()[-2:] == [("char_down", "b"), ("char_up", "b")]
    assert h.events.of("keys_released") == [{"keys": ["char:b"], "reason": "aborted"}]
    assert h.events.of("input_executed")[0]["aborted"] is True


def test_stop_during_hold_releases_in_reverse_order():
    h = make(stop_at=0.3)
    with pytest.raises(Stopped):
        h.session.press("ctrl+alt+delete", hold=1.0, profile=KB_LINEAR, rng=rng())
    assert h.calls("key_up") == [("key_up", "delete"), ("key_up", "alt"), ("key_up", "ctrl")]
    released = KeysReleasedEvent.from_json(h.events.of("keys_released")[0])
    assert released == KeysReleasedEvent(keys=["delete", "alt", "ctrl"], reason="aborted")
    assert InputStateEvent.from_json(h.events.of("input_state")[-1]).held == []


def test_release_all_releases_everything_held():
    h = make()
    h.session.press("shift", hold=None, profile=KB_DIRECT, rng=rng())
    assert h.session.held == []
    # Simulate a crash between primitives: keys pressed by earlier actions stay held.
    h.session._press("ctrl", lambda: h.backend.key_down("ctrl"),
                     lambda: h.backend.key_up("ctrl"), [])
    h.session._press("button:left", lambda: h.backend.button_down(Button.LEFT),
                     lambda: h.backend.button_up(Button.LEFT), [])
    assert h.session.held == ["ctrl", "button:left"]
    assert h.session.release_all("stop") == ["button:left", "ctrl"]
    assert h.calls()[-2:] == [("button_up", Button.LEFT), ("key_up", "ctrl")]
    assert h.events.events[-2:] == [
        ("keys_released", {"keys": ["button:left", "ctrl"], "reason": "stop"}),
        ("input_state", {"held": []})]
    count = len(h.events.events)
    assert h.session.release_all("stop") == []
    assert len(h.events.events) == count


def test_release_all_from_another_thread_while_holding():
    released: list[list[str]] = []

    def from_other_thread(now: float) -> None:
        if not released and h.session.held == ["ctrl", "c"]:
            worker = threading.Thread(
                target=lambda: released.append(h.session.release_all("stop")))
            worker.start()
            worker.join()

    h = make(on_wake=from_other_thread)
    h.session.press("ctrl+c", hold=1.0, profile=KB_LINEAR, rng=rng())
    assert released == [["c", "ctrl"]]
    # the action's own releases find nothing left and send nothing twice
    assert h.calls("key_up") == [("key_up", "c"), ("key_up", "ctrl")]
    assert h.events.of("keys_released") == [{"keys": ["c", "ctrl"], "reason": "stop"}]
    assert h.session.held == []


def test_release_all_is_thread_safe_under_contention():
    h = make()
    stop = threading.Event()

    def presser() -> None:
        while not stop.is_set():
            h.session.press("shift+a", hold=None, profile=KB_DIRECT, rng=rng())

    worker = threading.Thread(target=presser)
    worker.start()
    try:
        for _ in range(200):
            h.session.release_all("stop")
    finally:
        stop.set()
        worker.join()
    h.session.release_all("stop")
    assert h.session.held == []
    assert h.backend.held_keys == []


# --- corner failsafe --------------------------------------------------------------------


def test_in_corner():
    bounds = Rect(-1920, 0, 3840, 1080)
    assert in_corner(bounds, -1920, 0)
    assert in_corner(bounds, -1918, 2)
    assert in_corner(bounds, 1919, 1079)
    assert in_corner(bounds, 1917, 1077)
    assert not in_corner(bounds, 1916, 1079)
    assert not in_corner(bounds, 0, 0)


def user_pushes_to_corner_at(h_ref: list, when: float, corner=(0, 0)):
    def wake(now: float) -> None:
        if now >= when:
            h_ref[0].backend.pointer = corner
    return wake


def test_corner_failsafe_stops_and_releases():
    ref: list = []
    h = make(FakeBackend(pointer=(500, 500)), on_wake=user_pushes_to_corner_at(ref, 0.1))
    ref.append(h)
    with pytest.raises(EmergencyStop) as info:
        h.session.drag((500, 500), (1500, 500), button=Button.LEFT, profile=LINEAR,
                       rng=rng())
    assert info.value.reason == "cursor moved into a screen corner"
    assert h.backend.held_buttons == []
    assert h.events.of("keys_released") == [{"keys": ["button:left"], "reason": "failsafe"}]
    assert h.events.of("input_executed")[-1]["aborted"] is True
    assert h.session.held == []
    assert h.backend.pointer == (0, 0)  # no point was sent after the check


def test_corner_failsafe_ignores_movements_aiming_at_a_corner():
    ref: list = []
    h = make(FakeBackend(pointer=(500, 500)),
             on_wake=user_pushes_to_corner_at(ref, 0.05, corner=(1919, 0)))
    ref.append(h)
    # The user's push would be interference; with it off, only the failsafe could react.
    h.session.move(1919, 1079, profile=MouseProfile(strategy="linear", interference="off"),
                   rng=rng())
    assert h.backend.pointer == (1919, 1079)


def test_corner_failsafe_needs_pointer_position():
    ref: list = []
    h = make(FakeBackend(capabilities=caps(pointer_position=False)),
             on_wake=user_pushes_to_corner_at(ref, 0.05))
    ref.append(h)
    h.session.move(1000, 500, profile=LINEAR, rng=rng())
    assert h.calls()[-1] == ("move_abs", 1000, 500)


def test_corner_failsafe_does_not_fire_when_starting_in_a_corner():
    h = make(FakeBackend(pointer=(0, 0)))
    h.session.move(600, 600, profile=LINEAR, rng=rng())
    assert h.backend.pointer == (600, 600)


# --- user interference -----------------------------------------------------------------


def interfering(after_moves: int = 3, to: tuple[int, int] = (300, 700),
                **backend: object):
    """A harness whose user puts the pointer at ``to`` after ``after_moves`` moves."""
    fake = FakeBackend(**backend)  # type: ignore[arg-type]
    fake.user_moves_pointer(*to, after_moves=after_moves)
    return make(fake)


def test_interference_beyond_tolerance_pauses():
    h = interfering(after_moves=3)
    with pytest.raises(UserInterference) as info:
        h.session.move(1200, 0, profile=LINEAR, rng=rng())
    plan = plan_move((0, 0), (1200, 0), LINEAR, rng())
    third = plan.points[2]
    assert info.value.action == "pause"
    assert info.value.expected == (third.x, third.y)
    assert info.value.actual == (300, 700)
    assert len(h.calls("move_abs")) == 3  # nothing sent after the check
    assert h.backend.pointer == (300, 700)
    assert h.events.names() == ["input_planned", "user_interference", "input_executed"]
    event = UserInterferenceEvent.from_json(h.events.of("user_interference")[0])
    assert event == UserInterferenceEvent(expected=[third.x, third.y], actual=[300, 700],
                                          action="pause")
    assert h.events.of("input_executed")[0]["aborted"] is True


def test_interference_with_stop_action():
    h = interfering()
    with pytest.raises(UserInterference) as info:
        h.session.move(1200, 0, profile=MouseProfile(strategy="linear", interference="stop"),
                       rng=rng())
    assert info.value.action == "stop"
    assert h.events.of("user_interference")[0]["action"] == "stop"


def test_movement_within_tolerance_is_not_interference():
    h = make()
    moves = iter(range(1000))

    def drift(backend: FakeBackend) -> None:  # a slight drift, as a shaky hand would
        x, y = backend.pointer
        if next(moves) % 2 == 0:
            backend.pointer = (x, y + 15)

    h.backend.on_move = drift
    h.session.move(1200, 0, profile=MouseProfile(strategy="linear", tolerance=15), rng=rng())
    assert "user_interference" not in h.events.names()
    assert h.calls("move_abs")[-1] == ("move_abs", 1200, 0)


def test_interference_tolerance_is_a_distance_in_pixels():
    h = interfering(to=(300, 300))
    h.session.move(600, 0, profile=MouseProfile(strategy="linear", tolerance=1000),
                   rng=rng())
    assert "user_interference" not in h.events.names()


def test_interference_off_disables_detection():
    h = interfering()
    h.session.move(1200, 0, profile=MouseProfile(strategy="linear", interference="off"),
                   rng=rng())
    assert "user_interference" not in h.events.names()
    assert h.calls("move_abs")[-1] == ("move_abs", 1200, 0)


def test_interference_during_a_drag_releases_the_button():
    h = interfering(after_moves=4, pointer=(100, 100))
    with pytest.raises(UserInterference):
        h.session.drag((100, 100), (1000, 100), button=Button.LEFT, profile=LINEAR,
                       rng=rng())
    assert h.calls()[-1] == ("button_up", Button.LEFT)
    assert h.backend.held_buttons == []
    assert h.session.held == []
    names = h.events.names()
    assert names.index("user_interference") < names.index("keys_released")
    assert h.events.of("keys_released") == [{"keys": ["button:left"],
                                             "reason": "interference"}]
    assert h.events.of("input_state")[-1] == {"held": []}


def test_interference_during_the_approach_of_a_click_never_clicks():
    h = interfering()
    with pytest.raises(UserInterference):
        h.session.click(1200, 0, button=Button.LEFT, clicks=1, profile=LINEAR, rng=rng())
    assert h.calls("button_down") == []
    assert "keys_released" not in h.events.names()


@pytest.mark.parametrize("profile", [DIRECT, LINEAR, MouseProfile()],
                         ids=["direct", "linear", "human"])
def test_every_strategy_reads_the_pointer_once_per_point(profile):
    reads: list[tuple[int, int]] = []

    class Counting(FakeBackend):
        def pointer_position(self) -> tuple[int, int] | None:
            reads.append(self.pointer)
            return super().pointer_position()

    h = make(Counting(pointer=(5, 5)))
    h.session.move(900, 500, profile=profile, rng=rng())
    points = len(h.calls("move_abs"))
    assert points >= 1
    # one read for the start, then one before each point after the first
    assert len(reads) == 1 + points - 1


def test_the_next_movement_starts_where_the_user_left_the_pointer():
    h = interfering()
    with pytest.raises(UserInterference):
        h.session.move(1200, 0, profile=LINEAR, rng=rng())
    h.backend.on_move = None
    h.session.move(400, 700, profile=LINEAR, rng=rng())
    assert h.events.of("input_planned")[-1]["from"] == [300, 700]


def test_the_corner_failsafe_wins_over_interference():
    h = interfering(to=(0, 0), pointer=(500, 500))
    with pytest.raises(EmergencyStop):
        h.session.move(1500, 500, profile=LINEAR, rng=rng())
    assert "user_interference" not in h.events.names()
    assert h.events.of("input_executed")[0]["aborted"] is True


def test_without_pointer_position_the_session_cannot_detect_interference():
    # Steps fail earlier with capability_missing (j5_macro.runtime.input); the session
    # itself simply has nothing to compare.
    h = interfering(capabilities=caps(pointer_position=False))
    h.session.move(1200, 0, profile=LINEAR, rng=rng())
    assert h.calls("move_abs")[-1] == ("move_abs", 1200, 0)


# --- target-aware skip ------------------------------------------------------------------


def test_human_click_inside_the_target_does_not_move():
    target = Rect(80, 80, 40, 40)
    h = make(FakeBackend(pointer=(90, 95)))
    probe = rng(3)
    h.session.click(100, 100, button=Button.LEFT, clicks=1, profile=MouseProfile(),
                    rng=probe, target=target)
    assert h.calls("move_abs") == []
    assert "input_planned" not in h.events.names()
    assert h.calls("button_down") == [("button_down", Button.LEFT)]
    # The movement was still planned: the generator advanced as if it had moved.
    moved = make(FakeBackend(pointer=(500, 500)))
    probe_moved = rng(3)
    moved.session.click(100, 100, button=Button.LEFT, clicks=1, profile=MouseProfile(),
                        rng=probe_moved, target=target)
    assert moved.calls("move_abs")
    assert probe.random() == probe_moved.random()


def test_human_click_outside_the_target_moves():
    h = make(FakeBackend(pointer=(500, 500)))
    h.session.click(100, 100, button=Button.LEFT, clicks=1, profile=MouseProfile(),
                    rng=rng(), target=Rect(80, 80, 40, 40))
    assert h.calls("move_abs")
    assert h.events.of("input_planned")[0]["from"] == [500, 500]


def test_only_human_stays_inside_the_target():
    h = make(FakeBackend(pointer=(90, 95)))
    h.session.click(100, 100, button=Button.LEFT, clicks=1, profile=LINEAR, rng=rng(),
                    target=Rect(80, 80, 40, 40))
    assert h.calls("move_abs")[-1] == ("move_abs", 100, 100)


def test_move_steps_always_move_even_inside_the_target():
    h = make(FakeBackend(pointer=(90, 95)))
    h.session.move(100, 100, profile=MouseProfile(), rng=rng(), target=Rect(80, 80, 40, 40))
    assert h.events.of("input_planned")


# --- human, windows ---------------------------------------------------------------------


def test_human_strategy_is_planned_as_human_without_warnings():
    h = make()
    h.session.move(100, 0, profile=MouseProfile(), rng=rng())
    h.session.type_text("hi", profile=KeyboardProfile(), rng=rng())
    h.session.click(5, 5, button=Button.LEFT, clicks=1, profile=MouseProfile(), rng=rng())
    assert h.events.of("log") == []
    assert {p["strategy"] for p in h.events.of("input_planned")} == {"human"}


def test_find_window_delegates_when_supported():
    rect = Rect(10, 20, 300, 200)
    h = make(FakeBackend(windows={"Untitled - Notepad": rect}))
    assert h.session.find_window("Notepad") == rect
    assert h.session.find_window("Calculator") is None
    blind = make(FakeBackend(capabilities=caps(windows=False),
                             windows={"Untitled - Notepad": rect}))
    assert blind.session.find_window("Notepad") is None


def test_same_seed_same_plans():
    a, b = make(), make()
    for h in (a, b):
        h.session.move(700, 300, profile=LINEAR, rng=rng(9))
        h.session.click(10, 10, button=Button.LEFT, clicks=2, profile=LINEAR, rng=rng(9))
    assert a.calls() == b.calls()
    assert a.events.of("input_planned") == b.events.of("input_planned")
