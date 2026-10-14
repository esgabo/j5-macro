---
name: j5-macro
description: Automate desktop GUI tasks with j5 (j5-macro), a mouse and keyboard macro tool driven by YAML macro scripts. Use when asked to click, type, fill forms, wait for or click on images on the screen, repeat a GUI task, or write, debug or run a j5 script on Windows or Linux X11. Covers looking at the screen (j5 windows, j5 screenshot), capturing and testing image templates (j5 capture, j5 find), writing and validating scripts, dry runs, running them and reading the JSON events and failure captures.
---

# j5-macro: desktop automation with YAML scripts

j5 drives the real mouse and keyboard from a YAML script of steps (click, type, key,
wait for an image, if, loop, exec...). It finds things on the screen by image matching,
works relative to windows, moves and types like a person (or instantly with `--fast`),
and reports every step as JSON events with an exit code. You write the script; j5 runs
it. Use it when a task needs a GUI that has no API or command line.

Do not use it for web pages you can reach with a browser tool, or for anything a
command, file edit or API call can do: GUI automation is the slow, fragile last resort.

## Requirements

- **Platforms:** Windows 10/11, and Linux with an **X11** session. Wayland is not
  supported (no backend: input steps fail with `capability_missing`); the user must log
  in with an X11/Xorg session. macOS: validation and dry runs only.
- **Install** (not on PyPI yet, so from GitHub; Python 3.11+):
  `pipx install "j5-macro[x11,vision] @ git+https://github.com/esgabo/j5-macro"`
  (on Windows use the `windows,vision` extras). `vision` is needed for every image
  feature and the authoring tools. Check with `j5 --version`.
- A desktop session: on Linux, `DISPLAY` must be set. On a headless machine use
  `xvfb-run -a -s "-screen 0 1280x800x24" j5 ...`.

## Safety rules (always)

1. **j5 sends real input to the user's desktop.** Before any `j5 run` without
   `--dry-run`, tell the user what the script will do and get their go-ahead, unless they
   already asked you to run it. Whatever has the focus receives the keys.
2. **Dry run first.** `j5 run --dry-run` sends no input, searches each image once and
   checks params, expressions and windows. Fix everything it reports before a real run.
3. **Never put secrets in the script or the command line.** Declare a param with
   `secret: true` and pass it with `--param-env NAME=VAR` or `--param-file`. Results of
   steps that carry secrets get `as: {name: ..., secret: true}`. Secrets show as `***`
   in all output; do not echo or log them yourself.
4. **`exec` needs explicit consent.** Steps that start programs only run with
   `j5 run --allow-exec`. Add that flag only when the user agreed that this script may
   start those programs; never add it to make a failure go away.
5. **One run at a time**, and never while the user is working in the same windows.
   The user can stop any run with `ctrl+alt+shift+q` or by pushing the mouse into a
   screen corner (exit code 3). If they move the mouse during a run, j5 pauses the run;
   without a terminal it retries the step twice, then fails (exit 1). Do not restart a
   run the user stopped without asking.

## The agent loop

Every authoring command reads the screen and sends **no** input, so use them freely. A
screenshot shows whatever is on the screen, private data included: keep screenshots
local, look only at the windows the task needs (`--window`), and delete them when done.

```console
$ j5 windows                                   # 1. what is open: client areas and titles
[0, 0, 360, 220]         j5 practice form
$ j5 screenshot --window "practice form" -o shot.png        # 2. look at it
$ j5 capture --bbox 14,50,48,22 --window "practice form" \
      --embed form.yaml --name email_label                  # 3. cut out a template
$ j5 find '#email_label' --script form.yaml --window "practice form"   # 4. test it
j5: #email_label found at [14, 50, 48, 22] ([14, 50, 48, 22] in 'j5 practice form'), score 1.000; ...
$ j5 validate form.yaml                                     # 5. after writing steps
$ j5 run --dry-run form.yaml < /dev/null                    # 6. no input sent
$ j5 run --json form.yaml < /dev/null > events.jsonl; echo $?   # 7. for real
```

1. **List windows.** `j5 windows` (or `--json`) lists visible windows, topmost first, with
   each client area `[x, y, w, h]` on the desktop. A step's `window: {title: ...}`
   matches any part of the title, case-sensitive: use the stable part
   (`Mousepad`, not `*Untitled 1 - Mousepad`).
2. **Look.** `j5 screenshot --window TITLE -o shot.png` saves the window's content
   (without title bar and borders); without `--window` the whole desktop. **Open the PNG
   with your image-viewing ability** (e.g. read the file) and decide what to target.
   Pixel coordinates in that image are the `--window`-relative coordinates for
   `capture --bbox` and for `x`/`y` in steps. `--region X,Y,W,H` zooms into part of it.
3. **Capture templates.** `j5 capture --bbox X,Y,W,H --window TITLE --embed SCRIPT
   --name NAME` stores the rectangle in the script's `images` section, used as
   `image: "#NAME"` (quote it: a bare `#` starts a YAML comment). The script must already
   exist and be valid: start it with `version: 1`, `name: ...`, `steps: []`. Add `-f` to
   replace an entry, `--padding N` to grow the box, `--transparent` for an icon on a
   changing background, or `-o img/x.png` for a file instead. It prints a ready `click`
   step.
4. **Test each template.** `j5 find '#NAME' --script SCRIPT --window TITLE` searches
   exactly as a step would. Exit 0 = found; it prints the score (aim for ≥ 0.95 on the
   screen it came from) and where. Exit 1 = not found, with the best score. j5 takes the
   best match only, so check the reported bbox is the place you meant; if the same
   pixels appear elsewhere (two "OK" buttons), capture more context or narrow the search
   with `window` or `region`.
5. **Write the steps** (see below and `reference/script-format.md`), then
   `j5 validate SCRIPT` (or `--json`: errors with `code`, `message`, `line`, `step_id`).
6. **Dry run.** `j5 run --dry-run SCRIPT < /dev/null` with the same params. Read each
   image search (found? score?) and every warning.
7. **Run** after the user agrees: `j5 run --json SCRIPT < /dev/null > events.jsonl`.
   Read the exit code first, then the `step_failed` and `run_finished` events. Then
   verify the outcome yourself (a fresh `j5 screenshot`, the application's output) rather
   than trusting a green run.

Always close standard input (`< /dev/null`) so j5 never waits for a prompt.

### Template tips

- A template needs texture: text, an edge or an icon. A flat colour never matches (j5
  warns). Empty input fields are flat, so target their **label** and click next to it
  with an offset.
- Keep templates small and distinctive (a label, a button's caption with its border),
  not whole panels whose content changes.
- **The window must be visible and unobscured** when you capture, find or run: the
  screen is read, not the window's memory. If another window covers it, you capture the
  wrong pixels. Check the screenshot.
- Hover and focus effects change pixels. Capture in the state the script will see.
- Templates are tied to the theme, fonts and display scale they were captured with.
  After any of them changes, recapture (`-f`).

## Writing scripts

```yaml
version: 1
name: Sign in
params:
  email: {description: Account e-mail}
  password: {secret: true}
vars:
  form: j5 practice form
steps:
  - id: wait_form                      # give every step an explicit, stable id
    wait_image_in: {image: "#email_label", window: {title: "${form}"}, timeout: 10s}
    as: email_label                    # keep the match: {found, score, bbox, center, window, window_bbox}
  - id: click_email
    click: {at: "${email_label}", offset: [3.5w, 0]}   # 3.5 label widths right of its centre
  - id: type_email
    type: "${email}"
  - id: submit
    click: {image: "#submit", window: {title: "${form}"}}
  - id: confirm                        # check the outcome, do not assume it
    wait_image_in: {image: "#saved", window: {title: "${form}"}, timeout: 5s}
images:
  email_label: {png: "..."}            # written by j5 capture --embed
```

Prefer, in this order:

1. **Image targets** (`click: {image: "#name", window: {title: ...}}`): they follow the
   layout. Restrict them with `window` (faster, fewer false matches) or `region`.
2. **Kept results with `at`**: find once with `wait_image_in` + `as: label`, then
   `click: {at: "${label}", offset: [...]}`. No new search, and it follows the window if
   it moved. Use it for fields next to labels and repeated clicks in a loop.
3. **Window-relative coordinates** (`click: {window: {title: ...}, x: 120, y: 48}`) for
   things with no texture, measured on the window screenshot.
4. **Absolute desktop coordinates** only as a last resort: they break when a window moves.

Other rules:

- **Offsets**: `offset: [dx, dy]` in pixels, or relative to the target's size on image
  and `at` targets: `0.5w` (half its width), `1.5h`. Relative units survive font and
  scale changes; prefer them. `w`/`h` are an error on `x`/`y` targets.
- **Waiting**: wait for what the next step needs, not with `sleep`. One step per
  direction: `wait_window_in` / `wait_window_out` for a window to open or close (by
  title), `wait_image_in` / `wait_image_out` for an image to appear or go away. After a
  reload or a click that refreshes content, `wait_image_in` alone may match the old
  screen: wait for the spinner or "Loading..." to go (`wait_image_out`) first. Image
  waits (and image targets with the common `timeout`) also wait for their `window` to
  appear; a step without a timeout looks the window up once. `image_visible` is false
  when its window is missing.
- **Focus**: start with a step that clicks into the target window, since keys go to the
  focused window.
- **Unattended errors**: use `on_error: retry` (3 attempts) or `continue`, not `pause`.
- **Self-contained**: embed templates (`--embed`, or `j5 pack script.yaml -o
  script.yaml` for existing image files) so the script is one shareable file.
- **Display scale**: j5 resizes templates by `display scale / capture scale`. `capture
  --embed` records the scale of a scaled display in the entry's `scale`; for image files
  captured at 150 %, set top-level `capture_scale: 1.5`. If templates miss only on one
  machine, compare the `display_scale` and `capture_scale` that `j5 find` prints, and try
  `j5 run --display-scale 1` (or the right value) to override detection.
- **Data**: params are scalars (string, integer, number, boolean). Lists go in `vars`
  and loop with `loop: {for_each: rows, as: row, do: [...]}`; read items with
  `${row.name}`.
- **Running programs** (`exec`, needs `--allow-exec`): `exec: {command: [mousepad]}`
  starts a program and goes on; `wait: true` + `as: out` keeps `{exit_code, stdout,
  stderr}`. In a dry run nothing starts and the kept result has no `stdout`, so a later
  `${out.stdout}` fails there; guard it (`"strip(out.stdout) if 'stdout' in out else
  'x'"`). Pass secrets to programs through `env`, not `command`.

Full cheat sheet of keys, steps, results, expressions and conditions:
**`reference/script-format.md`**. Commands, flags, exit codes and JSON outputs:
**`reference/cli.md`**. A persistent engine session over JSON lines:
**`reference/protocol.md`**.

## Reading results

| Exit | `j5 run` | Authoring tools (`find`) |
| --- | --- | --- |
| 0 | completed | done (`find`: found) |
| 1 | a step failed | refused / not found |
| 2 | script invalid, params missing or wrong, bad options | usage error |
| 3 | stopped (emergency stop, corner, interference `stop`) | overlay cancelled |
| 130 | Ctrl+C | Ctrl+C |

With `--json`, each line is `{"type": ..., "run_id": ..., "payload": {...}}`. The useful
ones: `step_failed` (`payload.error`: `code`, `message`, `line`, `details`; `screenshot`
for a failed image search), `image_search` (`best_score`, `bbox`, `region`, `scale`),
`run_finished` (`status`, `error`). Quick filter:

```console
$ grep -E '"type":"(step_failed|run_finished)"' events.jsonl
```

A failed image search saves a capture of the searched area with the best candidate
outlined in red: `screenshot` is relative to the run's directory, which
`j5 runs show RUN_ID` prints (`captures  1 file in /home/me/.local/state/j5-macro/runs/<id>`).
**Open that PNG** to see what was on screen. `j5 runs` lists recent runs;
`j5 runs show --json RUN_ID` replays a run's events.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| `image_not_found` (best score given) | Open the failure capture. Score 0.8–0.9: same thing, slightly different pixels: recapture in the run's state or lower `threshold` a little (0.85). Score low: wrong window, obscured, not there yet (raise `timeout`), or a different theme/scale: re-screenshot and recapture. Test with `j5 find`. |
| Matches the wrong place | Make the template more distinctive (more context, `--padding`), add `window` or `region` (`region: {at: "${label}", offset: [3w, 0], size: [4w, 1.5h]}`). |
| `window_not_found` | `j5 windows`: is it open, visible, titled as you think (case-sensitive substring)? Still opening: add `wait_window_in: {title: ..., timeout: 20s}` before, or give the image step a longer `timeout` (image waits wait for their window). |
| `still_visible` | A `wait_image_out`/`wait_window_out` timed out: the spinner or dialog is still there. Raise `timeout`, or check the template matches only the thing that goes away (`j5 find`). |
| `unknown step type ...; did you mean ...?` | The step type was renamed: use the suggested name (the image wait is `wait_image_in`; its opposite `wait_image_out`). |
| `capability_missing` | Message says which: no backend (Wayland, no `DISPLAY`, missing `x11` extra), no `vision` extra, `exec` without `--allow-exec` (ask the user), or the overlay without tkinter. |
| `template_missing` | An image path that does not exist (relative to the script's folder), or a `${...}` reference to an unknown embedded name. |
| `expression_error` | Unknown variable (a step with `as` failed or was skipped earlier), missing key, bad syntax. Quote expressions with `:` or `#`. |
| `validation_failed` after interpolation | `log: "${count}"` with a number: write `"${str(count)}"`. |
| Clicks land off by a factor | Display scale mismatch: compare `display_scale`/`capture_scale` in `image_search` events; set `capture_scale` or `--display-scale`. |
| Exit 2, "missing required params" | Pass them: `--param`, `--param-env`, `--param-file`, `--params-file`. |
| Exit 3 | The user (or the corner failsafe) stopped it. Ask before trying again. |
| Typed text goes elsewhere | Focus the window first (click inside it); check nothing else stole focus. |
| `***` in odd places (`"backend": "***11"`) | Every occurrence of a secret's text is masked, so a very short test secret (`x`) mangles unrelated output. Test with realistic values. |

Note: `j5 find --show`, `j5 pick` and `j5 capture` without `--bbox` open a full-screen
overlay for a **human** to click or drag. As an agent, use `--bbox`, screenshots and
`--json` instead.

## Files in this skill

- `reference/script-format.md`: script format cheat sheet.
- `reference/cli.md`: commands, flags, exit codes, JSON outputs.
- `reference/protocol.md`: driving `python -m j5_macro.engine` over JSON lines.
- `examples/open_editor.yaml`: `exec` + `wait_window_in` + `wait_image_in` in that
  window + an `exec` output + `type`.
- `examples/sign_in.yaml`: embedded templates, `as` + `at` with relative offsets, a
  secret param, an outcome check.
- `examples/fill_rows.yaml`: a `for_each` loop over data, `on_error: retry`, a counter.
- `scripts/practice_form.py`: a small tkinter form ("j5 practice form") that the
  examples drive; start it with `python3 scripts/practice_form.py` to practise the loop
  safely. It prints each submission to standard output.

The examples' templates were captured under Xvfb; on a real desktop recapture them
before running (the commands are in each file's header).

Full documentation: https://github.com/esgabo/j5-macro/tree/main/docs (scripts.md,
cli.md, automation.md, debugging.md, contract.md).
