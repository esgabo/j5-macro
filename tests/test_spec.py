import random

import pytest

from j5_macro.script import Script, Step
from j5_macro.steps.spec import (
    Duration,
    ParamSpec,
    StepSpec,
    check_value,
    parse_duration,
    validate_params,
)

SLEEP = StepSpec(name="sleep", doc="Wait.", shorthand="duration",
                 params=(ParamSpec(name="duration", type="duration", required=True),))
CLICK = StepSpec(name="click", doc="Click.", params=(
    ParamSpec(name="x", type="integer"),
    ParamSpec(name="button", type="string", default="left", choices=("left", "right")),
    ParamSpec(name="clicks", type="integer", default=1),
))


@pytest.mark.parametrize(("text", "seconds", "variance"), [
    ("2s", 2.0, 0.0), ("500ms", 0.5, 0.0), ("1.5m", 90.0, 0.0), ("1h", 3600.0, 0.0),
    ("2s±20%", 2.0, 0.2), ("2s +- 20 %", 2.0, 0.2), (3, 3.0, 0.0), (0.25, 0.25, 0.0),
])
def test_parse_duration(text, seconds, variance):
    assert parse_duration(text) == Duration(seconds, variance)


@pytest.mark.parametrize("bad", ["2", "s", "-1s", "2 days", "2s±150%", True, None, -1, [1]])
def test_parse_duration_rejects(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_duration_sample_stays_in_range_and_is_seeded():
    d = Duration(2.0, 0.2)
    samples = [d.sample(random.Random(seed)) for seed in range(50)]
    assert all(1.6 <= s <= 2.4 for s in samples)
    assert d.sample(random.Random(7)) == d.sample(random.Random(7))
    assert Duration(2.0).sample(random.Random(1)) == 2.0


def test_validate_params_defaults_and_problems():
    params, problems = validate_params(CLICK, {"x": 10})
    assert params == {"x": 10, "button": "left", "clicks": 1}
    assert problems == []

    _, problems = validate_params(CLICK, {"x": "ten", "button": "middle", "speed": 2})
    assert {(p.param, p.message) for p in problems} == {
        ("x", "expected an integer"),
        ("button", "expected one of left, right"),
        ("speed", "unknown parameter for 'click'"),
    }

    _, problems = validate_params(SLEEP, {})
    assert [(p.param, p.message) for p in problems] == [("duration",
                                                         "missing required parameter")]


def test_interpolated_strings_are_checked_later():
    assert check_value(ParamSpec(name="x", type="integer"), "${pos_x}") is None
    assert check_value(ParamSpec(name="d", type="duration"), "${wait_time}") is None
    assert check_value(ParamSpec(name="o", type="object"), "${obj}") == "expected a mapping"


def test_bool_is_not_an_integer_and_conditions_have_one_key():
    assert check_value(ParamSpec(name="n", type="integer"), True) == "expected an integer"
    cond = ParamSpec(name="when", type="condition")
    assert check_value(cond, "attempts < 3") is None
    assert check_value(cond, {"image_visible": "img/x.png"}) is None
    assert check_value(cond, {"a": 1, "b": 2}) is not None


def test_script_walk_and_find():
    inner = Step(id="click_1", type="click", path=(1, 0, 0))
    loop = Step(id="loop_1", type="loop", path=(1,), blocks={"do": [inner]})
    script = Script(name="demo", steps=[Step(id="sleep_1", type="sleep", path=(0,)), loop])
    assert [s.id for s in script.walk()] == ["sleep_1", "loop_1", "click_1"]
    assert script.find("click_1") is inner
    assert script.find("nope") is None
