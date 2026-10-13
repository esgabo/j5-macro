"""End-to-end tests of ``j5`` against tests/fakes/fake_cli_engine.py."""

from __future__ import annotations

import faulthandler
import io
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from j5_macro import __version__
from j5_macro.cli import commands
from j5_macro.cli import main as cli_main
from j5_macro.cli.main import main
from j5_macro.cli.options import ENGINE_CMD_ENV, FAST_PROFILE

FAKE = Path(__file__).parent / "fakes" / "fake_cli_engine.py"
TEST_TIMEOUT = 60


@pytest.fixture(autouse=True)
def watchdog():
    """Abort the whole run with tracebacks instead of hanging CI forever."""
    faulthandler.dump_traceback_later(TEST_TIMEOUT, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


@pytest.fixture
def record(tmp_path):
    return tmp_path / "commands.jsonl"


@pytest.fixture
def engine(monkeypatch, record):
    """``engine("scenario")`` points the CLI at the fake engine playing ``scenario``."""

    def use(scenario: str) -> None:
        monkeypatch.setenv(ENGINE_CMD_ENV,
                           json.dumps([sys.executable, str(FAKE), scenario, str(record)]))

    monkeypatch.delenv("NO_COLOR", raising=False)
    return use


@pytest.fixture
def script(tmp_path, monkeypatch):
    """A relative script path (the fake engine does not read the file)."""
    monkeypatch.chdir(tmp_path)
    return "script.yaml"


def commands_sent(record: Path) -> list[dict]:
    if not record.exists():
        return []
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]


def sent(record: Path, type_: str) -> list[dict]:
    return [c["payload"] for c in commands_sent(record) if c["type"] == type_]


def wait_for(record: Path, type_: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not sent(record, type_):
        if time.monotonic() > deadline:
            raise AssertionError(f"the CLI never sent {type_}")
        time.sleep(0.02)


def press_ctrl_c(record: Path, after: list[str]) -> threading.Thread:
    """Deliver SIGINT once each command in ``after`` has been sent to the engine."""

    def press() -> None:
        for type_ in after:
            wait_for(record, type_)
            time.sleep(0.2)
            signal.raise_signal(signal.SIGINT)

    thread = threading.Thread(target=press, daemon=True)
    thread.start()
    return thread


# -- basics -------------------------------------------------------------------------------


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: j5" in capsys.readouterr().out


def test_bad_var_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["run", "script.yaml", "--var", "novalue"])
    assert exc.value.code == 2
    assert "NAME=VALUE" in capsys.readouterr().err


def test_bad_engine_command_override(monkeypatch, capsys):
    monkeypatch.setenv(ENGINE_CMD_ENV, "not json")
    assert main(["validate", "script.yaml"]) == 2
    assert ENGINE_CMD_ENV in capsys.readouterr().err


def test_missing_engine_executable(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv(ENGINE_CMD_ENV, json.dumps([str(tmp_path / "no-such-engine")]))
    assert main(["validate", "script.yaml"]) == 1
    assert "cannot launch the engine" in capsys.readouterr().err


# -- validate -----------------------------------------------------------------------------


def test_validate_ok(engine, script, record, capsys):
    engine("valid")
    assert main(["validate", script]) == 0
    assert capsys.readouterr().out.splitlines() == ["script.yaml: valid"]
    (load,) = sent(record, "load")
    assert os.path.isabs(load["path"])
    assert Path(load["path"]).name == "script.yaml"


def test_validate_warnings_are_allowed(engine, script, capsys):
    engine("warnings")
    assert main(["validate", script]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "script.yaml:3: warning: generated id 'click_1'",
        "script.yaml: warning: script has no name",
        "script.yaml: valid (2 warnings)",
    ]


def test_validate_errors(engine, script, capsys):
    engine("invalid")
    assert main(["validate", script]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "script.yaml:4: error[unknown_step_type]: unknown step type 'clik' (step clik_1)",
        "script.yaml:9: error[validation_failed]: sleep: missing duration",
        "script.yaml:9: warning: generated id 'sleep_2'",
        "script.yaml: invalid (2 errors, 1 warning)",
    ]


def test_validate_missing_script(engine, script, capsys):
    engine("not_found")
    assert main(["validate", script]) == 1
    out = capsys.readouterr().out
    assert "script.yaml: error[script_not_found]: cannot open" in out
    assert "invalid (1 error)" in out


def test_validate_json(engine, script, capsys):
    engine("invalid")
    assert main(["validate", script, "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is False
    assert report["path"] == "script.yaml"
    assert [e["code"] for e in report["errors"]] == ["unknown_step_type", "validation_failed"]
    assert report["errors"][0]["line"] == 4
    assert report["warnings"][0]["step_id"] == "sleep_2"


def test_validate_json_valid(engine, script, capsys):
    engine("warnings")
    assert main(["validate", script, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert report["script_id"] == "s-1"
    assert len(report["warnings"]) == 2


def test_validate_engine_death(engine, script, capsys):
    engine("crash_load")
    assert main(["validate", script]) == 1
    err = capsys.readouterr().err
    assert "the engine died (exit code 3)" in err
    assert "RuntimeError: boom" in err


# -- run ----------------------------------------------------------------------------------


def test_run_completed(engine, script, record, capsys):
    engine("valid")
    assert main(["run", script, "--seed", "7"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "run r-1  Save report  (mode normal, seed 7, backend fake)"
    body = [line.split("s  ", 1)[1] for line in out[1:-1]]
    assert body == [
        '▶ open_menu  click  {"x": 10, "y": 20}',
        "✓ open_menu  12ms",
        "↻ repeat  iteration 1/2",
        "  ⑂ check  if count > 1 = false → else",
        '    ▶ bump  set  {"count": 2}',
        "      count = 2  (was 1)",
        "    ✓ bump  0ms",
        "[info] vars={}",
    ]
    assert out[-1] == "✓ completed in 2.345s  (seed 7)"
    (run,) = sent(record, "run")
    assert run == {"script_id": "s-1", "mode": "normal", "seed": 7}


def test_run_lines_carry_elapsed_time(engine, script, capsys):
    engine("valid")
    assert main(["run", script]) == 0
    lines = capsys.readouterr().out.splitlines()[1:-1]
    for line in lines:
        stamp = line.split("s  ", 1)[0]
        assert float(stamp) >= 0


def test_run_failed(engine, script, capsys):
    engine("failed")
    assert main(["run", script]) == 1
    out = capsys.readouterr().out
    assert "✗ wait_dialog  [image_not_found] dialog.png not found" in out
    assert out.splitlines()[-1] == (
        "✗ failed after 1.500s  (seed 1234): [image_not_found] dialog.png not found")


def test_run_stopped_by_engine(engine, script, capsys):
    engine("interference")
    assert main(["run", script]) == 3
    out = capsys.readouterr().out
    assert "you moved the mouse" in out
    assert "stopping the run" in out
    assert out.splitlines()[-1] == "■ stopped (interference) after 0.300s  (seed 1234)"


def test_run_retries_after_interference_without_a_terminal(engine, script, record, capsys):
    engine("interference_pause")
    assert main(["run", script]) == 0
    captured = capsys.readouterr()
    assert "you moved the mouse" in captured.out
    assert "⏸ paused (interference)" in captured.out
    assert ("j5: you moved the mouse during step 'open_menu'; retrying it "
            "(no terminal to ask)") in captured.err
    assert sent(record, "resume") == [{"run_id": "r-1"}]
    assert sent(record, "step_over") == []


def interactive(monkeypatch, answer: str) -> None:
    """Make the CLI believe stdin is a terminal where the user types ``answer``."""
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(answer))


def test_run_interference_enter_retries_the_step(engine, script, record, monkeypatch,
                                                 capsys):
    engine("interference_pause")
    interactive(monkeypatch, "\n")
    assert main(["run", script]) == 0
    err = capsys.readouterr().err
    assert ("j5: you moved the mouse during step 'open_menu'; press Enter to retry the "
            "step, or s + Enter to skip it (Ctrl+C stops the run)") in err
    assert sent(record, "resume") == [{"run_id": "r-1"}]
    assert sent(record, "step_over") == []


def test_run_interference_s_skips_the_step(engine, script, record, monkeypatch, capsys):
    engine("interference_pause")
    interactive(monkeypatch, "s\n")
    assert main(["run", script]) == 0
    captured = capsys.readouterr()
    assert "↷ open_menu  skipped (user)" in captured.out
    # The pause that follows the skip is resumed without asking again.
    assert sent(record, "step_over") == [{"run_id": "r-1"}]
    assert sent(record, "resume") == [{"run_id": "r-1"}]
    assert "the run is paused" not in captured.err


def test_run_busy(engine, script, capsys):
    engine("busy")
    assert main(["run", script]) == 2
    assert "cannot start the run: [busy]" in capsys.readouterr().err


def test_run_load_errors(engine, script, record, capsys):
    engine("invalid")
    assert main(["run", script]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "script.yaml:4: error[unknown_step_type]" in captured.err
    assert "script.yaml:9: warning: generated id 'sleep_2'" in captured.err
    assert sent(record, "run") == []


def test_run_warnings_go_to_stderr(engine, script, capsys):
    engine("warnings")
    assert main(["run", script]) == 0
    assert "script.yaml:3: warning: generated id 'click_1'" in capsys.readouterr().err


def test_run_vars(engine, script, record, capsys):
    engine("valid")
    argv = ["run", script, "--var", "count=3", "--var", "flag=true", "--var", "name=bob",
            "--var", "ratio=0.5", "--var", "empty=", "--var", "list=[1, 2]"]
    assert main(argv) == 0
    expected = {"count": 3, "flag": True, "name": "bob", "ratio": 0.5, "empty": "",
                "list": "[1, 2]"}
    (run,) = sent(record, "run")
    assert run["vars"] == expected
    assert f"[info] vars={json.dumps(expected, sort_keys=True)}" in capsys.readouterr().out


def test_run_fast_and_dry(engine, script, record):
    engine("valid")
    assert main(["run", script, "--fast", "--dry-run"]) == 0
    (run,) = sent(record, "run")
    assert run["mode"] == "dry"
    assert run["input_profile"] == FAST_PROFILE
    assert run["input_profile"] == {"mouse": {"strategy": "direct"},
                                    "keyboard": {"strategy": "direct"}}
    assert "seed" not in run
    assert "allow_exec" not in run


def test_run_allow_exec(engine, script, record):
    engine("valid")
    assert main(["run", script, "--allow-exec"]) == 0
    (run,) = sent(record, "run")
    assert run["allow_exec"] is True
    assert "display_scale" not in run


@pytest.mark.parametrize(("value", "scale"), [("1.5", 1.5), ("150%", 1.5), ("2", 2.0)])
def test_run_display_scale(engine, script, record, value, scale):
    engine("valid")
    assert main(["run", script, "--display-scale", value]) == 0
    (run,) = sent(record, "run")
    assert run["display_scale"] == scale


@pytest.mark.parametrize("value", ["0", "-1", "big", "nan", "inf", "%"])
def test_bad_display_scale_is_a_usage_error(capsys, value):
    with pytest.raises(SystemExit) as exc:
        main(["run", "script.yaml", "--display-scale", value])
    assert exc.value.code == 2
    assert "positive number" in capsys.readouterr().err


def test_run_json(engine, script, capsys):
    engine("valid")
    assert main(["run", script, "--json"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert all(e["kind"] == "event" and e["run_id"] == "r-1" for e in events)
    assert events[0]["type"] == "run_started"
    assert events[-1]["type"] == "run_finished"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


def test_run_resumes_a_pause_without_a_terminal(engine, script, record, capsys):
    engine("pause")
    assert main(["run", script]) == 0
    captured = capsys.readouterr()
    assert "⏸ paused (user)" in captured.out
    assert "resuming it" in captured.err
    assert sent(record, "resume") == [{"run_id": "r-1"}]


def test_run_engine_death(engine, script, capsys):
    engine("crash_run")
    assert main(["run", script]) == 1
    captured = capsys.readouterr()
    assert "▶ open_menu" in captured.out
    assert "the engine died (exit code 3)" in captured.err
    assert "RuntimeError: boom" in captured.err


def test_run_engine_death_releases_held_keys(engine, script, monkeypatch, capsys):
    engine("crash_held")
    monkeypatch.setenv("J5_BACKEND", "fake")
    assert main(["run", script]) == 1
    err = capsys.readouterr().err
    released = "j5: released what the engine held: button:left, char:a, shift (backend fake)"
    assert released in err
    assert err.index(released) < err.index("the engine died (exit code 3)")


def test_run_engine_death_without_a_backend_says_so(engine, script, monkeypatch, capsys):
    engine("crash_held")
    monkeypatch.setenv("J5_BACKEND", "none")
    assert main(["run", script]) == 1
    err = capsys.readouterr().err
    assert ("j5: the engine held shift, char:a, button:left and they could not be released: "
            "no input backend (disabled by J5_BACKEND=none)") in err


def test_run_header_shows_the_backend(engine, script, capsys):
    engine("valid")
    assert main(["run", script]) == 0
    assert "backend fake" in capsys.readouterr().out


# -- Ctrl+C ------------------------------------------------------------------------------


def test_ctrl_c_stops_the_run(engine, script, record, capsys):
    engine("slow")
    presser = press_ctrl_c(record, ["run"])
    started = time.monotonic()
    assert main(["run", script]) == 130
    presser.join(5)
    assert time.monotonic() - started < 30
    assert sent(record, "stop") == [{"run_id": "r-1", "reason": "user"}]
    captured = capsys.readouterr()
    assert "stopping the run" in captured.err
    assert captured.out.splitlines()[-1].startswith("■ stopped (user) after")


def test_ctrl_c_gives_up_when_the_run_does_not_finish(engine, script, record, monkeypatch,
                                                      capsys):
    monkeypatch.setattr(commands, "STOP_WAIT", 0.5)
    engine("deaf")
    presser = press_ctrl_c(record, ["run"])
    assert main(["run", script]) == 130
    presser.join(5)
    assert len(sent(record, "stop")) == 1
    assert "did not finish" in capsys.readouterr().err


def test_second_ctrl_c_quits_at_once(engine, script, record, capsys):
    engine("deaf")
    presser = press_ctrl_c(record, ["run", "stop"])
    started = time.monotonic()
    assert main(["run", script]) == 130
    presser.join(5)
    assert time.monotonic() - started < commands.STOP_WAIT
    assert "interrupted again" in capsys.readouterr().err


# -- steps and schema ---------------------------------------------------------------------


def test_steps(engine, capsys):
    engine("valid")
    assert main(["steps"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "click  Click on an image or at coordinates."
    assert '  button: string = "left"' in out
    assert "  message: string (required)" in out


def test_steps_json(engine, capsys):
    engine("valid")
    assert main(["steps", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [s["name"] for s in result["step_types"]] == ["click", "log"]


def test_schema_to_stdout(capsys):
    assert main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["title"] == "j5-macro script"
    assert "step" in schema["$defs"]


def test_schema_to_file(tmp_path, capsys):
    target = tmp_path / "j5.schema.json"
    assert main(["schema", "-o", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["title"] == "j5-macro script"
    assert capsys.readouterr().out == ""


def test_schema_unwritable_file(tmp_path, capsys):
    assert main(["schema", "-o", str(tmp_path / "missing" / "x.json")]) == 1
    assert "cannot write" in capsys.readouterr().err


def test_schema_help_explains_vs_code(capsys):
    with pytest.raises(SystemExit):
        main(["schema", "--help"])
    assert "yaml-language-server: $schema=" in capsys.readouterr().out


def test_run_captures(engine, script, record):
    engine("valid")
    assert main(["run", script, "--captures", "all"]) == 0
    (run,) = sent(record, "run")
    assert run["captures"] == "all"


def test_run_without_captures_leaves_the_engine_default(engine, script, record):
    engine("valid")
    assert main(["run", script]) == 0
    (run,) = sent(record, "run")
    assert "captures" not in run


@pytest.mark.parametrize("combo", ["ctrl+alt", "ctrl+nosuchkey", ""])
def test_run_rejects_a_bad_stop_key(engine, script, record, capsys, combo):
    engine("valid")
    with pytest.raises(SystemExit) as exc:
        main(["run", script, "--stop-key", combo])
    assert exc.value.code == 2
    assert "--stop-key" in capsys.readouterr().err
    assert not record.exists()
