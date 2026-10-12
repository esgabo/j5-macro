import random
from typing import Any

import pytest

from j5_macro.protocol.errors import ErrorCode
from j5_macro.steps import StepError, validate_params
from j5_macro.steps.builtins import default_registry

REGISTRY = default_registry()

# name -> (minimal valid params, params validate_params rejects)
EXAMPLES: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "click": ({"x": 10, "y": 20}, {"x": 10, "y": 20, "button": "side"}),
    "move": ({"image": "img/a.png"}, {"x": "left"}),
    "drag": ({"from": {"x": 0, "y": 0}, "to": {"image": "img/b.png"}}, {"from": {"x": 0}}),
    "scroll": ({}, {"dy": 1.5}),
    "type": ({"text": "hello"}, {}),
    "key": ({"press": "ctrl+s"}, {"press": "ctrl+s", "hold": "forever"}),
    "wait_image": ({"image": "img/a.png"}, {"image": "img/a.png", "timeout": "soon"}),
    "sleep": ({"duration": "2s±20%"}, {"duration": "2"}),
    "if": ({"when": "x > 1"}, {"when": {"a": 1, "b": 2}}),
    "loop": ({"times": 3}, {"times": 3, "until": "x"}),
    "set": ({"name": "x", "value": 1}, {"value": 1}),
    "log": ({"message": "hi"}, {"message": "hi", "level": "trace"}),
    "pause": ({}, {"message": 3}),
}


def test_every_builtin_has_examples():
    assert set(EXAMPLES) == set(REGISTRY.names())


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_builtin_specs_accept_and_reject(name):
    spec = REGISTRY.spec(name)
    assert spec is not None
    good, bad = EXAMPLES[name]
    _, problems = validate_params(spec, good)
    assert problems == []
    assert REGISTRY.check_params(name, good) == []
    _, problems = validate_params(spec, bad)
    assert problems


def test_defaults():
    def defaults(name, params):
        spec = REGISTRY.spec(name)
        assert spec is not None
        return validate_params(spec, params)[0]

    assert defaults("click", {"x": 1, "y": 2}) == {"x": 1, "y": 2, "button": "left",
                                                   "clicks": 1, "threshold": 0.9}
    assert defaults("wait_image", {"image": "a.png"}) == {"image": "a.png", "timeout": "10s",
                                                          "threshold": 0.9}
    assert defaults("scroll", {}) == {"dx": 0, "dy": 0}
    assert defaults("log", {"message": "m"}) == {"message": "m", "level": "info"}
    assert defaults("loop", {"times": 2}) == {"times": 2, "as": "item"}


def test_shorthands_and_blocks():
    shorthand = {n: REGISTRY.spec(n).shorthand for n in REGISTRY.names()}  # type: ignore[union-attr]
    assert shorthand == {"click": None, "move": None, "drag": None, "scroll": None,
                         "type": "text", "key": "press", "wait_image": "image",
                         "sleep": "duration", "if": None, "loop": None, "set": None,
                         "log": "message", "pause": "message"}
    if_spec, loop_spec = REGISTRY.spec("if"), REGISTRY.spec("loop")
    assert if_spec and if_spec.blocks == ("then", "else")
    assert if_spec.required_blocks == ("then",)
    assert loop_spec and loop_spec.blocks == ("do",) and loop_spec.required_blocks == ("do",)


def _problems(name, params):
    return [(p.param, p.message) for p in REGISTRY.check_params(name, params)]


@pytest.mark.parametrize(("name", "params", "param"), [
    ("loop", {}, "times"),
    ("loop", {"times": 2, "while": "x"}, "while"),
    ("loop", {"times": -1}, "times"),
    ("loop", {"for_each": 3}, "for_each"),
    ("set", {"name": "x"}, "value"),
    ("set", {"name": "x", "value": 1, "expr": "2"}, "expr"),
    ("set", {"name": "", "value": 1}, "name"),
    ("click", {}, "image"),
    ("click", {"x": 1}, "y"),
    ("click", {"image": "a.png", "x": 1}, "image"),
    ("click", {"x": 1, "y": 2, "clicks": 0}, "clicks"),
    ("click", {"x": 1, "y": 2, "offset": [1]}, "offset"),
    ("click", {"image": "a.png", "threshold": 1.5}, "threshold"),
    ("move", {"y": 3}, "x"),
    ("drag", {"from": {"x": 1}, "to": {"x": 1, "y": 1}}, "from.y"),
    ("drag", {"from": {"x": 1, "y": 1}, "to": {}}, "to"),
    ("wait_image", {"image": "a.png", "region": [0, 0, 10]}, "region"),
    ("key", {"press": "ctrl+"}, "press"),
])
def test_cross_field_problems(name, params, param):
    assert [p for p, _ in _problems(name, params)] == [param]


@pytest.mark.parametrize(("name", "params"), [
    ("loop", {"while": "x < 3"}),
    ("loop", {"for_each": "${items}", "as": "row"}),
    ("loop", {"for_each": [1, 2]}),
    ("loop", {"times": "${n}"}),
    ("set", {"name": "x", "value": None}),
    ("set", {"name": "x", "expr": "a + 1"}),
    ("click", {"image": "a.png", "offset": [5, -5]}),
    ("key", {"press": "${combo}"}),
    ("key", {"press": "enter"}),
])
def test_cross_field_accepts(name, params):
    assert _problems(name, params) == []


# --- executors --------------------------------------------------------------------------


class FakeContext:
    """Records what the executor asks of the interpreter."""

    def __init__(self, *, dry_run: bool = False, seed: int = 1) -> None:
        self.step_id = "s1"
        self.dry_run = dry_run
        self.rng = random.Random(seed)
        self.variables: dict[str, Any] = {"a": 2}
        self.calls: list[tuple[str, Any]] = []

    def set_variable(self, name: str, value: Any) -> None:
        self.variables[name] = value
        self.calls.append(("set_variable", (name, value)))

    def evaluate(self, expression: str) -> Any:
        self.calls.append(("evaluate", expression))
        if expression == "a * 10":
            return self.variables["a"] * 10
        raise StepError(ErrorCode.EXPRESSION_ERROR, "bad expression")

    def log(self, level: str, message: str) -> None:
        self.calls.append(("log", (level, message)))

    def wait(self, seconds: float) -> None:
        self.calls.append(("wait", seconds))

    def request_pause(self, message: str | None = None) -> None:
        self.calls.append(("request_pause", message))


def run(name: str, params: dict[str, Any], ctx: FakeContext) -> Any:
    step_type = REGISTRY.get(name)
    assert step_type is not None and step_type.executor is not None
    return step_type.executor(ctx, params)


def test_sleep_samples_with_the_rng_and_waits():
    first, second = FakeContext(seed=5), FakeContext(seed=5)
    result = run("sleep", {"duration": "2s±20%"}, first)
    assert run("sleep", {"duration": "2s±20%"}, second) == result
    assert 1.6 <= result["seconds"] <= 2.4
    assert first.calls == [("wait", result["seconds"])]
    assert run("sleep", {"duration": 0.5}, FakeContext()) == {"seconds": 0.5}


def test_sleep_rejects_a_bad_interpolated_duration():
    with pytest.raises(StepError) as info:
        run("sleep", {"duration": "a while"}, FakeContext())
    assert info.value.info.code == "validation_failed"


def test_set_with_value_and_expr():
    ctx = FakeContext()
    assert run("set", {"name": "x", "value": [1, 2]}, ctx) == {"name": "x", "value": [1, 2]}
    assert run("set", {"name": "y", "expr": "a * 10"}, ctx) == {"name": "y", "value": 20}
    assert ctx.calls == [("set_variable", ("x", [1, 2])), ("evaluate", "a * 10"),
                         ("set_variable", ("y", 20))]
    with pytest.raises(StepError):
        run("set", {"name": "z", "expr": "oops"}, ctx)


@pytest.mark.parametrize("level", ["debug", "info", "warning", "error"])
def test_log_levels(level):
    ctx = FakeContext()
    assert run("log", {"message": "hello", "level": level}, ctx) == {"level": level,
                                                                     "message": "hello"}
    assert ctx.calls == [("log", (level, "hello"))]


def test_log_defaults_to_info_and_rejects_bad_levels():
    ctx = FakeContext()
    run("log", {"message": 42}, ctx)
    assert ctx.calls == [("log", ("info", "42"))]
    with pytest.raises(StepError):
        run("log", {"message": "x", "level": "loud"}, ctx)


def test_pause_requests_a_pause():
    ctx = FakeContext()
    assert run("pause", {"message": "check the dialog"}, ctx) == {"message": "check the dialog"}
    assert run("pause", {}, ctx) == {"message": None}
    assert ctx.calls == [("request_pause", "check the dialog"), ("request_pause", None)]


@pytest.mark.parametrize(("name", "params", "text"), [
    ("click", {"x": 10, "y": 20, "button": "left", "clicks": 1}, "would click left at (10, 20)"),
    ("click", {"x": 1, "y": 2, "button": "right", "clicks": 2, "window": {"title": "Notes"}},
     "would click right x2 at (1, 2) in window {'title': 'Notes'}"),
    ("move", {"x": 5, "y": 6}, "would move to (5, 6)"),
    ("drag", {"from": {"x": 0, "y": 0}, "to": {"x": 9, "y": 9}, "button": "left"},
     "would drag left from (0, 0) to (9, 9)"),
    ("scroll", {"dx": 0, "dy": -3}, "would scroll dx=0, dy=-3"),
    ("type", {"text": "secret"}, "would type 6 characters"),
    ("key", {"press": "ctrl+s"}, "would press ctrl+s"),
    ("key", {"press": "shift", "hold": "1s"}, "would press shift holding 1s"),
])
def test_input_placeholders_in_dry_run(name, params, text):
    ctx = FakeContext(dry_run=True)
    assert run(name, params, ctx) == {"dry_run": True, "description": text}
    assert ctx.calls == [("log", ("info", text))]


@pytest.mark.parametrize(("name", "params"), [
    ("click", {"image": "img/ok.png", "button": "left", "clicks": 1}),
    ("drag", {"from": {"image": "a.png"}, "to": {"x": 1, "y": 1}}),
    ("wait_image", {"image": "img/ok.png", "timeout": "10s"}),
])
def test_image_targets_warn_that_vision_is_pending(name, params):
    ctx = FakeContext(dry_run=True)
    result = run(name, params, ctx)
    assert result["dry_run"] is True
    assert ctx.calls[1][0] == "log" and ctx.calls[1][1][0] == "warning"
    assert "M3" in ctx.calls[1][1][1]


@pytest.mark.parametrize(("name", "params", "milestone"), [
    ("click", {"x": 1, "y": 2}, "M2"),
    ("type", {"text": "x"}, "M2"),
    ("wait_image", {"image": "a.png"}, "M3"),
])
def test_input_placeholders_fail_outside_dry_run(name, params, milestone):
    ctx = FakeContext()
    with pytest.raises(StepError) as info:
        run(name, params, ctx)
    assert info.value.info.code == "capability_missing"
    assert milestone in info.value.info.message
    assert ctx.calls == []
