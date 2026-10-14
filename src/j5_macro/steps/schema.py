"""JSON Schema (draft 2020-12) for script files, built from the step registry.

Editors such as VS Code (YAML extension) use it for autocompletion and validation.
Cross-field rules (``registry.check_params``) are not expressed here; ``validate`` is
still the authority. Print the default schema with ``python -m j5_macro.steps.schema``.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

from j5_macro.steps.spec import ParamSpec, ParamType, ResultField, StepSpec

if TYPE_CHECKING:
    from j5_macro.steps.registry import StepRegistry

DRAFT = "https://json-schema.org/draft/2020-12/schema"
VARIABLE_NAME = r"^[A-Za-z_][A-Za-z0-9_]*$"
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
        "as": {
            "anyOf": [
                {"type": "string", "pattern": VARIABLE_NAME},
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "pattern": VARIABLE_NAME},
                        "secret": {"type": "boolean", "default": False,
                                   "description": "Mask every string of the result as a "
                                                  "secret of the run."},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            ],
            "description": "Keep the step's result in this variable when the step "
                           "succeeds, e.g. as: label or as: {name: token, secret: true}.",
        },
    }


def _input_profile(description: str) -> JsonSchema:
    return {
        "type": "object",
        "properties": {"mouse": {"type": "object"}, "keyboard": {"type": "object"}},
        "additionalProperties": False,
        "description": description,
    }


def result_schema(spec: StepSpec) -> JsonSchema:
    """The schema of a step type's result (what ``as`` stores), from its declared fields;
    an open object when the step type declares none."""
    if not spec.result:
        return {"type": "object", "description": f"Result of '{spec.name}'."}
    properties = {f.name: _result_field_schema(f) for f in spec.result}
    schema: JsonSchema = {"type": "object", "description": f"Result of '{spec.name}'.",
                          "properties": properties}
    required = [f.name for f in spec.result if not f.optional]
    if required:
        schema["required"] = required
    return schema


def _result_field_schema(result: ResultField) -> JsonSchema:
    kind = result.type
    schema = dict(_STRUCTURED[kind] if kind in _STRUCTURED else _SCALAR[kind])
    if result.doc:
        schema["description"] = result.doc
    return schema


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
            "capture_scale": {
                "type": "number", "exclusiveMinimum": 0, "default": 1,
                "description": "Display scale the templates and pixel offsets were taken "
                               "at (1.5 for 150 %); j5 scales them by the run's display "
                               "scale over this one.",
            },
            "vars": {"type": "object", "description": "Initial variables."},
            "steps": {"type": "array", "items": {"$ref": STEP_REF}},
            "images": {
                "type": "object",
                "description": "Templates embedded in the script, referred to as "
                               "image: \"#name\".",
                "propertyNames": {"pattern": "^[A-Za-z_][A-Za-z0-9_-]*$"},
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "png": {"type": "string", "contentEncoding": "base64",
                                "contentMediaType": "image/png",
                                "description": "Base64 of the PNG file (at most 1 MiB "
                                               "decoded); line breaks are ignored."},
                        "scale": {"type": "number", "exclusiveMinimum": 0,
                                  "description": "Display scale the image was captured "
                                                 "at, e.g. 1.5; replaces capture_scale "
                                                 "for this image."},
                    },
                    "required": ["png"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["steps"],
        "additionalProperties": False,
        "$defs": {
            "step": step_schema(registry),
            # Not referenced: what each step type's result (and so ``as``) holds.
            **{f"{name}_result": result_schema(spec) for name in registry.names()
               if (spec := registry.spec(name)) is not None},
        },
    }


def main() -> None:
    from j5_macro.steps.builtins import default_registry

    json.dump(script_json_schema(default_registry()), sys.stdout, indent=2,
              ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
