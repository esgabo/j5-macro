"""Script params: the ``params`` section, value checks, resolution and secret masking."""

import pytest

from j5_macro.script import load_script
from j5_macro.script.model import ScriptParam
from j5_macro.script.params import (
    MASK,
    MIN_SECRET_LENGTH,
    ParamProblem,
    check_value,
    mask,
    mask_payload,
    missing_names,
    resolve,
    secret_values,
    short_secret_params,
    short_secret_warning,
)
from j5_macro.steps import default_registry

REGISTRY = default_registry()


def load(text: str):
    return load_script(text, lookup=REGISTRY.spec, check=REGISTRY.check_params)


GOOD = """\
params:
  customer: {description: Name to type}
  note:
  count: {type: integer, default: 2}
  ratio: {type: number, default: 1}
  confirm: {type: boolean, default: false}
  password: {secret: true}
vars: {greeting: hi}
steps:
  - type: "${greeting} ${customer}"
"""


def test_params_are_parsed_in_order_with_their_lines():
    result = load(GOOD)
    assert result.ok, result.errors
    params = result.script.params
    assert list(params) == ["customer", "note", "count", "ratio", "confirm", "password"]
    assert params["customer"] == ScriptParam(name="customer", description="Name to type",
                                             line=2)
    assert params["note"].required and params["note"].type == "string"
    assert (params["count"].default, params["count"].required) == (2, False)
    assert params["password"].secret and params["password"].required
    assert params["confirm"].default is False


def test_load_description_never_reports_a_secret_default():
    params = load(GOOD).script.params
    assert params["count"].to_json() == {"name": "count", "type": "integer",
                                         "secret": False, "required": False, "default": 2}
    assert params["password"].to_json() == {"name": "password", "type": "string",
                                            "secret": True, "required": True}
    assert params["customer"].to_json()["description"] == "Name to type"


@pytest.mark.parametrize(("section", "message"), [
    ("params: [a]", "'params' must be a mapping"),
    ("params: {1x: {}}", "invalid param name '1x'"),
    ("params: {greeting: {}}", "'greeting' is both a param and a var"),
    ("params: {a: text}", "param 'a' must be a mapping"),
    ("params: {a: {kind: string}}", "unknown field(s) kind"),
    ("params: {a: {type: text}}", "type must be one of string, integer, number, boolean"),
    ("params: {a: {description: 3}}", "description must be a string"),
    ("params: {a: {secret: yes please}}", "secret must be true or false"),
    ("params: {a: {secret: true, default: x}}", "is secret and cannot have a default"),
    ("params: {a: {type: integer, default: '3'}}", "the default must be an integer"),
    ("params: {a: {type: integer, default: true}}", "the default must be an integer"),
    ("params: {a: {type: boolean, default: 1}}", "the default must be a boolean"),
    ("params: {a: {default: 3}}", "the default must be a string"),
    ("params: {a: {default: [1]}}", "the default must be a string"),
])
def test_invalid_params_are_reported_with_their_line(section, message):
    result = load(f"vars: {{greeting: hi}}\n{section}\nsteps: []\n")
    assert not result.ok
    (error,) = result.errors
    assert message in error.message
    assert error.line == 2
    assert error.code == "validation_failed"


def test_a_number_param_accepts_an_integer_default():
    assert load("params: {r: {type: number, default: 2}}\nsteps: []\n").ok


# --- values ------------------------------------------------------------------------------

STRING = ScriptParam(name="s")
INTEGER = ScriptParam(name="i", type="integer")
NUMBER = ScriptParam(name="n", type="number")
BOOLEAN = ScriptParam(name="b", type="boolean")
SECRET = ScriptParam(name="pw", secret=True, type="integer")


@pytest.mark.parametrize(("param", "value", "expected"), [
    (STRING, "ACME #42 'quoted' 010\nline two", "ACME #42 'quoted' 010\nline two"),
    (STRING, "", ""),
    (INTEGER, 7, 7),
    (INTEGER, " -12 ", -12),
    (INTEGER, "010", 10),
    (NUMBER, 2, 2),
    (NUMBER, 2.5, 2.5),
    (NUMBER, "1e3", 1000.0),
    (NUMBER, "3", 3),
    (BOOLEAN, True, True),
    (BOOLEAN, "TRUE", True),
    (BOOLEAN, "false", False),
])
def test_values_are_converted_by_type(param, value, expected):
    result = check_value(param, value)
    assert result == expected and type(result) is type(expected)


@pytest.mark.parametrize(("param", "value"), [
    (STRING, 3), (STRING, True), (STRING, None),
    (INTEGER, "3.5"), (INTEGER, True), (INTEGER, 2.0), (INTEGER, "seven"),
    (NUMBER, "nan"), (NUMBER, float("inf")), (NUMBER, False), (NUMBER, ""),
    (BOOLEAN, "yes"), (BOOLEAN, 1), (BOOLEAN, "1"),
])
def test_wrong_types_are_rejected(param, value):
    with pytest.raises(ParamProblem) as info:
        check_value(param, value)
    assert (info.value.name, info.value.reason, info.value.kind) == (param.name, "type",
                                                                     "param")


def test_a_rejected_secret_value_is_not_repeated():
    with pytest.raises(ParamProblem) as info:
        check_value(SECRET, "hunter2")
    assert "hunter2" not in info.value.message
    assert "a secret value" in info.value.message


DECLARED = {p.name: p for p in [ScriptParam(name="user"),
                                ScriptParam(name="password", secret=True),
                                ScriptParam(name="count", type="integer", default=2)]}


def test_resolve_merges_vars_overrides_params_and_defaults():
    variables = resolve(DECLARED, {"user": "ann", "password": "pw"}, {"a": 1, "b": 2},
                        {"b": 3})
    assert variables == {"a": 1, "b": 3, "user": "ann", "password": "pw", "count": 2}
    assert resolve(DECLARED, {"user": "a", "password": "p", "count": "5"}, {}, None)[
        "count"] == 5


def test_resolve_reports_unknown_params_and_vars():
    with pytest.raises(ParamProblem) as info:
        resolve(DECLARED, {"user": "a", "password": "p", "colour": "red"}, {}, None)
    assert (info.value.name, info.value.reason) == ("colour", "unknown")
    with pytest.raises(ParamProblem) as info:
        resolve(DECLARED, {"user": "a", "password": "p"}, {"a": 1}, {"z": 2})
    assert (info.value.name, info.value.reason, info.value.kind) == ("z", "unknown", "var")
    with pytest.raises(ParamProblem) as info:
        resolve(DECLARED, {"password": "p"}, {}, {"user": "x"})
    assert "it is a param" in info.value.message


def test_resolve_lists_every_missing_param():
    with pytest.raises(ParamProblem) as info:
        resolve(DECLARED, {}, {}, None)
    assert info.value.reason == "missing"
    assert info.value.message == "missing required params: 'user', 'password'"
    assert missing_names(DECLARED, {"password": "x"}) == ["user"]


# --- masking -----------------------------------------------------------------------------


def test_secret_values_are_the_texts_of_secret_params_longest_first():
    declared = {"a": ScriptParam(name="a", secret=True), "b": ScriptParam(name="b",
                                                                          secret=True),
                "c": ScriptParam(name="c")}
    assert secret_values(declared, {"a": "pw", "b": "longer pw", "c": "public"}) == [
        "longer pw", "pw"]
    assert secret_values(declared, {"a": "", "b": None, "c": "x"}) == []


def test_mask_replaces_every_occurrence_of_a_value_everywhere():
    secrets = ["s3cr#t pass", "1234"]
    value = {"text": "s3cr#t pass", "message": "user ann with s3cr#t pass, again s3cr#t pass",
             "nested": [{"pin": 1234}, "pin=1234", 12345, True],
             "s3cr#t pass": "as a key"}
    assert mask(value, secrets) == {"text": MASK,
                                    "message": f"user ann with {MASK}, again {MASK}",
                                    "nested": [{"pin": MASK}, f"pin={MASK}", 12345, True],
                                    "s3cr#t pass": "as a key"}  # keys are names
    assert mask(value, []) is value


def test_a_short_secret_is_masked_only_as_a_whole_value():
    secrets = ["s3cr#t pass", "x", "12"]
    value = {"backend": "x11", "x": "x", "list": ["x", " x", "box"], "pin": 12, "n": 120,
             "message": "x and 12 inside s3cr#t pass", "number": "12"}
    assert mask(value, secrets) == {
        "backend": "x11", "x": MASK, "list": [MASK, " x", "box"], "pin": MASK, "n": 120,
        "message": f"x and 12 inside {MASK}", "number": MASK}
    assert MIN_SECRET_LENGTH == 4
    assert mask("abcd and abcde", ["abcd", "abc"]) == f"{MASK} and {MASK}e"


def test_mask_payload_never_masks_keys():
    payload = {"backend": "x11", "variables": {"x": "x", "box": {"x": 1}}, "path": [0]}
    assert mask_payload(payload, ["x"]) == {
        "backend": "x11", "variables": {"x": MASK, "box": {"x": 1}}, "path": [0]}


def test_short_secret_params_are_named_in_declaration_order():
    declared = {"a": ScriptParam(name="a", secret=True), "b": ScriptParam(name="b"),
                "c": ScriptParam(name="c", secret=True, type="integer"),
                "d": ScriptParam(name="d", secret=True)}
    variables = {"a": "abc", "b": "x", "c": 7, "d": "long enough"}
    assert short_secret_params(declared, variables) == ["a", "c"]
    assert short_secret_params(declared, {"a": "", "c": None}) == []
    message = short_secret_warning("secret param 'a'")
    assert message.startswith("secret param 'a' has fewer than 4 characters")
