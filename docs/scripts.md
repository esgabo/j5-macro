# Writing scripts

This is the reference for j5-macro scripts: the file format, every step type with its
parameters, and the rules for coordinates, images, durations, keys, expressions, input
profiles and params. For a guided introduction, start with the [tutorial](tutorial.md).
The design behind it is in [contract.md](contract.md#script-format-and-step-registry).

`j5 steps` prints the step types and their parameters from the step registry, the same
source this page is checked against:

```console
$ j5 steps
click  Click on an image or at x/y.
  image: string  Template image: a path relative to the script file, or "#name" of an image embedded in the script.
  x: integer  Horizontal position in physical pixels.
...
```

## File structure

A script is a UTF-8 YAML file with these top-level keys:

| Key | Required | Meaning |
| --- | --- | --- |
| `version` | no | Format version. Only `1` exists; it is the default |
| `name` | no | Name shown in the output and the run history; defaults to the file name without extension |
| `input` | no | The script's [input profile](#input-profiles): `{mouse: {...}, keyboard: {...}}` |
| `capture_scale` | no | The display scale the script's templates and pixel offsets were taken at, `1` (100 %) by default; see [Display scale](#display-scale) |
| `params` | no | Values the script takes from outside; see [Params and secrets](#params-and-secrets) |
| `vars` | no | Variables and their starting values (any YAML value: numbers, text, lists, mappings) |
| `steps` | yes | The list of steps |
| `images` | no | Template images carried inside the script; see [Embedded images](#embedded-images) |

Any other top-level key is ignored with a warning.

```yaml
version: 1
name: Save report
input:
  mouse: {strategy: human, speed: 1.2}
  keyboard: {strategy: human, wpm: 55}
params:
  customer: {description: Name to type in the form}
vars:
  filename: october_report
steps:
  - id: open_menu
    click: {image: img/file_menu.png}
  - id: type_name
    type: "${filename}_${customer}.txt"
```

### Editor support

`j5 schema` prints a JSON Schema of the format, generated from the step registry, so an
editor can complete step types and parameters and flag mistakes as you type. With the VS
Code YAML extension (`redhat.vscode-yaml`), save it next to your scripts:

```console
$ j5 schema -o j5-script.schema.json
wrote j5-script.schema.json
```

Then either start each script with this comment:

```yaml
# yaml-language-server: $schema=./j5-script.schema.json
```

or map it to your script files in `.vscode/settings.json`:

```json
{
  "yaml.schemas": {"./j5-script.schema.json": ["*.j5.yaml", "scripts/**/*.yaml"]}
}
```

Regenerate the schema after upgrading j5-macro.

## Steps

A step is a mapping with exactly one step-type key and, optionally, common fields:

```yaml
- id: confirm            # common field
  break: true            # common field
  key: {press: enter}    # the step type and its parameters
```

### Common fields

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `id` | string | generated | Stable id: letters, digits, `_` and `-`, starting with a letter or `_`; unique in the script |
| `break` | boolean | `false` | A breakpoint written in the script: the run pauses before this step (see [debugging.md](debugging.md)) |
| `timeout` | duration | none | For image searches: replaces `wait_image`'s `timeout`, and makes `click`, `move` and `drag` wait up to this long for their image targets. Also replaces `exec`'s `timeout`. Other steps ignore it |
| `on_error` | `stop`, `continue`, `retry`, `pause` | `stop` | What to do when the step fails (see below) |
| `input` | mapping | none | Input profile overrides for this step: `{mouse: {...}, keyboard: {...}}` |
| `as` | name or mapping | none | Keep the step's result in a variable: `as: label`, or `as: {name: token, secret: true}` (see [Keeping a result](#keeping-a-result-as)) |

`on_error`:

- `stop` ends the run with status `failed`.
- `continue` logs a warning and goes on with the next step.
- `retry` runs the step again at once, up to 3 attempts in total, then stops the run.
- `pause` pauses the run. In a terminal, Enter retries the step and `s` then Enter skips
  it; under `--step` or `--break`, the debugger's `c` retries and `n` skips. Without a
  terminal, `j5 run` retries it twice on its own; if it still fails, it stops the run
  and exits with code 1.

On an `if` or a `loop`, `on_error` applies to evaluating its condition or list, not to
the steps inside it.

### Keeping a result: `as`

Every step reports a result when it finishes (shown after `✓` in `j5 run`; see
[Step results](#step-results)). `as` keeps it in a variable, so later steps can use it:

```yaml
- id: find_label
  wait_image: {image: img/email_label.png, window: {title: Login}, timeout: 10s}
  as: label                     # label = {found, score, bbox, center, window, window_bbox}
- id: click_email_field
  click: {at: "${label}", offset: [0, 1.5h]}    # 1.5 label heights below its centre
- id: show
  log: "label found with score ${label.score}"
```

- The name follows the rules of variable names: letters, digits and `_`, not starting
  with a digit. It may replace an existing variable.
- The variable is written when the step succeeds, right after its result. A step that
  fails (also with `on_error: continue`) or is skipped writes nothing, so a later
  `${label}` fails with `expression_error` (unknown variable). An `if` or a `loop` writes
  its result (`{branch}`, `{iterations}`) after the steps inside it.
- In a dry run the dry result is kept (`dry_run: true`, a `description` and, when an
  image was searched, `found`, `bbox` and the rest), so the steps after it still check.
- Read items of a kept result with `.` or `[...]`: `label.bbox`, `label.center[0]`,
  `token["stdout"]`.

**Secret results.** `as: {name: token, secret: true}` treats the result as a secret of
the run from the moment the step finishes, its own result included: every string in it
(and the same string without leading and trailing spaces or line breaks) shows as `***`
in the output, the run history and the debugger, like a [secret param](#params-and-secrets).
Numbers, booleans and empty strings are not masked. Use the value as it is
(`type: "${token.stdout}"`), or with `strip(...)` when the trailing line break matters:
any other transformation (`upper(token.stdout)`) is not recognised and would show.

```yaml
- id: get_token
  exec: {command: [curl, -s, "https://vault.example/token"], wait: true}
  as: {name: token, secret: true}          # token = {exit_code, stdout, stderr}
- id: type_token
  type: "${strip(token.stdout)}"
```

### Stable ids

Ids are how breakpoints, the run history and errors name a step, so give every step an
explicit `id`. A step without one gets `<type>_<n>`, where `n` counts the steps of that
type in the whole script in document order, including those that have explicit ids (if
that id is taken, a suffix is added: `log_2_2`). Adding an id to one step therefore never
renames the others. Each generated id produces a warning:

```console
$ j5 validate noid.yaml
noid.yaml:2: warning: generated id 'log_1', add an explicit id to keep breakpoints stable
noid.yaml: valid (1 warning)
```

### Short form

Some step types take one value instead of a mapping, for their main parameter:

| Short form | Means |
| --- | --- |
| `type: "hello"` | `type: {text: "hello"}` |
| `key: ctrl+s` | `key: {press: ctrl+s}` |
| `wait_image: img/ok.png` | `wait_image: {image: img/ok.png}` |
| `sleep: 2s` | `sleep: {duration: 2s}` |
| `log: Done` | `log: {message: Done}` |
| `pause: Check the form` | `pause: {message: Check the form}` |
| `exec: mousepad notes.txt` | `exec: {command: mousepad notes.txt}` |

`pause:` with nothing after it is valid too. The other types (`click`, `move`, `drag`,
`scroll`, `if`, `loop`, `set`) need a mapping.

## Step types

Parameters marked "required" must be given. A dash in the Default column means the
parameter is optional and has no default value. Any string parameter can contain
[`${...}` interpolation](#interpolation).

### click

Clicks on an image, on a kept image result or at `x`/`y`. Give one of `image`, `at`, or
both `x` and `y`.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `image` | string | - | Template image to click on: a path relative to the script file, or `"#name"` of an [embedded image](#embedded-images) |
| `x`, `y` | integer | - | Position in physical pixels (relative to `window` when given) |
| `at` | kept result | - | An image result kept with `as`, usually `"${label}"`: click on its area, where it is now (see [Targets](#targets-and-coordinates)) |
| `window` | mapping | - | `{title: ...}`: work in the first visible window whose title contains this text (not with `at`) |
| `button` | `left`, `right`, `middle` | `left` | Mouse button |
| `clicks` | integer | `1` | Number of clicks (2 for a double click) |
| `offset` | `[dx, dy]` | - | Added to the target point: pixels, or with `image` and `at` multiples of the target's size such as `0.5w` (see [Offsets](#offsets)) |
| `threshold` | number | `0.9` | Minimum image match score, from 0 to 1 |
| `scale` | number | `1` | Extra factor the template is resized by, on top of the [display scale](#display-scale) (image targets only) |
| `region` | `[x, y, width, height]` or mapping | - | Area to search in (image targets only; relative to `window` when given), or [next to a kept result](#images) |

```yaml
- id: open_file_menu
  click: {image: img/file_menu.png, threshold: 0.85}
- id: open_context_menu
  click: {window: {title: Mousepad}, x: 200, y: 150, button: right}
```

### move

Moves the pointer to an image, a kept image result or `x`/`y`, without clicking.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `image` | string | - | Template image (a path, or `"#name"`) |
| `x`, `y` | integer | - | Position in physical pixels |
| `at` | kept result | - | An image result kept with `as` |
| `window` | mapping | - | `{title: ...}`; coordinates become relative to it (not with `at`) |
| `offset` | `[dx, dy]` | - | Added to the target point (see [Offsets](#offsets)) |
| `threshold` | number | `0.9` | Minimum image match score |
| `scale` | number | `1` | Extra template scale (image targets only) |
| `region` | `[x, y, width, height]` or mapping | - | Area to search in (image targets only) |

```yaml
- id: hover_tooltip
  move: {image: img/help_icon.png}
```

Unlike `click`, a `move` always moves, even if the pointer is already over the target.

### drag

Presses a button at one point, moves to another and releases it.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `from` | mapping | required | Start point: `{x, y, window?}`, `{image, threshold?, scale?, region?, window?}` or `{at}`, each with an optional `offset` |
| `to` | mapping | required | End point, same forms as `from` |
| `button` | `left`, `right`, `middle` | `left` | Mouse button |

```yaml
- id: move_card
  drag:
    from: {image: img/card.png}
    to: {window: {title: Board}, x: 600, y: 120}
```

Each point has its own `window`; there is no `window` for the whole step.

### scroll

Turns the mouse wheel, in notches.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `dx` | integer | `0` | Horizontal notches; positive scrolls right |
| `dy` | integer | `0` | Vertical notches; positive scrolls down |

```yaml
- id: scroll_down
  scroll: {dy: 5}
```

`scroll` acts wherever the pointer is; `move` there first.

### type

Types text, whatever the keyboard layout: accents, `ñ` and symbols work.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `text` | string | required | Text to type |
| `keyboard` | mapping | - | Keyboard profile override for this step, e.g. `{strategy: direct}` or `{wpm: 30}` |

```yaml
- id: type_address
  type: "Calle Ñandú 12\n"
- id: paste_code
  type: {text: "${code}", keyboard: {strategy: direct}}
```

Escapes in the text (written in double-quoted YAML strings, where YAML turns `\n` into a
line break): `\n` and `\r` press Enter, `\t` presses Tab, `\b` presses Backspace. Other
control characters (such as `\a` or `\x1b`) cannot be typed: the step fails with
`validation_failed` before typing anything, naming the character's position and code point
(`U+0007`) but never the text. Literal text is checked when the script is loaded, text
with `${...}` after interpolation. In single-quoted or unquoted YAML, `\n` is a backslash
and an `n`.

The step result reports how many characters were typed, never the text, which may be a
secret.

### key

Presses a key or a combination: the modifiers go down in order, then the key, and all are
released in reverse order.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `press` | string | required | Key or combination, e.g. `enter`, `ctrl+s`, `alt+f4` (see [Key combinations](#key-combinations)) |
| `hold` | duration | - | Keep the keys down this long (variance allowed) |

```yaml
- id: save
  key: ctrl+s
- id: hold_shift
  key: {press: shift, hold: 500ms}
```

### wait_image

Waits until an image is on screen, checking every 0.25 s.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `image` | string | required | Template image (a path, or `"#name"`) |
| `timeout` | duration | `10s` | Give up with `image_not_found` after this long; the step's common `timeout` replaces it |
| `threshold` | number | `0.9` | Minimum image match score |
| `region` | `[x, y, width, height]` | - | Area to search in (relative to `window` when given) |
| `window` | mapping | - | `{title: ...}`: search only in that window |
| `scale` | number | `1` | Extra template scale, on top of the [display scale](#display-scale) |

The result is `{found, score, bbox, center}` and, with a `window`, `window` (its title)
and `window_bbox` (the match inside the window); keep it with `as` to act on the match
later (`click: {at: "${dialog}"}`).

```yaml
- id: wait_dialog
  wait_image: {image: img/save_dialog.png, timeout: 30s}
```

### sleep

Waits for a duration.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `duration` | duration | required | How long, e.g. `2s`, `500ms`, `2s±20%` |

```yaml
- id: let_it_load
  sleep: 1.5s±20%
```

A `sleep` can be paused and stopped at any time; after a pause it waits only the time that
was left.

### if

Runs `then` when a condition holds, else the optional `else`.

| Parameter / block | Type | Default | Meaning |
| --- | --- | --- | --- |
| `when` | condition | required | An expression, or a predefined condition (see [Conditions](#conditions)) |
| `then` | steps | required | Steps run when the condition is true |
| `else` | steps | - | Steps run otherwise |

```yaml
- id: maybe_close_popup
  if:
    when: {image_visible: img/popup.png}
    then:
      - id: close_popup
        key: escape
    else:
      - id: no_popup
        log: no popup this time
```

### loop

Repeats its `do` block. Give exactly one of `times`, `while` and `for_each`.

| Parameter / block | Type | Default | Meaning |
| --- | --- | --- | --- |
| `times` | integer | - | Number of iterations, 0 or more |
| `while` | condition | - | Repeat while this holds; checked before each iteration |
| `for_each` | list or expression | - | A list, or an expression that gives one |
| `as` | string | `item` | Variable that holds the current item of `for_each` |
| `do` | steps | required | The steps to repeat |

```yaml
- id: each_row
  loop:
    for_each: rows            # an expression: the variable 'rows'
    as: row
    do:
      - id: type_row
        type: "${row}"
      - id: next_row
        key: down
- id: until_done
  loop:
    while: {image_visible: img/spinner.png}
    do:
      - id: wait_a_bit
        sleep: 1s
```

`for_each` also accepts a literal list (`for_each: [a, b, c]`). In a dry run a `while`
loop stops after 1000 iterations with a warning.

### set

Sets a variable, creating it if it does not exist.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | required | Variable name |
| `value` | any | - | A literal value; strings in it are interpolated |
| `expr` | expression | - | An expression whose result is stored |

Give exactly one of `value` and `expr`.

```yaml
- id: next_attempt
  set: {name: attempts, expr: "attempts + 1"}
- id: build_label
  set: {name: label, value: "batch-${attempts}"}
```

### log

Writes a message to the run's output and history.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `message` | string | required | Text to log |
| `level` | `debug`, `info`, `warning`, `error` | `info` | Log level |

```yaml
- id: report
  log: {message: "Only ${done} rows processed", level: warning}
```

### pause

Pauses the run after this step, like a breakpoint written in the script. In a terminal,
Enter resumes it; without one, the run resumes on its own and says so.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `message` | string | - | Reason shown in the step's parameters |

```yaml
- id: check_by_hand
  pause: Check the totals before saving
```

### exec

Runs a program: starts it and goes on, for example to launch an application, or waits for
it and keeps its output. Running programs is off unless the run allows it: pass
`j5 run --allow-exec` (`"allowExec": true` in VS Code). Without it an `exec` step fails
with `capability_missing`, so a script you did not write cannot start programs behind
your back.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `command` | list or string | required | The program and its arguments. A list runs the program directly, without a shell; numbers in it are written as text. A string is split like a command line (quotes group words) and is also run without a shell |
| `shell` | boolean | `false` | Run the `command` string through the system shell (`/bin/sh -c` on Linux, `cmd /c` on Windows), for pipes, redirections and `&&` |
| `wait` | boolean | `false` | `false`: start the program detached and go on at once; it keeps running after the run ends. `true`: wait until it exits |
| `timeout` | duration | - | With `wait`: kill the program (and its children) after this long, failing the step with `exec_failed` |
| `cwd` | string | the script's directory | Working directory, relative to the script's directory |
| `env` | mapping | - | Extra environment variables, added to j5's own |
| `check` | boolean | `true` | With `wait`: fail the step with `exec_failed` when the program exits with a code other than 0 |

Without `wait` the result is `{pid}`. With `wait` it is `{exit_code, stdout, stderr}`:
the program's output and errors as text, each cut at 64 KiB (the result then adds
`truncated: true`). Output is kept as written, final line break included, so use
`strip()` when you need the bare value. The program never gets any input.

```yaml
- id: open_editor
  exec: {command: [mousepad, notes.txt]}        # starts it and moves on
- id: today
  exec: {command: [date, +%F], wait: true}
- id: count_lines
  exec: {command: "wc -l < data.csv", shell: true, wait: true, timeout: 10s}
- id: upload
  exec:
    command: [./upload.sh, report.pdf]
    wait: true
    env: {API_TOKEN: "${token}"}                # a secret param: pass it in env
```

To open a file or a URL with its default application, run the system's opener:
`exec: {command: [xdg-open, report.pdf]}` on Linux, or
`exec: {command: [cmd, /c, start, "", report.pdf]}` on Windows (`start` reads its first
quoted argument as a window title, hence the `""`).

- A program that does not exist or cannot be started fails with `exec_failed`
  (`details.reason: start`); a non-zero exit with `check` with `exec_failed`
  (`details.reason: exit_code`, plus the exit code, output and errors); a `timeout` with
  `exec_failed` (`details.reason: timeout`).
- Stopping the run kills a program it is waiting for, with its children. Pausing does not
  pause the program: the step goes on waiting and the run pauses after it. A program
  started without `wait` is never killed by j5.
- A dry run starts nothing: the step says what it would run, where, and whether it would
  wait, and warns when the run does not have `--allow-exec`.
- Secrets in `command`, `env` or the output show as `***` in the output and the run
  history, like everywhere else, but the program gets the real values. Pass secrets
  through `env` rather than in `command`: other users of the machine can see a program's
  arguments, not its environment.

## Step results

What each step reports when it finishes, and so what `as` keeps. `bbox`, `window_bbox`
and `window_area` are `[x, y, width, height]`; points and `center` are `[x, y]`, all in
physical pixels.

| Step | Result |
| --- | --- |
| `click` | `x`, `y` (the point clicked), `button`, `clicks`; an image or `at` target adds `bbox`, `center` and, in a window, `window` (the title) and `window_bbox`; an image target adds `image` and `score`; an `x`/`y` target in a window adds `window` and `window_area` (the window's content area used) |
| `move` | `x`, `y` and the same target fields as `click` |
| `drag` | `from`, `to`, `button`, and each point's target fields prefixed with `from_` or `to_` |
| `scroll` | `dx`, `dy` |
| `type` | `chars` (never the text) |
| `key` | `press`, and `hold` (seconds) when given |
| `wait_image` | `found`, `score`, `bbox`, `center`, and `window`, `window_bbox` with a window |
| `sleep` | `seconds` |
| `if` | `branch` (`then` or `else`) |
| `loop` | `iterations` |
| `set` | `name`, `value` |
| `log` | `level`, `message` |
| `pause` | `message` |
| `exec` | `exit_code`, `stdout`, `stderr` (and `truncated`) with `wait`; `pid` without |

In a dry run, input steps and `exec` report `dry_run: true` and a `description` instead, plus the
image fields when an image was searched (or the kept area, for `at`). `j5 schema`
describes these fields too (`$defs/<type>_result`).

## Targets and coordinates

- **Coordinates.** `x` and `y` are physical pixels on the virtual desktop, which spans
  every screen; they can be negative on multi-monitor setups.
- **Windows.** With `window: {title: ...}`, `x`, `y` and `region` are relative to the
  top-left corner of the window's content area (inside its title bar and borders), looked
  up when the step runs. The first visible window whose title contains the text is used
  (case-sensitive). No match fails the step with `window_not_found`.
- **Offset.** `offset: [dx, dy]` (on `click`, `move` and each point of `drag`) is added
  to the final point; see [Offsets](#offsets) for its units.
- **Image targets.** `click`, `move` and the points of `drag` can take an `image`. It is
  searched once (or until the step's common `timeout`), and a miss fails the step with
  `image_not_found`. The action aims at the centre of the match. Without an `offset`, the
  `human` strategy lands somewhere natural inside the matched area, and a `human` click
  whose pointer is already inside it clicks without moving; with an `offset`, the pointer
  goes to the exact point.
- **Kept results (`at`).** Instead of searching again, `click`, `move` and the points of
  `drag` can take `at: "${label}"`, an image result kept with [`as`](#keeping-a-result-as)
  (from `wait_image`, or from an image or `at` target of `click` or `move`). It is aimed
  at like an image target's match, with the same `offset` rules. A result found in a
  `window` remembers the window's title and where the match was inside it: the window is
  looked up again when the step runs, so the action follows it if it moved, and
  `window_not_found` if it closed. `at` cannot be combined with `image`, `x`/`y` or
  `window`. A result that was not found, or is not an image result, fails with
  `validation_failed`.
- **Coordinates from a result.** `label.center[0]` and `label.bbox` are desktop
  coordinates from when the result was kept: use them without `window`, and prefer `at`
  when the window may move.

### Offsets

Each item of an `offset` is either a number of pixels or a multiple of the target's size:
a number followed by `w` (its width) or `h` (its height), written without spaces.

```yaml
- click: {image: img/email_label.png, offset: [0, 1.5h]}   # 1.5 label heights below its centre
- click: {at: "${label}", offset: [1w, 0]}                 # one label width to the right
- click: {at: "${label}", offset: [-0.5w, 10]}             # its left edge, 10 px lower
```

- The size is that of the match for an `image` target, and of the kept area for `at`.
  Either unit works on either axis: `[1h, 0]` moves right by one height.
- `x`/`y` targets have no size, so `w` and `h` there are an error (`validation_failed`,
  `param: offset`).
- Sizes follow the screen: if the application draws the label larger, `1.5h` is still
  one and a half label heights. Pixel offsets of `image` and `at` targets are scaled by
  the [display scale](#display-scale) too; the offsets of `x`/`y` targets never are,
  since those are positions.

### Display scale

The same application draws everything 1.5 times larger at 150 % than at 100 %, so a
template captured at 100 % no longer matches, and an `offset: [0, 20]` measured there
falls short. j5 compensates with two scales (1 means 100 %, 1.5 means 150 %):

- The **display scale** is that of the screen the run works on. j5 detects it: on
  Windows per monitor (the monitor the searched area or the target is on), on X11 from
  `Xft.dpi` (what `xrdb -query` shows; 144 is 150 %), else the `GDK_SCALE` environment
  variable, else 1. `j5 run --display-scale 1.5` sets it for the whole run instead.
- The **capture scale** is the display scale the templates and pixel offsets were
  taken at. The script's top-level `capture_scale` sets it (1 by default); an [embedded
  image](#embedded-images)'s `scale` sets it for that image only.

Every image search resizes its template by `display scale / capture scale` (times the
step's own `scale`, which still works on top of it), and pixel offsets of `image` and
`at` targets, and of [regions next to a kept result](#images), are multiplied by the
same ratio. `x`/`y` positions, their offsets and `region: [x, y, width, height]` are
never changed. `j5 run` shows the display and capture scales when they are not 1, and
each `image_search` event reports the effective `scale`, `display_scale` and
`capture_scale`.

```yaml
version: 1
name: Log in
capture_scale: 1.5          # the templates were captured on a 150 % screen
steps:
  - id: find_label
    wait_image: img/email_label.png
    as: label
  - click: {at: "${label}", offset: [0, 1.5h]}
```

On a 100 % screen the templates are searched at 2/3 of their size; on a 150 % one, as
they are. A script written before display scales were applied, with templates captured
at another scale, needs `capture_scale` (and should drop a step `scale` set by hand for
one machine). `--display-scale 1` with the default `capture_scale` turns the adjustment
off.

## Images

- **Paths** are relative to the script file's directory. A missing or unreadable file
  fails with `template_missing` when the step runs (`j5 validate` does not open image
  files). A value written `"#name"` is not a path but an [embedded
  image](#embedded-images), checked when the script loads.
- **Matching** uses normalized cross-correlation: a score from 0 to 1, and a match when
  the score is at least `threshold` (0.9 by default).
- **Transparency.** Pixels of a PNG with alpha below 128 are ignored, so a cut-out icon
  matches on any background.
- **Texture.** A template whose visible pixels are a single flat colour never matches:
  include an edge, text or an icon.
- **Region.** `region: [x, y, width, height]` limits the search. Without it j5 searches
  the `window`, else the whole desktop. A region is clipped to the desktop; a region
  entirely outside it fails with `validation_failed`.
- **Region next to a kept result.** `region: {at: "${label}", offset: [dx, dy], size:
  [width, height]}` searches an area the size of the kept result (or `size`), centred on
  its centre plus `offset`. It follows the result's window like an `at` target and is
  never relative to the step's `window`. `offset` and `size` take pixels or
  [multiples of the result's size](#offsets); pixels follow the [display
  scale](#display-scale). For example, the field to the right of a label: `region: {at:
  "${label}", offset: [2.5w, 0], size: [4w, 1.5h]}`.
- **Scale.** j5 resizes templates by the [display scale](#display-scale) ratio itself.
  `scale` (greater than 0; 1 by default) is an extra factor on top of it. On an `x`/`y`
  target any `scale` other than 1 is an error.
- **Captures.** When a search fails, the run saves a capture of the searched area with
  the best candidate outlined in red, in the run's `captures/` directory.
- Image searches need the `vision` extra (`pip install -e ".[vision]"`); without it they
  fail with `capability_missing`.

### Embedded images

A script that searches the screen normally needs its image files next to it, so sharing
it means sharing a folder. The `images` section carries the templates inside the script
instead, and any `image` parameter refers to one with `"#name"`:

```yaml
version: 1
name: Click the pattern
steps:
  - id: click_pattern
    click: {image: "#pattern"}
  - id: check
    if:
      when: {image_visible: "#pattern"}
      then:
        - id: seen
          log: still there
  - id: dialog
    wait_image: img/dialog.png        # files keep working
images:
  pattern:
    png: |
      iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAIAAADYYG7QAAAAkUlEQVR42u3XoQ2FQBBF0a0Ai0SC
      oC9KQNIEBVAEFlr47rcDDcwVKwgJczdPbmaOetkt/34Icy1NHDjdvIdZpyNMoSNIkKB0oPb8haEF
      T98XJEhQPtC4jWGot6joaAEVKRWvIEGC8oFq30k0CIuucr4gQYLygajoaDEVKYHoI1pqm1eQIEGf
      BdGgtyJIkKB0oBvc2aEmmX2YaQAAAABJRU5ErkJggg==
```

- **Entries.** Each name (letters, digits, `_` and `-`, not starting with a digit or `-`)
  maps to `{png: ..., scale: ...}`. `png` is the base64 of the PNG file itself, so it is
  lossless and its transparency still works as a mask. Line breaks and spaces in it are
  ignored; j5 writes it as a literal block (`|`) wrapped at 76 columns.
- **Quote the reference.** Write `image: "#pattern"` with quotes: in YAML a bare `#`
  starts a comment. Anything that does not start with `#` is a path, as before.
- **Checked on load.** Every entry must be valid base64 of a PNG file, at most 1 MiB
  (1,048,576 bytes) once decoded, and all of a script's images together at most 8 MiB.
  With the `vision` extra installed j5 decodes each image as a search would; without it,
  it checks the PNG signature and the size in its header. A `"#name"` that is not in
  `images` is an error with the step and the parameter (`click: {image: "#nope"}`), not
  a failure in the middle of a run. A reference built with `${...}` is resolved when the
  step runs; an unknown name then fails with `template_missing`.
- **`scale`** is the display scale the image was captured at (a positive number, `1.5`
  for 150 %): its capture scale, which replaces the script's `capture_scale` for this
  image (see [Display scale](#display-scale)).
- **In the output.** Events and errors show the reference as written (`#pattern`) and
  never the image data, which is not copied to captures or the run history either.
- **No working directory.** A script whose templates are all embedded does not depend on
  where it is stored or where j5 runs from, so it can be pasted into a chat or loaded from
  text.

`j5 pack` embeds the image files a script uses, and `j5 unpack` writes them back to files
(see [the CLI reference](cli.md#j5-pack)):

```console
$ j5 pack save_report.yaml -o save_report.bundle.yaml
j5: embedded 2 images (#save_button, #dialog), 3 image parameters rewritten; wrote save_report.bundle.yaml
```

## Durations

A duration is a number with a unit, `ms`, `s`, `m` or `h` (`500ms`, `2s`, `1.5m`, `1h`),
or a bare number of seconds (`sleep: 2`). An optional variance, `±` or `+-` followed by a
percentage from 0 to 100, picks a value uniformly in that range on every run:
`2s±20%` waits between 1.6 and 2.4 s. The random choice comes from the run's seed, so a
run with the same `--seed` waits the same.

## Key combinations

A combination is modifiers joined with `+`, then one key, case-insensitive, with optional
spaces: `ctrl+s`, `ctrl+shift+s`, `Alt + F4`, `super+e`. The last part may be a modifier
on its own (`shift`).

- **Modifiers:** `ctrl`, `shift`, `alt`, `super`.
- **Named keys:** `enter`, `tab`, `escape`, `backspace`, `delete`, `insert`, `space`,
  `up`, `down`, `left`, `right`, `home`, `end`, `pageup`, `pagedown`, `capslock`, `menu`,
  `printscreen`, `f1` to `f24` (some keymaps lack `f13` to `f24`).
- **Characters:** a single printable ASCII character names the physical key that types
  it on a US layout (`ctrl+a`, `ctrl+/`). Write `+` as `plus` and `-` as `minus`
  (`ctrl+plus`). For non-ASCII characters use `type`.
- **Aliases:** `control`/`ctl` (ctrl); `cmd`/`command`/`win`/`windows`/`meta` (super);
  `option`/`opt` (alt); `esc`; `return` (enter); `del`; `ins`; `bksp`; `spacebar`;
  `pgup`/`page_up`; `pgdn`/`pgdown`/`page_down`; `arrowup`, `arrowdown`, `arrowleft`,
  `arrowright`; `caps`/`caps_lock`; `prtsc`/`prtscr`/`print`; `apps`/`contextmenu` (menu).

`j5 validate` only checks the shape of a combination (no empty parts, as in `ctrl++`). An
unknown key name fails when the step runs:

```console
  0.509s  ✗ bad_key  [validation_failed] cannot press 'ctrl+banana': bad key combination 'ctrl+banana': unknown key 'banana'
```

## Expressions and interpolation

### Expressions

Expressions appear in `set`'s `expr`, in `when` and `while`, in `for_each`, and inside
`${...}`. They look like Python but are evaluated by j5's own safe evaluator:

| Kind | Examples |
| --- | --- |
| Literals | `3`, `2.5`, `"text"`, `'text'`, `true`, `false`, `null`, `[1, 2]`, `{"k": 1}` |
| Variables | `attempts`, `customer` (an unknown name is an `expression_error`) |
| Indexing | `rows[0]`, `rows[-1]`, `data["name"]` (no slices) |
| Keys as attributes | `label.bbox`, `token.stdout` (only for mappings: `data.name` is `data["name"]`) |
| Arithmetic | `+ - * / // %` (`+` also joins text and lists; `"ab" * 3` repeats) |
| Comparison | `== != < <= > >=`, chainable (`1 < n <= 10`), `in`, `not in` |
| Logic | `and`, `or`, `not`, `a if condition else b` |
| Functions | see below |

| Function | Result |
| --- | --- |
| `len(x)` | Length of a text, list or mapping |
| `str(x)` | Text form (`null` is empty, booleans are `true`/`false`) |
| `int(x)`, `float(x)` | Number from a number or text |
| `bool(x)` | Truth value |
| `round(x, digits?)` | Rounded number |
| `min(...)`, `max(...)` | Smallest or largest of several values or of a list |
| `abs(x)` | Absolute value |
| `lower(t)`, `upper(t)` | Text in lower or upper case |
| `strip(t, chars?)` | Text without leading and trailing spaces (or `chars`) |
| `contains(x, item)` | Whether a text, list or mapping contains `item` |
| `startswith(t, prefix)`, `endswith(t, suffix)` | Text tests |

Methods (`name.upper()`; use `upper(name)`), attributes of anything but a mapping, `**`,
lambdas, comprehensions and slices are rejected. Expressions are limited to 2000 characters, and the values they build to
100,000 items or characters.

In YAML, quote expressions that contain `:`, `#`, quotes or start with `{` or `[`:
`when: "status == 'done'"`.

### Interpolation

String parameters (and strings inside `value`, `window`, `from`/`to` and conditions)
can contain `${expression}`:

- In text, each `${...}` is replaced by its value as text: `"report_${n}.txt"`. `null`
  becomes empty, booleans `true`/`false`.
- A string that is exactly one `${...}`, with nothing else (not even a space or a line
  break), gives the value itself and keeps its type: `x: "${pos_x}"` is an integer,
  `set: {name: copy, value: "${rows}"}` copies a list. Any other string is text, kept
  as written around the values: `type: "${body}\n"` types the body and presses Enter.
- `$${` writes a literal `${`.
- After interpolation the value is checked again against the parameter's type. So
  `log: "${count}"` with an integer `count` fails (`message` must be text); write
  `log: "${str(count)}"` or `log: "count: ${count}"`.
- `expr`, `when`, `while` and a `for_each` without `${` are expressions themselves:
  they are evaluated, not interpolated.

```console
  1.006s    ✗ show  [validation_failed] parameter 'message' after interpolation: expected a string
```

### Conditions

`when` (`if`) and `while` (`loop`) take either an expression, true when its value is
truthy, or a predefined condition written as a one-key mapping. The only predefined
condition is `image_visible`, which makes one search (not finding the image is not an
error):

```yaml
when: {image_visible: img/error.png}
when:
  image_visible: {image: img/error.png, threshold: 0.8, region: [0, 0, 400, 300], window: {title: App}}
```

The mapping form accepts `image`, `threshold`, `scale`, `region` and `window`. Write a
constant condition as a string (`while: "true"`): a bare YAML `true` is not an
expression and fails validation (`while: expected an expression or a single predefined
condition`).

## Input profiles

An input profile says how the mouse moves and how text is typed.

**Mouse** (`input: {mouse: {...}}`):

| Parameter | Default | Meaning |
| --- | --- | --- |
| `strategy` | `human` | `direct` (jump), `linear` (straight line at constant speed) or `human` (curved, bell-shaped speed) |
| `speed` | `1.0` | Speed multiplier; greater than 0 |
| `path` | `windmouse` | Path shape for `human`: `windmouse` (slightly erratic) or `bezier` (smooth curves) |
| `overshoot` | `0.1` | Probability, 0 to 1, of overshooting a long movement (over 100 px) and correcting |
| `jitter` | `1.0` | Amplitude in pixels of the small sideways tremor during a movement |
| `interference` | `pause` | What to do when you move the mouse during a movement: `pause`, `stop` or `off` |
| `tolerance` | `20` | How far, in pixels, the pointer may stray from the planned path before it counts as interference |

**Keyboard** (`input: {keyboard: {...}}`):

| Parameter | Default | Meaning |
| --- | --- | --- |
| `strategy` | `human` | `direct` (all at once), `linear` (fixed interval) or `human` (irregular rhythm) |
| `wpm` | `60` | Typing speed in words per minute; greater than 0 |
| `variance` | `0.3` | Spread of the intervals between keys; `0` is fully regular, without pauses |
| `typos` | `false` | Occasionally hit a neighbouring key and correct it with Backspace |

**Precedence.** A step's effective profile starts from the defaults above, then applies,
in order, each one overriding only the settings it names:

1. the script's `input`,
2. the run's profile (`j5 run --fast` sets both strategies to `direct`),
3. the step's `input`,
4. step parameters, such as `type`'s `keyboard`.

```yaml
input:
  keyboard: {wpm: 80}                          # 2. the whole script
steps:
  - id: fast_field
    type: {text: "${code}", keyboard: {strategy: direct}}   # 4. this step only
  - id: careful_move
    move: {image: img/slider.png}
    input: {mouse: {strategy: linear, speed: 0.5}}          # 3. this step only
```

Profiles are checked when a step uses them: an unknown setting or value fails that step
with `validation_failed` (`invalid input profile: unknown input profile fields: wobble`).
The seed that makes human-like input reproducible is not a profile setting: it belongs to
the whole run, so pass it with `j5 run --seed` (a `seed` in a profile fails with a message
saying so). There is no per-step seed: each step attempt already gets its own sub-seed
derived from the run's seed (see [How it works](how-it-works.md)).

## Params and secrets

`params` declares the values a script takes from outside. Each entry maps a name (a valid
identifier, not also used in `vars`) to a mapping of these fields, or to nothing for a
required string:

| Field | Default | Meaning |
| --- | --- | --- |
| `type` | `string` | `string`, `integer`, `number` or `boolean` |
| `description` | - | Shown by `j5 validate` and when j5 asks for the value |
| `default` | - | Value when none is given; without one the param is required. Must already have the param's type (`default: 3`, not `"3"`) |
| `secret` | `false` | Mask the value as `***` in all output and the run history; a secret cannot have a default |

```yaml
params:
  customer:                                  # required string
  password: {secret: true}
  copies: {type: integer, default: 1}
  rate: {type: number, default: 0.5}
  express: {type: boolean, default: false}
```

When the run starts, params become variables, used as `${customer}`. Values are given
with `j5 run --param`, `--param-file`, `--param-env` or `--params-file` (see
[cli.md](cli.md)); required params that are missing are asked for in a terminal.

- A **string** param is taken verbatim: no YAML or expression parsing, so `#`, quotes,
  leading zeros and line breaks are kept.
- For the other types a text value is converted only when it is exactly an integer, a
  number, or `true`/`false` (any case). Anything else fails before the run starts:
  `param 'copies' expects an integer, got 'two'`.
- An undeclared param fails too: `the script declares no param 'x'`.

**Secrets.** Every occurrence of a secret's value is replaced with `***` in the events,
the output and the run history, whatever step or variable carries it. Results kept with
`as: {name: ..., secret: true}` are secrets too (see
[Keeping a result](#keeping-a-result-as)). Only the exact text
is masked: a value transformed by an expression (`upper(password)`) is not recognised, so
use secrets as they are. `type` reports only the number of characters it typed. Pass
secrets with `--param-env`, `--param-file` or the prompt: a `--param` value stays in your
shell history. Masking protects logs, not the screen.

`vars` can be overridden for one run with `j5 run --var NAME=VALUE`, for debugging; a
param cannot.

## Validation errors and warnings

`j5 validate` reports every problem it finds as
`path:line: severity[code]: message (step id)`, then a summary. Errors make the script
invalid (exit code 1); warnings do not. `j5 run` checks the script the same way and does
not start an invalid one (exit code 2). A few examples:

```console
$ j5 validate broken.yaml
broken.yaml:6: error[validation_failed]: duration: invalid duration '2 seconds': expected e.g. 2s, 500ms, 2s±20% (step greet)
broken.yaml:6: error[validation_failed]: duplicate id 'greet' on lines 4 and 6 (step greet)
broken.yaml:8: error[unknown_step_type]: unknown step type 'clik' (step go)
broken.yaml:10: error[validation_failed]: y: 'x' and 'y' go together (step click_1)
broken.yaml:11: error[validation_failed]: speed: unknown parameter for 'type' (step typing)
broken.yaml:13: error[validation_failed]: while: use only one of 'times', 'while', 'for_each' (step retry_loop)
broken.yaml: invalid (6 errors)
```

```console
$ j5 validate bad2.yaml
bad2.yaml:2: error[validation_failed]: scale: only applies to 'image' targets (step w)
bad2.yaml:4: error[validation_failed]: threshold: must be between 0 and 1 (step t)
bad2.yaml:6: error[validation_failed]: level: expected one of debug, info, warning, error (step l)
bad2.yaml:8: error[validation_failed]: expr: use only one of 'value', 'expr' (step s)
bad2.yaml:10: error[validation_failed]: invalid id 'bad id': use letters, digits, '_' or '-', starting with a letter or '_' (step log_2)
bad2.yaml:12: error[validation_failed]: press: invalid key combination 'ctrl++' (step k)
bad2.yaml:14: error[validation_failed]: missing block 'then' (step mi)
bad2.yaml: invalid (7 errors)
```

Other messages you may see:

| Message | Cause |
| --- | --- |
| `error[parse_error]: invalid YAML: ...` | The file is not valid YAML (wrong indentation, for example); the line is where the parser gave up |
| `'click' has no short form; write its parameters as a mapping` | A scalar given to a step type without a [short form](#short-form) |
| `block 'do' must be a list of steps` | A block (`then`, `else`, `do`) that is not a list |
| `step has several type keys (...); use one per step` | Two step types in one step, often a missing `-` |
| `warning: generated id 'log_1', add an explicit id ...` | A step without `id` (see [Stable ids](#stable-ids)) |
| `warning: unknown top-level key 'extra' is ignored` | A top-level key that is not part of the format |
| `image: no embedded image named 'save'; the script has ...` | A `"#save"` reference with no `save` entry under `images` (the parameter is named, e.g. `from.image` or `when.image_visible`) |
| `image 'save': 'png' is not valid base64 (...)` | An `images` entry whose data was cut or altered; the line is the entry's |
| `image 'save': not a PNG file (wrong signature)` | Base64 of another format (JPEG, WebP...): convert it to PNG first |
| `image 'save': ... bytes once decoded, over the limit of 1,048,576 bytes (1 MiB) per image` | An image too large to embed; crop it, or keep it as a file |
| `the embedded images add up to ... bytes once decoded, over the limit ... per script` | All the images together are over 8 MiB; keep some of them as files |

Warnings are listed only when the script has no errors. `j5 validate --json` gives the
same report as JSON, with `code`, `message`, `step_id`, `line` and `details` for each
problem.

Some problems can only be found while running, because they depend on variables, the
screen or the system: a `${...}` that refers to an unknown variable (`expression_error`),
a value of the wrong type after interpolation, an unknown key name, an invalid input
profile, a missing image file (`template_missing`), a window that is not there
(`window_not_found`). A dry run (`j5 run --dry-run`) finds most of them without sending
any input.
