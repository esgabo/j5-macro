# Technical design

This page describes how the engine works inside and why it is built that way. It assumes
you have read [architecture.md](architecture.md). For the exact wire format of every
command and event, see [contract.md](contract.md); this page links to it instead of
repeating its tables.

## Script loading

**Round-trip YAML.** Scripts are parsed with `ruamel.yaml` in round-trip mode (`typ="rt"`),
not with a plain YAML loader, for two reasons: the round-trip nodes remember the line of
every key and list item, which gives every step a 1-based `line` for events, error
messages and editor breakpoints; and the same library can write a script back without
losing its comments (see "Structured editing" below). The loader
([script/loader.py](../src/j5_macro/script/loader.py)) converts the round-trip containers
to plain Python values before they reach the model, so nothing downstream depends on
ruamel types.

**Collect every problem.** The loader does not stop at the first error. It walks the
whole document, records problems per step and returns them all, sorted by line, in a
`LoadResult`; `load` answers with the first problem's code and lists all of them in
`details.errors`. Unknown top-level keys and generated ids are warnings, not errors.

**The model.** A loaded script is an immutable
[`Script`](../src/j5_macro/script/model.py) of `Step`s. Each step has its `id`, `type`,
defaulted `params`, `path`, `line`, the common fields (`breakpoint`, `on_error`,
`timeout` already parsed to seconds, `input`, and `store_as` for `as`) and its child
`blocks`. `path` alternates
list positions and block slots (`(3, 1, 0)` is the first step of the `else` block of the
fourth top-level step), so it is a static address of the step in the tree; loop
iterations are runtime data and travel separately as `iterations`.

**Stable ids and the generation rule.** Breakpoints, run history and editing all address
steps by id, so ids must not change when unrelated steps change. Explicit ids are taken
first and checked for duplicates. Then, in document order, the n-th step of each type
(counting steps that have explicit ids too) gets `<type>_n` if it has no id, with a
`_2`, `_3`... suffix on a clash. Because the counter includes explicit ids, adding an id
to one step never renames the generated ids of the others. Each generated id is reported
as a warning suggesting an explicit one, and `edit_script` writes generated ids into the
file so they become permanent.

**The step registry and validation.** The loader knows nothing about concrete step
types: it receives a `StepLookup` (name to `StepSpec`) and an optional cross-field check
function, both provided by the [`StepRegistry`](../src/j5_macro/steps/registry.py). A
step is a mapping with exactly one key that is not a common field; that key is the type.
Its value is a parameter mapping, a scalar for the type's shorthand parameter
(`sleep: 2s`), or nothing. `validate_params` checks each parameter against its declared
type (`string`, `integer`, `number`, `boolean`, `duration`, `expression`, `condition`,
`any`, `object`, `list`), fills defaults and rejects unknown parameters; the cross-field
check (for example "exactly one of `times`, `while`, `for_each`") runs only when that
succeeds. A string containing `${` passes the type check for any scalar type, since its
type is only known after interpolation; it is checked again when the step runs. The
same registry also generates the `list_step_types` answer and the editor JSON Schema
([steps/schema.py](../src/j5_macro/steps/schema.py)).

**Params and vars.** `vars` are the script's own initial variables; `params` declare
values the script takes from outside, with an optional `type`, `description`, `default`
and `secret`. Declarations are validated at load time (a param cannot also be a var, a
secret cannot have a default, a default must already have the declared type). Values
arrive with `run` and are resolved by `resolve` in
[script/params.py](../src/j5_macro/script/params.py): unknown names, then missing
required params (all of them listed), then type conversion. Conversion is strict and
predictable: a string param is taken verbatim, and a string is converted to another type
only when it is exactly an integer, a finite number or `true`/`false`. The result is one
flat variable map: script `vars`, the run's `vars` overrides, then params.

## The interpreter

### An explicit program counter

The interpreter ([runtime/machine.py](../src/j5_macro/runtime/machine.py)) does not walk
the step tree recursively. It keeps a stack of [`Frame`](../src/j5_macro/runtime/frames.py)s,
each a step list with its own `index`; the root frame holds the top-level steps. The main
loop looks at the top frame:

- if it has a step left, the step passes a **checkpoint** and runs. An action step
  advances the index; an `if` or `loop` pushes a frame for the block it enters;
- if the frame is exhausted, a loop frame starts its next iteration, or the frame is
  popped and its owner step finishes (`step_finished` with `{branch}` or
  `{iterations}`).

The reason is debugging. With recursion, the position of the run is spread over the
Python call stack, and stopping "after this block" or "at the next step, wherever it is"
requires threading flags through every level. With an explicit stack the whole position
is data: the current step, the depth (`len(stack)`) and the loop iterations
(`iterations(stack)`, the index of each enclosing loop, outermost first). That makes
`get_state` a simple snapshot and stepping a comparison of depths.

### Checkpoints and stepping

Every step passes one checkpoint before it runs. In priority order, the run pauses there
when: it is the first step of a `step`-mode run (reason `step`); the step is a breakpoint,
from the script's `break: true` or the client's `set_breakpoints` (`breakpoint_hit`, then
reason `breakpoint`); a `pause` was requested (reason `user`); or a stepping target is
active and the step's depth is at most the target (reason `step`).

While paused, the worker blocks on the control condition variable until a command
arrives. The command sets the stepping target, measured in frame depths:

| Command | Target | Effect |
| --- | --- | --- |
| `resume` | none | Run until a breakpoint, a pause or the end |
| `step_over` | current depth | Next checkpoint at the same depth or shallower, so a whole `loop` or `if` runs |
| `step_into` | unlimited | The very next checkpoint, entering blocks |
| `step_out` | current depth minus 1 | Next checkpoint shallower than the current one; at the top level, the end |

A checkpoint is passed once per visit, so after resuming from a breakpoint the step runs
without hitting it again; inside a loop it fires on every iteration. The `pause:` step
asks for a pause after it finishes (reason `breakpoint`), like a breakpoint written in
the script. Time spent paused is accumulated and subtracted from `duration_ms`.

### Pause at the next atomic action, stop immediately

`pause` only sets a flag. The worker honours it at the next checkpoint or inside an
interruptible wait (`sleep`, and the intervals between `wait_image` attempts). An input
action (a whole movement, a click, a `type` text, a key combination) is atomic for
`pause`: the input masters wait through an `AbortSignal` whose `sleep` ignores pause
requests. This guarantees that a pause never leaves the cursor half-way or a modifier
down.

Waits are interruptible with remaining time: `RunControl.sleep(seconds)` returns the time
left when a pause is requested, the machine pauses at the current step (reason `user`),
and after the next command the wait continues with what was left. A `wait_image` timeout
counts only unpaused time.

`stop` records a reason and notifies the condition variable. It is raised as
`RunStopped` on the worker at the next `check_stop`, inside any wait, while paused, or
between two primitives of an input action, where the master first releases what the
action pressed (`keys_released`, reason `aborted`). `RunStopped` derives from
`BaseException` so a step executor catching `Exception` cannot swallow it. The interpreter
then emits `state_changed` `stopping`, releases everything still held and returns a
`stopped` outcome.

### Errors and attempts

Every step body runs inside `_guarded`, which applies `on_error`:

- `stop` (the default): the error ends the run (`status: failed`);
- `continue`: `step_failed` and a warning `log`, then the next step;
- `retry`: up to 3 attempts in total, each failure emitting `step_failed` with
  `details.attempt`, then like `stop`;
- `pause`: `step_failed`, then a pause with reason `error`; `step_over` skips the step
  (`step_skipped`), any other command retries it.

The attempt number matters beyond the error report: it is part of the step's random
sub-seed, so a retry moves the mouse differently. Two exceptions bypass `on_error`.
`EmergencyStop` (the corner failsafe) stops the run with reason `user`.
`UserInterference` either stops the run with reason `interference` or pauses it, as the
profile's `interference` setting says; a resumed interfered step is a new attempt and
emits neither `step_finished` nor `step_failed` for the interrupted one.

### Dry runs

In `dry` mode the run takes the same path through the interpreter, so variables, loops,
branches, logs and sleeps behave as usual. Input executors check `ctx.dry_run` and return
a description with `dry_run: true` instead of acting; as a safeguard the run's input
refuses to create a session in a dry run. Image searches make exactly one attempt and
report it, since without input the screen never changes and a wait would always fail.
Searches that cannot be made (no vision, no window) become warnings, and image
conditions are then false. A `while` loop is capped at 1000 iterations with a warning.

## Expressions

Conditions, `set` with `expr`, `for_each` and `${}` interpolation all use a small
expression language evaluated by [expr/evaluator.py](../src/j5_macro/expr/evaluator.py).

**A whitelisting AST evaluator.** The text is parsed with `ast.parse(mode="eval")` and
evaluated by walking the tree in j5 code; Python's `eval` is never called. Only listed
node types are accepted: constants, names (with `true`, `false` and `null` as aliases),
lists, tuples, dicts, subscripts, attributes (`label.bbox`, which only reads a
mapping's key: no Python attribute is ever looked up), unary and binary operators
(`+ - * / // %`),
`and`/`or`/`not`, chained comparisons including `in`, `a if c else b`, and calls to
whitelisted functions (`len`, `str`, `int`, `float`, `bool`, `round`, `min`, `max`,
`abs`, `lower`, `upper`, `strip`, `contains`, `startswith`, `endswith`). Method calls,
attributes of anything but a mapping, `**`, bitwise operators, lambdas, comprehensions, f-strings, slices, `:=` and
starred expressions are rejected with a specific message. Operand types are checked too,
so an expression cannot reach Python internals through an object's methods.

**Caps.** Scripts come from files people share, so evaluation is bounded:

| Limit | Value |
| --- | --- |
| Expression length | 2000 characters |
| Syntax tree depth | 100 |
| Syntax tree nodes | 1000 |
| Size of a built value | 100 000 characters or items, counted recursively |
| Integer magnitude | 2^63 - 1 |

Compiled expressions are cached (LRU of 512), which matters for loop conditions
evaluated on every iteration.

**Interpolation.** String parameters may contain `${expression}`
([expr/interpolation.py](../src/j5_macro/expr/interpolation.py)). A string that is
exactly one `${...}`, with no other character (not even whitespace), evaluates to the raw value and keeps its type, so
`x: "${pos_x}"` is an integer; otherwise each slot is rendered as text (`null` as empty,
booleans as `true`/`false`). `$${` is a literal `${`. Interpolation recurses into lists
and mapping values. Parameters of type `expression` or `condition` are never
interpolated, because the interpreter or the executor evaluates them. After
interpolation each parameter is type-checked again and the cross-field check re-runs,
so a variable holding the wrong type fails with `validation_failed` naming the parameter.
Errors carry the expression and, when known, the column inside the original template.
## Windows backend

[backends/windows/backend.py](../src/j5_macro/backends/windows/backend.py) injects input
with `SendInput` through ctypes; it needs no package beyond Python. Every system call
goes through a small `Win32Api` interface
([win32.py](../src/j5_macro/backends/windows/win32.py)): `User32` implements it with
ctypes, and the tests replace it with a fake, so the backend's logic is tested on every
platform. The `INPUT` structures use fixed-size fields, so they have their Windows layout
(40 bytes on 64-bit) everywhere, and a test checks it.

**DPI awareness.** A process that does not declare DPI awareness sees a virtualized,
scaled desktop on a scaled display: coordinates are rounded and captures are resampled.
The engine calls `declare_dpi_awareness()`
([dpi.py](../src/j5_macro/backends/windows/dpi.py)) first thing at startup, before any
window, pointer or capture call: per-monitor v2 (`SetProcessDpiAwarenessContext`), else
per-monitor (`SetProcessDpiAwareness`, Windows 8.1), else system awareness. Every
coordinate, window rectangle and mss capture is then in physical pixels.

**Pointer.** Moves are absolute `MOUSEINPUT` events over the virtual desktop
(`MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK`), so they span every monitor, accept
negative coordinates, and skip pointer acceleration. Windows maps a normalized
coordinate `n` (0 to 65535) to pixel `origin + n * size // 65536`; the backend sends
`ceil(offset * 65536 / size)`, the smallest `n` that lands on the wanted pixel, and a test
checks every pixel of several desktop sizes. `move_rel` reads the cursor and moves
absolutely too: relative events go through acceleration and would land elsewhere. The
wheel sends one `WHEEL_DELTA` (120) event per notch, negative for down; horizontal
notches use `MOUSEEVENTF_HWHEEL`.

**Keys and characters.** Physical keys are virtual keys with their scan code (from
`MapVirtualKeyW`; some applications read scan codes) and `KEYEVENTF_EXTENDEDKEY` for the
keys that need it (arrows, Home, End, Insert, Delete, the Windows and menu keys), which
otherwise arrive as their number pad twins. An ASCII key name is the key that types that
character in the active layout (`VkKeyScanW`), else the key of the US layout. Characters
are `KEYEVENTF_UNICODE` packets, one per UTF-16 code unit (a surrogate pair for an emoji),
so any character is typed without dead keys or layout lookups; `\n`, `\t` and `\b` press
Enter, Tab and Backspace instead. `close()` releases every held character, key and
button, newest first.

**Window lookup.** `find_window(title)` walks `EnumWindows` (top-level windows, topmost
first) and returns the client area (`GetClientRect` plus `ClientToScreen`) of the first
window whose title contains `title` and that is visible, not minimized and not cloaked
(`DWMWA_CLOAKED`: suspended UWP apps and windows on other virtual desktops are visible to
`IsWindowVisible` but not on screen).

**Hotkeys.** `RegisterHotKey` without a window posts `WM_HOTKEY` to the queue of the
registering thread, so each watcher owns a thread that registers the combination with
`MOD_NOREPEAT` and runs `GetMessageW`; stopping posts `WM_QUIT` to it. A registration
that fails means another program owns the combination: `HotkeyConflict`. Windows
ignores the lock keys when matching hotkeys, so no variants are needed.

**Refused input.** If `SendInput` sends fewer events than asked, Windows blocked them
(UIPI: the target belongs to an elevated program, or the secure desktop is showing). The
backend raises `InputDenied`, a `PermissionError`, which the interpreter reports as
`input_denied`.


## Input

### Plan, execute, close

Every input action follows the same three phases in
[input/session.py](../src/j5_macro/input/session.py):

1. **Plan.** A pure function of the start position, the target and a seeded generator
   returns a plan: timed points (`t_ms`, `x`, `y`) for a movement, or timed key, button
   or scroll actions. Planners live in [input/planning.py](../src/j5_macro/input/planning.py)
   (`direct`, `linear`), [input/human_mouse.py](../src/j5_macro/input/human_mouse.py) and
   [input/human_keys.py](../src/j5_macro/input/human_keys.py). `input_planned` is emitted
   with the plan's seed and a summary.
2. **Execute.** The plan is replayed against a monotonic clock. Each action is sent when
   `start + t_ms` is reached: the session sleeps until that absolute deadline instead of
   sleeping fixed intervals, so a late point is sent at once and the following ones keep
   their planned times. Timing errors never accumulate, and the action lasts what was
   planned.
3. **Close.** `input_executed` reports the actual duration and whether the action was
   aborted.

Separating planning from execution is what makes the human-like strategies testable
without a display and without sleeping.

### Held state and release

The session keeps a list of held tokens in press order, each with its release function:
physical keys by name (`ctrl`), layout-independent characters as `char:<c>`, buttons as
`button:<name>`. Every change emits `input_state`. Each action also tracks what it
pressed itself; if the action raises for any reason (stop, failsafe, interference, a
backend error), exactly those tokens are released, newest first, and `keys_released`
says why (`aborted`, `failsafe` or `interference`). When the run ends, whatever is still
held is released with reason `run_ended`. The client keeps the last `input_state` so it
can release the same tokens with its own backend if the engine process dies.

### Seeds and reproducibility

A run has one seed (given with `run`, else a random 31-bit one, reported in
`run_started`). Each step attempt gets its own generator seeded with a sub-seed from
[runtime/seeding.py](../src/j5_macro/runtime/seeding.py): the first 64 bits of the
SHA-256 of the canonical JSON `[seed, step_id, iterations, attempt]`. SHA-256 rather
than Python's `hash()` keeps sub-seeds stable across processes and Python versions;
including the iterations and the attempt makes every loop iteration and every retry
move differently, while keeping each one reproducible.

Each planner draws a 63-bit seed from the step's generator and plans with a fresh
generator seeded with it, and reports that seed in `input_planned`, so replanning with
that seed alone gives the same plan. The caller's generator advances by the same amount
whatever the strategy, and the session plans a movement even when it then decides not to
move (pointer already on the target), so later random choices of the step do not depend
on where the pointer happened to be. A whole run with the same seed sends the same input
as long as the screen behaves the same; a different start position or image match gives
a different plan.

### The human mouse

A human movement is a path shape for the geometry plus a shared timing layer:

- **Landing.** Without a target area the landing is the requested point. With one (an
  image match), it is a Gaussian point around the aim with sigma one sixth of the
  target's width and height, clamped 2 px inside its border. A `human` click whose
  pointer is already inside the target does not move at all.
- **Path shape.** `windmouse` (default): a gravity-and-wind simulation implemented here;
  gravity 9, wind 5 and step limit 15, all scaled by `sqrt(D / 300)` clamped to
  0.5 to 4, with the wind damped within 12 px (scaled) of the destination. `bezier`: a
  cubic curve whose control points are offset perpendicular to the line by 5 to 25 % of
  the distance (at most 200 px), bending to opposite sides (an S-curve) with probability
  0.15. Paths are clipped so they never pass the aim point or back up behind the start.
- **Duration.** Fitts' law, `20 + 125 * log2(D / W + 1)` ms, `W` the target's smaller
  side (20 px without a target), at least 30 ms, divided by `speed` and multiplied by
  log-normal noise (sigma 0.08).
- **Speed profile.** Minimum jerk (`10t^3 - 15t^4 + 6t^5`) over a time axis skewed so the
  peak speed comes at 42 to 50 % of the movement; one point every ~8 ms.
- **Overshoot.** With probability `overshoot` (default 0.1), movements over 100 px aim 3
  to 8 % past the landing (at most 40 px, with a small sideways error), pause 30 to
  100 ms, then make a corrective movement timed with `W = 12`.
- **Jitter.** Sideways noise from random knots every 60 ms with cosine interpolation,
  amplitude `jitter` px (default 1.0), multiplied by `sin(pi * t)` so it fades out at
  both ends.
- **Corner clearance.** All points stay inside the desktop, and every point except the
  landing stays at least 3 px from a corner on one axis, so the 2 px corner failsafe
  never fires on a planned point.

Clicks hold the button a log-normal ~90 ms (40 to 200 ms) with ~95 ms gaps between the
clicks of a multi-click (60 to 150 ms, short enough to register as a double click).
Scrolls send one notch per axis at a time, ~70 ms apart with occasional quick bursts.
Drags pause ~120 ms after pressing and before releasing.

### The human keyboard

Typing happens in two passes. First the text becomes strokes: one `char` per code point,
with `\n`, `\r\n` and `\r` as `enter`, `\t` as `tab` and `\b` as `backspace`; any other
control character is refused (the `type` step rejects it before planning). With `typos` on, each ASCII
letter has a 3 % chance of becoming a QWERTY neighbour, noticed at once or (40 %) one or
two correct characters later, then erased with `backspace` and retyped. A correction
never crosses an `enter` or `tab` and never runs past the end of the text, and a test
replays the planned events to check they produce the original text. Then the strokes are
timed:

| Aspect | Model |
| --- | --- |
| Intervals between key-downs | Log-normal with sigma `variance` (default 0.3) and its mean equal to `60000 / (wpm * 5)` ms (default `wpm` 60), clamped to 0.35 to 3 times that |
| Pauses | About 40 ms after a space, 180 ms after `.,;:!?`, 350 ms after `enter` or `tab` (each times 0.5 to 1.5); a 5 % chance of a 250 to 700 ms "thinking" pause after a space, `enter` or `tab`; 200 to 500 ms before correcting a typo |
| Hold times | Log-normal around 95 ms (50 to 180 ms), independent of the interval, so fast typing rolls over |
| Rollover rules | At most two keys down at once; `enter`, `tab` and `backspace` never overlap anything; a key is released before the same key is pressed again |

With `variance: 0` the timing is fully regular (no pauses, fixed holds). Key
combinations press modifiers in order with 40 to 90 ms gaps, hold the last key 80 to
140 ms (or exactly `hold`) and release in reverse order.

### Interference detection

Before each point of a movement after the first, the session reads the real pointer once
and compares it with the last point it sent. More than `tolerance` pixels away (default
20) means the user moved the mouse: the session emits `user_interference`, releases what
the action holds and raises `UserInterference`. This needs the `pointer_position`
capability; with `interference` other than `off` on a backend without it, `click`,
`move` and `drag` fail with `capability_missing` before sending anything. Movements with a
single point (every `direct` movement) have nothing to compare, and the keyboard is not
checked at all: on X11 injected key events cannot be told apart from the user's.

### Emergency stop

Two independent mechanisms stop a run:

- **The stop key.** At startup the engine asks the backend to watch a global hotkey
  (`ctrl+alt+shift+q`, or `--stop-key`, or `J5_STOP_KEY`). When it fires, the hotkey
  thread logs a warning in the active run and stops it with reason `user`. On X11 this is
  a passive key grab on the root window (see below), so it works whichever application
  has the focus. If the combination is already grabbed by another program, the engine
  logs a warning and relies on the corner failsafe.
- **The corner failsafe.** The same pointer read used for interference detection checks
  whether the user pushed the pointer within 2 px of a corner of the virtual desktop. If
  so (and the planned landing is not itself in a corner), the session releases everything
  held and raises `EmergencyStop`; the failsafe wins over interference. This only works
  while a master is moving the pointer; it is the fallback for platforms where global
  hotkeys are unreliable, such as Wayland.

## X11 backend

[backends/x11/backend.py](../src/j5_macro/backends/x11/backend.py) injects input with the
XTest extension through python-xlib. One display connection serves every call, guarded
by a lock; each hotkey watcher has its own connection and thread.

**Layout-independent typing.** `char_down(c)` must type `c` whatever the keyboard layout.
If a key produces the character's keysym at shift level 0 in the current mapping, that
key is pressed. Otherwise (uppercase letters, symbols, `ñ`, `€`, CJK) the backend picks a
spare keycode (one with no keysyms), maps it to the character's keysym, presses it, and
restores the original mapping 0.25 s after the character is released. The grace period
exists because applications fetch the new mapping after the `MappingNotify` that precedes
the key event; restoring at once could make them translate the key with the old mapping.
Pressing the same remap again within the grace period cancels the restore. If no spare
keycode is left, an idle remap is reused, and otherwise the character fails with
`UnknownKey`. Pressing a key that is already down releases it first, so the second press
produces a second character. `close()` releases every pressed key and button and restores
every remap.

**Window lookup.** `find_window(title)` returns the client area (inside the decorations)
of the first viewable window whose title (`_NET_WM_NAME`, else `WM_NAME`) contains
`title`, in root coordinates. Candidates come from `_NET_CLIENT_LIST_STACKING` (reversed,
so topmost first) or `_NET_CLIENT_LIST` when the window manager follows EWMH; without one
(a bare Xvfb) the root's children are walked, topmost first, looking for the window that
carries `WM_STATE` up to four levels down. Using the client area keeps window-relative
coordinates stable across themes and window managers.

**Hotkey grabbing with lock-key variants.** An X passive grab (`XGrabKey`) only matches
the exact modifier state, so with Caps Lock or Num Lock on, a plain grab would not fire.
The watcher grabs the combination once for every subset of the lock modifiers
(CapsLock, NumLock and ScrollLock, whose masks are looked up in the server's modifier
mapping, as are Alt and Super). A `BadAccess` error means another client owns the
combination and becomes a `HotkeyConflict`.

## Vision

[vision/](../src/j5_macro/vision) is a toolkit with no knowledge of runs; the run-aware
part is [runtime/vision.py](../src/j5_macro/runtime/vision.py).

**Capture.** `MssCapturer` grabs a rectangle of the virtual desktop with mss as a BGR
frame whose `origin` is its desktop position. mss handles are bound to the thread that
created them, so each thread lazily gets its own. Regions are clipped to the desktop; a
region entirely outside it fails with `validation_failed` (`details.param: region`).

**Matching.** `locate` uses OpenCV's `TM_CCOEFF_NORMED`: the correlation of the
mean-subtracted template and each frame window, which is insensitive to uniform
brightness and contrast changes. Scores are clipped to [0, 1], and NaN or out-of-range
values become 0. A template with no variation in its visible pixels scores 0 everywhere,
so a flat colour never matches: templates need some texture or an edge. `scale` resizes
the template (area interpolation down, cubic up) before matching. `found` means
`best_score >= threshold` (0.9 by default), and the best location is reported even when
it is below the threshold.

**Alpha masks.** A PNG's alpha channel becomes a binary mask (alpha below 128 is
ignored) passed to `TM_CCOEFF_NORMED`, so a cut-out icon matches on any background with
the same score meaning as an unmasked template. `TM_CCORR_NORMED` was rejected because,
without mean subtraction, it scores high on any plain bright area. Templates are cached
(LRU of 64) by absolute path, modification time and size, so editing an image on disk
is picked up by the next search.

**Regions and windows.** A search covers the step's `region` (relative to its `window`
when given), else the window's area, else the whole desktop. The window is looked up on
every attempt, so a wait follows a window that moves. Image paths are relative to the
script's directory.

**Waits and captures.** A wait searches every 0.25 s until found or out of (unpaused)
time, through the step's interruptible wait. Every attempt emits `image_search` with the
score, the best `bbox` and the searched region. Captures follow the run's `captures`
policy: `on_failure` (default) saves only the last attempt of a search that fails, and
`all` saves every attempt. They go to `<run_dir>/captures/<n>-<step_id>-<attempt>.png`
with the best match outlined in red, and events carry the path relative to the run's
directory. A single `image_visible` search never counts as a failure. A capture that
cannot be saved logs one warning and the run goes on.

## Engine services

### Event sequencing

Each stream has an `EventSequencer` ([engine/events.py](../src/j5_macro/engine/events.py)):
the engine stream (`run_id: null`) and one per run. It assigns `seq` (from 1, no gaps),
`ts` (UTC, milliseconds) and `t_ms` (monotonic milliseconds since the stream started)
under one lock. The stream's own publish lock covers stamping, buffering and writing, so
`seq` order is wire order. The engine keeps the last 1000 engine events and the events
of the last 20 runs of the session in memory for `get_events`; the client
([client/sequencer.py](../src/j5_macro/client/sequencer.py)) detects a gap per stream,
fetches the missing events and drops duplicates.

### Run persistence

Each run gets `<runs dir>/<run_id>/` ([engine/persistence.py](../src/j5_macro/engine/persistence.py)):

- `run.json`: `run_id`, `script_id`, `script_name`, `script_path`, `mode`, `seed`,
  `started`, and on completion `finished`, `status`, `reason`, `duration_ms` and `error`.
  It is rewritten atomically (temporary file and rename), so a reader never sees half a
  file.
- `events.jsonl`: every event of the run, appended and flushed as it is emitted.
- `captures/`: the screen captures of image searches.

The runs directory is `--runs-dir`, else `J5_RUNS_DIR`, else the platform state
directory plus `j5-macro/runs`. When a run starts, older run directories are deleted so
the newest 20 remain (ordered by the start time in the run id, then by modification
time); only directory names shaped like a run id are ever touched. Persistence never
affects a run: any failure becomes an engine `log` warning and the run continues
unsaved.

### Run history commands

`list_runs`, `get_run` and `delete_run` ([engine/history.py](../src/j5_macro/engine/history.py))
read the runs directory, so they also see runs of earlier engine sessions. A `run_id`
must match `r-YYYYMMDD-HHMMSS-xxxx` exactly before any path is built, so a request can
never reach outside the runs directory. `get_run` ignores an unfinished last line of
`events.jsonl` (a write in progress or a crash) and skips other unreadable lines with a
warning. The active run is listed and readable but cannot be deleted. See
[contract.md, "Script management"](contract.md#script-management) for the fields.

### Secret masking

Secret params must not end up in terminals, logs or history. The engine knows the values
of a run's secret params (longest first) and masks every occurrence with `***`:

- in every run event, before it is stamped, so the wire and `events.jsonl` agree;
- in the `get_state` and `evaluate` answers and in control-command errors;
- in `run.json`, which takes the masked `run_finished` payload.

**Secrets found during the run.** A step with `as: {name, secret: true}` makes its result
a secret: before emitting the step's `step_finished`, the interpreter hands every string
in the result (and its stripped form; blank strings left out, since they would mask every
space) to the engine through `RunOptions.add_secrets`. `Run.add_secrets` merges them into
the run's list under the publishing lock, replacing the list rather than changing it, so
every event published afterwards, the step's own result included, and every later answer
is masked. Events already published are not rewritten; nothing secret has been in them,
since the value did not exist before the step finished. Without an engine (the
interpreter in tests) nothing is masked.

Masking replaces the exact text in strings, mapping keys and nested containers, and a
number equal to a secret's text. Top-level position fields (`path`, `iterations`,
`line`, `index`, `total`, `duration_ms`) are left alone when they hold integers, so a
short numeric secret never breaks a position. A type error on a secret param says "a
secret value" instead of repeating it.

Limits, by design or not yet solved:

- Only the exact text is masked; a transformed value (`upper(password)`, a substring) is
  not recognised.
- The length is visible: `type` reports how many characters it typed (in its result and
  in `input_planned.chars`), never the text.
- Values travel unmasked from the client to the engine in the `run` command, and the
  interpreter uses the real values; masking protects what comes out.
- Masking protects logs and history, not the screen: the target application shows what
  is typed unless its field hides it.

### File watching

Every loaded script with a path is checked every 0.5 s by the `ScriptWatcher`
([engine/scripts.py](../src/j5_macro/engine/scripts.py)). Polling was chosen over
platform file-notification APIs because it behaves the same on every platform and
filesystem and needs no extra dependency. A cheap `stat` comparison (mtime, size, inode)
comes first; the file is read only when that changed or when it was modified within the
last 2 s (a coarse mtime may not tell two quick writes apart).

A script's `version` is the first 16 hex digits of the SHA-256 of its text, so it changes
exactly when the content changes, whoever changes it. An event is emitted only when the
version differs from the store's: touching a file or rewriting the same content emits
nothing. A valid new version replaces the loaded script; an invalid one is kept with its
errors and the last valid script stays loaded, but `run` answers `validation_failed`
until the file is fixed. A deleted file emits `source: deleted` once; when it comes back,
`source: disk` is emitted again even with the same content.

**Coordination with `edit_script`.** The watcher holds the store's `file_lock` while it
reads a file and updates the store, and `edit_script` holds the same lock while it writes
the file and records the new text and version. The watcher therefore never reads the
edited file before the store knows its version, and an edit is reported once, as
`source: edit`, never also as `disk`. `edit_script` also re-reads the file itself before
applying edits, so a change the watcher has not noticed yet still produces `conflict`
against a stale `base_version`.

### Structured editing

Clients never rewrite YAML; they send `edit_script` with typed edits (`insert`,
`update`, `move`, `delete`, `set_meta`) addressed by step id.
[script/editor.py](../src/j5_macro/script/editor.py) applies them as a pure function of
the text:

- **Round trip with untouched lines restored.** The text is loaded with ruamel's
  round-trip loader, edited as `CommentedMap`/`CommentedSeq` objects and dumped. The
  round trip alone reformats a few things (alignment after a colon, for example), so the
  editor also dumps the unedited document, diffs it against the original, and puts the
  original text back on every line the edits left alone. Untouched lines therefore stay
  byte for byte.
- **Formatting of new content.** Indentation is detected from the file's first block
  list and nested mapping (default: 2 spaces, `- ` indented by 2). New steps are block
  mappings with `id` first; a new mapping or list of scalars is written in flow style.
  An end-of-line comment keeps its column when its line changes. Lines are never wrapped.
- **Comment ownership.** The comment lines right above a step, indented no deeper than
  it, belong to that step: they move and are deleted with it. Comments after the last
  entry of a block stay after the block.
- **Id writing.** Before any edit, every step without an id gets the id the loader would
  generate, so breakpoints and history by id survive reordering. An invalid id is left
  as it is.
- **All or nothing.** An edit that cannot apply raises with its index, and nothing is
  written. When every edit applies, the new text is saved atomically (temporary file in
  the same directory, `fsync`, rename, original permission bits kept) even if the
  resulting script is invalid; its problems are returned, the store keeps the last
  valid version for `get_script`, and `run` answers `validation_failed` until the
  script is fixed. An active run keeps the version it started with.

## Debugging

The engine's debugging surface is a handful of commands: `run` with `mode: step`,
`set_breakpoints`, `pause`, `resume`, `step_over`, `step_into`, `step_out`, `get_state`,
`set_variable` and `evaluate`. Two clients build on them:

- **The CLI debugger** ([cli/debugger.py](../src/j5_macro/cli/debugger.py)), behind
  `j5 run --step` and `--break`, reads pdb-like commands every time the run pauses and
  turns them into those commands. Commands are read from stdin on a thread, so a session
  can be scripted.
- **The DAP adapter** ([dap/session.py](../src/j5_macro/dap/session.py)) starts one
  engine per debug session, maps YAML breakpoint lines to step ids
  ([dap/script_map.py](../src/j5_macro/dap/script_map.py)), maps `continue`, `next`,
  `stepIn`, `stepOut` and `pause` to the engine commands of the same meaning, builds the
  call stack and variables from `get_state`, and turns engine events into DAP `stopped`,
  `continued`, `output` and `terminated` events.

Usage is in [debugging.md](debugging.md); the full mapping is in
[contract.md, "Debug Adapter Protocol"](contract.md#debug-adapter-protocol).

## Testing strategy

The layering is designed so that most behaviour is tested without a display, a real
clock or a subprocess:

- **Pure unit tests.** Planners (`tests/input/`), the expression evaluator, the loader,
  the structured editor, params and secrets, payload codecs, DAP framing and breakpoint
  line mapping are pure functions tested directly. Human-like input is tested
  statistically on plans: reproducibility for a seed, the typo rate, and the replayed
  text including corrections.
- **Fake backend and fake clock.** `FakeBackend` ([backends/fake.py](../src/j5_macro/backends/fake.py))
  records every primitive, has configurable capabilities and windows, and an `on_move`
  hook that lets a test act as the user during a movement (interference, the corner
  failsafe). The interpreter tests (`tests/runtime/`) inject a fake clock, fake input
  sessions and fake vision through `create_run`.
- **In-process engine.** `tests/engine/` drives `EngineServer` over in-memory streams with
  an engine harness, covering scripts, editing, runs, persistence and history.
- **Processes.** Client tests use scripted fake engines (`tests/fakes/`) to provoke gaps,
  duplicates and crashes; integration tests run the real `EngineClient`, CLI and DAP
  adapter against the real engine process, with `J5_BACKEND` set to `fake` or `none` so
  nothing touches the real desktop.
- **A fake Win32 layer.** The Windows backend makes every system call through the
  `Win32Api` interface; `tests/backends/win32_fake.py` implements it with a small
  simulated desktop (windows, a cursor, a layout, hotkey message queues), so the
  backend's events, flags, coordinates, held state and hotkey threads are tested on Linux
  too. On Windows, a few tests also run against the real system.
- **Smoke scripts.** [examples/smoke](../examples/smoke/) drives a small tkinter window
  through every step type; the [manual test plan](manual-testing.md) runs it on Xfce and
  Windows before a release.
- **A real X server.** Tests marked `x11` run the X11 backend, screen capture and a full
  end-to-end run (`j5 run` clicking and typing into a test window, non-ASCII characters
  included) against Xvfb. They are skipped when no X server with XTEST is reachable;
  `J5_REQUIRE_X11=1` turns that skip into a failure.

`scripts/check.sh` runs `ruff check .`, `mypy` (strict, over `src/j5_macro`) and
`pytest -rs`; on Linux it requires `xvfb-run` and runs pytest under a 1280x800x24 Xvfb
screen with `J5_REQUIRE_X11=1`. GitHub Actions
([.github/workflows/ci.yml](../.github/workflows/ci.yml)) runs the same script on every
pull request and every push to `main`, on Ubuntu and Windows with Python 3.11, 3.12 and
3.13. Linux installs the `dev`, `x11` and `vision` extras; Windows installs `dev`,
`windows` and `vision`; the X11 tests are skipped there and the real-system Windows
backend tests run. See [development.md](development.md).

## Known limitations and open items

- **Windows and elevated windows.** Windows refuses input from a normal process to a
  window of a program running as administrator (UIPI). When `SendInput` reports it, the
  step fails with `input_denied`; Windows may also drop the input silently. The secure
  desktop (UAC prompts, the lock screen) receives no input at all.
- **Wayland** gets no backend; an experimental one (libei or a similar tool) is planned
  after v1. Without a reliable `pointer_position` it would have no interference detection.
- **Keyboard interference is not detected**, and neither are scrolls or single-point
  (`direct`) movements.
- **Secrets**: the masked value's length is visible through `type`, transformed values
  are not masked, and the screen shows whatever the target application shows.
- **One client per engine**, over stdio only. Sockets or named pipes for a long-lived
  engine, with read-only observers, are planned after v1.
- **Reserved, not implemented**: `input_planned.plan_ref` (the points or key events of a
  plan are not written to disk yet) and `trigger_fired` (triggers are out of v1).
- **File watching** is polling at 0.5 s, so a change is noticed within about half a
  second, and `list_scripts` looks at most at 1000 YAML files.
- **macOS** is not supported.

The open design decisions and their recommendations are listed in
[contract.md, "Errors and open decisions"](contract.md#errors-and-open-decisions).
