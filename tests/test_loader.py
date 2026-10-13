"""Tests for the YAML script loader, using fake step specs defined here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.messages import ErrorInfo
from j5_macro.script import LoadResult, Script, Step, StoreAs, load_file, load_script
from j5_macro.steps.spec import ParamProblem, ParamSpec, StepSpec

SPECS = {
    spec.name: spec
    for spec in (
        StepSpec(name="sleep", doc="Wait.", shorthand="duration",
                 params=(ParamSpec(name="duration", type="duration", required=True),)),
        StepSpec(name="log", doc="Log.", shorthand="message", params=(
            ParamSpec(name="message", type="string", required=True),
            ParamSpec(name="level", type="string", default="info",
                      choices=("debug", "info", "warning", "error")),
        )),
        StepSpec(name="click", doc="Click.", params=(
            ParamSpec(name="x", type="integer"),
            ParamSpec(name="y", type="integer"),
            ParamSpec(name="image", type="string"),
            ParamSpec(name="threshold", type="number", default=0.9),
            ParamSpec(name="button", type="string", default="left"),
            ParamSpec(name="window", type="object"),
        )),
        StepSpec(name="if", doc="Branch.", kind="control", blocks=("then", "else"),
                 required_blocks=("then",),
                 params=(ParamSpec(name="when", type="condition", required=True),)),
        StepSpec(name="loop", doc="Repeat.", kind="control", blocks=("do",),
                 required_blocks=("do",), params=(
                     ParamSpec(name="times", type="integer"),
                     ParamSpec(name="while", type="condition"),
                     ParamSpec(name="for_each", type="any"),
                     ParamSpec(name="as", type="string"),
                 )),
        StepSpec(name="type", doc="Type text.", params=(
            ParamSpec(name="text", type="string", required=True),
            ParamSpec(name="keyboard", type="object"),
        )),
    )
}


def lookup(name: str) -> StepSpec | None:
    return SPECS.get(name)


def check(step_type: str, params: dict[str, Any]) -> list[ParamProblem]:
    if step_type == "click" and params.get("image") is None and (
            params.get("x") is None or params.get("y") is None):
        return [ParamProblem("image", "give 'image' or both 'x' and 'y'")]
    return []


def load(text: str, **kwargs: Any) -> LoadResult:
    return load_script(text, lookup=lookup, check=check, **kwargs)


def ok(text: str, **kwargs: Any) -> Script:
    result = load(text, **kwargs)
    assert result.errors == [], result.errors
    assert result.script is not None
    return result.script


def errors(text: str) -> list[ErrorInfo]:
    result = load(text)
    assert result.script is None
    assert result.errors
    return result.errors


def only_error(text: str) -> ErrorInfo:
    found = errors(text)
    assert len(found) == 1, found
    return found[0]


def by_id(script: Script) -> dict[str, Step]:
    return {step.id: step for step in script.walk()}


CONTRACT_EXAMPLE = """\
version: 1
name: Save report
input:
  mouse:    {strategy: human, speed: 1.0}
  keyboard: {strategy: human, wpm: 55}
vars:
  filename: october_report
steps:
  - id: open_menu
    click: {image: img/file_menu.png, threshold: 0.9}
  - id: wait_dialog
    sleep: {duration: 10s}
    on_error: stop
  - type: {text: "${filename}", keyboard: {strategy: direct}}
  - id: confirm
    break: true
    log: {message: enter}
  - loop:
      times: 3
      do:
        - if:
            when: {image_visible: img/error.png}
            then:
              - click: {image: img/retry.png}
              - sleep: 2s
"""


# --- the contract example ---------------------------------------------------------------


def test_contract_example_loads() -> None:
    script = ok(CONTRACT_EXAMPLE)
    assert script.name == "Save report"
    assert script.version == 1
    assert script.input == {"mouse": {"strategy": "human", "speed": 1.0},
                            "keyboard": {"strategy": "human", "wpm": 55}}
    assert script.vars == {"filename": "october_report"}
    assert script.text == CONTRACT_EXAMPLE
    assert script.source_path is None

    rows = [(s.id, s.type, s.line, s.path) for s in script.walk()]
    assert rows == [
        ("open_menu", "click", 9, (0,)),
        ("wait_dialog", "sleep", 11, (1,)),
        ("type_1", "type", 14, (2,)),
        ("confirm", "log", 15, (3,)),
        ("loop_1", "loop", 18, (4,)),
        ("if_1", "if", 21, (4, 0, 0)),
        ("click_2", "click", 24, (4, 0, 0, 0, 0)),
        ("sleep_2", "sleep", 25, (4, 0, 0, 0, 1)),
    ]
    steps = by_id(script)
    assert steps["open_menu"].params == {"image": "img/file_menu.png", "threshold": 0.9,
                                          "button": "left"}
    assert steps["wait_dialog"].on_error == "stop"
    assert steps["type_1"].params == {"text": "${filename}",
                                      "keyboard": {"strategy": "direct"}}
    assert steps["confirm"].breakpoint is True
    assert steps["confirm"].params == {"message": "enter", "level": "info"}
    assert steps["loop_1"].params == {"times": 3}
    assert list(steps["loop_1"].blocks) == ["do"]
    assert steps["if_1"].params == {"when": {"image_visible": "img/error.png"}}
    assert [c.id for c in steps["if_1"].blocks["then"]] == ["click_2", "sleep_2"]
    assert script.find("sleep_2") is steps["sleep_2"]


def test_contract_example_warns_about_generated_ids() -> None:
    script = ok(CONTRACT_EXAMPLE)
    warned = [(w.step_id, w.line) for w in script.warnings]
    assert warned == [("type_1", 14), ("loop_1", 18), ("if_1", 21), ("click_2", 24),
                      ("sleep_2", 25)]
    for warning in script.warnings:
        assert warning.code == ErrorCode.VALIDATION_FAILED
        assert "add an explicit id" in warning.message


# --- shorthand and params ---------------------------------------------------------------


def test_shorthand_forms() -> None:
    script = ok("""\
steps:
  - sleep: 2s
  - sleep: 1.5
  - log: hello
  - log: {message: hi, level: debug}
""")
    assert [s.params for s in script.steps] == [
        {"duration": "2s"},
        {"duration": 1.5},
        {"message": "hello", "level": "info"},
        {"message": "hi", "level": "debug"},
    ]


def test_scalar_on_step_without_shorthand_is_an_error() -> None:
    error = only_error("steps:\n  - id: c\n    click: 5\n")
    assert error.code == ErrorCode.VALIDATION_FAILED
    assert error.step_id == "c"
    assert error.line == 2
    assert "short form" in error.message


def test_null_value_means_no_params() -> None:
    result = load_script("steps:\n  - click:\n", lookup=lookup)
    assert result.script is not None
    assert result.script.steps[0].params == {"threshold": 0.9, "button": "left"}


def test_null_value_still_runs_cross_field_check() -> None:
    error = only_error("steps:\n  - click:\n")
    assert error.details == {"param": "image"}


def test_params_are_plain_python_types() -> None:
    script = ok("""\
steps:
  - id: c
    click:
      x: 10
      y: -3
      threshold: 0.75
      window: {title: "Bloc de notas", nested: [1, 2.5, true, null, "s"]}
""")
    params = script.steps[0].params

    def assert_plain(value: Any) -> None:
        if isinstance(value, dict):
            assert type(value) is dict
            for key, item in value.items():
                assert type(key) is str
                assert_plain(item)
        elif isinstance(value, list):
            assert type(value) is list
            for item in value:
                assert_plain(item)
        else:
            assert value is None or type(value) in (str, int, float, bool)

    assert_plain(params)
    assert params["window"] == {"title": "Bloc de notas",
                                "nested": [1, 2.5, True, None, "s"]}
    assert type(params["window"]["nested"][2]) is bool
    assert type(params["x"]) is int and type(params["threshold"]) is float


def test_unicode_text() -> None:
    script = ok("""\
name: Configuración rápida
vars:
  greeting: "¡Hola, señor Muñoz!"
steps:
  # comentario con tildes: acción
  - id: escribir
    type: {text: "año, acción, pingüino"}
""")
    assert script.name == "Configuración rápida"
    assert script.vars == {"greeting": "¡Hola, señor Muñoz!"}
    assert script.steps[0].params["text"] == "año, acción, pingüino"
    assert script.steps[0].line == 6


def test_non_ascii_id_is_rejected() -> None:
    error = only_error("steps:\n  - id: escribir_ñandú\n    sleep: 1s\n")
    assert error.details == {"field": "id"}


# --- common fields ----------------------------------------------------------------------


def test_common_fields() -> None:
    script = ok("""\
steps:
  - id: s
    break: true
    timeout: 1.5m
    on_error: retry
    input: {mouse: {strategy: direct}}
    sleep: 1s
""")
    step = script.steps[0]
    assert step.breakpoint is True
    assert step.timeout == 90.0
    assert step.on_error == "retry"
    assert step.input == {"mouse": {"strategy": "direct"}}


def test_common_field_defaults() -> None:
    step = ok("steps:\n  - id: s\n    sleep: 1s\n").steps[0]
    assert (step.breakpoint, step.timeout, step.on_error, step.input, step.store_as) == (
        False, None, "stop", None, None)


@pytest.mark.parametrize(("value", "name", "secret"), [
    ("label", "label", False),
    ("_x1", "_x1", False),
    ("{name: token}", "token", False),
    ("{name: token, secret: true}", "token", True),
    ("{name: token, secret: false}", "token", False),
])
def test_as_keeps_the_result_in_a_variable(value: str, name: str, secret: bool) -> None:
    step = ok(f"steps:\n  - id: s\n    sleep: 1s\n    as: {value}\n").steps[0]
    assert step.store_as == StoreAs(name=name, secret=secret)


def test_as_works_on_block_steps_next_to_a_loop_as() -> None:
    step = ok("""\
steps:
  - id: each
    loop:
      for_each: [1, 2]
      as: row
      do:
        - id: show
          log: "${row}"
    as: rounds
""").steps[0]
    assert step.params["as"] == "row"
    assert step.store_as == StoreAs(name="rounds")


@pytest.mark.parametrize("value", [
    "1abc", "a-b", '""', "if", "[a]", "{secret: true}", "{name: a, secret: yes}",
    "{name: a, mask: true}", "null",
])
def test_bad_as(value: str) -> None:
    error = only_error(f"steps:\n  - id: s\n    sleep: 1s\n    as: {value}\n")
    assert error.code == ErrorCode.VALIDATION_FAILED
    assert error.details == {"field": "as"}
    assert error.step_id == "s"


@pytest.mark.parametrize(("field", "value"), [
    ("break", '"yes"'),
    ("break", "1"),
    ("on_error", "ignore"),
    ("timeout", "soon"),
    ("timeout", "-1"),
    ("input", "fast"),
    ("id", "1abc"),
    ("id", "a b"),
    ("id", "12"),
])
def test_bad_common_fields(field: str, value: str) -> None:
    error = only_error(f"steps:\n  - {field}: {value}\n    sleep: 1s\n")
    assert error.code == ErrorCode.VALIDATION_FAILED
    assert error.line == 2
    assert error.details == {"field": field}
    if field != "id":
        assert error.step_id == "sleep_1"


# --- blocks and paths -------------------------------------------------------------------


def test_nested_blocks_and_paths() -> None:
    script = ok("""\
steps:
  - id: a
    sleep: 1s
  - id: b
    sleep: 1s
  - id: c
    sleep: 1s
  - id: d
    sleep: 1s
  - id: outer
    loop:
      times: 2
      do:
        - id: branch
          if:
            when: "x > 1"
            then:
              - id: t0
                log: then
            else:
              - id: e0
                log: else 0
              - id: e1
                loop:
                  while: "y < 3"
                  do:
                    - id: deep
                      sleep: 10ms
""")
    paths = {s.id: s.path for s in script.walk()}
    assert paths == {
        "a": (0,), "b": (1,), "c": (2,), "d": (3,),
        "outer": (4,),
        "branch": (4, 0, 0),
        "t0": (4, 0, 0, 0, 0),
        "e0": (4, 0, 0, 1, 0),
        "e1": (4, 0, 0, 1, 1),
        "deep": (4, 0, 0, 1, 1, 0, 0),
    }
    lines = {s.id: s.line for s in script.walk()}
    assert lines["e0"] == 21 and lines["deep"] == 27
    branch = script.find("branch")
    assert branch is not None
    assert list(branch.blocks) == ["then", "else"]
    assert branch.params == {"when": "x > 1"}
    assert script.warnings == []


def test_else_before_then_keeps_slot_indices() -> None:
    script = ok("""\
steps:
  - if:
      when: "true"
      else:
        - log: e
      then:
        - log: t
""")
    paths = {s.params.get("message"): s.path for s in script.walk() if s.type == "log"}
    assert paths == {"e": (0, 1, 0), "t": (0, 0, 0)}


def test_optional_block_absent_or_null() -> None:
    script = ok("steps:\n  - if:\n      when: a\n      then:\n      else:\n")
    assert script.steps[0].blocks == {"then": [], "else": []}
    script = ok("steps:\n  - if: {when: a, then: [{log: x}]}\n")
    assert list(script.steps[0].blocks) == ["then"]


def test_missing_required_block() -> None:
    error = only_error("steps:\n  - id: l\n    loop: {times: 2}\n")
    assert error.step_id == "l"
    assert error.details == {"block": "do"}


def test_block_must_be_a_list() -> None:
    error = only_error("steps:\n  - id: l\n    loop: {times: 2, do: {log: x}}\n")
    assert error.details == {"block": "do"}


def test_errors_inside_blocks_have_their_own_line() -> None:
    error = only_error("""\
steps:
  - loop:
      times: 2
      do:
        - sleep: 1s
        - sleep: never
""")
    assert error.line == 6
    assert error.step_id == "sleep_2"
    assert error.details == {"param": "duration"}


# --- ids --------------------------------------------------------------------------------


def test_generated_ids_skip_explicit_ones() -> None:
    script = ok("""\
steps:
  - sleep: 1s
  - id: sleep_2
    sleep: 1s
  - sleep: 1s
  - log: a
  - id: sleep_3
    log: b
  - sleep: 1s
""")
    assert [s.id for s in script.steps] == [
        "sleep_1", "sleep_2", "sleep_3_2", "log_1", "sleep_3", "sleep_4"]
    assert [(w.step_id, w.line) for w in script.warnings] == [
        ("sleep_1", 2), ("sleep_3_2", 5), ("log_1", 6), ("sleep_4", 9)]


def test_explicit_ids_mean_no_warnings() -> None:
    script = ok("steps:\n  - id: one\n    sleep: 1s\n")
    assert script.warnings == []


def test_duplicate_ids() -> None:
    error = only_error("""\
steps:
  - id: same
    sleep: 1s
  - loop:
      times: 1
      do:
        - id: same
          log: x
""")
    assert error.code == ErrorCode.VALIDATION_FAILED
    assert error.step_id == "same"
    assert error.line == 7
    assert "lines 2 and 7" in error.message
    assert error.details == {"field": "id", "first_line": 2}


# --- step shape -------------------------------------------------------------------------


def test_unknown_step_type() -> None:
    error = only_error("steps:\n  - id: x\n    teleport: {to: mars}\n")
    assert error.code == ErrorCode.UNKNOWN_STEP_TYPE
    assert error.step_id == "x"
    assert error.line == 2
    assert error.details == {"type": "teleport"}


def test_two_type_keys() -> None:
    error = only_error("steps:\n  - sleep: 1s\n    log: hi\n")
    assert error.code == ErrorCode.VALIDATION_FAILED
    assert error.line == 2
    assert error.step_id is None
    assert error.details == {"types": ["sleep", "log"]}


def test_no_type_key() -> None:
    error = only_error("steps:\n  - id: lonely\n    break: true\n")
    assert error.code == ErrorCode.VALIDATION_FAILED
    assert error.step_id == "lonely"
    assert "no type key" in error.message


def test_step_must_be_a_mapping() -> None:
    error = only_error("steps:\n  - sleep: 1s\n  - just text\n")
    assert error.line == 3


def test_list_params_are_rejected() -> None:
    error = only_error("steps:\n  - click: [1, 2]\n")
    assert error.step_id == "click_1"


# --- param validation -------------------------------------------------------------------


def test_param_problems_from_validate_params() -> None:
    found = errors("""\
steps:
  - id: c
    click: {x: "ten", y: 3, colour: red}
  - id: l
    log: {level: loud}
""")
    got = sorted((e.step_id, e.line, (e.details or {}).get("param")) for e in found)
    assert got == [("c", 2, "colour"), ("c", 2, "x"), ("l", 4, "level"),
                   ("l", 4, "message")]
    assert all(e.code == ErrorCode.VALIDATION_FAILED for e in found)


def test_param_problems_from_check() -> None:
    error = only_error("steps:\n  - id: c\n    click: {x: 10}\n")
    assert error.step_id == "c"
    assert error.line == 2
    assert error.details == {"param": "image"}
    assert "both 'x' and 'y'" in error.message


def test_check_is_optional() -> None:
    result = load_script("steps:\n  - click: {x: 10}\n", lookup=lookup)
    assert result.errors == []


def test_interpolated_values_pass_validation() -> None:
    script = ok("steps:\n  - click: {x: '${px}', y: 2}\n")
    assert script.steps[0].params["x"] == "${px}"


def test_all_problems_are_collected_in_line_order() -> None:
    found = errors("""\
version: 2
steps:
  - teleport: x
  - sleep: forever
  - id: a
    sleep: 1s
  - id: a
    sleep: 1s
""")
    assert [(e.code, e.line) for e in found] == [
        (ErrorCode.VALIDATION_FAILED, 1),
        (ErrorCode.UNKNOWN_STEP_TYPE, 3),
        (ErrorCode.VALIDATION_FAILED, 4),
        (ErrorCode.VALIDATION_FAILED, 7),
    ]


# --- top level --------------------------------------------------------------------------


def test_yaml_syntax_error() -> None:
    error = only_error("steps:\n  - sleep: 1s\n  - log: [unclosed\n")
    assert error.code == ErrorCode.PARSE_ERROR
    assert error.line is not None and error.line >= 3
    assert error.details is not None and isinstance(error.details["column"], int)


def test_yaml_syntax_error_position() -> None:
    error = only_error("steps:\n  - sleep: 1s\n   bad: indent\n")
    assert error.code == ErrorCode.PARSE_ERROR
    assert error.line == 3
    assert error.details == {"column": 4}


def test_duplicate_yaml_keys_are_a_parse_error() -> None:
    error = only_error("steps: []\nsteps: []\n")
    assert error.code == ErrorCode.PARSE_ERROR
    assert error.line == 2


@pytest.mark.parametrize("text", ["", "- sleep: 1s\n", "just a string\n"])
def test_top_level_must_be_a_mapping(text: str) -> None:
    error = only_error(text)
    assert error.code == ErrorCode.VALIDATION_FAILED
    assert "mapping" in error.message


def test_missing_steps() -> None:
    error = only_error("name: x\n")
    assert error.details == {"field": "steps"}


def test_steps_must_be_a_list() -> None:
    error = only_error("name: x\nsteps: {sleep: 1s}\n")
    assert error.line == 2


def test_empty_steps_list_is_valid() -> None:
    assert ok("steps: []\n").steps == []


@pytest.mark.parametrize("value", ["2", "0", '"1"', "true"])
def test_unsupported_version(value: str) -> None:
    error = only_error(f"version: {value}\nsteps: []\n")
    assert error.code == ErrorCode.VALIDATION_FAILED
    assert error.line == 1
    assert error.details == {"field": "version"}


def test_name_defaults() -> None:
    assert ok("steps: []\n").name == "script"
    script = ok("steps: []\n", source_path="/tmp/scripts/guardar_informe.yaml")
    assert script.name == "guardar_informe"
    assert script.source_path == "/tmp/scripts/guardar_informe.yaml"


@pytest.mark.parametrize(("text", "field"), [
    ("name: [a]\nsteps: []\n", "name"),
    ("input: 3\nsteps: []\n", "input"),
    ("vars: [a, b]\nsteps: []\n", "vars"),
])
def test_bad_header_fields(text: str, field: str) -> None:
    error = only_error(text)
    assert error.details == {"field": field}
    assert error.line == 1


def test_unknown_top_level_key_is_a_warning() -> None:
    script = ok("steps: []\nauthor: Gabriel\n")
    [warning] = script.warnings
    assert warning.code == ErrorCode.VALIDATION_FAILED
    assert warning.line == 2
    assert warning.details == {"key": "author"}


# --- files ------------------------------------------------------------------------------


def test_load_file(tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    path.write_text("steps:\n  - log: ¡listo!\n", encoding="utf-8")
    result = load_file(path, lookup=lookup, check=check)
    assert result.script is not None
    assert result.script.name == "demo"
    assert result.script.source_path == str(path)
    assert result.script.steps[0].params["message"] == "¡listo!"


def test_load_file_missing(tmp_path: Path) -> None:
    result = load_file(tmp_path / "nope.yaml", lookup=lookup)
    assert result.script is None
    [error] = result.errors
    assert error.code == ErrorCode.SCRIPT_NOT_FOUND
    assert error.details == {"path": str(tmp_path / "nope.yaml")}


def test_load_file_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "latin1.yaml"
    path.write_bytes("name: año\nsteps: []\n".encode("latin-1"))
    [error] = load_file(path, lookup=lookup).errors
    assert error.code == ErrorCode.PARSE_ERROR
