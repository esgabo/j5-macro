# Manual test plan

The automated tests cover the engine, the CLI, the debugger and the X11 backend under a
virtual X server, and the Windows backend against a simulated Win32 layer. What they
cannot cover is a real desktop: a window manager, a scaled display, several monitors,
elevated windows, the user's own mouse. Before a release, run this plan by hand on
**Xfce (X11)** and on **Windows 10 or 11**, and note any difference in the release issue.

It takes about 20 minutes per platform.

## Setup

1. Install j5 from the commit under test, with the backend, vision and gui extras:
   `pip install -e ".[x11,vision,gui]"` on Linux, `pip install -e ".[windows,vision,gui]"`
   on Windows (see [install.md](install.md)).
2. Make sure tkinter is available for the target window and the authoring overlay: it
   ships with the python.org installers on Windows; on Debian and Ubuntu install
   `python3-tk`.
3. Start the target window and leave it visible, not covered by other windows:

   ```bash
   python examples/smoke/target.py
   ```

   It opens a 640x280 window titled **j5 smoke target** with a colour pattern, a grey pad,
   a text box and an event log (see [target.py](../examples/smoke/target.py)). Every
   button, wheel notch and key it receives is listed in the log and printed to the
   terminal it was started from.
4. In another terminal, check the backend: `j5 run --dry-run examples/hello.yaml` prints
   `backend x11` (Linux) or `backend windows` (Windows) on its first line.

## Smoke scripts

The scripts in [examples/smoke](../examples/smoke/) cover every step type. Each comment
in them says which line the target should log.

| Script | Command | Expected |
| --- | --- | --- |
| [pointer.yaml](../examples/smoke/pointer.yaml) | `j5 run examples/smoke/pointer.yaml` | Exit code 0. The target logs, in order: a click on the pattern, `left down`/`left up` at (150, 50), two clicks at (160, 60), right and middle clicks at (200, 70), a click on the pattern at (56, 44), another click on the pattern (the kept result), `left down`/`left up` at (140, 68), `left down at (120, 40)` and `left up at (280, 100)`, then `wheel down` twice and `wheel right` once at (250, 90). The run logs `pattern visible`, `pattern correctly absent from the event log area` and `pattern visible where it was kept` |
| [pointer_embedded.yaml](../examples/smoke/pointer_embedded.yaml) | Copy it to an empty directory and run `j5 run pointer_embedded.yaml` there | The same as `pointer.yaml`: the template is embedded in the script (made with `j5 pack`), so it runs from any directory, with no `img/` next to it. Its `image_search` events show the template as `#pattern` |
| [keyboard.yaml](../examples/smoke/keyboard.yaml) | `j5 run examples/smoke/keyboard.yaml --param extra=end` | Exit code 0. The target's last log line is the `text '...'` line written at the top of the script, character for character: accents, `€`, CJK, Greek, Cyrillic and the emoji included |
| [flow.yaml](../examples/smoke/flow.yaml) | `j5 run examples/smoke/flow.yaml --param name=Ana` | Exit code 0. Logs `hello Ana`, then `count 5, seen ['APPLE', 'BANANA', 'CHERRY']`; pauses at `checkpoint` until Enter; logs the warning |

Run each one twice more: with `--fast` (no human-like movement; the pattern click lands
exactly on its centre, (44, 44)) and with `--dry-run` (nothing is sent; image searches
run once and the steps describe what they would do).

Then repeat `pointer.yaml` and `keyboard.yaml` with `--seed 1` twice and compare the
`input_planned` events of both runs (`j5 runs show --json RUN_ID`): the same seed and the
same start position give the same plans.

## Display scale

The templates in `examples/smoke/img` are captured at 100 %. These runs check that they,
and the offsets in units of the pattern's size (`0.25w`, `[2w, 0.5h]`), follow a larger
display. `target.py --scale 1.5` draws the window 1.5 times larger, as an application
that follows a 150 % display does: the pattern is 72x72 at (30, 30), centre (66, 66),
and the pad is at (150, 30), 300x150. Positions in its log are real pixels.

| Run | How | Expected |
| --- | --- | --- |
| 100 % | `python examples/smoke/target.py`, then `j5 run examples/smoke/pointer.yaml` | As in the table above. The run header shows no display scale (it is 1), and every `image_search` (`j5 runs show --json RUN_ID`) has `scale`, `display_scale` and `capture_scale` 1 |
| 150 %, given | `python examples/smoke/target.py --scale 1.5`, then `j5 run --display-scale 1.5 examples/smoke/pointer.yaml` | Exit code 0. The header shows `display scale 1.5`; each `image_search` has `scale` 1.5 and a score near 1. The target logs clicks on the pattern (anywhere on it; (66, 66) with `--fast`), at (84, 66) for `click_with_offset` and `left down`/`left up` at (210, 102) for `click_next_to_pattern`. `x`/`y` steps are positions and are not scaled: the pad clicks and the wheel are logged at the same pixels as at 100 %, and `drag_across_pad` starts left of the larger pad, so the pad logs nothing for it. The three image conditions log as at 100 % |
| 150 %, detected | Set the display to 150 % (Windows: Settings, Display; Xfce: Appearance, Fonts, custom DPI 144, which sets `Xft.dpi`), start `target.py --scale 1.5` and run `j5 run examples/smoke/pointer.yaml` without `--display-scale` | The same as the run above, with `display scale 1.5` detected. `j5 run --json` shows `display_scale_source: detected` in `run_started` |
| Mismatch | At 150 % detected, start `target.py` without `--scale` (it draws at 100 % size) and run `pointer.yaml` | `wait_pattern` fails with `image_not_found` (the template is searched 1.5 times larger than the pattern); `j5 run --display-scale 1 examples/smoke/pointer.yaml` passes as at 100 % |
| Capture scale | Copy `pointer_embedded.yaml` to an empty directory, add `scale: 1.5` to its `pattern` entry, and run it with `--display-scale 1.5` against `target.py` without `--scale` | Exit code 0 and the results of the 100 % run: `image_search` reports `display_scale` 1.5, `capture_scale` 1.5 and `scale` 1. With `capture_scale: 1.5` at the top of the script instead of the entry's `scale`, the same; with both removed, `wait_pattern` fails as in the mismatch above |

## Authoring tools

The overlay of `j5 capture`, `j5 pick` and `j5 find --show` is checked here; the rest of
the tools are covered by the automated tests (see [cli.md](cli.md#authoring-tools)). Keep
`target.py` open, and on Windows start the terminal so it does **not** have the focus
when the overlay opens (click the target first): Escape must still reach the overlay.

| Check | How | Expected |
| --- | --- | --- |
| Windows | `j5 windows` | `j5 smoke target` is listed with its client area (its content, without the title bar); a `j5 screenshot --window "smoke target"` PNG shows exactly that content |
| Capture | `j5 capture -o /tmp/pattern.png` (`%TEMP%\pattern.png` on Windows) and drag tightly around the pattern | The screen freezes and the overlay covers every monitor, including one left of or above the primary. A magnifier follows the pointer with its desktop coordinates, the rectangle shows its position and size while dragging, and releasing ends it. The PNG is 48x48 (at 100 %) and identical to `examples/smoke/img/pattern.png`; the printed step is `click: {image: ..., window: {title: j5 smoke target}}` |
| Click without dragging | `j5 capture -o /tmp/x.png`, click once without moving, then drag | The click is ignored and the drag is captured |
| Cancel | `j5 pick`, press Escape; again, and press the right button | Each time the overlay closes at once, `j5: cancelled`, exit code 3, and the screen and focus are as before |
| Pick | `j5 pick`, click the centre of the pattern | Prints `click: {window: {title: j5 smoke target}, x: 44, y: 44}` (give or take a pixel); pasting it into a script and running it makes the target log a click on the pattern at that point |
| Embed and find | Copy `examples/hello.yaml` to `/tmp/s.yaml`, run `j5 capture --embed /tmp/s.yaml --name pattern` around the pattern, then `j5 find '#pattern' --script /tmp/s.yaml --show` | The script gets an `images` section with `pattern`, its comments untouched; `find` exits 0 and outlines the pattern in green for three seconds, with the score |
| Not found | `j5 find examples/smoke/img/pattern.png --region 300,0,100,100 --show` | A red outline at the best candidate with `not found: best score ...`; exit code 1 |
| Transparent | `j5 capture -o /tmp/t.png --transparent` around the pattern with a few pixels of window background around it | The PNG has a transparent border (open it in an image viewer); `j5 find /tmp/t.png` finds it with a score near 1 |
| Scaled display | At 150 % (see "Display scale"), `target.py --scale 1.5`, then `j5 capture --embed /tmp/s.yaml --name big -f` around the pattern, and `j5 capture -o /tmp/big.png` | The overlay is sharp and covers the whole screen (no blurry, scaled-up window on Windows); the PNG is 72x72; the embedded entry has `scale: 1.5`; the file capture prints the `capture_scale: 1.5` hint |
| Mixed monitors (Windows) | Two monitors at different scales: `j5 pick` on each monitor | The overlay covers both without gaps, the magnifier's coordinates match the reported `x`, `y`, and the picked point is where the pointer was |

## Desktop application

`j5 gui` ([gui.md](gui.md)) on the real desktop, with `target.py` open. The automated
tests drive the window offscreen against the fake backend; these checks cover what they
cannot: a real window manager, real input and the look of the window.

| Check | How | Expected |
| --- | --- | --- |
| Start | `j5 gui examples/smoke` | The window opens with the smoke scripts listed on the left. The status bar shows `backend: x11` (Linux) or `backend: windows`, `vision` and `stop key: ctrl+alt+shift+q`; the run panel shows the same stop key |
| Open | Double-click `Smoke - pointer` | The step tree shows every step with its type, `if` blocks with `then` and `else` rows; the YAML on the right is the file, comments included. Selecting a step shows its parameters and highlights its line in the YAML |
| External change | Edit `examples/smoke/flow.yaml` in a text editor while it is open in the GUI and save | Within a second the tree and the YAML show the change; an invalid edit shows its problems in red above the tree, and fixing it clears them |
| Dry run | Open `pointer.yaml`, Dry run | The log streams the events (`▶ step`, `would click ...`, image searches) and ends with `run completed`; nothing moves |
| Run | Run `pointer.yaml` | The mouse moves as with `j5 run`, the target logs the same clicks, the step being run is highlighted in the tree, and the run ends with `run completed`. The History tab lists the run |
| Stop | Run `keyboard.yaml`, press Stop while it types | The run ends with `run stopped`; no key stays held |
| Emergency stop key | Run `keyboard.yaml` and press the stop key shown | As Stop, with the engine's warning in the log |
| Engine crash | While the GUI is open, kill the engine process (`python -m j5_macro.engine`) | A red banner says the engine stopped, with its exit code; Restart engine brings it back and reopens the script |
| Without PySide6 | In an environment without the `gui` extra, `j5 gui` | `j5: the GUI needs PySide6, which is not installed: pip install "j5-macro[gui]"`, exit code 2 |
| Display scale | At 150 % (see "Display scale") | The window and its fonts are sharp and scaled, not blurry |

## Safety checks

| Check | How | Expected |
| --- | --- | --- |
| Emergency stop key | Start `j5 run examples/smoke/keyboard.yaml` and press Ctrl+Alt+Shift+Q while it types | The run stops within one key with `stopped (user)` and a warning; no key stays held (type in another window to check Shift and Ctrl) |
| Custom stop key | `j5 run --stop-key ctrl+alt+f12 examples/smoke/keyboard.yaml`, press Ctrl+Alt+F12 | Same as above; the first lines say `emergency stop key: ctrl+alt+f12` |
| Stop key taken | Start a second `j5 run` while the first one runs | The second engine warns that the stop key is taken and relies on the corner failsafe |
| Corner failsafe | During `pointer.yaml`, push the mouse into a corner of the desktop | The run stops with reason `user` |
| Interference | During `pointer.yaml`, move the mouse while j5 moves it | `user moved the mouse` and the run pauses (the script's profile says `interference: pause`); Enter runs the step again from where the pointer is |
| Ctrl+C | Press Ctrl+C during `keyboard.yaml` | The run stops, keys are released, exit code 130 |
| Engine crash | Run `j5 run examples/smoke/keyboard.yaml`, then kill the engine process (`python -m j5_macro.engine`) while `hold_shift` holds Shift | The CLI reports the dead engine and releases Shift (no stuck Shift afterwards) |
| Captures | `j5 run --captures all examples/smoke/pointer.yaml`, then `j5 runs show RUN_ID` | Every image search attempt has a capture under `captures/` |

## Platform checks

**Xfce (X11):**

- Move the target window, including partly off screen, and run `pointer.yaml` again: the
  coordinates follow the window's client area, so the log shows the same positions.
- Switch the keyboard layout (for example to Spanish or German) and run `keyboard.yaml`:
  the text is identical, and `ctrl+/` and `shift+end` still work.
- With two monitors, put the target on the second one and run `pointer.yaml`.

**Windows:**

- **Scaled display.** Set the display scale to 150 % (Settings, Display), sign out and
  in, start `target.py --scale 1.5` and run `pointer.yaml` and `keyboard.yaml`. The
  engine declares per-monitor DPI awareness, so coordinates and captures are physical
  pixels, and it detects the 150 % (`display scale 1.5` in the run header): the results
  are those of the "150 %, detected" row of [Display scale](#display-scale).
  `j5 run --json examples/hello.yaml` shows it in `run_started`.
- **Several monitors.** Put a second monitor to the left of the main one (so the desktop
  has negative coordinates), move the target there, and run `pointer.yaml`. Repeat with
  monitors at different scales (say the main one at 100 % and the second at 150 %, the
  target started with the second monitor's `--scale`): the `image_search` events of the
  window searches report the scale of the monitor the target is on (`display_scale`),
  and the run header shows the main monitor's.
- **Keyboard layouts.** Switch to another layout (Spanish, German, French AZERTY) and run
  `keyboard.yaml`: the text is identical (characters are sent as Unicode, not as keys),
  and the shortcuts press the key that types their character in the active layout.
- **Elevated windows.** Start the target from an administrator terminal and run
  `pointer.yaml` from a normal one. Windows does not let a normal process send input to
  an elevated window: the first step that sends input fails with `input_denied`, or,
  where Windows drops the input without saying so, the target logs nothing. Note which
  one happened. Run j5 as administrator too and it works.
- **Minimized and hidden windows.** Minimize the target and run `pointer.yaml`: the first
  step fails with `window_not_found`.
- **Lock screen.** Run `j5 run examples/smoke/flow.yaml --param name=Ana`, lock the
  screen with Win+L while it waits at `checkpoint`, unlock, press Enter: the run finishes.

## Reporting

For each platform, record the OS version, Python version, display scale, monitors and
keyboard layouts tested, and every difference from the expected results above, with the
run id (`j5 runs`) so the saved events can be inspected.
