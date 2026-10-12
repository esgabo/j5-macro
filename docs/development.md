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

On Windows, install `".[dev,vision]"` (the Windows input backend is not written yet; see
[Roadmap](#roadmap)).

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
| `tests/test_script_editor.py`, `test_script_params.py` | Comment-preserving script edits, params and secrets |
| `tests/engine/` | The engine in process (`engine_harness.py` runs a server on a thread, fed through the transport): runs, history and persistence, params, script management and editing, backend selection and the stop key |
| `tests/runtime/` | The interpreter with a fast clock and an event recorder that validates every payload: flow, stepping and pauses, errors, seeding, input steps, interference and vision, on the fake backend |
| `tests/input/` | The mouse and keyboard planners (pure, no display) and the input session against a fake backend |
| `tests/vision/` | Template matching, image files, and capturing a real X screen (`x11`) |
| `tests/backends/` | Backend selection, the X11 backend and its key mapping (`x11`), and `test_e2e_x11.py`: the whole stack (CLI, engine, interpreter, masters, X11 backend) on Xvfb, with an observer window that records the input it really receives |
| `tests/test_client*.py` | The asyncio client against a scriptable fake engine (`tests/fakes/fake_engine.py`) |
| `tests/test_cli*.py` | The CLI: rendering, params, `j5 runs`, releasing keys after an engine crash, and the debugger; some against a scripted fake engine (`tests/fakes/fake_cli_engine.py`, started through `J5_ENGINE_CMD`), others against the real engine on the fake backend |
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
| `windows-latest` | 3.11, 3.12, 3.13 | `".[dev,vision]"` | `scripts/check.sh` in bash (X11 tests skipped) |

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

1. **Contract.** Add the step to the "v1 step types" table of the
   [contract](contract.md#script-format-and-step-registry), with its parameters and
   behaviour (dry run included).
2. **Spec.** Declare a `StepSpec` (`j5_macro.steps.spec`): `name`, `doc`, its
   `ParamSpec`s (name, type, required, default, doc, choices), block slots for control
   steps, and an optional `shorthand` parameter for the scalar form (`sleep: 2s`).
3. **Executor.** Write `run_<name>(ctx, params) -> dict | None`. `params` are already
   interpolated and type-checked; the returned dict becomes `step_finished.result`. Talk
   to the run only through `StepContext`: `ctx.rng` (the step's seeded generator),
   `ctx.wait()`, `ctx.log()`, `ctx.evaluate()`, `ctx.set_variable()`, `ctx.input`,
   `ctx.vision`, `ctx.dry_run`. Raise `StepError(code, message)` to fail.
4. **Cross-parameter checks** (optional): a function returning `ParamProblem`s, such as
   "exactly one of `value` and `expr`".
5. **Register** the `StepType(spec, executor)` with its check in `BUILTINS`
   (`j5_macro.steps.builtins`). Validation, `list_step_types`, `j5 steps` and the JSON
   Schema of `j5 schema` all come from the registry; the interpreter does not change.
6. **Tests**: validation in `tests/test_steps_builtins.py`, behaviour in `tests/runtime/`.

**Third-party steps.** `StepRegistry.load_plugins()` loads every entry point of the
`j5_macro.steps` group; each must be a callable that takes the registry and registers its
step types:

```toml
[project.entry-points."j5_macro.steps"]
my_steps = "my_package.steps:register"
```

Note that the engine and `j5 schema` currently build the registry with
`default_registry()`, which leaves plugins out (`default_registry(plugins=True)` loads
them), so plugin steps are not available in `j5 run` yet.

## Adding a backend

A backend implements the `Backend` protocol of `j5_macro.backends.base`, and nothing more:
`screen_bounds`, `pointer_position`, `move_abs`/`move_rel`, `button_down`/`button_up`,
`scroll`, `key_down`/`key_up` (physical keys by lowercase name), `char_down`/`char_up`
(characters, whatever the layout), `find_window`, `watch_hotkey` and `close` (which
releases anything still held). Its `capabilities` say which optional parts exist
(`pointer_position`, `move_abs`, `move_rel`, `char_input`, `screen_capture`, `windows`,
`hotkey`), and the masters adapt: for example, without `pointer_position` there is no
interference detection. The table of primitives is in the
[contract](contract.md#mouse-and-keyboard-masters).

Then add it to `select_backend` (`j5_macro.backends.select`) and to the values of
`J5_BACKEND`, and test it like the X11 backend: unit tests of its pure parts, and an
end-to-end test that drives a real window. `src/j5_macro/backends/x11/` and the fake
backend (`fake.py`) are the references.

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
| M6 | Windows backend: input through SendInput, window-relative coordinates and capture, a manual test plan for both platforms | Open |
| M7 | Script management for the GUI and the run history | Done |

After v1: an experimental Wayland backend, triggers, and the GUI. The open design
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
