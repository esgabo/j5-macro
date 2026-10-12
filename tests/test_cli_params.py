"""``j5 run`` params: option parsing, prompts, warnings and real runs (real engine)."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from pathlib import Path

import pytest

from j5_macro.cli.commands import (
    Output,
    RunOptions,
    ask_missing,
    describe_param,
    warn_inline_secrets,
)
from j5_macro.cli.main import main
from j5_macro.cli.options import (
    ENGINE_CMD_ENV,
    collect_params,
    parse_param,
    parse_param_env,
    parse_scalar,
    read_param_file,
    read_params_file,
)

SECRET = "s3cr#t pass"
SCRIPT = """\
version: 1
name: login
params:
  user: {description: Account name}
  password: {secret: true}
  retries: {type: integer, default: 2}
steps:
  - id: type_user
    type: "${user}"
  - id: type_password
    type: "${password}"
  - id: note
    log: "${user} / ${password} / ${retries}"
"""


# --- option parsing ----------------------------------------------------------------------


@pytest.mark.parametrize(("text", "value"), [
    ("3", 3), ("-2", -2), ("2.5", 2.5), ("1e3", 1000.0), ("true", True), ("False", False),
    ("null", None), ("Dear customer, order #42 shipped", "Dear customer, order #42 shipped"),
    ("yes", "yes"), ("[1, 2]", "[1, 2]"), ("a: b", "a: b"), ("", ""), ("'quoted'", "quoted"),
    ("'3'", "3"), ('"a #b"', "a #b"), ("'", "'"),
])
def test_var_values_keep_text_verbatim(text, value):
    assert parse_scalar(text) == value


def test_param_values_are_never_parsed():
    assert parse_param("note=order #42: 'x' 010") == ("note", "order #42: 'x' 010")
    assert parse_param("empty=") == ("empty", "")
    assert parse_param("eq=a=b") == ("eq", "a=b")
    for bad in ("novalue", "=x", "1x=y"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_param(bad)
    with pytest.raises(argparse.ArgumentTypeError):
        parse_param_env("pw=")


def test_param_files_drop_one_final_line_break(tmp_path):
    for content, expected in [("one\n", "one"), ("one\r\n", "one"), ("a\nb\n\n", "a\nb\n"),
                              ("a\r\nb\r\n", "a\nb"),
                              ("no break", "no break"), ("ñé€ 日本\n", "ñé€ 日本")]:
        path = tmp_path / "p.txt"
        path.write_bytes(content.encode("utf-8"))
        assert read_param_file(str(path), io.StringIO()) == expected
    assert read_param_file("-", io.StringIO("from stdin\n")) == "from stdin"
    with pytest.raises(ValueError, match="cannot read"):
        read_param_file(str(tmp_path / "missing.txt"), io.StringIO())


def test_params_files_are_mappings_of_scalars(tmp_path):
    good = tmp_path / "p.yaml"
    good.write_text("user: ann\nretries: 3\nok: true\n", encoding="utf-8")
    assert read_params_file(str(good)) == {"user": "ann", "retries": 3, "ok": True}
    as_json = tmp_path / "p.json"
    as_json.write_text(json.dumps({"user": "bob"}), encoding="utf-8")
    assert read_params_file(str(as_json)) == {"user": "bob"}
    for text in ("[1, 2]", "user: [1]"):
        bad = tmp_path / "bad.yaml"
        bad.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            read_params_file(str(bad))


def test_collect_params_from_every_source(tmp_path):
    body = tmp_path / "body.txt"
    body.write_text("line 1\nline 2\n", encoding="utf-8")
    params_file = tmp_path / "p.yaml"
    params_file.write_text("retries: 4\n", encoding="utf-8")
    values = collect_params(str(params_file), [("user", "ann")], [("body", str(body)),
                                                                  ("token", "-")],
                            [("password", "PW")], environ={"PW": SECRET},
                            stdin=io.StringIO("tok\n"))
    assert values == {"retries": 4, "user": "ann", "body": "line 1\nline 2",
                      "token": "tok", "password": SECRET}


@pytest.mark.parametrize(("inline", "files", "env", "message"), [
    ([("a", "1"), ("a", "2")], [], [], "given twice (--param and --param)"),
    ([("a", "1")], [], [("a", "X")], "given twice (--param and --param-env)"),
    ([], [("a", "-"), ("b", "-")], [], "only one --param-file"),
    ([], [], [("a", "MISSING")], "MISSING is not set"),
])
def test_collect_params_problems(inline, files, env, message):
    with pytest.raises(ValueError, match=message.replace("(", r"\(").replace(")", r"\)")):
        collect_params(None, inline, files, env, environ={"X": "1"}, stdin=io.StringIO())


# --- prompts and warnings ----------------------------------------------------------------

DECLARED = [
    {"name": "user", "type": "string", "secret": False, "required": True,
     "description": "Account name"},
    {"name": "password", "type": "string", "secret": True, "required": True},
    {"name": "retries", "type": "integer", "secret": False, "required": False, "default": 2},
]


def output() -> tuple[Output, io.StringIO]:
    err = io.StringIO()
    return Output(out=io.StringIO(), err=err), err


def test_missing_required_params_are_asked_for_secrets_without_echo():
    asked: list[tuple[str, bool]] = []

    def ask(prompt: str, secret: bool) -> str:
        asked.append((prompt, secret))
        return SECRET if secret else "ann"

    options = RunOptions(params={}, ask=ask)
    out, _ = output()
    assert asyncio.run(ask_missing(DECLARED, options, out))
    assert asked == [("user (Account name): ", False), ("password: ", True)]
    assert options.params == {"user": "ann", "password": SECRET}

    given = RunOptions(params={"user": "x", "password": "y"}, ask=ask)
    asked.clear()
    assert asyncio.run(ask_missing(DECLARED, given, out)) and asked == []


def test_giving_up_at_the_prompt_stops_before_running():
    def ask(prompt: str, secret: bool) -> str:
        raise EOFError

    out, err = output()
    assert not asyncio.run(ask_missing(DECLARED, RunOptions(params={}, ask=ask), out))
    assert "no value for param 'user'" in err.getvalue()


def test_nobody_is_asked_without_a_terminal():
    out, _ = output()
    options = RunOptions(params={})
    assert asyncio.run(ask_missing(DECLARED, options, out)) and options.params == {}


def test_secret_given_on_the_command_line_is_warned_about():
    out, err = output()
    warn_inline_secrets(DECLARED, frozenset({"user", "password"}), out)
    assert err.getvalue().count("warning") == 1
    assert "secret param 'password' was given with --param" in err.getvalue()


def test_describe_param():
    assert [describe_param(p) for p in DECLARED] == [
        "param user (string, required): Account name",
        "param password (string, secret, required)",
        "param retries (integer, default 2)",
    ]


# --- real runs ---------------------------------------------------------------------------


@pytest.fixture
def script(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("J5_BACKEND", "fake")
    monkeypatch.delenv(ENGINE_CMD_ENV, raising=False)
    path = tmp_path / "login.yaml"
    path.write_text(SCRIPT, encoding="utf-8")
    return path


def test_validate_lists_the_params(script, capsys):
    assert main(["validate", str(script)]) == 0
    out = capsys.readouterr().out
    assert "param password (string, secret, required)" in out
    assert main(["validate", "--json", str(script)]) == 0
    assert [p["name"] for p in json.loads(capsys.readouterr().out)["params"]] == [
        "user", "password", "retries"]


def test_run_with_params_masks_the_secret_everywhere(script, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("FORM_PW", SECRET)
    code = main(["run", "--dry-run", "--json", "--param", "user=ACME #42",
                 "--param-env", "password=FORM_PW", "--param", "retries=3", str(script)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert SECRET not in captured.out + captured.err
    events = [json.loads(line) for line in captured.out.splitlines()]
    started = {e["payload"]["step_id"]: e["payload"]["params"] for e in events
               if e["type"] == "step_started"}
    assert started["type_user"] == {"text": "ACME #42"}
    assert started["type_password"] == {"text": "***"}
    (log,) = [e for e in events if e["type"] == "log" and e["payload"].get("step_id") == "note"]
    assert log["payload"]["message"] == "ACME #42 / *** / 3"
    (history,) = (tmp_path / "runs").rglob("events.jsonl")
    assert SECRET not in history.read_text(encoding="utf-8")


def test_missing_params_fail_with_a_hint(script, capsys):
    assert main(["run", "--dry-run", "--param", "user=ann", str(script)]) == 2
    err = capsys.readouterr().err
    assert "missing required param: 'password'" in err
    assert "--param-env" in err


def test_wrong_and_unknown_params_and_vars_fail(script, capsys, monkeypatch):
    monkeypatch.setenv("PW", "x")
    base = ["run", "--dry-run", "--param", "user=a", "--param-env", "password=PW"]
    assert main([*base, "--param", "retries=many", str(script)]) == 2
    assert "param 'retries' expects an integer, got 'many'" in capsys.readouterr().err
    assert main([*base, "--param", "colour=red", str(script)]) == 2
    assert "declares no param 'colour'" in capsys.readouterr().err
    assert main([*base, "--var", "user=x", str(script)]) == 2
    assert "it is a param" in capsys.readouterr().err
    assert main([*base, "--param-env", "user=NOPE", str(script)]) == 2
    assert "given twice" in capsys.readouterr().err
