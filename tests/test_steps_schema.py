import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from ruamel.yaml import YAML

from j5_macro.steps.builtins import default_registry
from j5_macro.steps.schema import script_json_schema

CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "contract.md"
SCHEMA = script_json_schema(default_registry())
VALIDATOR = Draft202012Validator(SCHEMA)


def contract_example() -> object:
    text = CONTRACT.read_text(encoding="utf-8")
    section = text.split("## Script format", 1)[1]
    match = re.search(r"```yaml\n(.*?)```", section, re.S)
    assert match is not None
    return YAML(typ="safe").load(match[1])


def script(*steps: object) -> dict[str, object]:
    return {"version": 1, "name": "t", "steps": list(steps)}


def errors(document: object) -> list[str]:
    return [e.message for e in VALIDATOR.iter_errors(document)]


def test_schema_is_a_valid_draft_2020_12_schema():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_contract_example_validates():
    assert errors(contract_example()) == []


@pytest.mark.parametrize("step", [
    {"sleep": "2s"},
    {"sleep": 1.5},
    {"sleep": {"duration": "500ms±10%"}},
    {"pause": None},
    {"pause": "check"},
    {"log": {"message": "hi", "level": "warning"}},
    {"click": {"x": "${px}", "y": 3, "button": "${btn}"}},
    {"loop": {"for_each": [1, 2], "as": "n", "do": [{"log": "${n}"}]}},
    {"if": {"when": "x > 1", "then": [{"sleep": "1s"}], "else": []}},
    {"id": "a", "break": True, "timeout": "5s", "on_error": "retry",
     "input": {"mouse": {"strategy": "direct"}}, "key": "ctrl+s"},
])
def test_valid_steps(step):
    assert errors(script(step)) == []


@pytest.mark.parametrize("step", [
    {"sleep": "2s", "log": "x"},          # two step types
    {"teleport": {"x": 1}},              # unknown step type
    {"id": "only_common_fields"},        # no step type
    {"sleep": "soon"},
    {"click": {"x": 1, "speed": 3}},     # unknown parameter
    {"log": {"message": "m", "level": "trace"}},
    {"if": {"when": "x"}},               # missing 'then'
    {"loop": {"times": 2, "do": [{"bogus": 1}]}},  # nested steps are checked
    {"sleep": "1s", "on_error": "ignore"},
])
def test_invalid_steps(step):
    assert errors(script(step))


def test_unknown_top_level_key_is_rejected():
    assert errors({"steps": [], "extra": 1})


def test_module_prints_the_schema():
    out = subprocess.run([sys.executable, "-m", "j5_macro.steps.schema"], check=True,
                         capture_output=True, text=True)
    assert json.loads(out.stdout) == SCHEMA
    assert out.stderr == ""
