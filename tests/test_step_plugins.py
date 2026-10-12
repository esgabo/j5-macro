"""Step plugins (``j5_macro.steps`` entry points): loading, the plugin rules, and a plugin
step running end to end through the real ``j5`` CLI and engine process.

Plugins are installed for real: a temporary directory with a module and a ``.dist-info``
directory holding ``entry_points.txt``, put on ``sys.path`` (for ``j5 schema``, which
builds the registry in-process) and on ``PYTHONPATH`` (for the engine subprocess). The
``greet`` plugin is the example of docs/development.md, so the documented plugin works.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from j5_macro.cli.main import main
from j5_macro.cli.options import ENGINE_CMD_ENV
from j5_macro.steps import (
    ParamSpec,
    StepRegistry,
    StepSpec,
    StepType,
    default_registry,
    registry_with_plugins,
)
from j5_macro.steps import registry as registry_module

ROOT = Path(__file__).resolve().parent.parent
GREET_HEADER = "# src/j5_greet/__init__.py\n"

BROKEN_IMPORT = "raise ImportError('missing dependency: frobnicate')\n"
CLASHING = """\
from j5_macro.steps import StepSpec, StepType


def register(registry):
    registry.register(StepType(StepSpec(name="click", doc="Not the real click."),
                               lambda ctx, params: None))
"""


def documented_plugin() -> str:
    """The source of the example plugin in docs/development.md."""
    text = (ROOT / "docs" / "development.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    (source,) = [b for b in blocks if b.startswith(GREET_HEADER)]
    return source


def install(site: Path, dist: str, modules: dict[str, str],
            entry_points: dict[str, str]) -> None:
    """Install a distribution ``dist`` into ``site``: ``modules`` maps a module name to
    its source, ``entry_points`` an entry point name of the plugin group to its value."""
    for name, source in modules.items():
        package = site / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(source, encoding="utf-8")
    info = site / f"{dist.replace('-', '_')}-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist}\nVersion: 1.0\n",
                                   encoding="utf-8")
    lines = [f"{name} = {value}" for name, value in entry_points.items()]
    (info / "entry_points.txt").write_text("[j5_macro.steps]\n" + "\n".join(lines) + "\n",
                                           encoding="utf-8")


@pytest.fixture
def site(tmp_path, monkeypatch):
    """An empty plugin directory visible to this process and to the engine subprocess."""
    path = tmp_path / "site"
    path.mkdir()
    monkeypatch.syspath_prepend(str(path))
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH",
                       str(path) if not existing else str(path) + os.pathsep + existing)
    monkeypatch.setenv("J5_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("J5_BACKEND", "fake")
    monkeypatch.delenv(ENGINE_CMD_ENV, raising=False)
    before = set(sys.modules)
    yield path
    for name in set(sys.modules) - before:
        if name.startswith(("j5_greet", "j5_broken", "j5_clash")):
            del sys.modules[name]


@pytest.fixture
def greet(site):
    install(site, "j5-greet", {"j5_greet": documented_plugin()},
            {"greet": "j5_greet:register"})
    return site


@pytest.fixture
def broken(site):
    install(site, "j5-broken", {"j5_broken": BROKEN_IMPORT, "j5_clash": CLASHING},
            {"broken": "j5_broken:register", "clash": "j5_clash:register"})
    return site


@pytest.fixture
def script(tmp_path):
    path = tmp_path / "greet.yaml"
    path.write_text("name: Greetings\nsteps:\n  - id: hi\n    greet: world\n"
                    "  - id: twice\n    greet: {who: j5, times: 2}\n",
                    encoding="utf-8", newline="\n")
    return str(path)


def run_json(args: list[str], capsys) -> tuple[int, list[dict], str]:
    code = main(["run", "--json", *args])
    captured = capsys.readouterr()
    return code, [json.loads(line) for line in captured.out.splitlines()], captured.err


# -- installed plugins, real engine ---------------------------------------------------------


def test_installed_plugin_is_in_the_registry(greet):
    registry, report = registry_with_plugins()
    assert report.loaded == ["greet"] and report.skipped == []
    assert registry.names()[-1] == "greet"
    assert registry.check_params("greet", {"who": "x", "times": 0})[0].param == "times"
    assert "greet" not in default_registry()


def test_j5_steps_lists_the_plugin_step(greet, capsys):
    assert main(["steps", "--json"]) == 0
    step_types = json.loads(capsys.readouterr().out)["step_types"]
    greet_type = next(s for s in step_types if s["name"] == "greet")
    assert [p["name"] for p in greet_type["params"]] == ["who", "times"]
    assert greet_type["doc"] == "Log a greeting."


def test_plugin_script_validates_and_runs_through_the_engine(greet, script, capsys):
    assert main(["validate", script]) == 0
    capsys.readouterr()
    code, events, _ = run_json([script], capsys)
    assert code == 0
    run_events = [e for e in events if e.get("run_id")]
    assert run_events[-1]["payload"]["status"] == "completed"
    logs = [e["payload"]["message"] for e in run_events if e["type"] == "log"]
    assert logs == ["hello, world", "hello, j5", "hello, j5"]
    results = {e["payload"]["step_id"]: e["payload"]["result"]
               for e in run_events if e["type"] == "step_finished"}
    assert results == {"hi": {"message": "hello, world", "times": 1},
                       "twice": {"message": "hello, j5", "times": 2}}


def test_plugin_cross_parameter_check_runs_at_load(greet, tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("steps:\n  - greet: {who: x, times: 0}\n", encoding="utf-8")
    assert main(["validate", str(bad)]) == 1
    assert "must be at least 1" in capsys.readouterr().out


def test_j5_schema_includes_the_plugin_step(greet, capsys):
    assert main(["schema"]) == 0
    captured = capsys.readouterr()
    schema = json.loads(captured.out)
    step = schema["$defs"]["step"]
    assert "greet" in step["properties"]
    assert {"required": ["greet"]} in step["oneOf"]
    assert captured.err == ""


def test_broken_plugins_are_warned_about_and_the_rest_works(greet, broken, script, capsys):
    code, events, _ = run_json([script], capsys)
    assert code == 0
    warnings = [e["payload"]["message"] for e in events
                if e["type"] == "log" and e["run_id"] is None
                and e["payload"]["level"] == "warning" and "plugin" in e["payload"]["message"]]
    assert warnings == [
        "step plugin 'broken = j5_broken:register' skipped: cannot be imported: "
        "ImportError: missing dependency: frobnicate",
        "step plugin 'clash = j5_clash:register' skipped: ValueError: step type 'click' "
        "is already registered",
    ]
    run_events = [e for e in events if e.get("run_id")]
    assert run_events[-1]["payload"]["status"] == "completed"

    assert main(["run", script]) == 0
    assert "step plugin 'broken = j5_broken:register' skipped" in capsys.readouterr().out

    assert main(["steps", "--json"]) == 0
    names = [s["name"] for s in json.loads(capsys.readouterr().out)["step_types"]]
    assert names == [*default_registry().names(), "greet"]


def test_j5_schema_warns_about_broken_plugins_on_stderr(broken, capsys):
    assert main(["schema"]) == 0
    captured = capsys.readouterr()
    assert set(json.loads(captured.out)["$defs"]["step"]["properties"]) >= set(
        default_registry().names())
    assert ("j5: warning: step plugin 'broken = j5_broken:register' skipped: cannot be "
            "imported: ImportError: missing dependency: frobnicate") in captured.err
    assert "step plugin 'clash = j5_clash:register' skipped" in captured.err


# -- plugin rules (in-process, fake entry points) --------------------------------------------


def noop(ctx, params):
    return None


def step(name: str, **spec) -> StepType:
    return StepType(StepSpec(name=name, doc="A step.", **spec), noop)


def entry_point(name: str, target) -> SimpleNamespace:
    def load():
        if isinstance(target, BaseException):
            raise target
        return target

    return SimpleNamespace(name=name, value=f"plugins.{name}:register", load=load)


def registers(*step_types: StepType, fail: Exception | None = None):
    def register(registry: StepRegistry) -> None:
        for step_type in step_types:
            registry.register(step_type)
        if fail is not None:
            raise fail

    return register


def load(monkeypatch, *found: SimpleNamespace):
    monkeypatch.setattr(registry_module, "entry_points",
                        lambda group: list(found) if group == "j5_macro.steps" else [])
    return registry_with_plugins()


def skipped_messages(report) -> dict[str, str]:
    return {p.entry_point.split(" = ")[0]: p.message for p in report.skipped}


@pytest.mark.parametrize(("target", "message"), [
    (ModuleNotFoundError("No module named 'gone'"),
     "cannot be imported: ModuleNotFoundError: No module named 'gone'"),
    (42, "expected a callable taking the step registry, got int"),
    (registers(step("half"), fail=RuntimeError("boom")), "RuntimeError: boom"),
    (registers(step("sleep")), "ValueError: step type 'sleep' is already registered"),
    (registers(step("timeout")), "would clash with the common step field"),
    (registers(step("bad-name")), "invalid step type name 'bad-name'"),
    (registers(StepType(StepSpec(name="ctl", doc="", kind="control", blocks=("do",)))),
     "plugins can only add action steps"),
    (registers(step("odd", params=(ParamSpec(name="p", type="color"),))),  # type: ignore[arg-type]
     "invalid parameter"),
    (registers(step("short", shorthand="nope")), "shorthand 'nope' is not a parameter"),
    (registers("not a step type"), "TypeError: expected a StepType, got str"),  # type: ignore[arg-type]
])
def test_a_bad_plugin_is_skipped_whole_and_others_still_load(monkeypatch, target, message):
    good = entry_point("good", registers(step("fine")))
    registry, report = load(monkeypatch, entry_point("bad", target), good)
    assert report.loaded == ["good"]
    (problem,) = report.skipped
    assert problem.entry_point == "bad = plugins.bad:register"
    assert message in problem.message
    assert str(problem).startswith("step plugin 'bad = plugins.bad:register' skipped: ")
    assert registry.names() == [*default_registry().names(), "fine"]
    assert "half" not in registry


def test_rollback_removes_the_checks_of_a_skipped_plugin(monkeypatch):
    def register(registry: StepRegistry) -> None:
        registry.register(step("checked"), lambda params: [])
        raise RuntimeError("late failure")

    registry, report = load(monkeypatch, entry_point("bad", register))
    assert skipped_messages(report) == {"bad": "RuntimeError: late failure"}
    assert "checked" not in registry and registry.check_params("checked", {}) == []


def test_plugins_load_in_entry_point_name_order_and_the_first_name_wins(monkeypatch):
    first, second = step("dup"), step("dup")
    registry, report = load(monkeypatch, entry_point("zeta", registers(second)),
                            entry_point("alpha", registers(first)))
    assert report.loaded == ["alpha"]
    assert skipped_messages(report) == {
        "zeta": "ValueError: step type 'dup' is already registered"}
    assert registry.get("dup") is first


def test_no_plugins_means_the_builtins(monkeypatch):
    registry, report = load(monkeypatch)
    assert registry.names() == default_registry().names()
    assert report.loaded == [] and report.skipped == []
