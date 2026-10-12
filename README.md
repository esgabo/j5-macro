# j5-macro

> "Need input!" — Johnny 5

Scriptable, debuggable desktop automation for Windows and Linux. Write macros as YAML steps
(click, type, wait for an image, branch, loop), run them step by step with breakpoints, and
let the mouse and keyboard move like a person instead of teleporting.

**Status:** planning / early scaffolding. Nothing runs yet.

## Design

The full design (in Spanish) is in [`docs/contract.md`](docs/contract.md). The short version:

- A **headless engine** runs in its own process and speaks a JSON-lines protocol:
  commands in, events out.
- The **CLI**, a future **GUI** and a **DAP debugger** (VS Code) are all clients of that protocol.
- **Mouse and keyboard masters** plan every movement with a strategy (`direct`, `linear` or
  `human`) and drive a thin per-platform **backend**.
- Runs are **reproducible** (seeded), **inspectable** (every step emits events) and
  **safe to abort** (held keys and buttons are always released).

```yaml
steps:
  - id: abrir_menu
    click: {image: img/menu_archivo.png}
  - type: {text: "hola"}
  - key: {press: ctrl+s}
```

## Layout

```
src/j5_macro/
  protocol/   message envelope, commands and events
  engine/     interpreter, run state machine, debugger
  steps/      step registry and built-in steps
  input/      mouse and keyboard masters and their strategies
  backends/   platform primitives (x11, windows; wayland later)
  vision/     screen capture and image matching
  cli/        the `j5` command
docs/contract.md
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
