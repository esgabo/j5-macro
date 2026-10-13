# j5-macro documentation

j5-macro automates the desktop: you write a macro as a YAML list of steps (click, type,
wait for an image, branch, loop), run it from the `j5` command, debug it step by step, and
the mouse and keyboard move like a person's. These pages explain how to use it, how it
works and how it is built.

## Start here

| Page | Read it to |
| --- | --- |
| [Installing and packaging](install.md) | Install j5 (from GitHub, pipx or a clone), check it works, build a wheel |
| [Tutorial](tutorial.md) | Go from an empty file to a working macro: clicking, typing, waiting for images, logic, params, debugging |

## Using j5

| Page | Read it to |
| --- | --- |
| [Script reference](scripts.md) | Look up every step type, parameter, expression, input profile and param |
| [Command-line reference](cli.md) | Look up every `j5` command, option, exit code and environment variable |
| [Debugging](debugging.md) | Step through scripts in the terminal (`j5 run --step`) or in VS Code |
| [Running from scripts, CI and agents](automation.md) | Run j5 unattended: exit codes, JSON output, params, no prompts |
| [How it works](how-it-works.md) | Understand a run, the human-like mouse and keyboard, image search, safety, run history |

## How j5 is built

| Page | Read it to |
| --- | --- |
| [Architecture](architecture.md) | See the processes, layers, packages, threads and extension points |
| [Technical design](technical-design.md) | Understand the internals and the reasons behind them: interpreter, input planning, X11 and Windows, vision, persistence, editing |
| [Contract](contract.md) | The protocol between the engine and its clients, and the source of truth for behaviour: commands, events, run states, script format, errors |
| [Development](development.md) | Set up a development environment, run the checks and tests, add steps or backends, contribute |
| [Manual test plan](manual-testing.md) | Check a release by hand on Xfce and Windows with the smoke scripts |

## Other resources

- [Example scripts](../examples/) to copy from.
- [VS Code extension](../editors/vscode/README.md) for the debugger.
- [Project README](../README.md) for the overview and status.
- [Issues and milestones](https://github.com/esgabo/j5-macro/milestones) for what is
  planned.
