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
| Bottom, Run tab | **Run**, **Dry run** (with **Strict** for a [strict dry run](cli.md#checking-a-script-with-a-dry-run)) and **Stop**, the run state, the emergency stop key, and the live log: one line per event of the run |
| Bottom, History tab | The saved runs of the open script, newest first, with their mode, status and duration |
| Status bar | The engine's input backend, whether image search is available, and the emergency stop key |

The steps view and the YAML always show the file as it is: when the script changes on
disk (you edit it in a text editor, or another program writes it), the window reloads it
within a second.

While a run is active the step being run is highlighted. Clicking the narrow column at
the left of a step marks a breakpoint (shown as a red dot); breakpoints do not pause runs
from the application yet.

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

Capturing templates and picking points from the window, the params prompt, debugging
(breakpoints, stepping, variables), and the details and captures of past runs come next.
Until then, use `j5 capture`, `j5 pick`, `j5 find`, `j5 run --step` and `j5 runs show`
from a terminal ([cli.md](cli.md)).
