"""Small option parsers shared by the CLI commands."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

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


_INTEGER = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


def parse_scalar(text: str) -> Any:
    """``--var`` values: ``3`` -> 3, ``2.5`` -> 2.5, ``true``/``false`` -> bool, ``null``
    -> None, and a value wrapped in matching quotes (``'3'``) -> the text inside; anything
    else stays the original string, verbatim (``#`` and spaces included)."""
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "'\"":
        return stripped[1:-1]
    if _INTEGER.match(stripped):
        return int(stripped)
    if _FLOAT.match(stripped):
        return float(stripped)
    lowered = stripped.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "~"):
        return None
    return text


def parse_pair(text: str, what: str = "VALUE") -> tuple[str, str]:
    """``NAME=VALUE`` with an identifier name; the value is kept verbatim. Raises
    :class:`argparse.ArgumentTypeError`."""
    name, sep, value = text.partition("=")
    name = name.strip()
    if not sep or not name:
        raise argparse.ArgumentTypeError(f"expected NAME={what}, got {text!r}")
    if not name.isidentifier():
        raise argparse.ArgumentTypeError(f"invalid name {name!r}")
    return name, value


def parse_param(text: str) -> tuple[str, str]:
    """``--param NAME=VALUE``: the value is passed as text, exactly as written."""
    return parse_pair(text)


def parse_param_file(text: str) -> tuple[str, str]:
    """``--param-file NAME=PATH`` (``-`` for standard input)."""
    return parse_pair(text, "PATH")


def parse_param_env(text: str) -> tuple[str, str]:
    """``--param-env NAME=VARIABLE``."""
    name, variable = parse_pair(text, "VARIABLE")
    if not variable:
        raise argparse.ArgumentTypeError(f"expected NAME=VARIABLE, got {text!r}")
    return name, variable


def read_param_file(path: str, stdin: TextIO) -> str:
    """The text of a ``--param-file``: the whole file (or standard input for ``-``) as
    UTF-8, without one trailing line break. Raises ValueError."""
    try:
        if path == "-":
            text = stdin.read()
        else:
            with open(path, encoding="utf-8", newline="") as handle:
                text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read {path!r}: {exc}") from exc
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith("\n"):
        return text[:-1]
    return text


def read_params_file(path: str) -> dict[str, Any]:
    """``--params-file``: a YAML or JSON mapping of param names to values. Raises
    ValueError."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = YAML(typ="safe", pure=True).load(handle)
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise ValueError(f"cannot read params from {path!r}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict) or not all(isinstance(k, str) for k in data):
        raise ValueError(f"{path!r} must contain a mapping of param names to values")
    for name, value in data.items():
        if not isinstance(value, str | int | float | bool):
            raise ValueError(f"{path!r}: param {name!r} must be a string, a number or a "
                             "boolean")
    return dict(data)


def collect_params(params_file: str | None, inline: Sequence[tuple[str, str]],
                   files: Sequence[tuple[str, str]], env: Sequence[tuple[str, str]],
                   *, environ: Mapping[str, str], stdin: TextIO) -> dict[str, Any]:
    """Every param given on the command line. A name may come from one source only.
    Raises ValueError."""
    values: dict[str, Any] = read_params_file(params_file) if params_file else {}
    sources = {name: "--params-file" for name in values}

    def add(name: str, value: Any, source: str) -> None:
        if name in sources:
            raise ValueError(f"param {name!r} is given twice ({sources[name]} and {source})")
        sources[name] = source
        values[name] = value

    seen = dict(sources)
    for flag, pairs in (("--param", inline), ("--param-file", files), ("--param-env", env)):
        for name, _ in pairs:
            if name in seen:
                raise ValueError(f"param {name!r} is given twice ({seen[name]} and {flag})")
            seen[name] = flag
    for name, value in inline:
        add(name, value, "--param")
    if sum(1 for _, path in files if path == "-") > 1:
        raise ValueError("only one --param-file can read standard input (-)")
    for name, path in files:
        add(name, read_param_file(path, stdin), "--param-file")
    for name, variable in env:
        if variable not in environ:
            raise ValueError(f"--param-env {name}={variable}: the environment variable "
                             f"{variable} is not set")
        add(name, environ[variable], "--param-env")
    return values


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
