# j5-macro

> "Need input!" — Johnny 5

Scriptable, debuggable desktop automation for Windows and Linux. Write macros as YAML steps
(click, type, wait for an image, branch, loop), run them step by step with breakpoints, and
let the mouse and keyboard move like a person instead of teleporting.

**Status:** early development. Scripts load, validate and run on Linux with an X11 session:
real mouse and keyboard input with `direct`, `linear` and human-like (`human`) strategies,
image search on screen (`wait_image`, clicking on images, `image_visible`), and a pause or
stop when you grab the mouse mid-movement, and a step debugger in the terminal
(`j5 run --step`). A VS Code debugger comes in M4 and Windows in M6.

## Design

The full design is in [`docs/contract.md`](docs/contract.md). The short version:

- A **headless engine** runs in its own process and speaks a JSON-lines protocol:
  commands in, events out.
- The **CLI**, a future **GUI** and a **DAP debugger** (VS Code) are all clients of that protocol.
- **Mouse and keyboard masters** plan every movement with a strategy (`direct`, `linear` or
  `human`) and drive a thin per-platform **backend**. `human` (the default) curves the
  pointer along WindMouse or Bézier paths with Fitts' law timing, a bell-shaped speed,
  occasional overshoot and micro-jitter, and types with irregular rhythm, pauses after
  punctuation, rolled-over keys and optional corrected typos.
- Runs are **reproducible** (seeded), **inspectable** (every step emits events) and
  **safe to abort** (held keys and buttons are always released).

```yaml
steps:
  - id: open_menu
    click: {image: img/file_menu.png}
  - type: {text: "hello"}
  - key: {press: ctrl+s}
```

Try it:

```bash
pip install -e ".[x11,vision]"    # X11 input and screen vision on Linux
j5 validate examples/hello.yaml
j5 run --dry-run examples/hello.yaml   # describes the input, touches nothing
j5 run examples/hello.yaml             # really clicks and types (X11 session)
```

Stop a run at any time with `ctrl+alt+shift+q`, or by pushing the mouse into a screen corner.

### Passing values in

Declare the values a script takes from outside in `params`, and use them like variables:

```yaml
params:
  customer: {description: Name to type in the form}
  password: {secret: true}
  copies: {type: integer, default: 1}
steps:
  - type: "${customer}"
  - key: tab
  - type: "${password}"
```

```bash
j5 run form.yaml --param 'customer=ACME #42' --param-env password=FORM_PASSWORD
j5 run letter.yaml --param-file body=letter.txt      # multi-line text from a file
printf %s "$TOKEN" | j5 run login.yaml --param-file password=-
```

`--param` values are typed exactly as written (`#`, quotes and leading zeros included).
Required params you leave out are asked for when you run `j5` in a terminal, secrets
without echo. Secret values show as `***` in the output and the run history; pass them
with `--param-env`, `--param-file` or the prompt rather than `--param`, which leaves them
in your shell history. `j5 validate` lists a script's params.

### Run history

Every run is saved with all its events and screen captures, and the newest 20 are kept,
in `~/.local/state/j5-macro/runs` on Linux and `%LOCALAPPDATA%\j5-macro\runs` on Windows
(`--runs-dir DIR` or `J5_RUNS_DIR` choose another directory):

```console
$ j5 runs
RUN                     STARTED              SCRIPT    MODE    STATUS     DURATION
r-20261012-205233-a083  2026-10-12 17:52:33  Hello j5  dry     completed    0.195s
r-20261012-202139-f5d2  2026-10-12 17:21:39  Hello j5  step    stopped      0.325s
$ j5 runs show r-20261012-202139-f5d2          # replays the output of that run
$ j5 runs show --json r-20261012-202139-f5d2   # its events, one JSON line each
$ j5 runs delete r-20261012-202139-f5d2
```

### Debugging

`j5 run --step` starts a script paused before its first step and gives a pdb-like prompt
every time the run pauses; `--break STEP_ID` (repeatable, with or without `--step`) pauses
before a step. A short session on the example (`--fast` skips the human-like timing):

```console
$ j5 run --step --fast --seed 7 examples/hello.yaml
run r-20261012-202139-f5d2  Hello j5  (mode step, seed 7, backend x11)
  0.001s  ⏸ paused (step)
-> start  log  (examples/hello.yaml:10)
(j5) n
  0.003s  ▶ start  log  {"message": "hello, need input!", "level": "info"}
  0.003s    [info] hello, need input!
  0.003s  ✓ start  1ms  {"level": "info", "message": "hello, need input!"}
  0.003s  ⏸ paused (step)
-> open_menu  click  (examples/hello.yaml:12)
(j5) w
step open_menu  click  path [1]  examples/hello.yaml:12
   10    - id: start
   11      log: "${greeting}, need input!"
-> 12    - id: open_menu
   13      click: {x: 120, y: 40}
   14    - id: retry_loop
   15      loop:
(j5) set attempts = 5
  0.007s  attempts = 5  (was 0)
(j5) b type_name
breakpoints: type_name
(j5) c
  ...
  0.323s  breakpoint type_name
  0.323s  ⏸ paused (breakpoint)
-> type_name  type  (examples/hello.yaml:27)
(j5) p attempts
8
(j5) q
j5: stopping the run
■ stopped (user) after 0.325s  (seed 7)
```

| Command | Does |
| --- | --- |
| `n`, `next` | Run the current step (a whole `loop` or `if`) and pause at the next one |
| `s`, `step` | Pause at the very next step, entering loops and branches |
| `o`, `out`, `finish` | Finish the current block and pause after it |
| `c`, `continue` | Run to the next breakpoint or the end |
| `p EXPR`, `print EXPR` | Evaluate an expression over the variables |
| `v`, `vars` | Show the variables (secrets show as `***`) |
| `set NAME = EXPR` | Set a variable to the value of an expression, evaluated by the engine; JSON literals are expressions too (`set name = "bob"`, `set xs = [1, 2]`) |
| `b [ID...]`, `break [ID...]` | Add breakpoints; alone, list them (with the script's `break: true` steps) |
| `clear [ID...]` | Remove breakpoints; alone, remove all of yours |
| `w`, `where` | The current step, its path, loop iterations and the source around it |
| `l`, `list` | The script's steps around the current one |
| `h`, `help` | List the commands |
| `q`, `quit` | Stop the run and exit (exit code 3) |
| empty line | Repeat the last `n`, `s` or `o` |

After a failed step (`on_error: pause`) or when you moved the mouse during a step, `c`
retries the step and `n` skips it. Ctrl+C stops the run as usual. Commands are read from
standard input, so a session can be scripted; when they run out the run continues to the
end: `printf 'n\nvars\n' | j5 run --step script.yaml`. With `--json`, events stay on
stdout and the debugger talks on stderr.

### Debugging in VS Code

`j5 dap` is a Debug Adapter Protocol server, and [`editors/vscode`](editors/vscode) is a
small extension (no build step) that registers it as the `j5` debugger. Install it as its
README explains, open a script and press F5: it stops before the first step, and you can set
breakpoints in the YAML gutter, step over, into and out of `loop` and `if` blocks, inspect
and change variables (secrets show as `***`) and evaluate expressions in the Debug Console.

```json
{
  "type": "j5",
  "request": "launch",
  "name": "Debug j5 script",
  "program": "${file}",
  "stopOnEntry": true,
  "params": {"customer": "ACME #42"},
  "paramsFromEnv": {"password": "FORM_PASSWORD"}
}
```

Add `"dryRun": true` to step through a script without sending any input, or `"fast": true`
to skip the human-like movement while debugging.

## Layout

```
src/j5_macro/
  protocol/   message envelope, typed commands and events, error codes
  engine/     the engine process: commands, runs, persistence
  client/     asyncio client that launches and talks to the engine
  script/     script model and the YAML loader
  steps/      step specs, registry, built-in steps, editor JSON Schema
  expr/       safe expression evaluator and ${} interpolation
  runtime/    the interpreter (program counter, debugger controls, dry-run)
  input/      mouse and keyboard masters and their strategies (M2, M5)
  backends/   platform primitives: x11, windows (M2, M6); wayland later
  vision/     screen capture and image matching (M3)
  cli/        the `j5` command
  dap/        Debug Adapter Protocol server (`j5 dap`) for editors
docs/contract.md
editors/vscode/   VS Code extension for the j5 debugger
examples/
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,x11,vision]"
scripts/check.sh    # lint, types and tests (on Linux under Xvfb: apt install xvfb)
```

Platform extras: `pip install -e ".[x11,vision]"` on Linux, `".[windows,vision]"` on Windows.

## Roadmap

Tracked as GitHub milestones:

| Milestone | Scope |
| --- | --- |
| M0 | Protocol types and the engine process skeleton |
| M1 | Interpreter, step registry, dry-run and the CLI |
| M2 | X11 backend with `direct` and `linear` input |
| M3 | Vision: `wait_image` and image-based clicks |
| M4 | Debugger: step mode, breakpoints, inspection |
| M5 | Human-like mouse and keyboard strategies |
| M6 | Windows backend |
| M7 | Script management commands for the GUI |

## License

j5-macro is licensed under the [Apache License 2.0](LICENSE).
