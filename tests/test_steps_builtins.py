import random
from typing import Any

import pytest

from j5_macro.protocol.errors import ErrorCode
from j5_macro.steps import StepError, validate_params
from j5_macro.steps.builtins import default_registry
from j5_macro.steps.images import image_visible
from j5_macro.steps.spec import parse_duration
from j5_macro.vision.api import DEFAULT_SCALE, DEFAULT_THRESHOLD, Match

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
    "exec": ({"command": ["ls", "-l"]}, {"command": "ls", "wait": "yes"}),
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
                                                   "clicks": 1, "threshold": 0.9,
                                                   "scale": 1.0}
    assert defaults("move", {"x": 1, "y": 2}) == {"x": 1, "y": 2, "threshold": 0.9,
                                                  "scale": 1.0}
    assert defaults("wait_image", {"image": "a.png"}) == {"image": "a.png", "timeout": "10s",
                                                          "threshold": 0.9, "scale": 1.0}
    assert defaults("scroll", {}) == {"dx": 0, "dy": 0}
    assert defaults("log", {"message": "m"}) == {"message": "m", "level": "info"}
    assert defaults("loop", {"times": 2}) == {"times": 2, "as": "item"}
    assert defaults("exec", {"command": "ls"}) == {"command": "ls", "shell": False,
                                                   "wait": False, "check": True}


def test_shorthands_and_blocks():
    shorthand = {n: REGISTRY.spec(n).shorthand for n in REGISTRY.names()}  # type: ignore[union-attr]
    assert shorthand == {"click": None, "move": None, "drag": None, "scroll": None,
                         "type": "text", "key": "press", "wait_image": "image",
                         "sleep": "duration", "if": None, "loop": None, "set": None,
                         "log": "message", "pause": "message", "exec": "command"}
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
    ("click", {"x": 1, "y": 2, "scale": 2}, "scale"),
    ("move", {"x": 1, "y": 2, "scale": "${s}"}, "scale"),
    ("click", {"x": 1, "y": 2, "region": [0, 0, 5, 5]}, "region"),
    ("move", {"y": 3}, "x"),
    ("drag", {"from": {"x": 1}, "to": {"x": 1, "y": 1}}, "from.y"),
    ("drag", {"from": {"x": 1, "y": 1}, "to": {}}, "to"),
    ("wait_image", {"image": "a.png", "region": [0, 0, 10]}, "region"),
    ("click", {"at": "${a}", "image": "a.png"}, "image"),
    ("click", {"at": "${a}", "x": 1, "y": 2}, "at"),
    ("click", {"at": "${a}", "window": {"title": "Notes"}}, "window"),
    ("click", {"at": "${a}", "region": [0, 0, 5, 5]}, "region"),
    ("click", {"at": "label"}, "at"),
    ("click", {"at": {"found": True}}, "at"),
    ("click", {"at": {"bbox": [1, 2, 0, 4]}}, "at"),
    ("click", {"at": {"bbox": [1, 2, 3, 4], "window": "Notes"}}, "at"),
    ("click", {"at": {"bbox": [1, 2, 3, 4], "window": "", "window_bbox": [1, 2, 3, 4]}},
     "at"),
    ("move", {"at": {"bbox": [1, 2, 3, 4]}, "offset": [1, 2, 3]}, "offset"),
    ("drag", {"from": {"at": "${a}", "window": {"title": "N"}}, "to": {"x": 1, "y": 1}},
     "from.window"),
    ("drag", {"from": {"at": "${a}", "offset": [1]}, "to": {"x": 1, "y": 1}},
     "from.offset"),
    ("wait_image", {"image": "a.png", "region": {"offset": [0, 40]}}, "region"),
    ("wait_image", {"image": "a.png", "region": {"at": "${a}", "near": 1}}, "region"),
    ("wait_image", {"image": "a.png", "region": {"at": "${a}", "size": [1]}}, "region"),
    ("wait_image", {"image": "a.png", "region": {"at": {"found": True}}}, "region"),
    ("wait_image", {"image": "a.png", "region": "left"}, "region"),
    # Offsets in units of the target's size need a target with a size.
    ("click", {"x": 1, "y": 2, "offset": ["1w", 0]}, "offset"),
    ("move", {"x": 1, "y": 2, "offset": [0, "-0.5h"]}, "offset"),
    ("drag", {"from": {"x": 1, "y": 1, "offset": ["2h", 0]}, "to": {"x": 1, "y": 1}},
     "from.offset"),
    ("click", {"image": "a.png", "offset": ["1 w", 0]}, "offset"),
    ("click", {"image": "a.png", "offset": ["1x", 0]}, "offset"),
    ("click", {"image": "a.png", "offset": ["w", 0]}, "offset"),
    ("click", {"at": "${a}", "offset": [True, 0]}, "offset"),
    ("click", {"image": "a.png", "offset": "1w"}, "offset"),
    ("wait_image", {"image": "a.png", "region": {"at": "${a}", "size": ["0w", 10]}},
     "region"),
    ("wait_image", {"image": "a.png", "region": {"at": "${a}", "size": [-5, 10]}},
     "region"),
    ("wait_image", {"image": "a.png", "region": {"at": "${a}", "offset": ["1q", 0]}},
     "region"),
    ("key", {"press": "ctrl+"}, "press"),
    ("type", {"text": "a\x07b"}, "text"),
    ("type", {"text": "\x1b[2J"}, "text"),
    ("type", {"text": "del\x7f"}, "text"),
    ("type", {"text": "c1\x85"}, "text"),
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
    ("click", {"at": "${label}"}),
    ("click", {"at": "${label}", "offset": [0, 40], "threshold": 0.9, "scale": 1.0}),
    ("click", {"at": {"bbox": [1, 2, 3, 4], "window": "Notes",
                      "window_bbox": [0, 0, 3, 4], "found": True}}),
    ("click", {"at": {"bbox": None, "dry_run": True}}),  # a dry run that did not search
    ("click", {"at": {"dry_run": True, "description": "would wait"}}),  # nor could
    ("click", {"at": {"bbox": "${b}"}}),
    ("move", {"at": "${label}", "offset": [3, 4]}),
    ("drag", {"from": {"at": "${label}", "offset": [0, 5]},
              "to": {"image": "a.png", "offset": [1, 1]}}),
    ("wait_image", {"image": "a.png", "region": {"at": "${label}"}}),
    ("wait_image", {"image": "a.png", "region": {"at": "${label}", "offset": [0, 40],
                                                 "size": [200, 30]}}),
    ("wait_image", {"image": "a.png", "region": "${r}"}),
    ("click", {"image": "a.png", "offset": ["0.5w", "-2h"]}),
    ("click", {"at": "${label}", "offset": ["1.5h", ".25w"]}),
    ("click", {"at": "${label}", "offset": ["+1w", 0.5]}),
    ("click", {"x": 1, "y": 2, "offset": ["${dx}", 3]}),  # checked when interpolated
    ("drag", {"from": {"image": "a.png", "offset": ["-1w", 0]},
              "to": {"at": "${label}", "offset": [0, "1h"]}}),
    ("wait_image", {"image": "a.png", "region": {"at": "${label}", "offset": [0, "1h"],
                                                 "size": ["3w", "1h"]}}),
    ("click", {"x": 1, "y": 2, "threshold": 0.9, "scale": 1.0}),
    ("move", {"x": 1, "y": 2, "threshold": 0.9, "scale": 1}),
    ("key", {"press": "${combo}"}),
    ("key", {"press": "enter"}),
    ("type", {"text": "a\tb\nc\r\nd\re\bñ😀"}),
    ("type", {"text": "${t}\x07"}),  # checked after interpolation
])
def test_cross_field_accepts(name, params):
    assert _problems(name, params) == []


# --- executors --------------------------------------------------------------------------


class FakeContext:
    """Records what the executor asks of the interpreter."""

    def __init__(self, *, dry_run: bool = False, seed: int = 1, strict: bool = False) -> None:
        self.step_id = "s1"
        self.dry_run = dry_run
        self.strict = strict
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

    def check_stop(self) -> None:
        pass

    @property
    def input(self) -> Any:
        return self.input_for(None)

    def input_for(self, overrides: Any) -> Any:
        raise StepError(ErrorCode.CAPABILITY_MISSING, "step 's1' needs an input backend")

    @property
    def vision(self) -> Any:
        raise StepError(ErrorCode.CAPABILITY_MISSING, "vision is not available (test)")


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
def test_image_targets_warn_when_vision_is_missing_in_dry_run(name, params):
    ctx = FakeContext(dry_run=True)
    result = run(name, params, ctx)
    assert result["dry_run"] is True
    assert "found" not in result
    assert ctx.calls[0][0] == "log" and ctx.calls[0][1][0] == "warning"
    assert "not searched: vision is not available" in ctx.calls[0][1][1]
    assert ctx.calls[1] == ("log", ("info", result["description"]))


@pytest.mark.parametrize(("name", "params", "milestone"), [
    ("click", {"x": 1, "y": 2}, "input backend"),
    ("type", {"text": "x"}, "input backend"),
    ("click", {"image": "a.png"}, "input backend"),
    ("wait_image", {"image": "a.png"}, "vision is not available"),
])
def test_input_steps_fail_without_input_outside_dry_run(name, params, milestone):
    ctx = FakeContext()
    with pytest.raises(StepError) as info:
        run(name, params, ctx)
    assert info.value.info.code == "capability_missing"
    assert milestone in info.value.info.message
    assert ctx.calls == []


# --- declared defaults are the ones the executors use -----------------------------------


class RecordingVision:
    """Records the options of every search; never finds anything."""

    def __init__(self) -> None:
        self.searches: list[dict[str, Any]] = []

    def find(self, image: str, **options: Any) -> Match:
        self.searches.append({"image": image, **options})
        return Match(found=False, score=0.0, bbox=None)

    def wait(self, image: str, *, timeout: float, **options: Any) -> Match:
        return self.find(image, timeout=timeout, **options)


class VisionContext(FakeContext):
    def __init__(self, *, dry_run: bool = True) -> None:
        super().__init__(dry_run=dry_run)
        self.recorder = RecordingVision()

    @property
    def vision(self) -> Any:
        return self.recorder


def _searches(name: str, params: dict[str, Any], *,
              dry_run: bool = True) -> list[dict[str, Any]]:
    ctx = VisionContext(dry_run=dry_run)
    run(name, params, ctx)
    return ctx.recorder.searches


def _defaulted(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """``params`` as the loader keeps them: declared defaults filled in, and valid."""
    spec = REGISTRY.spec(name)
    assert spec is not None
    defaulted, problems = validate_params(spec, params)
    assert problems == []
    assert REGISTRY.check_params(name, defaulted) == []
    return defaulted


IMAGE_STEPS = [name for name in REGISTRY.names()
               if REGISTRY.spec(name).param("image") is not None]  # type: ignore[union-attr]
IMAGE_DEFAULTS = {"threshold": DEFAULT_THRESHOLD, "scale": DEFAULT_SCALE}


def _options(search: dict[str, Any]) -> dict[str, Any]:
    return {name: search[name] for name in IMAGE_DEFAULTS}


def test_image_steps_are_the_expected_ones():
    assert IMAGE_STEPS == ["click", "move", "wait_image"]


@pytest.mark.parametrize("name", IMAGE_STEPS)
def test_image_param_defaults_are_declared_and_used(name):
    spec = REGISTRY.spec(name)
    assert spec is not None
    declared = {p: spec.param(p).default for p in IMAGE_DEFAULTS}  # type: ignore[union-attr]
    assert declared == IMAGE_DEFAULTS
    # As written (no defaults filled in) and as loaded (defaults filled in), the search
    # uses the declared values.
    for params in ({"image": "a.png"}, _defaulted(name, {"image": "a.png"})):
        assert [_options(s) for s in _searches(name, params)] == [declared]
        if name == "wait_image":  # outside dry runs too
            searches = _searches(name, params, dry_run=False)
            assert [_options(s) for s in searches] == [declared]


def test_nested_and_condition_image_targets_use_the_same_defaults():
    searches = _searches("drag", {"from": {"image": "a.png"}, "to": {"image": "b.png"}})
    assert [_options(s) for s in searches] == [IMAGE_DEFAULTS, IMAGE_DEFAULTS]
    ctx = VisionContext()
    assert image_visible(ctx, "a.png") is False
    assert [_options(s) for s in ctx.recorder.searches] == [IMAGE_DEFAULTS]


def test_wait_image_timeout_default_is_the_one_used():
    spec = REGISTRY.spec("wait_image")
    assert spec is not None
    seconds = parse_duration(spec.param("timeout").default).seconds  # type: ignore[union-attr]
    for params in ({"image": "a.png"}, _defaulted("wait_image", {"image": "a.png"})):
        for dry_run in (True, False):
            searches = _searches("wait_image", params, dry_run=dry_run)
            assert [s["timeout"] for s in searches] == [seconds]


@pytest.mark.parametrize(("name", "params"), [
    ("click", {"x": 1, "y": 2}),
    ("move", {"x": 1, "y": 2}),
    ("drag", {"from": {"x": 0, "y": 0}, "to": {"x": 9, "y": 9}}),
    ("scroll", {}),
    ("log", {"message": "hi"}),
])
def test_filling_in_defaults_does_not_change_what_a_step_does(name, params):
    """A step run as written and with its declared defaults filled in (as the loader
    keeps it) does the same, so the declared defaults are the executors' ones. An
    ``x``/``y`` target stays valid with the image defaults filled in."""
    first, second = FakeContext(dry_run=True), FakeContext(dry_run=True)
    assert run(name, dict(params), first) == run(name, _defaulted(name, params), second)
    assert first.calls == second.calls


@pytest.mark.parametrize(("name", "param", "default"), [
    ("click", "button", "left"),
    ("click", "clicks", 1),
    ("drag", "button", "left"),
    ("scroll", "dx", 0),
    ("scroll", "dy", 0),
    ("wait_image", "timeout", "10s"),
    ("log", "level", "info"),
    ("loop", "as", "item"),
])
def test_other_declared_defaults(name, param, default):
    spec = REGISTRY.spec(name)
    assert spec is not None
    assert spec.param(param).default == default  # type: ignore[union-attr]
