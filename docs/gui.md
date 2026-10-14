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
| Bottom, Run tab | The run and debugging buttons, the run state, the emergency stop key, the pause box, the live log and, while paused, the variables (see below) |
| Bottom, History tab | The saved runs of the open script, newest first, with their mode, status and duration |
| Status bar | The engine's input backend, whether image search is available, and the emergency stop key |

The steps view and the YAML always show the file as it is: when the script changes on
disk (you edit it in a text editor, or another program writes it), the window reloads it
within a second.

While a run is active the step being run is highlighted, and the step a run is paused at
gets a stronger highlight; the tree scrolls to it. Clicking the narrow column at the left
of a step (or selecting it and pressing **F9**) sets or removes a breakpoint, shown as a
red dot; steps with `break: true` in the script show one too. Breakpoints apply at once,
also to the active run, and are kept per script until the application closes.

## Running a script

The **Run** tab has the run buttons on the left and the debugging buttons on the right:

| Button | Key | Does |
| --- | --- | --- |
| **Run** | F5 | Runs the script, sending real input with the human-like movement and typing of the script's input profile |
| **Dry run** (+ **Strict**) | Ctrl+F5 | Describes what the script would do without moving the mouse or typing: image searches are tried once, and the log says where each click would land. With **Strict**, a [strict dry run](cli.md#checking-a-script-with-a-dry-run): what the real run would fail on (an image not on the screen, a missing window, `exec` not allowed) fails the run |
| **Debug** | F10 or F11 | Starts the run paused before its first step (real input). Its menu has **Debug a dry run**: the same, sending no input (Ctrl+F11) |
| **Options…** | | The run dialog (below), to change the options or the mode |
| **Continue** | F5 | Runs to the next breakpoint or the end |
| **Pause** | F6 | Pauses at the next action (never in the middle of a movement or a key sequence) |
| **Step over** | F10 | Runs the current step (a whole `loop` or `if`) and pauses at the next one |
| **Step into** | F11 | Pauses at the very next step, entering loops and branches |
| **Step out** | Shift+F11 | Finishes the current `loop` or `if` and pauses after it |
| **Stop** | Shift+F5 | Stops the run; held keys and buttons are released |

Each button is enabled only when it applies: the run buttons while no run is active, the
stepping buttons while the run is paused, Pause while it runs. The keys are those of VS
Code, and the Run menu lists the same commands. The emergency stop key shown at the right
(`ctrl+alt+shift+q` by default) and pushing the mouse into a corner of the screen also
stop a run, even when the window is not in front.

**Real input.** Before a run that sends real input (Run and Debug, not the dry ones; not
with the `fake` backend either) the application asks for confirmation and names the
emergency stop key. Tick **Do not ask again** to skip the question; **Run > Confirm runs
that send real input** turns it back on. The setting is kept between sessions.

**The run dialog.** A script that declares [params](scripts.md) opens the run dialog
before it runs; **Options…** opens it for any script. It has a field per param, with its
type, its description and its default filled in; required params are marked with `*`,
and the dialog says what is missing or of the wrong type before it starts anything. An
empty field leaves the param to its default. Secret params are typed in password fields:
their value is never shown, never remembered and, as in every run, masked as `***` in the
log and the history. The other values are remembered for the next run of the same script
until the application closes. Below the params:

| Option | Meaning |
| --- | --- |
| Mode | Dry run, Run, Debug or Debug a dry run |
| Strict | Only for dry runs, as the toolbar's **Strict** |
| Allow exec | Lets `exec` steps start programs. It asks first, and is only for that run: the next run starts without it again |
| Seed | The run's seed; empty for a new one (the log and the summary show it). The same seed gives the same human-like movement and typing |
| Captures | Screen captures of failed image searches only (the default), or of every attempt |
| Display scale | Empty to detect it, or a number such as `1.5` (or `150%`) for the whole run |

The seed, captures and display scale are kept for the next runs.

## Debugging

A run pauses at a breakpoint (in every mode), after a `pause:` step, at each step while
you step, when you press **Pause**, and, depending on the script, when a step fails or
you move the mouse while j5 moves it. A box above the log says why and where:

- **At a breakpoint, a step or a pause**: the step the run is paused **before**. Go on
  with the debugging buttons.
- **A failed step** (`on_error: pause`): the error and, when an image search failed, the
  screen capture of its last attempt. **Retry** runs the step again, **Skip** goes on
  without it, **Stop** ends the run as failed.
- **The mouse was moved** (the input profile's `interference: pause`): where j5 expected
  the pointer and where it was. Held keys and buttons were already released. **Retry**
  runs the step again from where the pointer is now, **Skip** goes on without it,
  **Stop** ends the run.

After **Skip**, a run started with Run or Dry run carries on by itself; a run started
with Debug pauses at the next step.

While paused, the **Variables** list on the right shows every variable of the run (lists
and maps expand). Secret values are shown as `***` and cannot be edited. To change a
variable, select it, type the new value and press **Set** (or Enter): the text is read as
JSON when it is valid JSON (`3`, `true`, `"text"`, `[1, 2]`), else as text. With
**Expression** ticked, the text is an expression evaluated over the variables, such as
`attempts + 1`. Below, type an expression and press **Evaluate** to see its value.
Changes apply at once: the run goes on with the new value. See
[debugging.md](debugging.md) for expressions and how stepping works in loops.

## The log

The log has one line per event of the run: steps started (`▶`), finished (`✓`, with the
description in a dry run), failed (`✗`) and skipped (`↷`), the script's `log` messages,
variables set, branches taken, loop iterations, image searches with their best score and
threshold, the planned movements and typing (`mouse plan (human): (10, 20) → (300, 40)
in 412 ms`, `keyboard plan (human): 12 chars at 70 wpm`) and the state changes. Warnings
are orange and errors red. **Show** filters the lines by level (all, info and above,
warnings and errors, errors only), **Clear** empties the log (the run's history keeps
every event), and the line below it sums the run up: its state or how it ended, its
duration, its mode, the number of warnings and its seed.

## When the engine stops

If the engine process ends unexpectedly, a red banner says so, with its exit code and
the last lines it printed, and releases any key or button it was holding. **Restart
engine** (also in the Engine menu) starts a new one and opens the script again.

## Not yet

Editing steps from the window (a palette, forms for each step, drag and drop, the
script header), capturing templates and picking points from the window, the details and
captures of past runs, and saving a script as a single file come next. Until then, edit
the YAML in any editor (the window follows the file) and use `j5 capture`, `j5 pick`,
`j5 find` and `j5 runs show` from a terminal ([cli.md](cli.md)).
