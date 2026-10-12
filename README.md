# j5-macro

> "Need input!" — Johnny 5

Scriptable, debuggable desktop automation for Windows and Linux. Write macros as YAML steps
(click, type, wait for an image, branch, loop), run them step by step with breakpoints, and
let the mouse and keyboard move like a person instead of teleporting.

**Status:** early development. Scripts load, validate and run in dry-run mode; real mouse and
keyboard input arrives with the backends milestone (M2).

## Design

The full design is in [`docs/contract.md`](docs/contract.md). The short version:

- A **headless engine** runs in its own process and speaks a JSON-lines protocol:
  commands in, events out.
- The **CLI**, a future **GUI** and a **DAP debugger** (VS Code) are all clients of that protocol.
- **Mouse and keyboard masters** plan every movement with a strategy (`direct`, `linear` or
  `human`) and drive a thin per-platform **backend**.
- Runs are **reproducible** (seeded), **inspectable** (every step emits events) and
  **safe to abort** (held keys and buttons are always released).

```yaml
steps:
  - id: open_menu
    click: {image: img/file_menu.png}
  - type: {text: "hello"}
  - key: {press: ctrl+s}
```

Try it without touching your desktop (input backends arrive in milestone M2):

```bash
j5 validate examples/hello.yaml
j5 run --dry-run examples/hello.yaml
```

## Layout

```
src/j5_macro/
  protocol/   message envelope, typed commands and events, error codes
  engine/     the engine process: commands, runs, persistence
  client/     asyncio client that launches and talks to the engine
  script/     script model and the YAML loader
  steps/      step specs, registry, built-in steps, editor JSON Schema
  expr/       safe expression evaluator and ${} interpolation
  runtime/    the interpreter (program counter, debugger controls, dry-run)
  input/      mouse and keyboard masters and their strategies (M2, M5)
  backends/   platform primitives: x11, windows (M2, M6); wayland later
  vision/     screen capture and image matching (M3)
  cli/        the `j5` command
docs/contract.md
examples/
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && mypy && pytest
```

Platform extras: `pip install -e ".[x11,vision]"` on Linux, `".[windows,vision]"` on Windows.

## Roadmap

Tracked as GitHub milestones:

| Milestone | Scope |
| --- | --- |
| M0 | Protocol types and the engine process skeleton |
| M1 | Interpreter, step registry, dry-run and the CLI |
| M2 | X11 backend with `direct` and `linear` input |
| M3 | Vision: `wait_image` and image-based clicks |
| M4 | Debugger: step mode, breakpoints, inspection |
| M5 | Human-like mouse and keyboard strategies |
| M6 | Windows backend |
| M7 | Script management commands for the GUI |
