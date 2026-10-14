# Developing j5-macro

j5-macro is open source under the [Apache License 2.0](../LICENSE). This page explains how
to set up a development environment, run the checks, find your way around the tests, and
change the project without breaking its rules. The design is described in
[architecture.md](architecture.md) and [technical-design.md](technical-design.md); the
protocol in the [contract](contract.md), which is the source of truth.

## Setup

j5-macro needs Python 3.11 or later. On Linux, the tests that really click, type and
capture the screen run inside a virtual X server, so install Xvfb too.

```bash
git clone https://github.com/esgabo/j5-macro.git
cd j5-macro
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,x11,vision]"
sudo apt install xvfb            # Debian and Ubuntu; provides xvfb-run
```

On Windows, install `".[dev,windows,vision]"`. The X11 tests are skipped there; the
Windows backend is tested against a simulated Win32 layer on every platform and against
the real system on Windows. Before a release, also run the
[manual test plan](manual-testing.md) on both platforms.

| Extra | Installs | For |
| --- | --- | --- |
| `x11` | `python-xlib`, `mss` | The X11 input backend and screen capture on Linux |
| `vision` | `opencv-python-headless`, `numpy`, `mss` | Image search |
| `windows` | `mss` | Screen capture on Windows |
| `dev` | `pytest`, `ruff`, `mypy`, `jsonschema`, `types-python-xlib` | Tests, lint and types |

## Checks

`scripts/check.sh` runs everything CI runs, from the repository root inside the venv:

1. `ruff check .` (lint and import order; line length 100);
2. `mypy` (strict, over `src/j5_macro`; it passes with or without the `vision` extra);
3. `pytest -rs`. On Linux it runs under `xvfb-run -a -s "-screen 0 1280x800x24"` with
   `J5_REQUIRE_X11=1`, so the X11 tests **fail** instead of being skipped when no X server
   is reachable. Elsewhere, the X11 tests are skipped.

```console
$ scripts/check.sh
All checks passed!
Success: no issues found in 99 source files
........................................................................ [  4%]
...
1608 passed in 66.00s (0:01:06)
```

Arguments after `check.sh` go to pytest (ruff and mypy still check everything), and you can
run pytest directly while you work:

```bash
scripts/check.sh tests/test_dap_e2e.py -k breakpoint
pytest tests/input -q                       # no display needed
xvfb-run -a -s "-screen 0 1280x800x24" pytest tests/backends -rs
```

Without `J5_REQUIRE_X11`, tests marked `x11` are skipped with the reason (no `DISPLAY`,
`python-xlib` missing, no XTEST extension). Vision tests are skipped when the `vision`
extra is not installed.

## Test layout

| Location | What it covers |
| --- | --- |
| `tests/test_protocol.py`, `test_payloads.py`, `test_errors.py` | The message envelope, the typed payload of every command, result and event, and error codes |
| `tests/test_loader.py`, `test_spec.py`, `test_steps_*.py`, `test_expr.py` | Script loading and validation, step specs, the registry, built-in steps, the JSON Schema, the expression language |
| `tests/test_script_editor.py`, `test_script_params.py`, `test_script_images.py` | Comment-preserving script edits, params and secrets, embedded images (`png_support.py` builds small PNG files without numpy) |
| `tests/engine/` | The engine in process (`engine_harness.py` runs a server on a thread, fed through the transport): runs, history and persistence, params, script management and editing, backend selection and the stop key |
| `tests/runtime/` | The interpreter with a fast clock and an event recorder that validates every payload: flow, stepping and pauses, errors, seeding, input steps, interference and vision, on the fake backend |
| `tests/input/` | The mouse and keyboard planners (pure, no display) and the input session against a fake backend |
| `tests/vision/` | Template matching, image files, and capturing a real X screen (`x11`) |
| `tests/backends/` | Backend selection, the Windows backend against a fake Win32 layer (`win32_fake.py`; a few tests use the real system on Windows), the X11 backend and its key mapping (`x11`), and `test_e2e_x11.py`: the whole stack (CLI, engine, interpreter, masters, X11 backend) on Xvfb, with an observer window that records the input it really receives |
| `tests/test_client*.py` | The asyncio client against a scriptable fake engine (`tests/fakes/fake_engine.py`) |
| `tests/test_cli*.py` | The CLI: rendering, params, `j5 runs`, `j5 pack` and `j5 unpack`, releasing keys after an engine crash, and the debugger; some against a scripted fake engine (`tests/fakes/fake_cli_engine.py`, started through `J5_ENGINE_CMD`), others against the real engine on the fake backend |
| `tests/test_engine_process.py`, `test_engine_server.py`, `test_integration.py` | The real engine process, and the real client against it with every payload checked against the typed payloads |
| `tests/test_e2e_cli.py`, `test_e2e_debugger.py` | The real `j5` CLI, engine and interpreter, with no fakes but the input backend; debugger commands piped through standard input |
| `tests/test_dap_*.py` | The DAP adapter: framing, line to step mapping, the session against a fake engine, and end to end over framed stdio against the real engine (`J5_BACKEND=fake`) |

The fake backend (`J5_BACKEND=fake`) records input and touches nothing, so most of the
suite runs without a display.

## Continuous integration

GitHub Actions runs [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) on every pull
request and every push to `main`. A newer push to the same branch cancels the run it
supersedes.

| OS | Python | Installs | Runs |
| --- | --- | --- | --- |
| `ubuntu-latest` | 3.11, 3.12, 3.13 | `xvfb`, `".[dev,x11,vision]"` | `scripts/check.sh` (X11 tests required) |
| `windows-latest` | 3.11, 3.12, 3.13 | `".[dev,windows,vision]"` | `scripts/check.sh` in bash (X11 tests skipped) |

Merge only when every job is green.

## Project rules

These come from [`CLAUDE.md`](../CLAUDE.md) and apply to every change, by people and by
coding agents alike:

- **Contract first.** [`docs/contract.md`](contract.md) is the source of truth for the
  protocol and the architecture. Change it first, in the same pull request, whenever a
  command, event, step type or backend primitive changes.
- **The engine never prints.** The engine core never prints or reads standard input
  directly: all its output is a protocol event. Only clients (the CLI, the DAP adapter)
  talk to people.
- **Backends are primitives.** A backend moves, presses, captures and reports; every
  human-like behaviour belongs in `j5_macro.input`, so it is identical on every platform.
- **Planning is pure.** Input planning is a pure, seeded function, testable without a
  display. The same seed and arguments give the same plan.
- **English everywhere.** Code, identifiers, comments, docstrings, docs, test data,
  commit messages, issues and pull requests.
- **Checks pass before pushing.** `scripts/check.sh` must pass, and CI must be green before
  merging. Every supported Python version runs on Linux and Windows.

## Adding a step type

A step type is data plus a function. Look at `src/j5_macro/steps/basic.py` for small
examples (`sleep`, `set`, `log`, `pause`).

1. **Contract.** Add the step to the "v1 step types" and "Step results" tables of the
   [contract](contract.md#script-format-and-step-registry), with its parameters, result
   and behaviour (dry run included).
2. **Spec.** Declare a `StepSpec` (`j5_macro.steps.spec`): `name`, `doc`, its
   `ParamSpec`s (name, type, required, default, doc, choices), the `ResultField`s of its
   result (name, type, doc, optional; what `as` keeps, shown by `list_step_types` and
   `j5 schema`), block slots for control steps, and an optional `shorthand` parameter
   for the scalar form (`sleep: 2s`).
3. **Executor.** Write `run_<name>(ctx, params) -> dict | None`. `params` are already
   interpolated and type-checked; the returned dict becomes `step_finished.result`. Talk
   to the run only through `StepContext`: `ctx.rng` (the step's seeded generator),
   `ctx.wait()`, `ctx.log()`, `ctx.evaluate()`, `ctx.set_variable()`, `ctx.input`,
   `ctx.vision`, `ctx.dry_run`, `ctx.check_stop()` (for long work that polls, such as
   `exec` waiting for a program), `ctx.timeout` (the common field), `ctx.script_dir`,
   `ctx.allow_exec` and `ctx.strict` (a strict dry run: raise where a dry run would only
   warn that the real run fails). Raise `StepError(code, message)` to fail.
4. **Cross-parameter checks** (optional): a function returning `ParamProblem`s, such as
   "exactly one of `value` and `expr`".
5. **Register** the `StepType(spec, executor)` with its check in `BUILTINS`
   (`j5_macro.steps.builtins`). Validation, `list_step_types`, `j5 steps` and the JSON
   Schema of `j5 schema` all come from the registry; the interpreter does not change.
6. **Tests**: validation in `tests/test_steps_builtins.py`, behaviour in `tests/runtime/`.

## Writing a step plugin

Step types can also live in a separate package, installed next to j5-macro, without
changing j5-macro itself. The engine process and `j5 schema` load every installed plugin
at startup, after the built-in steps; `j5 steps`, `j5 validate` and `j5 run` then see the
plugin's steps like any other. The rules are in the
[contract](contract.md#script-format-and-step-registry) ("Step plugins").

**The interface.** A plugin is an entry point in the `j5_macro.steps` group. It must
resolve to a callable that takes the `StepRegistry` and calls
`registry.register(StepType(spec, executor), check=None)` for each of its step types. The
spec, the executor and the optional cross-parameter check are written exactly like a
built-in's (steps 2 to 4 above). In addition, a plugin step must:

- be an action step with an executor (`if` and `loop` style steps with blocks are only
  built in),
- have a name made of letters, digits and `_`, not one of the common step fields (`id`,
  `break`, `timeout`, `on_error`, `input`, `as`), and not one already taken. Built-in names
  always win, and between plugins the first entry point by name wins,
- use only the parameter types of `ParamType` (`j5_macro.steps.spec`), with a
  `shorthand` that names one of its parameters,
- respect dry runs: an executor that sends input checks `ctx.dry_run` and, when it is set,
  returns a description instead (see `j5_macro.steps.input_steps`).

A plugin that cannot be imported, is not callable, raises while registering, or breaks
these rules is skipped as a whole (none of its steps stay registered), and the engine
carries on: it logs a `warning` such as
`step plugin 'greet = j5_greet:register' skipped: ImportError: ...` right after `ready`,
which `j5 run` shows, and `j5 schema` prints the same warning on standard error.

**A minimal plugin.** A package `j5-greet` with one step type, `greet`:

```text
j5-greet/
  pyproject.toml
  src/j5_greet/__init__.py
```

```toml
# pyproject.toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "j5-greet"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["j5-macro"]

[project.entry-points."j5_macro.steps"]
greet = "j5_greet:register"
```

```python
# src/j5_greet/__init__.py
"""j5-greet: a j5-macro step plugin with one step type, ``greet``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from j5_macro.steps import (
    ParamProblem,
    ParamSpec,
    StepContext,
    StepRegistry,
    StepSpec,
    StepType,
)

GREET = StepSpec(
    name="greet",
    doc="Log a greeting.",
    params=(
        ParamSpec(name="who", type="string", required=True, doc="Who to greet."),
        ParamSpec(name="times", type="integer", default=1, doc="How many times."),
    ),
    shorthand="who",  # allows `greet: world`
)


def run_greet(ctx: StepContext, params: dict[str, Any]) -> dict[str, Any]:
    """Log the greeting; the returned dict becomes ``step_finished.result``."""
    message = f"hello, {params['who']}"
    times = params.get("times", 1)
    for _ in range(times):
        ctx.check_stop()
        ctx.log("info", message)
    return {"message": message, "times": times}


def check_greet(params: Mapping[str, Any]) -> list[ParamProblem]:
    """Cross-parameter rules, checked when the script is loaded."""
    times = params.get("times", 1)
    if isinstance(times, int) and times < 1:
        return [ParamProblem("times", "must be at least 1")]
    return []


def register(registry: StepRegistry) -> None:
    """The entry point: register every step type of the plugin."""
    registry.register(StepType(GREET, run_greet), check_greet)
```

Install it in the same environment as j5-macro (`pip install -e ./j5-greet` while working
on it). `j5 steps` now lists `greet`, `j5 schema` completes it in editors, and scripts can
use it:

```yaml
steps:
  - id: hello
    greet: world
  - id: twice
    greet: {who: j5, times: 2}
```

```console
$ j5 steps
...
greet  Log a greeting.
  who: string (required)  Who to greet.
  times: integer = 1  How many times.
$ j5 run greet.yaml
...
  0.001s  ▶ hello  greet  {"who": "world", "times": 1}
  0.001s    [info] hello, world
  0.001s  ✓ hello  0ms  {"message": "hello, world", "times": 1}
...
```

Test the executor like the built-in steps, with a registry that has your step
(`registry.register(...)` on a `default_registry()`) and `create_run` from
`j5_macro.runtime`; `tests/test_step_plugins.py` loads this very example as an installed
plugin and runs it through the engine.

## Adding a backend

A backend implements the `Backend` protocol of `j5_macro.backends.base`, and nothing more:
`screen_bounds`, `pointer_position`, `move_abs`/`move_rel`, `button_down`/`button_up`,
`scroll`, `key_down`/`key_up` (physical keys by lowercase name), `char_down`/`char_up`
(characters, whatever the layout), `find_window`, `display_scale`/`display_scales`
(the display scale of a monitor, and of every monitor), `watch_hotkey` and `close`
(which releases anything still held). Its `capabilities` say which optional parts exist
(`pointer_position`, `move_abs`, `move_rel`, `char_input`, `screen_capture`, `windows`,
`hotkey`, `display_scale`), and the masters adapt: for example, without
`pointer_position` there is no interference detection, and without `display_scale`
every display scale is 1 unless the run sets one. The table of primitives is in the
[contract](contract.md#mouse-and-keyboard-masters).

Then add it to `select_backend` (`j5_macro.backends.select`) and to the values of
`J5_BACKEND`, and test it like the X11 backend: unit tests of its pure parts, and an
end-to-end test that drives a real window. `src/j5_macro/backends/x11/`,
`src/j5_macro/backends/windows/` (whose system calls sit behind an interface the tests
fake) and the fake backend (`fake.py`) are the references. Add the platform to the
[manual test plan](manual-testing.md).

## Changing the protocol

1. Change the [contract](contract.md) first: the command, event or field, its payload,
   its errors and its semantics.
2. Update the typed payloads in `src/j5_macro/protocol/payloads/` (`commands.py`,
   `events.py`). Every message that crosses the wire is checked against them by the tests,
   so the engine, the client and the payloads cannot drift apart.
3. Implement it in the engine (`src/j5_macro/engine/`) and in the clients that need it:
   the CLI (`src/j5_macro/cli/`) and the DAP adapter (`src/j5_macro/dap/`).
4. Keep compatibility: clients ignore unknown events and fields, and `v` only increases on
   incompatible changes.
5. Add tests at each layer it touches, from payloads to the end-to-end CLI or DAP tests.

## Roadmap

Work is tracked as GitHub milestones:

| Milestone | Scope | Status |
| --- | --- | --- |
| M0 | Protocol types, the engine process skeleton and the client library | Done |
| M1 | Interpreter, step registry, dry runs and the CLI | Done |
| M2 | X11 backend with `direct` and `linear` input | Done |
| M3 | Vision: `wait_image`, image-based clicks, `image_visible` | Done |
| M4 | Debugger: step mode, breakpoints, inspection, the terminal debugger and the DAP adapter | Done |
| M5 | Human-like mouse and keyboard, interference detection | Done |
| M6 | Windows backend: input through SendInput, window-relative coordinates and capture, a manual test plan for both platforms | Done |
| M7 | Script management for the GUI and the run history | Done |
| M8 | Authoring: capture templates, pick points and test matches from the engine and CLI ([#94](https://github.com/esgabo/j5-macro/issues/94)); self-contained scripts with embedded images ([#93](https://github.com/esgabo/j5-macro/issues/93)) | Open |

After v1: an experimental Wayland backend, triggers, and the standalone GUI
([#95](https://github.com/esgabo/j5-macro/issues/95)). The open design
decisions are listed at the end of the [contract](contract.md#errors-and-open-decisions).

## Contributing

- **Issues.** Report bugs with the script (or a minimal one), the command you ran, the
  output, and `j5 runs show --json RUN_ID` when it helps; mask anything private. Say which
  platform, session (X11 desktop) and Python version you use.
- **Pull requests.** Keep them focused. Change the contract first when the protocol,
  a step type or a backend primitive changes; add tests; run `scripts/check.sh`; write
  everything in English. A pull request is merged when CI is green.
- **License.** Contributions are made under the Apache License 2.0, like the rest of the
  project.
