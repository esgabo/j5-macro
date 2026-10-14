# Tutorial

This tutorial walks you through j5-macro from installation to a complete script that
fills in a document in a text editor. Each chapter adds one idea, with a script you can
copy and the command to run it. It takes about half an hour.

You need Linux with an X11 session. Everything you type in a terminal is shown in
`console` blocks; the output is real, trimmed where it gets long.

If you want the full list of parameters for a step type, see [scripts.md](scripts.md).
For what happens under the hood, see [how-it-works.md](how-it-works.md).

## 1. Install

j5-macro needs Python 3.11 or later. It is not on PyPI yet, so install it from a clone
of the repository:

```console
$ git clone https://github.com/esgabo/j5-macro.git
$ cd j5-macro
$ python -m venv .venv && source .venv/bin/activate
$ pip install -e ".[x11,vision]"
$ j5 --version
j5-macro 0.0.1
```

The extras matter:

- `x11` lets j5 move the mouse and press keys in an X11 session.
- `vision` adds screen capture and image matching (OpenCV), which you need for the
  image steps in chapter 4 and the authoring tools in chapter 11.

**Which desktops work.** j5 drives real input on **Windows** (install the `windows` extra
instead of `x11`: `pip install -e ".[windows,vision]"`) and on **Linux with an X11
session** (it is tested on Xfce). On a **Wayland** session j5 has no input backend yet:
log out and pick an X11 session at the login screen (for example "Ubuntu on Xorg").
Until then, on Wayland you can still validate scripts and use `--dry-run`, but steps
that click or type fail with `capability_missing`:

```console
$ j5 run type_note.yaml
  0.000s  [warning] no input backend: Wayland is not supported yet; log in with an X11 session (e.g. Xfce, or 'Ubuntu on Xorg'); input steps fail with capability_missing (dry runs still work)
...
  0.004s  ✗ focus_editor  [capability_missing] step 'focus_editor' needs an input backend: Wayland is not supported yet; log in with an X11 session (e.g. Xfce, or 'Ubuntu on Xorg')
```

## 2. Your first script

A script is a YAML file with a list of `steps`. Save this as `first.yaml`:

```yaml
version: 1
name: First script
steps:
  - id: hello
    log: Hello from j5!
```

Every step has exactly one **type** (here `log`) and, ideally, an **id** (`hello`). The
id is how breakpoints, the run history and error messages refer to the step. If you leave
it out, j5 makes one up and warns you; see [Stable ids](scripts.md#stable-ids).

Check the script without running it:

```console
$ j5 validate first.yaml
first.yaml: valid
```

Now do a **dry run**. A dry run goes through the script but sends no mouse or keyboard
input, so it is always safe:

```console
$ j5 run --dry-run first.yaml
  0.006s  [info] emergency stop key: ctrl+alt+shift+q
run r-20261012-215531-430d  First script  (mode dry, seed 1091314718, backend x11)
  0.002s  ▶ hello  log  {"message": "Hello from j5!", "level": "info"}
  0.002s    [info] Hello from j5!
  0.002s  ✓ hello  1ms  {"level": "info", "message": "Hello from j5!"}
✓ completed in 0.002s  (seed 1091314718)
```

How to read it:

- The first line names the **emergency stop key** (chapter 8).
- `run r-...` is the run id, then the script's `name`, the mode, the **seed** that drives
  the random parts of human-like input, and the input **backend**.
- `▶` starts a step: its id, its type and its parameters, with defaults filled in
  (`level: info`).
- Indented lines are what happens inside the step, here the message itself.
- `✓` finishes the step with how long it took and its result.
- The last line is the outcome. The exit code is 0 for completed, 1 for failed, 3 for
  stopped (see [cli.md](cli.md)).

Drop `--dry-run` for a real run. With only a `log` step it looks the same, except for
`mode normal`.

## 3. Clicking and typing

Open a text editor with an empty document. The examples use Mousepad (Xfce's editor);
any editor works if you change the window title. Save this as `type_note.yaml`:

```yaml
version: 1
name: Type a note
steps:
  - id: focus_editor
    click: {window: {title: Mousepad}, x: 200, y: 150}
  - id: type_greeting
    type: "Dear team,\nthe report is ready.\n"
  - id: save
    key: ctrl+s
```

- `click` with `x`/`y` alone clicks at those **desktop** pixels. With
  `window: {title: Mousepad}`, `x` and `y` are relative to the top-left corner of the
  first visible window whose title contains `Mousepad`, so the script keeps working when
  you move the window.
- `type` types text. `\n` presses Enter; that needs double quotes in YAML. Accents and
  symbols (`Ñandú café`) work whatever your keyboard layout.
- `key` presses a key or a combination: `enter`, `tab`, `ctrl+s`, `alt+f4`,
  `ctrl+shift+s`.

Do a dry run first to see what would happen:

```console
$ j5 run --dry-run type_note.yaml
run r-20261012-214808-0caf  Type a note  (mode dry, seed 837908993, backend x11)
  0.002s  ▶ focus_editor  click  {"x": 200, "y": 150, "window": {"title": "Mousepad"}, "button": "left", "clic...
  0.003s    [info] would click left at (200, 150) in window {'title': 'Mousepad'}
  0.003s  ▶ type_greeting  type  {"text": "Dear team,\nthe report is ready.\n"}
  0.003s    [info] would type 32 characters
...
✓ completed in 0.003s  (seed 837908993)
```

Then run it for real and keep your hands off the mouse:

```console
$ j5 run --seed 42 type_note.yaml
run r-20261012-214809-ef70  Type a note  (mode normal, seed 42, backend x11)
  0.001s  ▶ focus_editor  click  {"x": 200, "y": 150, "window": {"title": "Mousepad"}, "button": "left", "clic...
  0.638s  ✓ focus_editor  637ms  {"x": 300, "y": 230, "button": "left", "clicks": 1, "window": "Mousepad", "wi...
  0.639s  ▶ type_greeting  type  {"text": "Dear team,\nthe report is ready.\n"}
  7.679s  ✓ type_greeting  7040ms  {"chars": 32}
  7.679s  ▶ save  key  {"press": "ctrl+s"}
  7.860s  ✓ save  181ms  {"press": "ctrl+s"}
✓ completed in 7.861s  (seed 42)
```

The result of the click shows the absolute point it used (`300, 230`), the window and
its area (`window_area`, `[x, y, width, height]`). The pointer took 0.6 s to get there along a curved path,
and the text took 7 s: by default j5 moves and types like a person.

### Input profiles

How the mouse moves and how text is typed is the **input profile**. Each device has a
**strategy**:

| Strategy | Mouse | Keyboard |
| --- | --- | --- |
| `direct` | jumps to the target | types the whole text at once |
| `linear` | straight line at constant speed | a fixed interval between keys |
| `human` (default) | curved path, bell-shaped speed, occasional overshoot | irregular rhythm, pauses after punctuation |

Set the profile for the whole script with `input`, and override it for one step with the
step's `input`, or for one `type` step with `keyboard`:

```yaml
input:
  mouse: {strategy: human, path: bezier, speed: 1.5}
  keyboard: {strategy: human, wpm: 90}
steps:
  - id: quick_move
    move: {x: 900, y: 600}
    input: {mouse: {strategy: linear, speed: 2}}
  - id: paste_like
    type: {text: "SKU-0042", keyboard: {strategy: direct}}
  - id: slow_typing
    type: {text: "slowly", keyboard: {wpm: 30, typos: true}}
```

`typos: true` makes the occasional mistake and corrects it with Backspace, like a person.
It is off by default because autocomplete in the target application could get in the
way. All profile settings are listed in [scripts.md](scripts.md#input-profiles).

When you are developing a script and do not want to wait, add `--fast`. It makes `direct`
the run's strategy for both devices; a step's own `input` or `keyboard` still wins:

```console
$ j5 run --fast type_note.yaml
...
  0.007s  ▶ type_greeting  type  {"text": "Dear team,\nthe report is ready.\n"}
  0.030s  ✓ type_greeting  23ms  {"chars": 32}
...
✓ completed in 0.032s  (seed 918250029)
```

Runs are reproducible: `--seed 42` gives the same movements and typing rhythm again, as
long as the screen and the pointer start in the same place.

## 4. Waiting for things on screen

Fixed coordinates break when a button moves. Instead, give j5 a picture of the button and
let it find it.

**Make a template.** A template is a small PNG of what you want to find, cut from a
screenshot taken at the same display scaling. The quickest way is `j5 capture -o
img/save_button.png`: drag a rectangle around the button and it is saved (chapter 11
shows it and its siblings). By hand:

1. Take a screenshot with your usual tool (Xfce's Screenshot tool can capture a
   region), or with ImageMagick: `import -window root full.png`.
2. Crop it tightly around the button. With ImageMagick, for a 90×36 button at (620, 440):
   `convert full.png -crop 90x36+620+440 +repage img/save_button.png`.
3. Keep some texture or an edge in it (text, an icon border). A template of a single flat
   colour never matches.

Transparent pixels of a PNG are ignored, so you can erase the background around an icon
in an image editor and it will match on any background.

Put templates next to the script (`img/save_button.png`): image paths are relative to the
script file. Now the script:

```yaml
version: 1
name: Save with the button
steps:
  - id: wait_save_button
    wait_image_in: {image: img/save_button.png, timeout: 15s}
  - id: click_save
    click: {image: img/save_button.png}
  - id: check_error
    if:
      when: {image_visible: img/error_icon.png}
      then:
        - id: report_error
          log: {message: "The editor shows an error", level: warning}
      else:
        - id: report_ok
          log: Saved without errors
```

- `wait_image_in` checks the screen every 0.25 s until the image appears, for up to
  `timeout` (10 s by default). Its opposite, `wait_image_out`, waits until an image is
  gone (a spinner, a "Loading..." label), and `wait_window_in` and `wait_window_out`
  wait for a window to open or close (see [Waiting](scripts.md#waiting-wait_image_in-wait_image_out-wait_window_in-wait_window_out)).
- `click: {image: ...}` searches once and clicks inside the match. With the `human`
  strategy it lands at a natural spot inside the button, not always its exact centre.
- `image_visible` in an `if` searches once and picks a branch; not finding the image is
  not an error.

```console
$ j5 run --seed 7 save_button.yaml
run r-20261012-215611-a8b5  Save with the button  (mode normal, seed 7, backend x11)
  0.003s  ▶ wait_save_button  wait_image_in  {"image": "img/save_button.png", "timeout": "15s", "threshold": 0.9}
  1.513s  ✓ wait_save_button  1510ms  {"found": true, "score": 1.0, "bbox": [620, 440, 90, 36]}
  1.514s  ▶ click_save  click  {"image": "img/save_button.png", "button": "left", "clicks": 1, "threshold": ...
  1.906s  ✓ click_save  392ms  {"x": 665, "y": 458, "button": "left", "clicks": 1, "image": "img/save_button...
  1.906s  ▶ check_error  if  {"when": {"image_visible": "img/error_icon.png"}}
  1.916s  ⑂ check_error  if {"image_visible": "img/error_icon.png"} = false → else
  1.916s    ▶ report_ok  log  {"message": "Saved without errors", "level": "info"}
  1.916s      [info] Saved without errors
...
✓ completed in 1.915s  (seed 7)
```

**Tuning the search.**

- `threshold` (0.9 by default) is the minimum match score, from 0 to 1. Lower it a little
  if the button looks slightly different each time (anti-aliasing, a hover glow).
- `region: [x, y, width, height]` searches only part of the screen, which is faster and
  avoids look-alikes. With `window`, the region is relative to that window.
- Templates follow the display scale on their own: j5 detects the screen's scale and
  resizes them from the scale they were captured at (the script's `capture_scale`, 1 by
  default; see [Display scale](scripts.md#display-scale)). `scale` is an extra factor
  on top, rarely needed.
- A `click`, `move` or `drag` on an image searches only once. Add the common field
  `timeout: 5s` to the step to let it wait for the image first.

**When an image is not found**, the step fails with `image_not_found`, reports the best
score it saw, and saves a capture of the searched area with the best candidate outlined
in red. Here the button is not on screen yet and the search is limited to a region of the
editor's window:

```yaml
  - id: wait_save_button
    wait_image_in:
      image: img/save_button.png
      timeout: 2s
      region: [400, 300, 300, 100]
      window: {title: Mousepad}
```

```console
  2.147s  ✗ wait_save_button  [image_not_found] img/save_button.png not found after 9 attempt(s) in 2s (best score 0.096, threshold 0.9)
  2.147s    screenshot: captures/0001-wait_save_button-9.png
```

The capture is in the run's directory (chapter 9). A best score close to the threshold
means the template is nearly right; a very low one means the image is not on screen, or
the search looked in the wrong place.

In a dry run each image search runs once and the step says where it would act, so a dry
run is a good way to check your templates.

## 5. Logic: variables, branches and loops

Scripts can keep **variables**, compute with **expressions**, and repeat or skip steps.
This script runs on any machine (it sends no input):

```yaml
version: 1
name: Logic tour
vars:
  customers: [ACME, Globex, Initech]
  done: 0
  retries_left: 3
steps:
  - id: each_customer
    loop:
      for_each: customers
      as: customer
      do:
        - id: greet
          log: "Processing ${customer} (${len(customer)} letters)"
        - id: count
          set: {name: done, expr: "done + 1"}
  - id: summary
    if:
      when: "done == len(customers)"
      then:
        - id: all_done
          log: "All ${done} customers processed"
      else:
        - id: some_missing
          log: {message: "Only ${done} processed", level: warning}
  - id: countdown
    loop:
      while: "retries_left > 0"
      do:
        - id: decrement
          set: {name: retries_left, expr: "retries_left - 1"}
        - id: breathe
          sleep: 200ms±50%
  - id: typed_value
    set: {name: total, value: "${done * 10}"}
  - id: show
    log: "total ${total} (${'big' if total > 20 else 'small'}), literal $${done}"
```

- `vars` declares variables with their starting values.
- `set` changes (or creates) a variable, from a literal `value` or an `expr`ession.
- `${...}` inside a string inserts the value of an expression. A string that is only one
  `${...}` keeps the value's type, so `total` is the number 30, not the text `"30"`.
  `$${` writes a literal `${`.
- `if` takes a condition in `when`: an expression, or `image_visible` as in chapter 4.
- `loop` repeats its `do` block: `times: 3` (or `times: "${n}"`), `while: <condition>` (checked before each
  iteration) or `for_each: <list>` with the item in `as` (`item` by default).
- `sleep: 200ms±50%` waits between 100 and 300 ms; the variance makes pauses less
  mechanical.

```console
$ j5 run logic.yaml
run r-20261012-215729-6b24  Logic tour  (mode normal, seed 5, backend x11)
  0.003s  ▶ each_customer  loop  {"for_each": "customers", "as": "customer"}
  0.004s  ↻ each_customer  iteration 1/3
  0.004s    customer = "ACME"  (was null)
  0.004s    ▶ greet  log  {"message": "Processing ACME (4 letters)", "level": "info"}
  0.004s      [info] Processing ACME (4 letters)
  0.005s    ✓ greet  1ms  {"level": "info", "message": "Processing ACME (4 letters)"}
...
  0.006s  ⑂ summary  if done == len(customers) = true → then
...
  0.565s    total = 30  (was null)
...
  0.566s    [info] total 30 (big), literal ${done}
✓ completed in 0.564s  (seed 5)
```

The output shows every variable change (`customer = "ACME"`), loop iteration (`↻`) and
branch taken (`⑂`). Expressions are not Python: only a safe subset is allowed, listed in
[scripts.md](scripts.md#expressions).

### When a step fails

By default a failing step stops the run. The common field `on_error` changes that:

```yaml
steps:
  - id: dismiss_banner
    click: {image: img/cookie_banner.png}
    on_error: continue     # the banner may not be there: log a warning and go on
  - id: open_report
    click: {image: img/report.png}
    timeout: 5s
    on_error: retry        # up to 3 attempts in total, then stop
  - id: submit
    click: {image: img/submit.png}
    on_error: pause        # let me fix it by hand
```

With `pause`, j5 asks you what to do (here the template file does not exist yet):

```console
  0.003s  ▶ submit  click  {"image": "img/submit.png", "button": "left", "clicks": 1, "threshold": 0.9}
  0.143s  ✗ submit  [template_missing] image file not found: /home/me/macros/img/submit.png
  0.144s  ⏸ paused (error)
j5: step 'submit' failed; press Enter to retry the step, or s + Enter to skip it (Ctrl+C stops the run)
s
  1.047s  ↷ submit  skipped (user)
```

Use `pause` only when someone is at the terminal: without one, j5 retries the step on its
own, again and again.

## 6. Passing values in

Hard-coded text makes a script good for one job. Declare **params** instead and give
them values when you run it:

```yaml
version: 1
name: Log in
params:
  user: {description: Account name}
  password: {secret: true, description: Account password}
  copies: {type: integer, default: 1}
  remember: {type: boolean, default: false}
steps:
  - id: type_user
    type: "${user}"
  - id: next_field
    key: tab
  - id: type_password
    type: "${password}"
```

A param has a `type` (`string` by default, `integer`, `number` or `boolean`), an optional
`description`, and a `default`. A param without a default is **required**. `j5 validate`
lists them:

```console
$ j5 validate login.yaml
param user (string, required): Account name
param password (string, secret, required): Account password
param copies (integer, default 1)
param remember (boolean, default false)
login.yaml: valid
```

Give values with any of these options:

| Option | Value from |
| --- | --- |
| `--param NAME=VALUE` | the command line, exactly as written (`#`, quotes and leading zeros kept) |
| `--param-file NAME=PATH` | a UTF-8 file, without its final line break; `-` reads standard input |
| `--param-env NAME=VARIABLE` | an environment variable |
| `--params-file FILE` | a YAML or JSON mapping of names to values |

```console
$ export LOGIN_PASSWORD='s3cr3t!'
$ j5 run --dry-run login.yaml --param 'user=ACME #42' --param-env password=LOGIN_PASSWORD --param copies=3
run r-20261012-215533-1b51  Log in  (mode dry, seed 387413297, backend x11)
  0.002s  ▶ type_user  type  {"text": "ACME #42"}
...
  0.003s  ▶ type_password  type  {"text": "***"}
  0.003s    [info] would type 7 characters
...
```

If you leave out a required param and run `j5` in a terminal, it asks for it; a secret
is read without echo. Outside a terminal the run does not start
(`missing required params: 'user', 'password'`, exit code 2). Values are checked against
the type: `--param copies=two` fails with `param 'copies' expects an integer, got 'two'`.

**Secrets.** Mark passwords and tokens `secret: true`. Their values show as `***` in the
output and in the run history, and they cannot have a default (a secret must not live in
the script file). Pass them with `--param-env`, `--param-file` or the prompt, never with
`--param`: a value on the command line stays in your shell history and is visible in the
process list. Masking protects logs, not the screen: the application shows what j5 types
unless its field hides it.

## 7. Debugging, briefly

`--step` starts the run paused before the first step and gives you a prompt; `--break ID`
pauses before a given step (repeat it for several). At the prompt, `n` runs the current
step, `p EXPR` prints an expression, `vars` shows the variables and `c` continues:

```console
$ j5 run --break summary logic.yaml
...
  0.008s  breakpoint summary
  0.008s  ⏸ paused (breakpoint)
-> summary  if  (logic.yaml:17)
(j5) p done
3
(j5) n
  0.012s  ⑂ summary  if done == len(customers) = true → then
...
  0.012s  ⏸ paused (step)
-> countdown  loop  (logic.yaml:26)
(j5) c
```

You can also put `break: true` on a step in the script, or a `pause:` step that waits for
Enter. The full debugger, and debugging in VS Code, are in [debugging.md](debugging.md).

## 8. Safety

A macro that goes wrong moves your mouse and types into whatever has the focus, so j5
gives you three ways to take back control:

- **Emergency stop key.** Press `ctrl+alt+shift+q` at any time, whichever window has the
  focus. The run stops and every key and button j5 was holding is released. Choose
  another combination with the `J5_STOP_KEY` environment variable
  (`J5_STOP_KEY=ctrl+alt+k j5 run ...`).
- **Corner failsafe.** Push the mouse into a corner of the screen while j5 is moving it.
- **Interference pause.** If you move the mouse while j5 is moving it, the run pauses
  instead of fighting you. In a terminal j5 asks whether to retry or skip the step;
  without one it retries from wherever you left the pointer. Set
  `input: {mouse: {interference: stop}}` to stop instead, or `off` to disable it.

```console
  1.595s  you moved the mouse: the pointer was at (1200, 50), expected near (421, 251); pausing the run
  1.595s  ⏸ paused (interference)
  1.634s  [warning] emergency stop key (ctrl+alt+shift+q) pressed: stopping the run
■ stopped (user) after 1.635s  (seed 1)
```

(Two runs combined: the first was paused by interference, the second stopped with the
key.)

The corner failsafe and the interference check work by watching the pointer during a
movement, so they need movements that take time: with the `direct` strategy (`--fast`)
the pointer jumps and only the stop key remains. A run stopped by the key or the
failsafe exits with code 3. Ctrl+C in the terminal stops the run too (exit code 130).

## 9. Run history

Every run is saved with its events and screen captures. j5 keeps the newest 20, in
`~/.local/state/j5-macro/runs` (choose another directory with `--runs-dir` or
`J5_RUNS_DIR`):

```console
$ j5 runs
RUN                     STARTED              SCRIPT           MODE    STATUS     DURATION
r-20261012-215542-edc9  2026-10-12 18:55:42  Wait that fails  normal  failed       2.146s
r-20261012-215533-1b51  2026-10-12 18:55:33  Log in           dry     completed    0.003s
r-20261012-215532-34cd  2026-10-12 18:55:32  Logic tour       normal  completed    0.566s
r-20261012-215531-430d  2026-10-12 18:55:31  First script     dry     completed    0.002s
$ j5 runs show r-20261012-215542-edc9
saved run r-20261012-215542-edc9
  script    /home/me/macros/fail_wait.yaml
  started   2026-10-12 18:55:42
  finished  2026-10-12 18:55:45
  captures  1 file in /home/me/.local/state/j5-macro/runs/r-20261012-215542-edc9
    captures/0001-wait_save_button-9.png
run r-20261012-215542-edc9  Wait that fails  (mode normal, seed 1051440346, backend x11)
...
✗ failed after 2.146s  (seed 1051440346): [image_not_found] img/save_button.png not found after 9 attempt(s) in 2s (best score 0.096, threshold 0.9)
```

`j5 runs show` replays the output as `j5 run` showed it, and lists the captures; open the
PNG to see what j5 saw. `j5 runs show --json RUN_ID` prints the raw events, and
`j5 runs delete RUN_ID` removes a run. See [cli.md](cli.md) for every option.

## 10. A complete example

[`examples/write_note.yaml`](../examples/write_note.yaml) puts it all together: it
focuses a text editor by its window title, types a greeting and a body passed in as
params (a multi-line body comes from a file), repeats the body as many times as asked,
signs, and saves the document under a new name with Save As.

The parts worth a look:

```yaml
params:
  recipient: {description: Who the note is for}
  body: {description: Text of the note; pass multi-line text with --param-file}
  filename: {description: File name to save the note as}
  signature: {description: Closing line, default: "Best regards"}
  copies: {type: integer, default: 1, description: How many times to repeat the body}
vars:
  editor: Mousepad
steps:
  - id: focus_editor
    click: {window: {title: "${editor}"}, x: 200, y: 150}
    on_error: pause
  - id: write_body
    loop:
      times: "${copies}"
      do:
        - id: type_body
          type: "${body}\n"
  - id: type_filename
    type: {text: "${filename}", keyboard: {strategy: linear, wpm: 120}}
```

Open an empty document in Mousepad (or change `editor`), write the body in `note.txt`,
and try it dry first:

```console
$ j5 run --dry-run examples/write_note.yaml --param recipient=Ana \
    --param-file body=note.txt --param filename=note_for_ana.txt
run r-20261012-215714-563f  Write a note  (mode dry, seed 1702225079, backend x11)
...
  0.007s  ▶ write_body  loop  {"times": 1, "as": "item"}
  0.007s  ↻ write_body  iteration 1/1
  0.007s    ▶ type_body  type  {"text": "Thanks for the review.\nThe figures are in the shared folder.\n"}
  0.007s      [info] would type 61 characters
...
  1.033s    [info] Saved note_for_ana.txt with 1 copy of the body
✓ completed in 1.030s  (seed 1702225079)
$ j5 run examples/write_note.yaml --param recipient=Ana \
    --param-file body=note.txt --param filename=note_for_ana.txt --param copies=2
```

`"${body}\n"` types the body and then presses Enter: only a string that is exactly one
`${...}`, with nothing else around it, gives the bare value; any other string is text
(see [Interpolation](scripts.md#interpolation)).

## 11. Writing scripts faster

A script is mostly text, except for the parts that need the screen: a template to look
for, the coordinates of a point inside a window, and whether a template really matches.
`j5` does those three for you and prints a step you can paste (see [cli.md](cli.md#authoring-tools)
for every option). They need the `vision` extra, and the interactive ones need tkinter
(on Debian and Ubuntu: `sudo apt install python3-tk`).

**Capture a template.** Open the editor, then:

```console
$ j5 capture --embed save_button.yaml --name save_button
j5: drag a rectangle on the screen (Escape cancels)
j5: embedded [620, 440, 90, 36] as #save_button in save_button.yaml
click: {image: "#save_button", window: {title: Untitled 1 - Mousepad}}
```

The screen freezes and you drag a rectangle around the button; a magnifier next to the
pointer shows the pixels under it, so you can start and end exactly on its edge. The
template goes into the script itself, in its `images` section, so the script stays one
file; `-o img/save_button.png` saves a PNG file instead. The last line is a step that
uses it. For an icon on a background that changes, add `--transparent`: the pixels of the
border's colour become transparent and are ignored when matching. If you capture a flat
colour, j5 warns you, since it would never match. And if your display is scaled (125 %,
150 %), the embedded image remembers the scale it was captured at, so the script still
finds it on a machine at another scale.

**Pick a point.** For fixed coordinates inside a window:

```console
$ j5 pick
j5: click a point on the screen (Escape cancels)
j5: (212, 176) on the desktop, (212, 148) in 'Untitled 1 - Mousepad'
click: {window: {title: Untitled 1 - Mousepad}, x: 212, y: 148}
```

The point is relative to the window's content, so the step still works when the window
moves. Shorten the title to the part that does not change (`{title: Mousepad}`).

**Test a match.** Before running anything, check that a template is found, and where:

```console
$ j5 find '#save_button' --script save_button.yaml --show
j5: #save_button found at [620, 440, 90, 36], score 1.000; scale 1 = display 1 / capture 1
click: {image: "#save_button"}
```

`find` searches exactly as a step would. `--show` outlines the best match on the screen
for three seconds: green when found, red with its score when not, which tells you
whether the template is wrong (a low score) or the threshold is a little too strict (a
score just under it). It exits with code 1 when the image is not found.

**Without the mouse.** Every tool also works without the overlay, which is how an agent
(or a script of your own) writes scripts: list the windows, look at a screenshot, capture
a rectangle by its coordinates, and check it.

```console
$ j5 windows
[0, 28, 1280, 772]       Untitled 1 - Mousepad
$ j5 screenshot --window Mousepad
/tmp/j5-macro/screenshot-20261013-101500-123456.png
$ j5 capture --bbox 620,412,90,36 --window Mousepad --embed save_button.yaml --name save_button
$ j5 find '#save_button' --script save_button.yaml --window Mousepad
$ j5 validate save_button.yaml
$ j5 run --dry-run save_button.yaml
```

With `--bbox` and `--window`, the rectangle is relative to the window's content, as the
screenshot of that window shows it. Add `--json` to any of them for the engine's full
answer.

## Next steps

- [scripts.md](scripts.md): every step type and parameter, expressions, input profiles
  and params, as a reference.
- [how-it-works.md](how-it-works.md): what a run is, how human-like input and image
  search work, and the safety features.
- [cli.md](cli.md): every `j5` command and option.
- [debugging.md](debugging.md): the terminal debugger and debugging in VS Code.
