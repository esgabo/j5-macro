# Debugging scripts

j5 scripts can be debugged like programs: pause before a step, run one step at a time,
look at and change variables, evaluate expressions, and set breakpoints. There are two
debuggers on top of the same engine commands: one in the terminal (`j5 run --step`) and
one in VS Code (`j5 dap` with the extension in `editors/vscode`). When you only need to
see what happened, the [run history](#debugging-without-the-debugger) and dry runs are
often enough.

## The terminal debugger

`j5 run --step` starts the run paused before its first step and gives a `(j5)` prompt,
like pdb or gdb. `--break STEP_ID` (repeatable, with or without `--step`) pauses before
that step instead. Once a debugger is active, it takes over **every** pause of the run:
steps, breakpoints, `pause:` steps, `break: true` steps, failed steps with
`on_error: pause`, and mouse interference.

```bash
j5 run --step script.yaml
j5 run --break save --break retry_loop script.yaml
j5 run --step --fast --seed 7 script.yaml    # no human-like timing, reproducible
j5 run --dry-run --break type_name script.yaml   # step through without sending input
```

Breakpoints are set on step **ids**, so give the steps you want to stop at an explicit
`id` (a generated one changes when steps of the same type are added before it). An id the
script does not have stops `j5` before the run starts, listing the ids it has.

### Commands

| Command | Does |
| --- | --- |
| `n`, `next` | Run the current step (a whole `loop` or `if`) and pause at the next one at the same depth or shallower |
| `s`, `step` | Pause at the very next step, entering loops and branches |
| `o`, `out`, `finish` | Finish the current `loop` or `if` (its remaining iterations included) and pause after it |
| `c`, `cont`, `continue` | Run to the next breakpoint or the end |
| `p EXPR`, `print EXPR` | Evaluate an expression over the variables, e.g. `p attempts + 1` |
| `v`, `vars` | Show the variables (secret values show as `***`) |
| `set NAME = EXPR` | Set a variable to the value of an expression, evaluated by the engine. JSON literals are expressions too: `set name = "bob"`, `set n = 3`, `set xs = [1, 2]`. The engine assigns the real value, so `set copy = password` copies a secret even though `p password` shows `***` |
| `b ID...`, `break ID...` | Add breakpoints on steps by id (separated by spaces or commas) |
| `b`, `break` | List the breakpoints: yours and the script's `break: true` steps |
| `clear ID...` | Remove some of your breakpoints; `clear` alone removes all of yours |
| `w`, `where` | The current step, its path, loop iterations, and the source around it |
| `l`, `list` | The steps around the current one, indented by depth; `->` marks the current step and `*` a breakpoint |
| `h`, `help`, `?` | List the commands |
| `q`, `quit`, `exit` | Stop the run and exit (exit code 3) |
| empty line | Repeat the last `n`, `s` or `o` |

Commands that let the run go on (`n`, `s`, `o`, `c`, `q`) end the prompt; the others can be
repeated as often as you like. Breakpoints added with `b` apply to the current run at once.
The script's own `break: true` steps can only be removed by editing the script. A
breakpoint inside a loop fires on every iteration.

### A session

This is a real session on [`examples/hello.yaml`](../examples/hello.yaml) (with the fake
backend, which touches nothing):

```console
$ j5 run --step --fast --seed 7 examples/hello.yaml
run r-20261012-214821-4503  Hello j5  (mode step, seed 7, backend fake)
  0.001s  ⏸ paused (step)
-> start  log  (examples/hello.yaml:10)
(j5) n
  0.003s  ▶ start  log  {"message": "hello, need input!", "level": "info"}
  0.003s    [info] hello, need input!
  0.004s  ✓ start  1ms  {"level": "info", "message": "hello, need input!"}
  0.004s  ⏸ paused (step)
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
  0.006s  attempts = 5  (was 0)
(j5) b type_name
breakpoints: type_name
(j5) l
    start        log    (line 10)
->  open_menu    click  (line 12)
    retry_loop   loop   (line 14)
      count      set    (line 18)
      check      if     (line 20)
        log_2    log    (line 24)
        sleep_1  sleep  (line 26)
  * type_name    type   (line 27)
(j5) c
  0.007s  ▶ open_menu  click  {"x": 120, "y": 40, "button": "left", "clicks": 1, "threshold": 0.9}
  0.008s  ✓ open_menu  1ms  {"x": 120, "y": 40, "button": "left", "clicks": 1}
  0.008s  ▶ retry_loop  loop  {"times": 3, "as": "item"}
  0.008s  ↻ retry_loop  iteration 1/3
  0.009s    ▶ count  set  {"name": "attempts", "expr": "attempts + 1"}
  0.009s      attempts = 6  (was 5)
  ...
  0.302s  ✓ retry_loop  294ms  {"iterations": 3}
  0.302s  breakpoint type_name
  0.302s  ⏸ paused (breakpoint)
-> type_name  type  (examples/hello.yaml:27)
(j5) p attempts
8
(j5) vars
greeting = "hello"
attempts = 8
(j5) b
breakpoint type_name
(j5) q
j5: stopping the run
  0.304s  ■ stopping (user)
■ stopped (user) after 0.304s  (seed 7)
```

The `-> step  type  (file:line)` line names the step the run is paused **before**. Inside
loops it adds the iterations, e.g. `(examples/hello.yaml:18, iterations [1])` (0-based,
outermost loop first). After a `pause:` step the line reads `after <step>` instead, and the
step's message is shown:

```console
after check_screen  pause  (pauses.yaml:6)
pause: Check the dialog, then resume
(j5)
```

### Failed steps and interference

When a step with `on_error: pause` fails, or you move the mouse while j5 is moving it
(with the default `interference: pause`), the run pauses on that step and the debugger
says what you can do: `c` retries the step (a new attempt, with new random choices), and
`n` skips it.

```console
$ j5 run --break find_button flaky.yaml
...
(j5) c
  0.130s  ▶ find_button  wait_image  {"image": "img/button.png", "timeout": "1s", "threshold": 0.9}
  0.130s  ✗ find_button  [capability_missing] the screen cannot be captured: ...
  0.130s  ⏸ paused (error)
-> find_button  wait_image  (flaky.yaml:4)
step 'find_button' failed: c retries it, n skips it
(j5) n
  0.131s  ↷ find_button  skipped (user)
  0.131s  ⏸ paused (step)
-> done  log  (flaky.yaml:7)
```

`s` and `o` at such a pause also retry the step, then pause as stepping says. Use `p`,
`vars` and `set` first if a variable needs fixing before the retry.

### Piped commands and `--json`

Commands are read from standard input line by line, so a session can be scripted. When
standard input is not a terminal, each command is echoed after the prompt so the output
reads like a transcript. When the commands run out, the run continues to the end, and
later pauses resume on their own:

```console
$ printf 'v\nc\n' | j5 run --step pauses.yaml
...
(j5)
j5: end of debugger commands; continuing the run to the end
```

Lines typed while the run is running are kept for the next prompt. Ctrl+C stops the run
as usual, at the prompt too.

With `--json`, events stay on standard output as JSON lines and the debugger (prompt,
answers, locations) talks on standard error, so the two can be separated:

```bash
printf 'n\nvars\nc\n' | j5 run --json --step script.yaml > events.jsonl 2> debugger.txt
```

## Debugging in VS Code

`j5 dap` is a [Debug Adapter Protocol](https://microsoft.github.io/debug-adapter-protocol/)
server, and [`editors/vscode`](../editors/vscode/README.md) is a small extension (plain
JavaScript, no build step) that registers it as the `j5` debugger. Breakpoints go on the
lines of the YAML file.

### Installing the extension

The extension is not published on the Marketplace; install it from the repository.

1. Install j5-macro so that the `j5` command works (a virtual environment is fine):

   ```bash
   pip install -e ".[x11,vision]"     # from the repository root
   j5 dap --help
   ```

2. Make the extension visible to VS Code, either by linking the folder into the
   extensions directory and restarting VS Code:

   ```bash
   # Linux and macOS
   ln -s "$PWD/editors/vscode" ~/.vscode/extensions/j5-macro.j5-macro-debug-0.0.1
   ```

   ```powershell
   # Windows (PowerShell; a junction needs no administrator rights)
   New-Item -ItemType Junction -Path "$env:USERPROFILE\.vscode\extensions\j5-macro.j5-macro-debug-0.0.1" -Target "$PWD\editors\vscode"
   ```

   or by packaging it with [vsce](https://github.com/microsoft/vscode-vsce):

   ```bash
   cd editors/vscode
   npx @vscode/vsce package
   code --install-extension j5-macro-debug-0.0.1.vsix
   ```

3. If `j5` is not on the `PATH` that VS Code sees (for example, it lives in a virtual
   environment that is not activated), tell the extension how to start the adapter. The
   first of these that is set wins:

   | Where | Adapter command |
   | --- | --- |
   | `"python": "/path/to/venv/bin/python"` in the launch configuration | `<python> -m j5_macro.dap` |
   | The `j5.adapterCommand` setting, e.g. `["/path/to/venv/bin/j5", "dap"]` | That command |
   | Neither | `j5 dap` from the `PATH` |

The adapter starts in the workspace folder and starts the engine like `j5` does
(`J5_ENGINE_CMD` included).

### Launch configurations

Open a j5 script and press F5. Without a `launch.json`, the extension debugs the YAML file
in the active editor and stops before its first step. An example `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "type": "j5",
      "request": "launch",
      "name": "Debug j5 script",
      "program": "${file}",
      "stopOnEntry": true
    },
    {
      "type": "j5",
      "request": "launch",
      "name": "Fill the form (dry run)",
      "program": "${workspaceFolder}/scripts/form.yaml",
      "dryRun": true,
      "params": {"customer": "ACME #42", "copies": 2},
      "paramsFromEnv": {"password": "FORM_PASSWORD"},
      "seed": 7
    }
  ]
}
```

| Attribute | Meaning |
| --- | --- |
| `program` | The YAML script (required); a relative path is resolved against `cwd` |
| `cwd` | Directory a relative `program` is resolved against (default: the adapter's directory, the workspace folder) |
| `stopOnEntry` | Pause before the first step (the engine's step mode) |
| `dryRun` | Send no input: input steps describe what they would do, image searches run once. With `stopOnEntry` it stops at the first step too |
| `fast` | Use the `direct` strategy for mouse and keyboard instead of human-like movement |
| `allowExec` | Let the script's `exec` steps run programs (`j5 run --allow-exec`) |
| `seed` | Integer seed of the run, to repeat the same human-like input |
| `params` | Values for the script's `params`, by name |
| `paramsFromEnv` | Params read from environment variables of the adapter process (param name to variable name). Use it for secrets, so they stay out of `launch.json` |
| `noDebug` | Run without stopping (set by Run Without Debugging) |
| `python` | Start the adapter with this interpreter (`python -m j5_macro.dap`) instead of `j5 dap` |

Other attributes are ignored. A param given in both `params` and `paramsFromEnv`, an
environment variable that is not set, a missing required param or a script that does not
load makes the launch fail; load problems appear in the Debug Console as `path:line:`
messages.

### Breakpoints and lines

Breakpoints are set on lines but the engine pauses at **steps**, so each line is mapped
to a step:

- The line where a step starts (its `- id:` or `- click:` line) is that step.
- A line inside a step (its parameters, a nested mapping, the `else:` of an `if`) is the
  innermost step that contains it.
- A blank or comment line counts as the next line with content, so a breakpoint on the
  comment above a step stops at that step.
- A header line before the steps (`name:`, `vars:`...) is the first step after it.
- A line after the last step has no step: the breakpoint stays unverified ("no step at or
  after this line").

A verified breakpoint moves to the line where its step starts. In
[`examples/hello.yaml`](../examples/hello.yaml), a breakpoint on line 19
(`set: {name: attempts, ...}`) moves to line 18 (`- id: count`), and one on line 25
(`else:`) moves to line 20 (`- id: check`, the `if`). Breakpoints set before the script
loads are verified once it does. Breakpoints in other files are never verified.

### Call stack, variables and the Debug Console

The debug session has one thread, `j5 run`. Its call stack is the current step, written
`<id> (<type>)` at its line, followed by the `loop` and `if` steps that contain it,
innermost first. Loops show the iteration (1-based):

```text
count (set)                            hello.yaml 18
retry_loop (loop) iteration 1 of 3     hello.yaml 14
```

The **Variables** view has one scope with the run's variables. Lists and mappings expand,
and secret values show as `***`. You can change a value while paused: it is read as JSON
(`5`, `"text"`, `[1, 2]`, `true`), and anything that is not JSON is taken as text. Changing
an item inside a list or mapping replaces the whole variable, and is refused when the
variable holds a masked secret.

The **Debug Console** evaluates expressions in the j5 expression language (`attempts + 1`,
`len(items)`) while the run is paused; so do watch expressions and hovers. The run's
progress appears there too: each step as it starts (`> open_menu (click)`, linked to its
line), log messages, and failures and warnings in the error colour.

### Stepping and pauses

| VS Code | Engine | Effect |
| --- | --- | --- |
| Continue (F5) | `resume` | Run to the next breakpoint or the end |
| Step Over (F10) | `step_over` | Run the current step (a whole `loop` or `if`) |
| Step Into (F11) | `step_into` | Pause at the very next step, entering blocks |
| Step Out (Shift+F11) | `step_out` | Finish the current block and pause after it |
| Pause (F6) | `pause` | Pause at the next atomic action (never in the middle of a movement or a key sequence) |
| Stop (Shift+F5) | `stop` | Stop the run and release every held key and button |

A failed step with `on_error: pause` stops like an exception, with the error message;
Continue retries the step and Step Over skips it. Mouse interference stops with the
description "user moved the mouse", with the same choices. With Run Without Debugging
(`noDebug`), breakpoints are not sent and every pause resumes on its own, except a pause
for an error or interference, which stops the run since nobody can decide.

When the run ends, the debug session ends with exit code 0 (completed), 1 (failed) or 3
(stopped). The emergency stop key and the screen-corner failsafe work as in `j5 run`.

### Limitations

- The adapter is tested end to end over stdio, framed the way VS Code frames it, against
  the real engine (`tests/test_dap_e2e.py`), and its request handling has unit tests. The
  extension's own code (`extension.js`, which only chooses the adapter command and fills
  in a configuration for F5) has no automated tests, and the repository does not record a
  check inside VS Code itself yet. Please report problems you find there.
- One debug session runs one script once. There is no restart, step back, conditional
  breakpoint, hit count or exception filter.
- The Debug Console only evaluates while the run is paused.

## Debugging without the debugger

- **Dry runs.** `j5 run --dry-run` runs the whole logic (variables, loops, branches, logs,
  sleeps) without sending input. Input steps say what they would do
  (`[info] would click left at (120, 40)`, `would type 12 characters`) and image searches
  run once and report their score, so you can check templates against the current screen.
  It works without an input backend too.
- **Logs and pauses in the script.** A `log:` step prints any expression
  (`log: "attempts=${attempts}"`), and a `pause:` step stops the run until you press Enter.
- **Variables from the command line.** `--var NAME=VALUE` overrides a script variable for
  one run, to try a branch without editing the script.
- **Fast and reproducible.** `--fast` removes the human-like timing; `--seed` repeats the
  same random choices (see [how-it-works.md](how-it-works.md#reproducibility)).
- **The run history.** Every run is saved. `j5 runs` lists them, `j5 runs show RUN_ID`
  replays the output of a run (also one started by VS Code), and `j5 runs show --json
  RUN_ID` gives every event, including the ones the text output leaves out: `image_search`
  with each attempt's score and location, `input_planned` with the plan's seed and
  duration, and `input_state` with the held keys.
- **Captures.** A failed image search saves the screen of its last attempt, with the best
  match outlined in red, under the run's `captures/` folder; `j5 runs show` prints where.
  Comparing it with the template usually shows why it did not match.
- **Validation.** `j5 validate` reports every problem of a script with its line, and
  `j5 schema` gives editors completion and validation as you type.

See [cli.md](cli.md) for every option.
