"""JSON Schema (draft 2020-12) for script files, built from the step registry.

Editors such as VS Code (YAML extension) use it for autocompletion and validation.
Cross-field rules (``registry.check_params``) are not expressed here; ``validate`` is
still the authority. Print the default schema with ``python -m j5_macro.steps.schema``.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

from j5_macro.steps.spec import ParamSpec, ParamType, StepSpec

if TYPE_CHECKING:
    from j5_macro.steps.registry import StepRegistry

DRAFT = "https://json-schema.org/draft/2020-12/schema"
STEP_REF = "#/$defs/step"

JsonSchema = dict[str, Any]

INTERPOLATION: JsonSchema = {"type": "string", "pattern": r"\$\{"}
"""A string with ``${...}``: accepted for any scalar, checked after interpolation."""

DURATION: JsonSchema = {
    "anyOf": [
        {"type": "number", "minimum": 0},
        {"type": "string",
         "pattern": r"^\s*\d+(\.\d+)?\s*(ms|s|m|h)\s*((±|\+-)\s*\d+(\.\d+)?\s*%)?\s*$"},
    ],
    "description": "A duration such as 2s, 500ms, 1.5m or 2s±20%; a bare number is seconds.",
}

_SCALAR: dict[ParamType, JsonSchema] = {
    "string": {"type": "string"},
    "expression": {"type": "string"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "duration": DURATION,
    "condition": {
        "anyOf": [
            {"type": "string"},
            {"type": "object", "minProperties": 1, "maxProperties": 1},
        ],
    },
}
_STRUCTURED: dict[ParamType, JsonSchema] = {
    "any": {},
    "object": {"type": "object"},
    "list": {"type": "array"},
}


def param_schema(param: ParamSpec) -> JsonSchema:
    """The schema of one parameter value."""
    if param.type in _STRUCTURED:
        schema = dict(_STRUCTURED[param.type])
    else:
        base = dict(_SCALAR[param.type])
        free_text = param.type in ("string", "expression", "condition")
        if param.choices is not None:
            base, free_text = {"enum": list(param.choices)}, False
        if not free_text:
            base = {"anyOf": [base, INTERPOLATION]}
        schema = base
    if param.doc:
        schema["description"] = param.doc
    if param.default is not None:
        schema["default"] = param.default
    return schema


def params_schema(spec: StepSpec, step_ref: str | None = None) -> JsonSchema:
    """The mapping form of a step: its parameters and block slots.

    Block items reference ``step_ref`` when given, else are plain objects.
    """
    properties: dict[str, JsonSchema] = {p.name: param_schema(p) for p in spec.params}
    items: JsonSchema = {"$ref": step_ref} if step_ref else {"type": "object"}
    for block in spec.blocks:
        properties[block] = {"type": "array", "items": items,
                             "description": f"Steps of the '{block}' block."}
    required = [p.name for p in spec.params if p.required] + list(spec.required_blocks)
    schema: JsonSchema = {"type": "object", "properties": properties,
                          "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def step_value_schema(spec: StepSpec, step_ref: str | None = None) -> JsonSchema:
    """What may follow the step-type key: the mapping, the shorthand scalar, or nothing
    when the step has no required parameters or blocks (``pause:``)."""
    forms = [params_schema(spec, step_ref)]
    if spec.shorthand is not None:
        shorthand = spec.param(spec.shorthand)
        if shorthand is not None:
            forms.append(param_schema(shorthand))
    if not any(p.required for p in spec.params) and not spec.required_blocks:
        forms.append({"type": "null"})
    schema: JsonSchema = forms[0] if len(forms) == 1 else {"anyOf": forms}
    return {"description": spec.doc, **schema}


def _common_fields() -> dict[str, JsonSchema]:
    return {
        "id": {"type": "string", "description": "Stable step id, used by breakpoints."},
        "break": {"type": "boolean", "description": "Pause before this step."},
        "timeout": {**DURATION, "description": "Maximum time for this step."},
        "on_error": {"enum": ["stop", "continue", "retry", "pause"], "default": "stop",
                     "description": "What to do when the step fails."},
        "input": _input_profile("Input profile override for this step."),
    }


def _input_profile(description: str) -> JsonSchema:
    return {
        "type": "object",
        "properties": {"mouse": {"type": "object"}, "keyboard": {"type": "object"}},
        "additionalProperties": False,
        "description": description,
    }


def step_schema(registry: StepRegistry) -> JsonSchema:
    """One step: the common fields plus exactly one step-type key."""
    names = registry.names()
    properties = _common_fields()
    for name in names:
        spec = registry.spec(name)
        assert spec is not None
        properties[name] = step_value_schema(spec, STEP_REF)
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "oneOf": [{"required": [name]} for name in names],
    }


def script_json_schema(registry: StepRegistry) -> JsonSchema:
    """The JSON Schema of a whole script file."""
    return {
        "$schema": DRAFT,
        "title": "j5-macro script",
        "type": "object",
        "properties": {
            "version": {"const": 1, "description": "Script format version."},
            "name": {"type": "string"},
            "input": _input_profile("Default input profile of the script."),
            "params": {
                "type": "object",
                "description": "Values the script takes from outside (j5 run --param).",
                "propertyNames": {"pattern": "^[A-Za-z_][A-Za-z0-9_]*$"},
                "additionalProperties": {
                    "oneOf": [
                        {"type": "null", "description": "A required string."},
                        {
                            "type": "object",
                            "properties": {
                                "type": {"enum": ["string", "integer", "number", "boolean"],
                                         "default": "string"},
                                "description": {"type": "string"},
                                "default": {"type": ["string", "number", "boolean"],
                                            "description": "Makes the param optional."},
                                "secret": {"type": "boolean", "default": False,
                                           "description": "Masked as *** in events and "
                                                          "history; cannot have a default."},
                            },
                            "additionalProperties": False,
                        },
                    ],
                },
            },
            "vars": {"type": "object", "description": "Initial variables."},
            "steps": {"type": "array", "items": {"$ref": STEP_REF}},
        },
        "required": ["steps"],
        "additionalProperties": False,
        "$defs": {"step": step_schema(registry)},
    }


def main() -> None:
    from j5_macro.steps.builtins import default_registry

    json.dump(script_json_schema(default_registry()), sys.stdout, indent=2,
              ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
