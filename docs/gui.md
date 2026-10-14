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
| Bottom, History tab | The saved runs of the open script, newest first, with their mode, status and duration |
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

## When the engine stops

If the engine process ends unexpectedly, a red banner says so, with its exit code and
the last lines it printed, and releases any key or button it was holding. **Restart
engine** (also in the Engine menu) starts a new one and opens the script again.

## Not yet

Editing steps from the window (a palette, forms for each step, drag and drop, the
script header), capturing templates and picking points from the window, the params
prompt, debugging (breakpoints, stepping, variables), the details and captures of past
runs, and saving a script as a single file come next. Until then, edit the YAML in any
editor (the window follows the file) and use `j5 capture`, `j5 pick`, `j5 find`,
`j5 run --step` and `j5 runs show` from a terminal ([cli.md](cli.md)).
