# How j5 works

This page explains what happens when you run a script: how the engine runs it, how the
mouse and keyboard move like a person, how image search works, what keeps you safe, and
where runs are saved. It is written for people who use j5; the exhaustive protocol
details are in the [contract](contract.md), and the internals in
[architecture.md](architecture.md) and [technical-design.md](technical-design.md).

## What happens when you run a script

`j5 run script.yaml` does not run the script itself. It starts the **engine**, a separate
Python process (`python -m j5_macro.engine`), and talks to it with JSON messages: the CLI
sends commands (`load`, `run`, `stop`...) and the engine answers with events
(`step_started`, `step_finished`, `run_finished`...). The VS Code debugger and a future GUI
are clients of the same engine, so a script behaves the same whichever one starts it.

A run goes through these phases:

1. **Start.** The engine picks an input backend (X11 on Linux, see
   [Platforms](#platforms-and-backends)) and starts watching the emergency stop key.
2. **Load and validate.** The script is parsed and every step is checked against its
   type's parameters. Problems are reported as `path:line:` messages and nothing runs.
   Steps without an `id` get a generated one, with a warning.
3. **Params.** The values of the script's `params` are checked; required ones you left out
   are asked for when you run `j5` in a terminal.
4. **Run.** The engine runs the steps in order and emits an event for each thing it does.
5. **Finish.** The run ends as `completed`, `failed` or `stopped`, every held key and
   button is released, and the run is saved in the [run history](#run-history).

### Run states

A run is always in one of four states:

| State | Meaning |
| --- | --- |
| `running` | Steps are being run |
| `paused` | Waiting: at a breakpoint, a `pause:` step, after `--step`, after a failed step with `on_error: pause`, or after you moved the mouse |
| `stopping` | A stop was asked for; held keys and buttons are being released |
| `finished` | Over, with a status: `completed`, `failed` or `stopped` |

A normal run starts `running`; `j5 run --step` starts `paused` before the first step. Only a
stop (Ctrl+C, the emergency stop key, the corner failsafe, `q` in the debugger, or mouse
interference configured to stop) goes through `stopping`.

When a run pauses and you are not using the debugger, `j5 run` asks you to press Enter to
resume. When nobody can answer (standard input is not a terminal), it resumes on its own
and says so. See [debugging.md](debugging.md) for what you can do at a pause.

### Reading the output

Each line starts with the time since the run started. Steps inside a `loop` or `if` are
indented. This run of [`examples/hello.yaml`](../examples/hello.yaml) used the fake backend,
which records input instead of sending it:

```console
$ j5 run --seed 7 examples/hello.yaml
  0.001s  [info] emergency stop key: ctrl+alt+shift+q
run r-20261012-214813-6669  Hello j5  (mode normal, seed 7, backend fake)
  0.001s  ▶ start  log  {"message": "hello, need input!", "level": "info"}
  0.001s    [info] hello, need input!
  0.001s  ✓ start  1ms  {"level": "info", "message": "hello, need input!"}
  0.002s  ▶ open_menu  click  {"x": 120, "y": 40, "button": "left", "clicks": 1, "threshold": 0.9}
  0.591s  ✓ open_menu  589ms  {"x": 120, "y": 40, "button": "left", "clicks": 1}
  0.591s  ▶ retry_loop  loop  {"times": 3, "as": "item"}
  0.591s  ↻ retry_loop  iteration 1/3
  0.591s    ▶ count  set  {"name": "attempts", "expr": "attempts + 1"}
  0.592s      attempts = 1  (was 0)
  0.592s    ✓ count  0ms  {"name": "attempts", "value": 1}
  0.592s    ▶ check  if  {"when": "attempts == 2"}
  0.592s    ⑂ check  if attempts == 2 = false → else
  ...
  0.784s  ✓ retry_loop  193ms  {"iterations": 3}
  0.784s  ▶ type_name  type  {"text": "report_3.txt"}
  3.203s  ✓ type_name  2419ms  {"chars": 12}
  3.203s  ▶ save  key  {"press": "ctrl+s"}
  3.384s  ✓ save  182ms  {"press": "ctrl+s"}
✓ completed in 3.384s  (seed 7)
```

| Symbol | ASCII | Meaning |
| --- | --- | --- |
| `▶` | `>` | A step starts, with its parameters after variables were filled in |
| `✓` | `+` | A step finished, with its duration and result; on the last line, the run completed |
| `✗` | `x` | A step failed (with the error code and message); on the last line, the run failed |
| `↷` | `-` | A step was skipped |
| `↻` | `@` | A loop iteration starts (`iteration 2/3`) |
| `⑂` | `?` | An `if` was evaluated: the condition, its value and the branch taken (`→ then`) |
| `⏸` | `\|\|` | The run paused, with the reason: `step`, `breakpoint`, `user`, `error`, `interference` |
| `■` | `#` | The run is stopping, or was stopped (with the reason: `user` or `interference`) |

Other lines: `name = value  (was old)` is a variable change, `[info] message` is a log
(also the `log:` step), `breakpoint <id>` is a breakpoint being hit, `screenshot: ...` is
the capture of a failed image search, `released ctrl, a (aborted)` lists keys released
after an interrupted action, and `you moved the mouse: ...` reports interference.

The ASCII symbols are used when the output cannot encode the Unicode ones (for example a
Windows pipe in cp1252). Colours are used only on a terminal, and never when the
`NO_COLOR` environment variable is set. `j5 run --json` prints the raw events instead,
one JSON object per line (see [cli.md](cli.md#json-output)).

### Exit codes

| Code | `j5 run` |
| --- | --- |
| 0 | The run completed |
| 1 | The run failed, or the engine died |
| 2 | Usage error, or the script could not be loaded or the run could not start (invalid script, missing param, unknown `--break` id) |
| 3 | The run was stopped by the engine or the debugger: `q` in the debugger, the emergency stop key, the corner failsafe, interference set to `stop` |
| 130 | Interrupted with Ctrl+C: the first Ctrl+C stops the run and waits for it, the second quits at once |

The other commands' codes are in [cli.md](cli.md#exit-codes).

## Human-like mouse

Every mouse action is first **planned** and then **executed**. Planning is a pure function
of a seed: it computes timed points (where the cursor should be after so many
milliseconds) without touching the screen. Execution replays those points against a
monotonic clock, so a movement lasts what was planned even when the machine is busy.

The `strategy` chooses how a movement is planned:

| Strategy | Mouse |
| --- | --- |
| `human` (default) | A curved path with a human speed profile, landing somewhere inside the target |
| `linear` | A straight line at constant speed: 1200 px/s at `speed: 1.0`, at least 60 ms |
| `direct` | Jumps to the target at once |

With `human`, a movement is built in layers:

- **Path shape** (`path`). `windmouse` (default) simulates gravity pulling towards the
  target and a random wind pushing sideways, which gives slightly erratic, natural paths.
  `bezier` draws a smooth cubic curve whose control points are offset sideways by 5 to 25%
  of the distance (at most 200 px), sometimes bending both ways in an S. Both are
  implemented in j5 as pure seeded functions.
- **Duration (Fitts' law).** Far and small targets take longer:
  `MT = 20 ms + 125 ms × log2(D / W + 1)`, where `D` is the distance and `W` the target's
  smaller side (20 px when there is no target area). 500 px to a 20 px target takes about
  610 ms; 50 px about 245 ms. The result is divided by `speed` and varied slightly
  (log-normal noise, sigma 0.08).
- **Speed curve.** Speed follows a minimum-jerk bell: slow start, fast middle, slow
  landing, peaking at 42 to 50% of the movement time. A point is sent about every 8 ms.
- **Landing inside the target.** When the target is an area (an image match), the
  cursor lands at a Gaussian point around its centre, at least 2 px inside its border,
  instead of the exact centre every time. A `human` click whose pointer is already inside
  the target clicks there without moving, as a person would. With an `offset`, the mouse
  aims at that exact point.
- **Overshoot.** With probability `overshoot` (default 0.1), a movement longer than
  100 px aims 3 to 8% past the landing (at most 40 px), pauses 30 to 100 ms, and corrects.
- **Jitter.** A smoothed sideways tremor of about `jitter` pixels (default 1.0) that fades
  out at both ends of the movement.
- **Bounds.** Points stay inside the desktop and, except the landing, 3 px clear of its
  corners, so a planned point never triggers the corner failsafe.

Clicks, scrolls and drags are human too: a button stays down about 90 ms (40 to 200), the
clicks of a double click are 60 to 150 ms apart, scroll notches come about 70 ms apart
with occasional quick bursts, and a drag pauses 80 to 200 ms after pressing and before
releasing.

Mouse settings, set in the script's `input`, a step's `input`, or the run:

| Setting | Default | Effect |
| --- | --- | --- |
| `strategy` | `human` | `direct`, `linear` or `human` |
| `speed` | `1.0` | Speed multiplier for `linear` and `human` (2.0 is twice as fast) |
| `path` | `windmouse` | Path shape for `human`: `windmouse` or `bezier` |
| `overshoot` | `0.1` | Probability (0 to 1) of overshooting a movement over 100 px |
| `jitter` | `1.0` | Sideways tremor amplitude, in pixels |
| `interference` | `pause` | What to do when you move the mouse during a movement: `pause`, `stop` or `off` |
| `tolerance` | `20` | How far (pixels) the real pointer may be from where j5 put it before it counts as interference |

The script format is described in [scripts.md](scripts.md). For fast debugging,
`j5 run --fast` (or `"fast": true` in VS Code) switches both devices to `direct`.

## Human-like keyboard

The keyboard has the same three strategies. `direct` types the whole text at once;
`linear` presses a key every `60000 / (wpm × 5)` milliseconds and holds each one 30% of
that interval. `human`:

- **Rhythm.** Intervals between key presses are log-normal, so most are close to the
  average and a few are much longer. Their mean matches `wpm` (5 characters per word), and
  `variance` is the spread (log-normal sigma); a single interval stays between 0.35 and 3
  times the average.
- **Pauses.** On top of that: about 40 ms after a space, 180 ms after `. , ; : ! ?`, 350 ms
  after Enter or Tab (each varied between half and one and a half times), and in 5% of
  cases a 250 to 700 ms "thinking" pause between words.
- **Hold and rollover.** Each key stays down about 95 ms (50 to 180), independently of the
  interval, so when typing fast the next key goes down before the previous one is up. At
  most two keys are down at once, and Enter, Tab and Backspace never overlap another key.
- **Typos** (`typos: true`, off by default). About 3% of ASCII letters hit a neighbouring
  key on a QWERTY layout. The mistake is noticed at once or one or two characters later,
  then, after a 200 to 500 ms reaction, erased with Backspace and retyped. A test checks
  that the planned keys always produce the original text; the target application cannot
  be checked (autocomplete may interfere), which is why typos are off by default.
- **`variance: 0`** makes typing fully regular: no variation, no pauses.

| Setting | Default | Effect |
| --- | --- | --- |
| `strategy` | `human` | `direct`, `linear` or `human` |
| `wpm` | `60` | Words per minute |
| `variance` | `0.3` | Spread of the intervals; `0` is fully regular |
| `typos` | `false` | Make and correct occasional typos |

**Any character, any layout.** `type` presses *characters*, not keys: `ñ`, `€`, `日` or `!`
are typed whatever your keyboard layout is. On X11 a character that no key types directly
is typed by mapping a spare keycode to it for a moment and restoring the mapping
afterwards. `key` steps (`ctrl+s`, `alt+f4`) press *physical* keys instead, named as on a
US layout. In `type`, `\n` presses Enter, `\t` Tab and `\b` Backspace.

## Reproducibility

Every run has a **seed**: the one you give with `--seed`, or a random one the engine
chooses. It is shown in the run header and on the last line (`(seed 7)`), and saved with
the run.

From the run seed, each step attempt gets its own random generator, seeded from the run
seed, the step id, the current loop iterations and the attempt number. So:

- Every iteration of a `loop` moves and types differently, and so does every retry.
- The same seed gives the same plans: in two runs of `examples/hello.yaml` with
  `--seed 7`, the click was planned with the same plan seed
  (`6991577597064561139`) and the `sleep: 100ms±20%` of the first iteration waited exactly
  `0.09500498990976972` s both times.
- A plan also depends on where the cursor starts and where the target is. With the same
  seed, the same click took 509 ms starting from (0, 0) on the fake backend and 905 ms
  starting from the screen centre on X11. A whole run repeats exactly only if the screen
  behaves the same.

Each `input_planned` event carries the plan's own seed (see `j5 run --json`).

## Image search

Steps can look for a picture on the screen: `wait_image`, `click`/`move`/`drag` with an
`image` target, and the `{image_visible: ...}` condition of `if` and `while`. Image search
needs the `vision` extra (`pip install j5-macro[vision]`).

- **Templates.** A template is a PNG (or other image) file, relative to the script's
  folder. Crop it tightly around something with texture or an edge: a template of a single
  flat colour never matches. Transparent pixels of a PNG (alpha below 128) are ignored, so
  a cut-out icon matches on any background.
- **Score and threshold.** j5 compares the template with every position on the screen
  using normalised cross-correlation and reports the **best score**, from 0 to 1. The image
  is found when the best score reaches `threshold` (0.9 by default). The score ignores
  uniform brightness and contrast changes, but not changes in the pattern itself.
- **Where to search.** The whole desktop (all screens) by default; `window: {title: ...}`
  searches the first visible window whose title contains that text; `region: [x, y, w, h]`
  limits the search to a rectangle (relative to the window when one is given). `scale`
  resizes the template, for templates captured at another display scaling.
- **Waiting.** `wait_image` retries every 0.25 s until it finds the image or its `timeout`
  (10 s by default) runs out; pausing the run pauses the wait. `click`, `move` and `drag`
  search once, or wait up to the step's `timeout` when you set one. An action aims at the
  centre of the match (plus `offset`), and a `human` movement lands inside it.
- **Captures on failure.** When a search fails, the screen of its last attempt is saved in
  the run's folder, with the best match outlined in red:

```console
$ j5 run search.yaml
run r-20261012-214849-bba4  Search  (mode normal, seed 1659557458, backend x11)
  0.001s  ▶ find_button  wait_image  {"image": "img/button.png", "timeout": "1s", "threshold": 0.9}
  1.275s  ✗ find_button  [image_not_found] img/button.png not found after 4 attempt(s) in 1s (best score 0.000, threshold 0.9)
  1.275s    screenshot: captures/0001-find_button-4.png
✗ failed after 1.275s  (seed 1659557458): [image_not_found] img/button.png not found after 4 attempt(s) in 1s (best score 0.000, threshold 0.9)
```

`j5 runs show <run_id>` prints where that folder is. A low best score usually means the
template does not look like the screen (other theme, scaling or state); a score just
below the threshold means you can lower `threshold` a little. Every attempt's score is in
the `image_search` events (`j5 run --json`).

In a dry run (`--dry-run`) each search runs once and the run moves on, found or not, and
nothing is clicked.

## Safety

j5 moves your real mouse and presses your real keys, so there are several ways to take
control back:

- **Emergency stop key.** `ctrl+alt+shift+q` stops the active run from any application.
  Change it with the `J5_STOP_KEY` environment variable (for example
  `J5_STOP_KEY=ctrl+alt+f12`). The engine logs the key it watches when it starts, or a
  warning when it cannot watch one (no backend, or another program owns the combination).
- **Corner failsafe.** While j5 is moving the pointer, pushing it into a corner of the
  desktop (within 2 pixels) stops the run with a `warning` log `emergency stop: cursor
  moved into a screen corner`. It works even where global hotkeys do not.
- **Interference detection.** Before each point of a movement, j5 reads where the pointer
  really is. If it is more than `tolerance` pixels (20) from where j5 put it, you moved
  the mouse: the movement stops, what the action held is released, and the run pauses
  (`interference: pause`, the default; resume to retry the step, or skip it), stops
  (`stop`), or j5 ignores it (`off`). Only the mouse is watched: on X11, injected keys are
  hard to tell apart from yours.
- **Ctrl+C** in the terminal stops the run; a second Ctrl+C quits without waiting.
- **Held keys are always released.** j5 tracks every key and button it holds. Stopping a
  run, a failed step, interference and the end of the run all release them. If the
  engine process dies, `j5` releases whatever it last reported as held, from its own
  process, and says so (`j5: released what the engine held: ...`), so a crash never
  leaves Shift or Ctrl stuck.

## Params and secrets

`params` declares the values a script takes from outside. You give them on the command
line (`--param`, `--param-file`, `--param-env`, `--params-file`; see
[cli.md](cli.md#script-params)), in a VS Code launch configuration, or at the prompt that
`j5 run` shows in a terminal for required params you left out. Values arrive as text and
are converted for `integer`, `number` and `boolean` params; a string param is used exactly
as given, so `#`, quotes, leading zeros and line breaks survive. When the run starts,
params become variables, used as `${name}`.

A param with `secret: true` is **masked**: every occurrence of its value is replaced with
`***` in the output, the events, the run history, the debugger's variables and
`p`/`evaluate` answers, whatever carried it (a step parameter, a log message, an error, a
variable built by concatenation):

```console
$ FORM_PASSWORD=hunter2 j5 run login.yaml --fast --param 'user=ACME #42' --param-env password=FORM_PASSWORD
  0.004s  ▶ greet  log  {"message": "logging in as ACME #42 with ***", "level": "info"}
  0.006s    [info] logging in as ACME #42 with ***
  ...
  0.014s  ▶ type_password  type  {"text": "***"}
  0.017s  ✓ type_password  3ms  {"chars": 7}
```

The limits of masking:

- Only the exact text is masked. A value transformed by an expression
  (`upper(password)`) is not recognised, so use secrets as they are.
- `type` reports how many characters it typed, so a secret's length is visible.
- A number equal to a secret's text is masked too, except in fields that only say where or
  how long (`path`, `iterations`, `line`, `index`, `total`, `duration_ms`).
- Masking protects logs and history, not the screen: the application shows what is typed
  unless its field hides it.
- A secret cannot have a `default` (it must not live in the script file). Do not pass it
  with `--param`: `j5` warns that it ends up in your shell history and the process list.

## Run history

Every run is saved, with all its events and captures, in the runs directory:

| Platform | Default runs directory |
| --- | --- |
| Linux | `$XDG_STATE_HOME/j5-macro/runs` when `XDG_STATE_HOME` is set (absolute), else `~/.local/state/j5-macro/runs` |
| Windows | `%LOCALAPPDATA%\j5-macro\runs` |
| macOS | `~/Library/Application Support/j5-macro/runs` |

`--runs-dir DIR` or the `J5_RUNS_DIR` environment variable choose another directory. Each
run has a folder named after its id (`r-YYYYMMDD-HHMMSS-xxxx`):

| File | Content |
| --- | --- |
| `run.json` | Script name and path, mode, seed, start and end times, status, reason, duration and error |
| `events.jsonl` | Every event of the run, one JSON line each, written as it happens |
| `captures/` | Screen captures of failed image searches, named `<n>-<step_id>-<attempt>.png` |

When a run starts, older runs are deleted so that the **newest 20** remain. Saving never
affects a run: if the disk fails, the engine only logs a warning. List, replay and delete
runs with `j5 runs` (see [cli.md](cli.md#j5-runs)).

## Scripts that change while open

The engine watches every script it has loaded from a file, about twice a second. When the
content changes, a GUI or editor connected to the engine is told (`script_changed`) and the
new version is validated. A run always keeps the version it started with; changes apply
to the next run. Edits made through the engine (by a GUI) keep the file's comments and
formatting and write every step's `id`, so breakpoints and history survive reordering.
`j5 run` loads the script once per run, so it always runs the file as it is when you start
it. The details are in the [contract](contract.md#script-management).

## Platforms and backends

The **backend** is the small, platform-specific part that sends input and reads the
screen; everything human-like lives above it and is identical on every platform.

| Platform | Status |
| --- | --- |
| Linux with an X11 session | Supported (tested on XFCE). Needs `pip install j5-macro[x11]` |
| Linux with Wayland | No backend yet: Wayland has no common way for an application to inject input, read the pointer position or watch global hotkeys (it depends on the compositor), and the X11 backend through XWayland only reaches X11 applications. Log in with an X11 session (for example Xfce, or "Ubuntu on Xorg"). An experimental Wayland backend may come after v1 |
| Windows 10 and 11 | Supported. Coordinates are physical pixels on every monitor, whatever the display scale: the engine declares per-monitor DPI awareness. Input to a window of a program running as administrator needs j5 to run as administrator too; otherwise Windows refuses it (`input_denied`) |
| macOS | Not supported |

The engine chooses the backend when it starts and shows it in the run header
(`backend x11`). The `J5_BACKEND` environment variable overrides the choice:

| `J5_BACKEND` | Effect |
| --- | --- |
| `auto` (default) | Windows on Windows; X11 when `DISPLAY` is set and the session is not Wayland |
| `x11` | X11 even in a Wayland session (it only reaches XWayland applications) |
| `windows` | The Windows backend (only on Windows) |
| `fake` | Records input and touches nothing, for tests and demos |
| `none` | No backend |

**Without a backend**, dry runs still work, and so do steps that send no input; input
steps fail with `capability_missing`, giving the reason:

```console
$ J5_BACKEND=none j5 run examples/hello.yaml
  0.002s  [warning] no input backend: disabled by J5_BACKEND=none; input steps fail with capability_missing (dry runs still work)
run r-20261012-214928-c01c  Hello j5  (mode normal, seed 99240906, backend none)
  ...
  0.003s  ✗ open_menu  [capability_missing] step 'open_menu' needs an input backend: disabled by J5_BACKEND=none
✗ failed after 0.002s  (seed 99240906): [capability_missing] step 'open_menu' needs an input backend: disabled by J5_BACKEND=none
```

A **dry run** (`j5 run --dry-run`) sends no input at all: input steps describe what they
would do (`would click left at (120, 40)`), image searches run once, and variables, loops,
branches, logs and sleeps run as usual.
