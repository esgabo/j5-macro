# Desktop application

`j5 gui` opens j5 as a desktop application: a window that lists your scripts, shows a
script's steps and its YAML side by side, runs it (or dry-runs it) and shows what
happened. It is for people who would rather not use a terminal and a text editor; it
needs neither, and VS Code stays optional.

The application is being built in stages ([issue #95](https://github.com/esgabo/j5-macro/issues/95)).
This page says what works today.

## Install and start

The application is an optional extra, built with [Qt](https://www.qt.io/) through
PySide6:

```console
$ pip install "j5-macro[x11,vision,gui] @ git+https://github.com/esgabo/j5-macro"
$ j5 gui                        # the scripts of the current directory
$ j5 gui examples               # the scripts of a folder
$ j5 gui examples/hello.yaml    # a script, and its folder
```

On Windows use the `windows` extra instead of `x11`. From a clone, `pip install -e
".[x11,vision,gui]"`. `python -m j5_macro.gui` is the same as `j5 gui`. Without the
`gui` extra, `j5 gui` says how to install it and exits with code 2. See
[install.md](install.md) for the extras and [cli.md](cli.md#j5-gui) for the options
(`--runs-dir`, `--stop-key`).

Like the command line, the application starts the j5 engine as a child process and
talks to it through the [contract](contract.md); everything it shows comes from the
engine, so it behaves exactly as `j5 run` does.

## The window

| Area | What it shows |
| --- | --- |
| Left: scripts | The scripts in the open folder and its subfolders (scripts that are not valid are shown in red). **Open folder…** picks another folder, **New…** creates an empty script, double-click (or Enter) opens one |
| Center: steps | The steps of the open script with their id, type and parameters; the steps inside `if` (under `then` and `else`) and `loop` blocks are nested. Selecting a step shows its parameters below and highlights its line in the YAML. Problems of an invalid script are listed in red above |
| Right: YAML | The script's file, read-only, exactly as it is on disk |
| Bottom, Run tab | **Run**, **Dry run** (with **Strict** for a [strict dry run](cli.md#checking-a-script-with-a-dry-run)) and **Stop**, the run state, the emergency stop key, and the live log: one line per event of the run |
| Bottom, History tab | The saved runs of the open script, newest first, and the selected run's events and captures (see [History](#history)) |
| Bottom, Screen tab | The screen tools of the selected step: capture its template, test its search, pick its point (see [Screen tools](#screen-tools)) |
| Status bar | The engine's input backend, whether image search is available, and the emergency stop key |

The steps view and the YAML always show the file as it is: when the script changes on
disk (you edit it in a text editor, or another program writes it), the window reloads it
within a second.

While a run is active the step being run is highlighted. Clicking the narrow column at
the left of a step marks a breakpoint (shown as a red dot); breakpoints do not pause runs
from the application yet.

## Running a script

**Dry run** describes what the script would do without moving the mouse or typing: image
searches are tried once, and the log says where each click would land. **Run** really
sends input, with the human-like movement and typing of the script's input profile.
**Stop**, the emergency stop key shown next to the buttons (`ctrl+alt+shift+q` by
default) and pushing the mouse into a corner of the screen all stop a run; held keys and
buttons are released.

Scripts with required params cannot be run from the application yet; use `j5 run`.

## Screen tools

Select a step in the tree and open the **Screen** tab (or use the **Tools** menu, which
acts on the selected step). For every image the step searches for (the `image` of
`click`, `move`, `wait_image_in` and `wait_image_out`, an endpoint of `drag`, the
`image_visible` of an `if` or `loop` condition) it shows the template and two buttons;
for every point it clicks or moves to (`click`, `move`, the endpoints of `drag`), two
more. They are the engine's [authoring tools](contract.md#authoring-tools), the same as
`j5 capture`, `j5 pick`, `j5 find` and `j5 windows` ([cli.md](cli.md#authoring-tools)).

| Tool | What it does |
| --- | --- |
| Template preview | The image the step looks for: a file (relative to the script) or an embedded `#name`, over a checkerboard so transparent pixels show, with its size and, for an embedded image captured at another display scale, that scale |
| **Capture…** (Ctrl+Shift+C) | Opens the capture dialog: **Embed in the script as** a name (the step id by default) adds the template to the script's `images`; **Save as a file next to the script** writes a PNG beside it. **Padding** grows the rectangle on every side, **Transparent background** makes the border's colour transparent (for icons on any background), **Replace** overwrites an image or file of the same name. **Capture…** hides the application, freezes the screen and lets you drag a rectangle; Escape or the right button cancels. The step then searches for the new template (a click on a point becomes a click on the image). A template of one flat colour gets a warning: it would match anywhere |
| **Test** (Ctrl+T) | Searches the screen once, exactly as the step would (its window, region, threshold and scale), with the application hidden for a moment, and shows the result: found or not, the score against the threshold, where the best match is, and a screenshot of the searched area with the match outlined (green when found, red with the best score when not). A template, window title or region computed at run time (`${...}`) cannot be tested outside a run |
| **Pick…** (Ctrl+Shift+P) | Hides the application, freezes the screen and lets you click a point. The step's `x` and `y` become that point, relative to the window it is in (and `window: {title}` is set to that window), so the step keeps working when the window moves; clear **Relative to its window** for desktop coordinates. Picking replaces an `image` or `at` the step had |
| **Window…** | Lists the visible windows, topmost first; choosing one sets the step's `window` title |

Every change is an edit of the script through the engine, so the step tree, the YAML
and the file show it at once, with the file's comments and formatting kept.

Escape on the overlay just cancels. While a script runs, capturing and picking are
refused ("A script is running"); stop it first. The overlay needs Python's tkinter
(included with Python for Windows; on Debian and Ubuntu install `python3-tk`); image
search needs the `vision` extra. When either is missing the tools say what to install.

## History

The **History** tab lists the saved runs of the open script (the runs directory keeps
the newest 20 runs, see [`j5 runs`](cli.md#j5-runs)), newest first, with their start time, mode,
status, duration and warnings. It refreshes when a run ends. Selecting a run shows:

- its outcome (status, duration, the error and the step it happened in);
- **Events**: every event of the run, one line each, as in the live log;
- **Failed search**: for a step that failed because an image was not found, the template
  next to the screen capture saved when it failed, with the best location outlined and
  its score. The template is read from the open script: a file next to it or an
  embedded image;
- **Captures**: every capture the run saved (with `j5 run --captures all`, one per
  search), with the match outlined.

**Delete…** deletes the selected run (after asking), its events and its captures.
**Open folder** opens the run's directory in the file manager.

## When the engine stops

If the engine process ends unexpectedly, a red banner says so, with its exit code and
the last lines it printed, and releases any key or button it was holding. **Restart
engine** (also in the Engine menu) starts a new one and opens the script again.

## Not yet

Editing steps from the window (a palette, forms for each step, drag and drop, the
script header), the params prompt, debugging (breakpoints, stepping, variables) and
saving a script as a single file come next. Until then, edit the YAML in any editor (the
window follows the file) and use `j5 run --step` from a terminal ([cli.md](cli.md)).
