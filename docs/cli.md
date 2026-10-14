# Command-line reference

`j5` is the command-line client of the j5 engine. Every command except `schema`, `pack`,
`unpack` and `dap` starts the engine as a child process, sends it commands and shows what comes back (see
[how-it-works.md](how-it-works.md)).

```console
$ j5 --help
usage: j5 [-h] [--version] <command> ...

Need input! Desktop automation.

positional arguments:
  <command>
    run         run a script
    validate    validate a script without running it
    runs        list, show and delete saved runs
    steps       list the step types and their parameters
    schema      print the JSON Schema of scripts for editors
    pack        embed the image files a script uses into the script
    unpack      write a script's embedded images back to files
    windows     list the visible windows and their client areas
    screenshot  save a PNG of the desktop, a window or a region
    capture     capture a template: drag a rectangle on the screen
    pick        pick a point on the screen and print a click step
    find        search the screen for a template, as a step would
    dap         serve the Debug Adapter Protocol on stdio (for VS Code)

options:
  -h, --help    show this help message and exit
  --version     show program's version number and exit
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
| [`j5 pack`](#j5-pack) | Embed the image files a script uses into the script |
| [`j5 unpack`](#j5-unpack) | Write a script's embedded images back to files |
| [`j5 runs`](#j5-runs) | List, replay and delete saved runs |
| [`j5 windows`](#j5-windows) | List the visible windows and their client areas |
| [`j5 screenshot`](#j5-screenshot) | Save a PNG of the desktop, a window or a region |
| [`j5 capture`](#j5-capture) | Capture a template, into a file or a script |
| [`j5 pick`](#j5-pick) | Pick a point and print a click step for it |
| [`j5 find`](#j5-find) | Test a template: one search, as a step would make it |
| [`j5 dap`](#j5-dap) | Serve the Debug Adapter Protocol (started by VS Code) |

## `j5 run`

```text
j5 run [-h] [--runs-dir DIR] [--step | --dry-run] [--break STEP_ID]
       [--seed SEED] [--fast] [--allow-exec] [--display-scale SCALE]
       [--param NAME=VALUE]
       [--param-file NAME=PATH] [--param-env NAME=VARIABLE]
       [--params-file FILE] [--var NAME=VALUE]
       [--captures {on_failure,all}] [--stop-key COMBO] [--json]
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
| `--allow-exec` | Let the script's [`exec`](scripts.md#exec) steps run programs. Without it they fail with `capability_missing` |
| `--display-scale SCALE` | The [display scale](scripts.md#display-scale) of this run, `1.5` or `150%`, for every monitor, instead of detecting it (per monitor on Windows, from `Xft.dpi` on X11). Templates and pixel offsets are scaled by it over the script's `capture_scale`. Not a positive number: usage error (exit 2) |
| `--param NAME=VALUE` | Set a script param. The value is taken as text exactly as written (converted for `integer`, `number` and `boolean` params). Repeatable |
| `--param-file NAME=PATH` | Set a param to the contents of a UTF-8 file, without its final line break; `-` reads standard input (only one param can). Repeatable |
| `--param-env NAME=VARIABLE` | Set a param from an environment variable; fails if the variable is not set. Repeatable |
| `--params-file FILE` | Set params from a YAML or JSON mapping of names to strings, numbers or booleans |
| `--var NAME=VALUE` | Override one of the script's `vars`, for debugging. `3`, `2.5`, `true`, `false` and `null` are converted, a value in matching quotes (`'3'`) is forced to text, anything else is text as written. Repeatable |
| `--captures {on_failure,all}` | Which screen captures of image searches the run saves in its directory: `on_failure` (default) keeps only the last attempt of a search that fails, `all` keeps every attempt. See [Saved runs](#j5-runs) |
| `--stop-key COMBO` | The global emergency stop key the engine watches during this run, e.g. `ctrl+alt+f12`, written like a `key` step's `press` (modifiers and one key joined with `+`). Default: `J5_STOP_KEY`, else `ctrl+alt+shift+q`. A malformed combination is a usage error (exit 2) |
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
params, its [embedded images](scripts.md#embedded-images), and a verdict. It sends no
input. Problems with an embedded image (data that is not base64 of a PNG file, an image
over the size limits, a `"#name"` with no such image) are reported at the line of the
entry or of the step.

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

```console
$ j5 validate examples/smoke/pointer_embedded.yaml
image #pattern (png, 48x48, 202 bytes)
examples/smoke/pointer_embedded.yaml: valid
```

`--json` prints one object with `path`, `valid`, `script_id`, `errors`, `warnings`,
`params` and `images`.

Exit codes: 0 valid (warnings allowed), 1 invalid or unreadable, 2 usage error.

## `j5 steps`

```text
j5 steps [-h] [--json]
```

Lists the registered step types, their parameters, types, defaults and documentation, as
the engine reports them: the built-in ones and those of installed
[step plugins](development.md#writing-a-step-plugin). `--json` prints the engine's full answer, including each type's
JSON Schema.

```console
$ j5 steps
click  Click on an image or at x/y.
  image: string  Template image: a path relative to the script file, or "#name" of an image embedded in the script.
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

Prints the JSON Schema of j5 scripts, for completion and validation in editors, including
the steps of installed step plugins (a plugin that cannot be loaded is left out with a
warning on standard error). It does not start the engine. `-o FILE` writes it to a file instead of standard output.

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

## `j5 pack`

```text
j5 pack [-h] [-o FILE] script
```

Embeds every image file the script references into its `images` section (see [Embedded
images](scripts.md#embedded-images)), so it can be shared as a single file. Each image
parameter written as a path (`img/save button.png`, in `click`, `move`, `drag`,
`wait_image` or `image_visible`) becomes a reference to the embedded copy
(`"#save_button"`):

- names come from the file stems, made valid (`save button` gives `save_button`) and
  unique (`dialog`, `dialog_2`);
- identical files share one entry, also with an image the script already embeds;
- a path built with `${...}` is left as it is, with a warning;
- comments and formatting are kept, and no step id is added (the same structured editing
  as the engine's `edit_script`); the new section goes at the end of the file.

The script must be valid, and every file must be a PNG of at most 1 MiB (8 MiB for all
of a script's images together); otherwise nothing is written. `-o FILE` writes the result
to a file, which may be the script itself; without it the packed script goes to standard
output. A summary goes to standard error. It does not start the engine.

```console
$ j5 pack examples/smoke/pointer.yaml -o pointer_bundle.yaml
j5: embedded 1 image (#pattern), 5 image parameters rewritten; wrote pointer_bundle.yaml
```

Exit codes: 0 done, 1 invalid script or an image that cannot be embedded, 2 usage error.

## `j5 unpack`

```text
j5 unpack [-h] -d DIR [-f] script
```

The reverse of `j5 pack`: writes each embedded image to `DIR/img/<name>.png` and the
script to `DIR/<script file name>`, with every `"#name"` turned back into the path
`img/<name>.png` and the `images` section removed. When a step builds its image with
`${...}` (it may name an embedded image at run time), the section is kept and j5 says
so. A capture `scale` cannot be kept in a file: it is dropped with a warning. Existing
files are not overwritten unless `-f` / `--force` is given.

```console
$ j5 unpack pointer_bundle.yaml -d pointer/
j5: wrote 1 image to pointer/img and the script to pointer/pointer_bundle.yaml
```

Exit codes: 0 done, 1 invalid script, no embedded images or a file in the way, 2 usage
error.

## Authoring tools

Five commands help write scripts: they read the screen, send no input and need no run.
`capture`, `pick` and `find` print the YAML of a step, ready to paste into a script;
`--json` prints the engine's answer instead (see [Authoring
tools](contract.md#authoring-tools) in the contract). Notes and hints go to standard
error. Like image steps, they need the `vision` extra and a display; `windows`, `--window`
and the window an answer names need a backend that finds windows.

`capture` without `--bbox`, `pick` and `find --show` freeze the screen and show it
full-screen above every window, on every monitor: drag a rectangle (or click a point)
with the left button, with a magnifier next to the pointer for pixel accuracy. Escape or
the right button cancels, and the command exits with code 3. The overlay is drawn with
tkinter, which comes with Python on Windows; on Linux it may be a separate package
(`python3-tk` on Debian and Ubuntu). Only one overlay opens at a time, and never during a
run.

### `j5 windows`

```text
j5 windows [-h] [--json]
```

Lists the visible windows, topmost first: the client area of each (the window's content
without the title bar and borders, as `[x, y, width, height]` on the desktop) and its
title. `window: {title: ...}` in a script matches any part of the title.

```console
$ j5 windows
[0, 28, 1280, 772]       Untitled - Notes
[300, 200, 640, 480]     Save As
```

### `j5 screenshot`

```text
j5 screenshot [-h] [-o FILE] [--window TITLE] [--region X,Y,W,H] [--json]
```

Saves a PNG of the whole desktop, of a window's client area (`--window`) or of a region
(relative to the window when both are given) and prints its path. Without `-o` the file
goes to a new `j5-macro/screenshot-<time>.png` in the system's temporary directory; with
`-o` an existing file is replaced. For agents, this is how they look at the screen before
capturing anything.

```console
$ j5 screenshot --window Notes
/tmp/j5-macro/screenshot-20261013-101500-123456.png
j5: [0, 28, 1280, 772] of window 'Untitled - Notes' at [0, 0, 1280, 772], display scale 1
```

### `j5 capture`

```text
j5 capture [-h] (-o FILE | --embed SCRIPT --name NAME) [--window TITLE]
           [--bbox X,Y,W,H] [--padding PIXELS] [--transparent] [-f] [--json]
```

Captures a template. Without `--bbox` you drag the rectangle on the overlay; with it the
rectangle is taken as given (relative to `--window`'s client area when given), which is
what scripts and agents use. The template goes to a PNG file (`-o`) or into a script's
`images` section (`--embed SCRIPT --name NAME`, then used as `"#NAME"`; comments and
formatting of the script are kept). Existing files and entries are only replaced with
`-f`. It prints a `click` step on the template, with the window the rectangle lies in.

- `--padding PIXELS` grows the rectangle on every side.
- `--transparent` makes the pixels of the rectangle's border colour transparent, so an
  icon matches on any background (transparent pixels are ignored when matching).
- A capture of a single flat colour gets a warning: it would never match.
- **Display scale.** When the display scale (125 %, 150 %...) differs from the script's
  `capture_scale`, an embedded image records it as its `scale`, so the script finds it at
  any scale. A file cannot record it: j5 prints a hint when the scale is not 100 %.

```console
$ j5 capture --embed notes.yaml --name save_button
j5: drag a rectangle on the screen (Escape cancels)
j5: embedded [812, 40, 24, 24] as #save_button in notes.yaml
click: {image: "#save_button", window: {title: Untitled - Notes}}
$ j5 capture --bbox 812,12,24,24 --window Notes -o img/save.png
j5: saved [812, 40, 24, 24] to /home/me/scripts/img/save.png
click: {image: img/save.png, window: {title: Untitled - Notes}}
```

### `j5 pick`

```text
j5 pick [-h] [--window TITLE] [--json]
```

Click a point on the overlay; prints a `click` step at it, relative to the client area of
the window under it (or of `--window`), or on the desktop when it is in no window.

```console
$ j5 pick
j5: click a point on the screen (Escape cancels)
j5: (212, 176) on the desktop, (212, 148) in 'Untitled - Notes'
click: {window: {title: Untitled - Notes}, x: 212, y: 148}
```

### `j5 find`

```text
j5 find [-h] [--script SCRIPT] [--window TITLE] [--region X,Y,W,H]
        [--threshold T] [--scale S] [--display-scale SCALE] [--show]
        [--show-ms MS] [--json] IMAGE
```

Searches the screen once for IMAGE exactly as a step would, without running anything:
the same matcher, threshold, region, window and display scale adjustment. With `--script`,
IMAGE is relative to the script's directory and may be `"#name"`, an embedded image, and
the script's `capture_scale` applies; without it IMAGE is a file relative to the current
directory. When found, it prints a `click` step on it; either way the score, the place
and the scales go to standard error. `--show` highlights the best match on the screen
for `--show-ms` milliseconds (3000 by default; a key or a click closes it sooner): green
when found, red with the score when not.

```console
$ j5 find '#save_button' --script notes.yaml --window Notes
j5: #save_button found at [812, 40, 24, 24] ([812, 12, 24, 24] in 'Untitled - Notes'), score 0.998; scale 1 = display 1 / capture 1
click: {image: "#save_button", window: {title: Notes}}
```

Exit codes: 0 found, 1 not found (or an error), 2 usage error, 3 cancelled.

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

| Code | `run` | `validate` | `runs` | `pack`, `unpack` | Authoring tools | All commands |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | Completed | Valid (warnings allowed) | Done | Done | Done (`find`: found) | |
| 1 | The run failed, or a paused step kept failing with no terminal to ask | Invalid or unreadable | No saved run with that id | Invalid script, an image that cannot be embedded, nothing to unpack or a file in the way | The engine refused (no vision, a file in the way...); `find`: not found | The engine could not be started or died |
| 2 | The script could not be loaded or the run could not start | | Malformed run id | | | Usage error (bad options) |
| 3 | The run was stopped (emergency stop, corner failsafe, interference set to `stop`, `q` in the debugger) | | | | Cancelled with Escape | |
| 130 | Interrupted with Ctrl+C | | | | | Interrupted with Ctrl+C |

## Environment variables

| Variable | Read by | Meaning |
| --- | --- | --- |
| `J5_BACKEND` | Engine | Input backend: `auto` (default), `x11`, `windows`, `fake` (records input, touches nothing) or `none`. See [how-it-works.md](how-it-works.md#platforms-and-backends) |
| `J5_RUNS_DIR` | Engine, `j5 runs` | Runs directory, when `--runs-dir` is not given |
| `J5_STOP_KEY` | Engine | Emergency stop key when `j5 run --stop-key` (the engine's `--stop-key`) is not given, e.g. `ctrl+alt+f12` (default `ctrl+alt+shift+q`): modifiers and one key joined with `+` |
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
