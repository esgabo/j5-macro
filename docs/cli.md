# Command-line reference

`j5` is the command-line client of the j5 engine. Every command except `schema` and `dap`
starts the engine as a child process, sends it commands and shows what comes back (see
[how-it-works.md](how-it-works.md)).

```console
$ j5 --help
usage: j5 [-h] [--version] <command> ...

Need input! Desktop automation.

positional arguments:
  <command>
    run       run a script
    validate  validate a script without running it
    runs      list, show and delete saved runs
    steps     list the step types and their parameters
    schema    print the JSON Schema of scripts for editors
    dap       serve the Debug Adapter Protocol on stdio (for VS Code)

options:
  -h, --help  show this help message and exit
  --version   show program's version number and exit
$ j5 --version
j5-macro 0.0.1
```

`j5` without a command prints this help and exits with 0. Every command accepts `-h` /
`--help`.

| Command | Does |
| --- | --- |
| [`j5 run`](#j5-run) | Run a script, optionally in the debugger or as a dry run |
| [`j5 validate`](#j5-validate) | Check a script without running it |
| [`j5 steps`](#j5-steps) | List the step types and their parameters |
| [`j5 schema`](#j5-schema) | Print the JSON Schema of scripts, for editors |
| [`j5 runs`](#j5-runs) | List, replay and delete saved runs |
| [`j5 dap`](#j5-dap) | Serve the Debug Adapter Protocol (started by VS Code) |

## `j5 run`

```text
j5 run [-h] [--runs-dir DIR] [--step | --dry-run] [--break STEP_ID]
       [--seed SEED] [--fast] [--param NAME=VALUE]
       [--param-file NAME=PATH] [--param-env NAME=VARIABLE]
       [--params-file FILE] [--var NAME=VALUE] [--json]
       script
```

Loads the script, runs it and shows its progress. The output is explained in
[how-it-works.md](how-it-works.md#reading-the-output).

| Option | Meaning |
| --- | --- |
| `script` | Path to the YAML script |
| `--step` | Debug: start paused before the first step and give the debugger prompt at every pause (see [debugging.md](debugging.md)) |
| `--break STEP_ID` | Pause before the step with this id and give the debugger prompt, with or without `--step`. Repeatable. An id the script does not have fails before the run starts (exit 2) |
| `--dry-run` | Send no input: input steps describe what they would do, image searches run once. Cannot be combined with `--step` (use `--break` to pause a dry run) |
| `--seed SEED` | Integer seed of the run, to repeat the same human-like input. Without it the engine picks one and shows it |
| `--fast` | Use the `direct` strategy for mouse and keyboard: no human-like movement or typing |
| `--param NAME=VALUE` | Set a script param. The value is taken as text exactly as written (converted for `integer`, `number` and `boolean` params). Repeatable |
| `--param-file NAME=PATH` | Set a param to the contents of a UTF-8 file, without its final line break; `-` reads standard input (only one param can). Repeatable |
| `--param-env NAME=VARIABLE` | Set a param from an environment variable; fails if the variable is not set. Repeatable |
| `--params-file FILE` | Set params from a YAML or JSON mapping of names to strings, numbers or booleans |
| `--var NAME=VALUE` | Override one of the script's `vars`, for debugging. `3`, `2.5`, `true`, `false` and `null` are converted, a value in matching quotes (`'3'`) is forced to text, anything else is text as written. Repeatable |
| `--json` | Print every event as one JSON line instead of text (see [JSON output](#json-output)) |
| `--runs-dir DIR` | Save the run in this directory instead of the default (see [Environment variables](#environment-variables)) |

### Script params

A script's `params` can come from four sources, and each name from exactly one of them;
giving a name twice is a usage error:

```console
$ j5 run --dry-run --params-file params.yaml --param user=x login.yaml
j5: param 'user' is given twice (--params-file and --param)
```

```bash
j5 run form.yaml --param 'customer=ACME #42' --param-env password=FORM_PASSWORD
j5 run letter.yaml --param-file body=letter.txt          # multi-line text from a file
printf %s "$TOKEN" | j5 run login.yaml --param-file token=-
j5 run form.yaml --params-file values.yaml               # customer: ACME, copies: 2
```

Required params you left out are asked for when standard input is a terminal (and is not
being read by `--param-file NAME=-`); secret ones are read without echo. Without a
terminal, the run does not start:

```console
$ j5 run login.yaml </dev/null
j5: cannot start the run: [validation_failed] missing required params: 'user', 'password'
j5: pass params with --param NAME=VALUE, --param-file, --param-env or --params-file (see j5 run --help)
```

Pass secrets with `--param-env`, `--param-file` or the prompt. A secret given with
`--param` works, but `j5` warns that it is visible in the shell history and the process
list:

```console
$ j5 run login.yaml --param user=x --param password=hunter2 --fast
j5: warning: secret param 'password' was given with --param, so it is visible in the shell history and the process list; prefer --param-env, --param-file or the prompt
```

`j5 validate` lists a script's params. How params and secrets behave during a run is in
[how-it-works.md](how-it-works.md#params-and-secrets).

### Pauses and Ctrl+C

When a run pauses and no debugger is active (`--step` or `--break`):

| Pause | In a terminal | Without a terminal |
| --- | --- | --- |
| A `pause:` step or a `break: true` step | "press Enter to resume or Ctrl+C to stop" | Resumes on its own and says so |
| A failed step with `on_error: pause`, or mouse interference | Enter retries the step, `s` and Enter skips it | Retries the step up to twice and says so; if it fails again, stops the run and exits with code 1 |

The first Ctrl+C sends `stop` to the engine, which releases held keys and buttons and
ends the run; `j5` waits up to 10 seconds for it. A second Ctrl+C quits at once. Either
way the exit code is 130.

### JSON output

With `--json`, standard output carries one JSON object per line: every event of the run
and the engine's `log` events (the engine's `ready` event is not printed). Problems found
while loading, warnings and messages from `j5` itself go to standard error, as does the
debugger with `--step` or `--break`, so standard output stays machine-readable.

```console
$ j5 run --json --seed 7 examples/hello.yaml 2>/dev/null
{"v":1,"kind":"event","type":"log","run_id":null,"seq":2,"ts":"2026-10-12T21:49:03.834Z","t_ms":5,"payload":{"level":"info","message":"emergency stop key: ctrl+alt+shift+q"}}
{"v":1,"kind":"event","type":"run_started","run_id":"r-20261012-214903-d10b","seq":1,"ts":"2026-10-12T21:49:03.852Z","t_ms":0,"payload":{"script_id":"s1","script_name":"Hello j5","mode":"normal","seed":7,"backend":"x11","input_profile":{...}}}
{"v":1,"kind":"event","type":"step_started","run_id":"r-20261012-214903-d10b","seq":3,"ts":"2026-10-12T21:49:03.854Z","t_ms":2,"payload":{"step_id":"start","path":[0],"line":10,"type":"log","params":{"message":"hello, need input!","level":"info"}}}
...
{"v":1,"kind":"event","type":"input_planned","run_id":"r-20261012-214903-d10b","seq":7,"ts":"2026-10-12T21:49:03.856Z","t_ms":4,"payload":{"device":"mouse","strategy":"human","seed":6991577597064561139,"from":[640,400],"to":[120,40],"duration_ms":905}}
{"v":1,"kind":"event","type":"input_executed","run_id":"r-20261012-214903-d10b","seq":8,"ts":"2026-10-12T21:49:04.762Z","t_ms":911,"payload":{"device":"mouse","actual_duration_ms":906,"aborted":false}}
{"v":1,"kind":"event","type":"input_state","run_id":"r-20261012-214903-d10b","seq":9,"ts":"2026-10-12T21:49:04.763Z","t_ms":911,"payload":{"held":["button:left"]}}
...
{"v":1,"kind":"event","type":"run_finished","run_id":"r-20261012-214903-d10b","seq":78,"ts":"2026-10-12T21:49:07.637Z","t_ms":3785,"payload":{"status":"completed","duration_ms":3785}}
```

Each line is an envelope:

| Field | Meaning |
| --- | --- |
| `v` | Protocol version (1) |
| `kind` | Always `event` here |
| `type` | The event: `run_started`, `step_started`, `image_search`, `input_planned`, `run_finished`... |
| `run_id` | The run, or `null` for engine events such as `log` |
| `seq` | Gap-free counter, one sequence per run plus one for engine events |
| `ts` | UTC time, for display |
| `t_ms` | Milliseconds since the run (or the engine) started, on a monotonic clock |
| `payload` | The event's content |

Clients must ignore event types and fields they do not know. The full list of events and
their payloads is in the [contract](contract.md#events). The exit code is the same as in
text mode.

## `j5 validate`

```text
j5 validate [-h] [--json] script
```

Loads the script in the engine and reports its problems as `path:line:` messages, its
params, and a verdict. It sends no input.

```console
$ j5 validate examples/hello.yaml
examples/hello.yaml:24: warning: generated id 'log_2', add an explicit id to keep breakpoints stable
examples/hello.yaml:26: warning: generated id 'sleep_1', add an explicit id to keep breakpoints stable
examples/hello.yaml: valid (2 warnings)
$ j5 validate login.yaml
param user (string, required): Account name
param password (string, secret, required)
param copies (integer, default 1)
login.yaml: valid
$ j5 validate nope.yaml
nope.yaml: error[script_not_found]: cannot open script '/home/me/j5-macro/nope.yaml': No such file or directory
nope.yaml: invalid (1 error)
```

`--json` prints one object with `path`, `valid`, `script_id`, `errors`, `warnings` and
`params`.

Exit codes: 0 valid (warnings allowed), 1 invalid or unreadable, 2 usage error.

## `j5 steps`

```text
j5 steps [-h] [--json]
```

Lists the registered step types, their parameters, types, defaults and documentation, as
the engine reports them. `--json` prints the engine's full answer, including each type's
JSON Schema.

```console
$ j5 steps
click  Click on an image or at x/y.
  image: string  Template image, relative to the script file.
  x: integer  Horizontal position in physical pixels.
  y: integer  Vertical position in physical pixels.
  window: object  Window to work in, e.g. {title: ...}; coordinates become relative.
  button: string = "left"  Mouse button.
  clicks: integer = 1  Number of clicks.
  ...
```

## `j5 schema`

```text
j5 schema [-h] [-o FILE]
```

Prints the JSON Schema of j5 scripts, for completion and validation in editors. It does
not start the engine. `-o FILE` writes it to a file instead of standard output.

With the VS Code YAML extension (`redhat.vscode-yaml`):

```bash
j5 schema -o j5-script.schema.json
```

Then either start each script with

```yaml
# yaml-language-server: $schema=./j5-script.schema.json
```

or map the schema to your scripts in `.vscode/settings.json`:

```json
"yaml.schemas": {"./j5-script.schema.json": ["*.j5.yaml", "scripts/**/*.yaml"]}
```

## `j5 runs`

```text
j5 runs [-h] [--runs-dir DIR] [-n LIMIT] [--json] [<action> ...]
j5 runs show [-h] [--runs-dir DIR] [--json] RUN_ID
j5 runs delete [-h] [--runs-dir DIR] RUN_ID [RUN_ID ...]
```

Every run is saved in the runs directory (see
[how-it-works.md](how-it-works.md#run-history)), and the newest 20 are kept.

| Command | Does |
| --- | --- |
| `j5 runs` | List the saved runs, newest first. `-n`/`--limit N` shows at most N (default 20); `--json` prints the engine's answer |
| `j5 runs show RUN_ID` | The run's script, start and end times and captures, then its events rendered as `j5 run` showed them |
| `j5 runs show --json RUN_ID` | Its events, one JSON line each, exactly as saved |
| `j5 runs delete RUN_ID...` | Delete one or more runs |

`--runs-dir DIR` is accepted before or after the action.

```console
$ j5 runs
RUN                     STARTED              SCRIPT    MODE    STATUS     DURATION
r-20261012-214850-9b05  2026-10-12 18:48:50  Search    dry     completed    0.307s
r-20261012-214849-bba4  2026-10-12 18:48:49  Search    normal  failed       1.275s
r-20261012-214836-4783  2026-10-12 18:48:36  Flaky     normal  completed    0.132s
r-20261012-214821-4503  2026-10-12 18:48:21  Hello j5  step    stopped      0.304s
$ j5 runs show r-20261012-214849-bba4
saved run r-20261012-214849-bba4
  script    /home/me/macros/search.yaml
  started   2026-10-12 18:48:49
  finished  2026-10-12 18:48:50
  captures  1 file in /home/me/.local/state/j5-macro/runs/r-20261012-214849-bba4
    captures/0001-find_button-4.png
run r-20261012-214849-bba4  Search  (mode normal, seed 1659557458, backend x11)
  0.001s  ▶ find_button  wait_image  {"image": "img/button.png", "timeout": "1s", "threshold": 0.9}
  1.275s  ✗ find_button  [image_not_found] img/button.png not found after 4 attempt(s) in 1s (best score 0.000, threshold 0.9)
  1.275s    screenshot: captures/0001-find_button-4.png
✗ failed after 1.275s  (seed 1659557458): [image_not_found] img/button.png not found after 4 attempt(s) in 1s (best score 0.000, threshold 0.9)
$ j5 runs show r-20990101-000000-abcd
j5: no saved run r-20990101-000000-abcd
$ j5 runs show nope
j5: 'nope' is not a run id (r-YYYYMMDD-HHMMSS-xxxx; see j5 runs)
```

Times in the list are local; `STATUS` is `unfinished` for a run that never ended (or is
running now), and `j5 runs show` says so too.

Exit codes: 0 done, 1 no saved run with that id (or the engine died), 2 usage error or a
malformed run id. `j5 runs delete` with several ids deletes every one it can and exits
with the worst code.

## `j5 dap`

```text
j5 dap [-h]
```

Serves the [Debug Adapter Protocol](https://microsoft.github.io/debug-adapter-protocol/) on
standard input and output. You do not run it yourself: an editor starts it, such as VS Code
with the extension in `editors/vscode`. `python -m j5_macro.dap` is the same server. See
[debugging.md](debugging.md#debugging-in-vs-code).

## Exit codes

| Code | `run` | `validate` | `runs` | All commands |
| --- | --- | --- | --- | --- |
| 0 | Completed | Valid (warnings allowed) | Done | |
| 1 | The run failed, or a paused step kept failing with no terminal to ask | Invalid or unreadable | No saved run with that id | The engine could not be started or died |
| 2 | The script could not be loaded or the run could not start | | Malformed run id | Usage error (bad options) |
| 3 | The run was stopped (emergency stop, corner failsafe, interference set to `stop`, `q` in the debugger) | | | |
| 130 | Interrupted with Ctrl+C | | | Interrupted with Ctrl+C |

## Environment variables

| Variable | Read by | Meaning |
| --- | --- | --- |
| `J5_BACKEND` | Engine | Input backend: `auto` (default), `x11`, `windows`, `fake` (records input, touches nothing) or `none`. See [how-it-works.md](how-it-works.md#platforms-and-backends) |
| `J5_RUNS_DIR` | Engine, `j5 runs` | Runs directory, when `--runs-dir` is not given |
| `J5_STOP_KEY` | Engine | Emergency stop key, e.g. `ctrl+alt+f12` (default `ctrl+alt+shift+q`): modifiers and one key joined with `+` |
| `J5_ENGINE_CMD` | `j5`, `j5 dap` | The command that starts the engine, as a JSON list of strings, e.g. `["/opt/venv/bin/python", "-m", "j5_macro.engine"]`. Default: the current Python with `-m j5_macro.engine`. Used by the tests |
| `NO_COLOR` | `j5` | Any non-empty value turns colours off |
| `DISPLAY`, `XDG_SESSION_TYPE`, `WAYLAND_DISPLAY` | Engine | Used by `J5_BACKEND=auto` to detect an X11 or Wayland session |
| `XDG_STATE_HOME` (Linux), `LOCALAPPDATA` (Windows) | Engine | Base of the default runs directory |
| `J5_REQUIRE_X11` | Test suite only | Makes the X11 tests fail instead of being skipped when no X server is reachable (set by `scripts/check.sh`; see [development.md](development.md)) |

The engine inherits the environment of `j5`, so `J5_BACKEND=fake j5 run ...` and
`J5_STOP_KEY=... j5 run ...` work as expected.

## The engine command

Integrators who write their own client start the engine themselves:

```console
$ python -m j5_macro.engine --help
usage: python -m j5_macro.engine [-h] [--runs-dir DIR] [--stop-key COMBO]

j5-macro engine: speaks the JSON-lines protocol over stdin/stdout.

options:
  -h, --help        show this help message and exit
  --runs-dir DIR    where run history is saved (default: $J5_RUNS_DIR, else
                    the platform state directory, e.g.
                    ~/.local/state/j5-macro/runs on Linux)
  --stop-key COMBO  global emergency stop key that stops the active run
                    (default: $J5_STOP_KEY, else ctrl+alt+shift+q)
```

The engine reads commands on standard input and writes responses and events on standard
output, one JSON object per line. It emits `ready` when it starts, then expects `hello`:

```console
$ printf '%s\n' '{"v":1,"kind":"command","type":"hello","id":"1","payload":{"v_min":1,"v_max":1,"client":"demo"}}' \
    '{"v":1,"kind":"command","type":"load","id":"2","payload":{"path":"examples/hello.yaml"}}' \
  | J5_BACKEND=fake python -m j5_macro.engine
{"v":1,"kind":"event","type":"ready","run_id":null,"seq":1,"ts":"2026-10-12T21:52:06.161Z","t_ms":0,"payload":{"v":1,"backend":"fake","capabilities":{"backend":"fake","input_strategies":["direct","linear","human"],"step_types":["click","move",...]}}}
{"v":1,"kind":"event","type":"log","run_id":null,"seq":2,"ts":"2026-10-12T21:52:06.161Z","t_ms":0,"payload":{"level":"info","message":"emergency stop key: ctrl+alt+shift+q"}}
{"v":1,"kind":"response","type":"hello","id":"1","ok":true,"result":{"v":1,"capabilities":{...}}}
{"v":1,"kind":"response","type":"load","id":"2","ok":true,"result":{"script_id":"s1","steps":[{"id":"start","type":"log","path":[0],"line":10,...},...]}}
```

The engine ignores SIGINT (the client decides what Ctrl+C means) and exits when its
standard input closes, stopping an active run first. A client that starts the engine is
expected to release the keys and buttons of the last `input_state` event if the engine
dies. Every command, event and error code is specified in the [contract](contract.md).
