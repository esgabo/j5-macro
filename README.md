# j5-macro

> "Need input!" — Johnny 5

Scriptable, debuggable desktop automation. Write macros as YAML steps (click, type, wait
for an image, branch, loop), run them from the command line or from another program,
step through them with breakpoints, and let the mouse and keyboard move like a person
instead of teleporting.

```yaml
version: 1
name: Save a report
params:
  customer: {description: Name to type in the form}
steps:
  - id: open_menu
    click: {image: img/file_menu.png}
  - id: wait_dialog
    wait_image_in: {image: img/save_dialog.png, timeout: 10s}
  - id: write_name
    type: "${customer}"
  - id: save
    key: ctrl+s
```

```console
$ j5 run report.yaml --param 'customer=ACME #42'
```

**New here? Start with the [tutorial](docs/tutorial.md).** All the documentation is in
[docs/](docs/index.md).

## Features

- **Readable scripts.** YAML steps with stable ids, variables, expressions, `if`, loops,
  error handling (`continue`, `retry`, `pause`) and a JSON Schema for editor completion.
  An `exec` step launches applications or reads values from commands, when the run
  allows it (`--allow-exec`).
- **Human-like input.** Curved mouse paths (WindMouse or Bézier) with Fitts' law timing, a
  bell-shaped speed, overshoot and jitter; typing with a natural rhythm, pauses and
  optional corrected typos. Or `direct` and `linear` when speed matters. Runs are seeded
  and reproducible.
- **Sees the screen.** Waits for and clicks on images, with thresholds, regions,
  transparent templates and screenshots of failed searches. Templates and offsets follow
  the display scale, so a script captured at 100 % still works at 150 %.
- **Quick to write.** `j5 capture` freezes the screen so you drag out a template (into a
  file or the script itself), `j5 pick` gives the coordinates of a point inside a window,
  and `j5 find` tests a template exactly as a step would; each prints a step to paste.
  Agents use the same tools with explicit coordinates, plus `j5 windows` and
  `j5 screenshot`.
- **Debuggable.** `j5 run --step` with breakpoints, stepping into loops and branches,
  variables and expressions; the same in VS Code through a Debug Adapter Protocol server.
- **Values from outside.** Params from the command line, files or environment variables;
  secrets are masked as `***` in the output and the history.
- **Safe.** An emergency stop key (`ctrl+alt+shift+q`), a screen-corner failsafe, a pause
  when you grab the mouse, and held keys always released.
- **Automatable and GUI-ready.** Exit codes and JSON events for scripts, CI and AI agents.
  A headless engine speaks a JSON-lines protocol that the CLI, the debugger and a future
  GUI all use, including commands to list, edit and watch scripts and to review past
  runs.

## Status

Early development (0.0.1). Works on **Linux with an X11 session** (tested on XFCE) and on
**Windows**: input, image search, debugging and everything else. **Wayland** is not
supported yet. Not on PyPI yet.

## Install

```console
$ pipx install "j5-macro[x11,vision] @ git+https://github.com/esgabo/j5-macro"
$ j5 --version
```

On Windows use the `windows` extra instead of `x11`: `"j5-macro[windows,vision] @ ..."`.
Or `pip install` the same into a virtual environment, or from a clone with
`pip install -e ".[x11,vision]"`. Details, extras and packaging (wheels, containers):
[docs/install.md](docs/install.md).

## Quick start

```console
$ git clone https://github.com/esgabo/j5-macro && cd j5-macro
$ j5 validate examples/hello.yaml
$ j5 run --dry-run examples/hello.yaml   # describes the input, touches nothing
$ j5 run examples/hello.yaml             # really clicks and types
$ j5 run --step examples/hello.yaml      # step through it
$ j5 runs                                # past runs
```

Stop a run at any time with `ctrl+alt+shift+q`, or by pushing the mouse into a screen
corner.

## For AI agents

[skills/j5-macro](skills/j5-macro/SKILL.md) is an Agent Skill that teaches an AI agent
to use j5: look at the screen with `j5 windows` and `j5 screenshot`, capture and test
templates, write and validate scripts, dry-run them and read the results, with the
safety rules for sending input to someone's desktop. Copy the folder into the agent's
skills directory, for Claude Code `~/.claude/skills/`:

```console
$ cp -r skills/j5-macro ~/.claude/skills/
```

See [docs/automation.md](docs/automation.md#for-ai-agents).

## Documentation

| | |
| --- | --- |
| [Tutorial](docs/tutorial.md) | From an empty file to a working macro |
| [Installing and packaging](docs/install.md) | Install options, requirements, wheels |
| [Script reference](docs/scripts.md) | Every step, parameter, expression and setting |
| [Command-line reference](docs/cli.md) | Every command, option and exit code |
| [Debugging](docs/debugging.md) | The terminal debugger and VS Code |
| [Running from scripts, CI and agents](docs/automation.md) | Unattended runs, JSON output, params |
| [How it works](docs/how-it-works.md) | Runs, human-like input, image search, safety |
| [Architecture](docs/architecture.md) and [technical design](docs/technical-design.md) | How j5 is built, and why |
| [Contract](docs/contract.md) | The engine protocol, the source of truth |
| [Development](docs/development.md) | Building, testing and contributing |

## Roadmap

Tracked as [GitHub milestones](https://github.com/esgabo/j5-macro/milestones):

| Milestone | Scope | |
| --- | --- | --- |
| M0 | Protocol types and the engine process | Done |
| M1 | Interpreter, step registry, dry-run and the CLI | Done |
| M2 | X11 backend with `direct` and `linear` input | Done |
| M3 | Vision: `wait_image_in` and image-based clicks | Done |
| M4 | Debugger: step mode, breakpoints, inspection, VS Code | Done |
| M5 | Human-like mouse and keyboard | Done |
| M6 | Windows backend, manual test plan | Done |
| M7 | Script management for a GUI, run history | Done |
| M8 | Authoring: capture templates, pick points, test matches; self-contained scripts | Next |

## Contributing

Issues and pull requests are welcome; [docs/development.md](docs/development.md) explains
the setup, the checks every pull request must pass and the project's rules.

## License

j5-macro is licensed under the [Apache License 2.0](LICENSE).
