# j5 script format: cheat sheet

The full reference is `docs/scripts.md` in the repository
(https://github.com/esgabo/j5-macro/blob/main/docs/scripts.md). `j5 steps` prints every
step type and parameter of the installed version; `j5 schema` prints the JSON Schema.

## Top-level keys

| Key | Meaning |
| --- | --- |
| `version` | `1` (the default and only version) |
| `name` | Shown in output and history; defaults to the file stem |
| `input` | Input profile: `{mouse: {...}, keyboard: {...}}` (below) |
| `capture_scale` | Display scale the templates and pixel offsets were taken at (`1` by default, `1.5` for 150 %) |
| `params` | Values from outside: `name: {type?, description?, default?, secret?}` |
| `vars` | Variables and starting values (any YAML: numbers, text, lists, mappings) |
| `steps` | **Required.** The list of steps |
| `images` | Embedded templates: `name: {png: <base64>, scale?}`, used as `"#name"` |

Unknown top-level keys are ignored with a warning.

## A step

Exactly one step-type key plus optional common fields:

| Common field | Meaning |
| --- | --- |
| `id` | Stable id (`[A-Za-z_][A-Za-z0-9_-]*`, unique). Always set it: generated ids give warnings |
| `as` | Keep the step's result in a variable: `as: label` or `as: {name: token, secret: true}` |
| `on_error` | `stop` (default), `continue`, `retry` (3 attempts in all), `pause` (unattended: retried twice, then exit 1) |
| `timeout` | Replaces the `timeout` of the `wait_*` steps and `exec`; makes `click`/`move`/`drag` image targets wait up to this long (for their `window` too) |
| `input` | Input profile override for this step |
| `break` | `true`: breakpoint before this step |

Short forms: `type: "text"`, `key: ctrl+s`, `wait_image_in: img/ok.png` (also
`wait_image_out`), `wait_window_in: Mousepad` (also `wait_window_out`), `sleep: 2s`,
`log: Done`, `pause: Check it`, `exec: mousepad notes.txt`. Other types need a mapping.

## Step types

| Step | Parameters (default) | Result (what `as` keeps) |
| --- | --- | --- |
| `click` | one of `image` / `at` / `x`+`y`; `window`, `button` (`left`/`right`/`middle`), `clicks` (1), `offset`, `threshold` (0.9), `scale` (1), `region` | `x`, `y`, `button`, `clicks`; image/`at` targets add `bbox`, `center`, `window?`, `window_bbox?`; image targets `image`, `score`; `x`/`y` in a window adds `window`, `window_area` |
| `move` | as `click` without `button`/`clicks` | `x`, `y` + the same target fields |
| `drag` | `from`, `to` (each `{x, y, window?}`, `{image, threshold?, scale?, region?, window?}` or `{at}`, each with `offset?`), `button` | `from`, `to`, `button`, target fields prefixed `from_`/`to_` |
| `scroll` | `dx` (0), `dy` (0), in notches; positive = right/down; acts where the pointer is | `dx`, `dy` |
| `type` | `text` (required), `keyboard` (profile override) | `chars` (never the text) |
| `key` | `press` (required, e.g. `ctrl+shift+s`), `hold` | `press`, `hold?` |
| `wait_image_in` | `image` (required), `timeout` (10s), `threshold` (0.9), `region`, `window`, `scale` (1); waits until the image is visible, else `image_not_found` | `found`, `score`, `bbox`, `center`, `window?`, `window_bbox?` |
| `wait_image_out` | as `wait_image_in`; waits until the image is no longer visible, else `still_visible` | `gone` (true), `waited_ms` |
| `wait_window_in` | `title` (required, substring, case-sensitive), `timeout` (10s); waits until such a window is visible, else `window_not_found` | `window` (full title), `bbox` (client area) |
| `wait_window_out` | `title` (required), `timeout` (10s); waits until no visible window matches, else `still_visible` | `gone` (true), `waited_ms` |
| `sleep` | `duration` (required; `2s`, `500ms`, `2s±20%`) | `seconds` |
| `if` | `when` (condition), `then` (steps), `else?` (steps) | `branch` |
| `loop` | exactly one of `times`, `while`, `for_each` (+ `as`, default `item`); `do` (steps) | `iterations` |
| `set` | `name`, and one of `value` (literal, interpolated) or `expr` | `name`, `value` |
| `log` | `message` (required), `level` (`debug`/`info`/`warning`/`error`) | `level`, `message` |
| `pause` | `message?` (unattended: resumes on its own) | `message` |
| `exec` | `command` (list, or string split without a shell), `shell` (false), `wait` (false), `timeout`, `cwd` (script dir), `env`, `check` (true). **Needs `j5 run --allow-exec`** | with `wait`: `exit_code`, `stdout`, `stderr`, `truncated?`; without: `pid` |

Waits poll every 0.25 s; paused time does not count; `stop` ends them at once. An `_out`
step whose target is already gone finishes at once.

In a dry run, input steps, waits and `exec` report `dry_run: true` and a `description`,
plus `found`, `score`, `bbox`, `center` when an image was searched (`found`, `window`,
`bbox?` for window waits; an unmatched `wait_window_in` keeps the `title` as `window`).
Each wait checks once (`--strict`: and fails if that check is not satisfied, until
the first input or `exec` step; after it, a warning). A dry
`exec` result has `exit_code: 0` and empty `stdout` and `stderr` with `wait` (`pid:
null` without), so later steps that use them still check.

## Targets, windows, offsets

- `x`/`y`: physical pixels on the desktop; with `window: {title: T}` relative to the
  client area of the first visible window whose title **contains** `T` (case-sensitive).
  Missing window: `window_not_found` at once, except in steps that wait (image waits,
  image targets with the common `timeout`): they wait for the window too, and fail with
  `window_not_found` only if it never appeared.
- `image: "#name"` (embedded, quoted) or `image: img/x.png` (relative to the script's
  folder). The action aims at the match; without `offset` the human strategy lands
  anywhere inside it.
- `at: "${label}"`: a kept image result. No new search; follows the window if it moved.
  Not combinable with `image`, `x`/`y` or `window`.
- `offset: [dx, dy]`: pixels, or with image/`at` targets multiples of the target size:
  `0.5w`, `-2h`, `1.5h` (no spaces). `w`/`h` on `x`/`y` targets is an error.
- `region: [x, y, w, h]` (relative to `window` when given), or next to a kept result:
  `region: {at: "${label}", offset: [2.5w, 0], size: [4w, 1.5h]}`.
- Matching: normalized cross-correlation, score 0..1, found when `score >= threshold`.
  PNG pixels with alpha < 128 are ignored. Flat-colour templates never match.
- Scale: templates are resized by `display_scale / capture_scale` (times `scale`). Pixel
  offsets of image/`at` targets follow it; `x`/`y` and `region: [...]` never change.

## Expressions and interpolation

- `${expr}` inside strings. A string that is exactly one `${...}` keeps the value's type
  (`x: "${px}"` is an integer); otherwise it becomes text. `$${` is a literal `${`.
- `set.expr`, `when`, `while` and `for_each` (without `${`) are expressions themselves.
- Literals `3`, `2.5`, `"t"`, `true`, `false`, `null`, `[1, 2]`, `{"k": 1}`; indexing
  `rows[0]`, `d["k"]`; mapping keys as attributes `label.bbox`, `row.name`;
  `+ - * / // %`; comparisons (chainable), `in`, `not in`; `and`, `or`, `not`;
  `a if cond else b`.
- Functions: `len str int float bool round min max abs lower upper strip contains
  startswith endswith`. No methods (`x.upper()`), `**`, lambdas, comprehensions, slices.
- Quote YAML values that contain `:` or `#` or start with `{`/`[`:
  `when: "status == 'done'"`.
- Interpolated values are type-checked again: `log: "${n}"` with an integer fails; write
  `"${str(n)}"` or `"n = ${n}"`.

## Conditions

`when`/`while` take an expression (truthy) or `image_visible` (one search; not found is
false, and so is a missing `window`):

```yaml
when: {image_visible: "#error_icon"}
when: {image_visible: {image: "#error_icon", threshold: 0.8, window: {title: App}}}
while: "attempts < 5"           # a constant must be a string: while: "true"
```

## Params and secrets

```yaml
params:
  customer:                                   # required string
  password: {secret: true}                    # never has a default
  copies: {type: integer, default: 1}         # string, integer, number, boolean
```

Given with `--param NAME=VALUE`, `--param-env NAME=VAR`, `--param-file NAME=PATH` (`-` is
stdin) or `--params-file values.yaml`. Strings are taken verbatim. Secret values (and
results kept with `as: {..., secret: true}`) show as `***` in all events and history;
only the exact text is masked, so use them as they are (`"${password}"`, or
`"${strip(token.stdout)}"`), not transformed.

## Keys

Modifiers `ctrl`, `shift`, `alt`, `super` + one key: `enter`, `tab`, `escape`,
`backspace`, `delete`, `insert`, `space`, `up`/`down`/`left`/`right`, `home`, `end`,
`pageup`, `pagedown`, `f1`..`f24`, `menu`, `printscreen`, or one ASCII character
(`ctrl+a`). Write `+`/`-` as `plus`/`minus`. In `type`, `"\n"` (double-quoted YAML)
presses Enter, `\t` Tab, `\b` Backspace; non-ASCII text works in `type`, not in `key`.

## Input profiles

```yaml
input:
  mouse: {strategy: human, speed: 1.0, path: windmouse, overshoot: 0.1, jitter: 1.0,
          interference: pause, tolerance: 20}
  keyboard: {strategy: human, wpm: 60, variance: 0.3, typos: false}
```

Strategies: `direct` (instant), `linear`, `human` (default). `j5 run --fast` forces
`direct` for both. `interference`: `pause` (default), `stop` or `off`. Precedence: script
`input` < run (`--fast`) < step `input` < step parameters (`type.keyboard`). The seed is
per run (`--seed N`), never in a profile.
