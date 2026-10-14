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
| Center: palette | Every step type, grouped (Mouse, Keyboard, Wait, Flow, Data, Programs, and Plugins for steps installed by plugins); hover one for its documentation and parameters |
| Center: steps | The steps of the open script with their id, type and parameters; the steps inside `if` (under `then` and `else`) and `loop` (under `do`) blocks are nested. Steps with problems are red with a ⚠, and the problems of an invalid script are listed in red above |
| Center: Step and Script tabs | The form of the selected step, and the form of the script's header (see [Editing a script](#editing-a-script)) |
| Right: YAML | The script's file, read-only, exactly as it is on disk. The selected step's line is highlighted; clicking a line selects its step |
| Bottom, Run tab | The run and debugging buttons, the run state, the emergency stop key, the pause box, the live log and, while paused, the variables (see below) |
| Bottom, History tab | The saved runs of the open script, newest first, and the selected run's events and captures (see [History](#history)) |
| Status bar | The engine's input backend, whether image search is available, and the emergency stop key |

The steps view and the YAML always show the file as it is: when the script changes on
disk (you edit it in a text editor, or another program writes it), the window reloads it
within a second.

While a run is active the step being run is highlighted, and the step a run is paused at
gets a stronger highlight; the tree scrolls to it. Clicking the narrow column at the left
of a step (or selecting it and pressing **F9**) sets or removes a breakpoint, shown as a
red dot; steps with `break: true` in the script show one too. Breakpoints apply at once,
also to the active run, and are kept per script until the application closes.

## Editing a script

Everything you change in the window is saved to the file at once, through the engine's
structured editing: the rest of the file stays as it was, comments included, so the
file stays readable and you can keep editing it by hand (the window follows it). There
is no Save: the file on disk is always the script.

**Adding steps.** Double-click a step type in the palette (or select it and press
**Insert**) to add a step of that type after the selected step. Select a block row
(`then`, `else`, `do`) to add it inside that block instead; an empty `else` is created
when you add a step to it. You can also drag a type from the palette onto the tree. A new
step starts with values that keep the script valid (a `click` at `x: 0, y: 0`, a
`sleep` of `1s`...); change them in its form.

**Moving steps.** Drag a step in the tree: dropped on the top or bottom edge of a step it
goes before or after it, dropped on the middle of an `if` or a `loop` (or on one of its
block rows) it goes inside. **Up** and **Down** (Alt+Up, Alt+Down) move the selected
step past its neighbour, out of a block at its first or last step. **Duplicate**
(Ctrl+D) copies the step (with the steps of its blocks) right after it, with new ids;
**Delete** removes it with its blocks. The same actions are in the tree's context menu.

**The step form.** The Step tab shows a field for each parameter of the selected step,
chosen by the parameter's type in the step registry, so plugin steps get forms too:

| Parameter | Field |
| --- | --- |
| Text, numbers, expressions | A text box; `${...}` is accepted anywhere, as in the file |
| A fixed set (`button`, `level`) | A list; *(default: ...)* leaves the parameter out |
| Yes or no (`wait`, `shell`) | A check box |
| Durations (`sleep`, `timeout`) | `2s`, `500ms`, `1.5m`, `2s±20%`; a bare number is seconds |
| The target of `click` and `move`, the points of `drag` | **Aim at** an Image, a Position (`x`, `y`) or a Stored result (`${label}`), with its window, offset and, for an image, threshold, scale and region |
| Images | A path relative to the script (**File…** picks one) or `#name`, one of the script's embedded images (listed) |
| Offsets | `dx` and `dy`: pixels, or multiples of the target's size such as `0.5w` or `-2h` |
| Regions | Everywhere, a Rectangle (`x`, `y`, `w`, `h`), or Next to a result (`{at, offset, size}`) |
| Windows | The text the window's title contains |
| Conditions (`when`, `while`) | An Expression, or Image is visible (image, threshold, window) |
| `exec`'s command | The program and each argument on its own line, or a command line |
| `exec`'s env | A table of names and values |
| Anything else (`set`'s `value`, `for_each`) | A value as JSON (`3`, `true`, `[1, 2]`, `{"a": 1}`); plain text is a string |

Below them, the step options: its **id** (renaming it keeps it unique), **timeout**,
**on error**, **keep result as** (the `as` variable, optionally **secret**), **break**
(a breakpoint saved in the file) and an **input** profile override for this step.

Fields are checked as you type: a value of the wrong type shows its problem under the
field and is not saved. A change is saved half a second after you stop typing (choices and
check boxes at once). Problems only the engine can find (a `click` with no target, a
`#name` that is not embedded) are saved, then shown under their field, in red in the tree
and above it; until they are fixed the tree shows the last valid version of the script.
Fields with a default show it in grey when they are empty; emptying a field removes the
parameter from the file.

**The Script tab** edits the header: the **name**, the **capture scale** (see
[Display scale](scripts.md#display-scale)), the **params** (name, type, description,
secret, required, default; a param that is not required needs a default, a secret one
cannot have one), the **vars** (values as in the step form), the **embedded images**
(name, size and scale; **Add from file…** embeds a PNG file like `j5 pack`; double-click
a name or use **Rename…** to rename one, which also renames the steps' `#name`
references; **Delete** asks first and names the steps that still use it) and the
script's **input profile**.

**One file.** **File > Save as self-contained file…** writes a copy of the script with
every image file it refers to embedded, like `j5 pack`; the copy can be shared on its
own and the open script stays as it is. **File > Unpack images to files…** writes the
embedded images to `img/<name>.png` in a folder you choose, and the script, referring to
them by path, next to them, like `j5 unpack`, and opens it. **File > New script…**
creates an empty script in the open folder.

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

## Screen tools

The screen tools are in the step form, next to the fields they fill: every image field
(the `image` of `click`, `move`, `wait_image_in` and `wait_image_out`, an endpoint of
`drag`, the `image_visible` of an `if` or `loop` condition) shows the template and
**Capture…** and **Test**; every position (`x` and `y` of `click`, `move` and the
endpoints of `drag`) has **Pick…**; every window field has **Window…**. The **Tools**
menu runs them on the selected step's first image or position field. They are the engine's [authoring tools](contract.md#authoring-tools), the same as
`j5 capture`, `j5 pick`, `j5 find` and `j5 windows` ([cli.md](cli.md#authoring-tools)).

| Tool | What it does |
| --- | --- |
| Template preview | The image the step looks for: a file (relative to the script) or an embedded `#name`, over a checkerboard so transparent pixels show, with its size and, for an embedded image captured at another display scale, that scale |
| **Capture…** (Ctrl+Shift+C) | Opens the capture dialog: **Embed in the script as** a name (the step id by default) adds the template to the script's `images`; **Save as a file next to the script** writes a PNG beside it. **Padding** grows the rectangle on every side, **Transparent background** makes the border's colour transparent (for icons on any background), **Replace** overwrites an image or file of the same name. **Capture…** hides the application, freezes the screen and lets you drag a rectangle; Escape or the right button cancels. The step then searches for the new template (a click on a point becomes a click on the image). A template of one flat colour gets a warning: it would match anywhere |
| **Test** (Ctrl+T) | Searches the screen once, exactly as the step would (its window, region, threshold and scale), with the application hidden for a moment, and shows the result: found or not, the score against the threshold, where the best match is, and a screenshot of the searched area with the match outlined (green when found, red with the best score when not). A template, window title or region computed at run time (`${...}`) cannot be tested outside a run |
| **Pick…** (Ctrl+Shift+P) | Hides the application, freezes the screen and lets you click a point. The step's `x` and `y` become that point, relative to the window it is in (and `window: {title}` is set to that window), so the step keeps working when the window moves; clear **Relative to its window** for desktop coordinates. Picking replaces an `image` or `at` the step had; in a click's form **Pick…** is under **Aim at** Position, and Tools > Pick point… switches to it |
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
