# Manual test plan

The automated tests cover the engine, the CLI, the debugger and the X11 backend under a
virtual X server, and the Windows backend against a simulated Win32 layer. What they
cannot cover is a real desktop: a window manager, a scaled display, several monitors,
elevated windows, the user's own mouse. Before a release, run this plan by hand on
**Xfce (X11)** and on **Windows 10 or 11**, and note any difference in the release issue.

It takes about 15 minutes per platform.

## Setup

1. Install j5 from the commit under test, with the backend and vision extras:
   `pip install -e ".[x11,vision]"` on Linux, `pip install -e ".[windows,vision]"` on
   Windows (see [install.md](install.md)).
2. Make sure tkinter is available for the target window: it ships with the python.org
   installers on Windows; on Debian and Ubuntu install `python3-tk`.
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
| [pointer.yaml](../examples/smoke/pointer.yaml) | `j5 run examples/smoke/pointer.yaml` | Exit code 0. The target logs, in order: a click on the pattern, `left down`/`left up` at (150, 50), two clicks at (160, 60), right and middle clicks at (200, 70), a click on the pattern at (54, 44), `left down at (120, 40)` and `left up at (280, 100)`, then `wheel down` twice and `wheel right` once at (250, 90). The run logs `pattern visible` and `pattern correctly absent from the event log area` |
| [pointer_embedded.yaml](../examples/smoke/pointer_embedded.yaml) | Copy it to an empty directory and run `j5 run pointer_embedded.yaml` there | The same as `pointer.yaml`: the template is embedded in the script (made with `j5 pack`), so it runs from any directory, with no `img/` next to it. Its `image_search` events show the template as `#pattern` |
| [keyboard.yaml](../examples/smoke/keyboard.yaml) | `j5 run examples/smoke/keyboard.yaml --param extra=end` | Exit code 0. The target's last log line is the `text '...'` line written at the top of the script, character for character: accents, `€`, CJK, Greek, Cyrillic and the emoji included |
| [flow.yaml](../examples/smoke/flow.yaml) | `j5 run examples/smoke/flow.yaml --param name=Ana` | Exit code 0. Logs `hello Ana`, then `count 5, seen ['APPLE', 'BANANA', 'CHERRY']`; pauses at `checkpoint` until Enter; logs the warning |

Run each one twice more: with `--fast` (no human-like movement; the pattern click lands
exactly on its centre, (44, 44)) and with `--dry-run` (nothing is sent; image searches
run once and the steps describe what they would do).

Then repeat `pointer.yaml` and `keyboard.yaml` with `--seed 1` twice and compare the
`input_planned` events of both runs (`j5 runs show --json RUN_ID`): the same seed and the
same start position give the same plans.

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
  in, and run `pointer.yaml` and `keyboard.yaml`. The positions in the target's log are
  the same as at 100 %: the engine declares per-monitor DPI awareness, so coordinates and
  captures are physical pixels. The image search still finds the pattern with score 1.
- **Several monitors.** Put a second monitor to the left of the main one (so the desktop
  has negative coordinates), move the target there, and run `pointer.yaml`. Repeat with
  monitors at different scales.
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
