"""Small option parsers shared by the CLI commands."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from j5_macro.client import DEFAULT_COMMAND

ENGINE_CMD_ENV = "J5_ENGINE_CMD"
"""Overrides the engine command with a JSON list of strings (used by the tests)."""

FAST_PROFILE: dict[str, Any] = {
    "mouse": {"strategy": "direct"},
    "keyboard": {"strategy": "direct"},
}
"""``input_profile`` sent by ``j5 run --fast``."""


def parse_scalar(text: str) -> Any:
    """A YAML 1.2 scalar: ``3`` -> 3, ``2.5`` -> 2.5, ``true`` -> True, ``null`` -> None.

    Anything else, including text that YAML would read as a list or a mapping, stays
    the original string, and so does an empty value.
    """
    if not text.strip():
        return text
    try:
        value = YAML(typ="safe", pure=True).load(text)
    except YAMLError:
        return text
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return text


def parse_var(text: str) -> tuple[str, Any]:
    """``NAME=VALUE`` for ``--var``; raises :class:`argparse.ArgumentTypeError`."""
    name, sep, value = text.partition("=")
    name = name.strip()
    if not sep or not name:
        raise argparse.ArgumentTypeError(f"expected NAME=VALUE, got {text!r}")
    if not name.isidentifier():
        raise argparse.ArgumentTypeError(f"invalid variable name {name!r}")
    return name, parse_scalar(value)


def engine_command(environ: Mapping[str, str] | None = None) -> list[str]:
    """The command that launches the engine: ``$J5_ENGINE_CMD`` (JSON list) or the default."""
    env = os.environ if environ is None else environ
    raw = env.get(ENGINE_CMD_ENV)
    if not raw:
        return list(DEFAULT_COMMAND)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{ENGINE_CMD_ENV} is not valid JSON: {exc}") from exc
    if (not isinstance(value, list) or not value
            or not all(isinstance(item, str) for item in value)):
        raise ValueError(f"{ENGINE_CMD_ENV} must be a non-empty JSON list of strings")
    return value
