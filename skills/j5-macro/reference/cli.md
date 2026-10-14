# j5 command line: what an agent needs

Full reference: `docs/cli.md` (https://github.com/esgabo/j5-macro/blob/main/docs/cli.md).
Every command has `-h`. Messages from `j5` itself go to standard error; with `--json`,
standard output is machine-readable only. Close standard input (`< /dev/null`) so nothing
ever waits for a prompt.

## Authoring tools (read the screen, send no input)

They need the `vision` extra and a display; `windows` and `--window` need a backend that
finds windows (X11 or Windows).

| Command | Does | Default output | `--json` |
| --- | --- | --- | --- |
| `j5 windows` | Visible windows, topmost first | `[x, y, w, h]  title` per line (client area on the desktop) | `{"windows": [{"title", "bbox"}]}` |
| `j5 screenshot [-o FILE] [--window T] [--region X,Y,W,H]` | PNG of the desktop, a window's client area or a region (relative to the window when both) | the PNG's path; a note on stderr | `{path, bbox, display_scale, window?, window_bbox?}` |
| `j5 capture --bbox X,Y,W,H [--window T] (-o FILE \| --embed SCRIPT --name NAME)` | Save a template; `--padding N`, `--transparent`, `-f` (replace) | a `click:` step using it | `{path \| image_name+version, bbox, window?, window_bbox?, display_scale, scale?, warnings}` |
| `j5 find IMAGE [--script S] [--window T] [--region R] [--threshold T] [--scale S] [--display-scale D]` | One search, exactly as a step | found: a `click:` step; score and place on stderr | `{found, score, bbox, center, window?, window_bbox?, region, scale, display_scale, capture_scale, elapsed_ms}` |
| `j5 pick [--window T]` | Interactive only (a human clicks a point) | a `click:` step | the point and its window |

- `--bbox` with `--window` is relative to the window's client area, the same pixels as
  `j5 screenshot --window`.
- `capture --embed SCRIPT` needs an existing, valid script (minimal:
  `version: 1`, `name: x`, `steps: []`). It keeps the file's comments and formatting.
  Without `-f` an existing name or file is refused.
- `find IMAGE`: with `--script`, `IMAGE` may be `'#name'` (quote it in the shell) or a
  path relative to the script; the script's `capture_scale` applies. Without it, a file
  relative to the current directory.
- `capture` without `--bbox`, `pick` and `find --show` open a full-screen overlay for a
  human (needs tkinter). Escape cancels: exit 3.

Exit codes: 0 done (`find`: found), 1 failure (`find`: not found, or the engine refused:
no vision, window not found, file in the way...), 2 usage error, 3 cancelled.

## Checking and running

```text
j5 validate [--json] SCRIPT
j5 run [--dry-run | --step] [--break STEP_ID] [--json] [--fast] [--seed N]
       [--allow-exec] [--display-scale SCALE]
       [--param NAME=VALUE] [--param-env NAME=VAR] [--param-file NAME=PATH]
       [--params-file FILE] [--var NAME=VALUE]
       [--captures {on_failure,all}] [--stop-key COMBO] [--runs-dir DIR] SCRIPT
```

| Option | Use |
| --- | --- |
| `--dry-run` | No input; each image search once; `exec` starts nothing. Always first |
| `--json` | One event per line on stdout |
| `--fast` | Instant mouse and keyboard (no human-like movement) |
| `--seed N` | Repeat the same human-like input |
| `--allow-exec` | Let `exec` steps run programs. **Only with the user's consent** |
| `--display-scale 1.5` | Override the detected display scale for the run |
| `--param-env NAME=VAR` | Params from the environment: the way to pass secrets |
| `--params-file values.yaml` | Many params at once (a mapping of scalars) |
| `--var NAME=VALUE` | Override a `vars` entry (debugging) |
| `--captures all` | Keep a capture of every image search attempt, not only failures |
| `--step`, `--break ID` | Debugger; reads commands from stdin (`printf 'n\nvars\nc\n' \| j5 run --step s.yaml`) |

`j5 validate --json` prints `{path, valid, script_id, errors, warnings, params, images}`;
each problem has `code`, `message`, `step_id`, `line`, `details`. Exit 0 valid (warnings
allowed), 1 invalid.

| `j5 run` exit | Meaning |
| --- | --- |
| 0 | completed |
| 1 | a step failed (or a paused step kept failing without a terminal) |
| 2 | invalid script, missing/wrong params, bad options: nothing ran |
| 3 | stopped: emergency stop key, corner failsafe, interference `stop` |
| 130 | Ctrl+C |

## JSON events (`j5 run --json`)

Each line: `{"v":1, "kind":"event", "type":..., "run_id":..., "seq":..., "ts":...,
"t_ms":..., "payload":{...}}`. Ignore types and fields you do not know.

| Event | Payload highlights |
| --- | --- |
| `run_started` | `run_id`, `seed`, `backend`, `mode`, `display_scale`, `capture_scale` |
| `step_started` | `step_id`, `type`, `line`, `params` (resolved; secrets `***`) |
| `step_finished` | `step_id`, `duration_ms`, `result` |
| `step_failed` | `step_id`, `error: {code, message, line, details}`, `screenshot?` |
| `image_search` | `template`, `found`, `best_score`, `bbox`, `region`, `scale`, `display_scale`, `capture_scale`, `attempt`, `screenshot?` |
| `variable_changed` | `name`, `old`, `new` (from `set`, `as`, loop items) |
| `log` | `level`, `message` (script `log:` steps and engine messages) |
| `user_interference` | the user moved the mouse during a movement |
| `run_finished` | `status` (`completed`/`failed`/`stopped`), `reason?`, `duration_ms`, `error?` |

```console
$ j5 run --json s.yaml < /dev/null | grep -E '"type":"(step_failed|run_finished)"'
```

Error codes: `image_not_found` (`details.best_score`, `threshold`, `attempts`),
`window_not_found` (`details.title`), `template_missing`, `expression_error`,
`validation_failed`, `capability_missing` (no backend, no vision, `exec` not allowed,
no tkinter), `exec_failed` (`details.reason`: `start`, `timeout`, `exit_code`),
`input_denied` (Windows: an elevated target window).

## Run history and failure captures

```text
j5 runs [-n N] [--json]           # newest first
j5 runs show [--json] RUN_ID      # details, capture paths, events
j5 runs delete RUN_ID...
```

Runs live in `~/.local/state/j5-macro/runs/<run_id>/` on Linux
(`%LOCALAPPDATA%\j5-macro\runs` on Windows; `--runs-dir`/`J5_RUNS_DIR` override):
`events.jsonl`, `run.json`, and `captures/` with a PNG of each failed image search, the
best candidate outlined in red. A `screenshot` field in an event is relative to that
directory. The newest 20 runs are kept.

## Scripts as files

```text
j5 pack SCRIPT [-o FILE]          # embed referenced image files; -o SCRIPT rewrites in place
j5 unpack SCRIPT -d DIR [-f]      # write embedded images to DIR/img/ and the script to DIR/
j5 steps [--json]                 # step types and parameters of this installation
j5 schema [-o FILE]               # JSON Schema of scripts
```

## Environment

`J5_BACKEND` (`auto`, `x11`, `windows`, `fake` records input and touches nothing,
`none`), `J5_STOP_KEY` (default `ctrl+alt+shift+q`), `J5_RUNS_DIR`, `NO_COLOR`.
