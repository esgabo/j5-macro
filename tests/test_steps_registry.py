from types import SimpleNamespace

import pytest

from j5_macro.protocol.payloads.commands import ListStepTypesResult
from j5_macro.steps import ParamProblem, ParamSpec, StepRegistry, StepSpec, StepType
from j5_macro.steps import registry as registry_module
from j5_macro.steps.builtins import default_registry
from j5_macro.steps.spec import RENAMED_STEP_TYPES

V1_STEPS = ["click", "move", "drag", "scroll", "type", "key", "wait_image_in",
            "wait_image_out", "wait_window_in", "wait_window_out", "sleep", "if", "loop",
            "set", "log", "pause", "exec"]

NOOP = StepType(StepSpec(name="noop", doc="Do nothing.",
                         params=(ParamSpec(name="a", type="integer"),
                                 ParamSpec(name="b", type="integer"))),
                lambda ctx, params: None)


def test_register_get_spec_and_names():
    registry = StepRegistry()
    registry.register(NOOP)
    assert registry.get("noop") is NOOP
    assert registry.spec("noop") is NOOP.spec
    assert registry.get("other") is None and registry.spec("other") is None
    assert registry.names() == ["noop"]
    assert "noop" in registry and len(registry) == 1


def test_duplicate_registration_fails():
    registry = StepRegistry()
    registry.register(NOOP)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(NOOP)


def test_action_step_needs_an_executor():
    with pytest.raises(ValueError, match="executor"):
        StepRegistry().register(StepType(StepSpec(name="x", doc="")))


def test_check_params_uses_the_registered_check():
    def check(params):
        return [] if "a" in params else [ParamProblem("a", "needed")]

    registry = StepRegistry()
    registry.register(NOOP, check)
    assert registry.check_params("noop", {"a": 1}) == []
    assert registry.check_params("noop", {}) == [ParamProblem("a", "needed")]
    assert registry.check_params("unknown", {}) == []


def test_default_registry_has_every_v1_step():
    registry = default_registry()
    assert registry.names() == V1_STEPS
    for name in V1_STEPS:
        step_type = registry.get(name)
        assert step_type is not None
        spec = step_type.spec
        assert spec.doc and all(p.doc for p in spec.params), name
        assert spec.result and all(f.doc for f in spec.result), name
        assert (step_type.executor is None) == (spec.kind == "control"), name


def test_renamed_step_types_point_to_registered_ones():
    registry = default_registry()
    assert RENAMED_STEP_TYPES
    for old, new in RENAMED_STEP_TYPES.items():
        assert registry.get(old) is None and registry.get(new) is not None


def test_describe_matches_the_wire_shape():
    entries = default_registry().describe()
    result = ListStepTypesResult.from_json({"step_types": entries})
    assert [info.name for info in result.step_types] == V1_STEPS
    sleep = next(e for e in entries if e["name"] == "sleep")
    assert sleep["params"] == [{"name": "duration", "type": "duration", "required": True,
                                "default": None, "doc": sleep["params"][0]["doc"]}]
    assert "anyOf" in sleep["schema"]
    loop = next(e for e in entries if e["name"] == "loop")
    assert loop["schema"]["required"] == ["do"]
    wait = next(e for e in entries if e["name"] == "wait_image_in")
    fields = {f["name"]: (f["type"], f["optional"]) for f in wait["result"]}
    assert {k: fields[k] for k in ("found", "score", "bbox", "center", "window",
                                   "window_bbox")} == {
        "found": ("boolean", False), "score": ("number", False), "bbox": ("list", False),
        "center": ("list", False), "window": ("string", True),
        "window_bbox": ("list", True)}


def test_load_plugins_runs_entry_points(monkeypatch):
    def register(registry):
        registry.register(NOOP)

    fake = SimpleNamespace(name="demo", value="demo:register", load=lambda: register)
    monkeypatch.setattr(registry_module, "entry_points",
                        lambda group: [fake] if group == "j5_macro.steps" else [])
    registry = default_registry()
    report = registry.load_plugins()
    assert report.loaded == ["demo"] and report.skipped == []
    assert registry.names()[-1] == "noop"


def test_register_rejects_objects_that_are_not_step_types():
    with pytest.raises(TypeError, match="StepType"):
        StepRegistry().register(NOOP.spec)  # type: ignore[arg-type]
